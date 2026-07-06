"""제품 레이어(프론트 백엔드) 테스트 — 비동기 job + 진행 로그 오케스트레이션."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

DIVEIN_TEXT = """이름: 다이브인그룹
국가: 한국
산업: hospitality_renovation
설명: 노후 호텔 객실을 예술 경험형 상품으로 전환하는 스타트업. 매출 쉐어 구조.
문제: 노후 호텔 객실의 매출 정체와 리뉴얼 자본 부담
솔루션: 저자본·무철거 예술 경험형 객실 전환, 매출 쉐어
타겟: 노후 객실을 보유한 중소 호텔 오너
판매가치: 매출, 비용
레퍼런스: 성수 Poco Hotel 전환, 한국 부티크 호텔 업셀링 데이터"""

INTENT = {"value_props": ["revenue_growth"], "target_region": "베트남"}


def _run_job(path: str, body: dict) -> dict:
    """202 접수 → job 폴링 → 완료된 job 전체(result/error/logs) 반환."""
    res = client.post(path, json=body)
    assert res.status_code == 202, res.text
    job_id = res.json()["job_id"]
    for _ in range(50):
        job = client.get(f"/product/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
    raise AssertionError("job이 완료되지 않음")


def _onboard() -> str:
    job = _run_job("/product/onboard", {
        "assets": [{"type": "text", "content": DIVEIN_TEXT}],
        "dialogue": [{"q": "판매의향", "a": "매우 적극적"}],
        "private_state": [
            {"key": "최저선:share", "value": "8:2", "source": "observed"},
            {"key": "전략 단계", "value": "동남아 레퍼런스 0 — 첫 레퍼런스 확보",
             "source": "observed"}]})
    assert job["status"] == "done"
    return job["result"]["company_id"]


def test_onboard_below_minimum_then_recover():
    """부족한 자료 → job error(409 계약) + 보강 질문 → 답변 반영 → 통과."""
    sparse = {"assets": [{"type": "text", "content": "이름: 미니\n국가: 한국\n산업: saas"}]}
    job = _run_job("/product/onboard", sparse)
    assert job["status"] == "error"
    assert job["error"]["code"] == "profile_below_minimum"
    assert job["error"]["details"]["open_questions"]

    job = _run_job("/product/onboard", {**sparse, "dialogue": [
        {"q": "문제", "a": "중소 유통사의 재고 낭비"},
        {"q": "솔루션", "a": "AI 수요예측"},
        {"q": "타겟", "a": "중소 유통사"},
        {"q": "판매가치", "a": "비용"}]})
    assert job["status"] == "done"
    assert job["result"]["company_id"].startswith("co-")


def test_below_minimum_carries_clarify_options():
    """게이트 미달 시 보강 질문마다 4지선다(label+hint)가 함께 온다."""
    sparse = {"assets": [{"type": "text", "content": "이름: 미니\n국가: 한국\n산업: saas"}]}
    job = _run_job("/product/onboard", sparse)
    assert job["status"] == "error"
    clarify = job["error"]["details"]["clarify"]
    questions = job["error"]["details"]["open_questions"]
    assert len(clarify) == len(questions)
    for item, q in zip(clarify, questions):
        assert item["question"] == q               # 질문 원문 그대로 (매핑 계약)
        assert item["why"]
        assert len(item["options"]) == 4
        for opt in item["options"]:
            assert opt["label"] and opt["hint"]
    # 가치 제안 질문의 선지는 4축 번역이어야 한다 (Mock 규칙 선지 기준)
    vp = next(i for i in clarify if "가치" in i["question"])
    assert {o["label"] for o in vp["options"]} == {"매출 증대", "비용 절감",
                                                   "임팩트", "문제 해결"}


def test_reanalyze_accepts_full_question_text():
    """프론트가 보강 질문 '원문'을 그대로 되돌려줘도 프로필이 채워진다 (무한 반복 방지).

    이전 버그: 질문 원문↔정규 필드 매핑이 프론트에 있어 '솔루션' 질문이 '문제'로
    잘못 매핑돼 게이트가 계속 실패했다. 매핑을 백엔드로 옮긴 뒤 회귀 테스트.
    """
    sparse = {"assets": [{"type": "text", "content": "이름: 미니\n국가: 한국\n산업: saas"}]}
    job = _run_job("/product/onboard", sparse)
    questions = job["error"]["details"]["open_questions"]     # 백엔드가 준 원문
    # 판별 키워드는 구체적인 것부터 (value_prop 질문에도 '문제'가 들어있으므로 '가치'를 먼저)
    answers = [("가치", "비용 절감으로 매출 개선"), ("방식", "AI 수요예측"),
               ("팔고", "중소 유통사"), ("문제", "중소 유통사의 재고 낭비")]

    def _answer(q: str) -> str:
        for kw, a in answers:
            if kw in q:
                return a
        return "적극적"

    dialogue = [{"q": q, "a": _answer(q)} for q in questions]  # 원문 그대로 회신
    job = _run_job("/product/onboard", {**sparse, "dialogue": dialogue})
    assert job["status"] == "done", job.get("error")
    prof = job["result"]["profile"]
    assert prof["solution"]["value"] == "AI 수요예측"           # '솔루션'이 실제로 채워짐
    assert prof["sell_value_props"] or prof["purchase_value_props"]


def test_progress_logs_visible():
    """진행 로그 — 수집·청킹·게이트 단계가 기록되어 UI가 과정을 보여줄 수 있다."""
    job = _run_job("/product/onboard", {
        "assets": [{"type": "text", "content": DIVEIN_TEXT}]})
    stages = {entry["stage"] for entry in job["logs"]}
    assert {"수집", "청킹", "게이트", "완료"} <= stages
    assert all("t" in e and "message" in e for e in job["logs"])


def test_full_product_cycle():
    """온보딩 → 후보 → 판단 → 초안(send_blocked) → 협상(시뮬 표시) — 전부 job 경유."""
    company_id = _onboard()

    job = _run_job("/product/match", {
        "company_id": company_id, "intent": INTENT, "pool": "external", "k": 5})
    cands = job["result"]["candidates"]
    assert cands[0]["company_id"] == "ext-livi-hanoi"
    assert cands[0]["name"] == "리비 하노이 함롱"

    job = _run_job("/product/judge", {
        "company_id": company_id, "candidate_id": "ext-livi-hanoi", "intent": INTENT})
    jr = job["result"]["judge_result"]
    assert jr["decision"] == "conditional"

    job = _run_job("/product/compose", {
        "company_id": company_id, "candidate_id": "ext-livi-hanoi",
        "judge_result": jr, "variants": 2})
    assert job["result"]["send_blocked"] is True
    assert len(job["result"]["messages"]) == 2

    job = _run_job("/product/negotiate", {
        "company_id": company_id, "candidate_id": "ext-livi-hanoi", "intent": INTENT})
    assert job["result"]["buyer_simulated"] is True
    assert job["result"]["negotiation"]["termination"] in {
        "agreement", "breakdown", "round_limit"}
    # 협상 라운드 과정이 로그로 보인다
    assert any(e["stage"] == "협상" for e in job["logs"])


def test_judge_deal_breaker_via_product():
    company_id = _onboard()
    job = _run_job("/product/judge", {
        "company_id": company_id, "candidate_id": "ext-global-aero", "intent": INTENT})
    assert job["status"] == "error"
    assert job["error"]["code"] == "deal_breaker"


def test_unknown_company_404():
    res = client.post("/product/match", json={
        "company_id": "co-없음", "intent": INTENT})
    assert res.status_code == 404


def test_upload_pdf_only():
    res = client.post("/product/upload",
                      files={"file": ("자료.txt", b"not a pdf", "text/plain")})
    assert res.status_code == 400
    res = client.post("/product/upload",
                      files={"file": ("IR덱.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert res.status_code == 200
    assert res.json()["path"].endswith(".pdf")


def test_ui_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "받아야 할 것" in res.text
