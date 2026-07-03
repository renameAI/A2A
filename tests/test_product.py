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
