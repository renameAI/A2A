"""Phase 1 합격 기준 테스트 — 각 테스트에 대응 PRD ID를 명시한다."""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

DIVEIN_ASSET = """이름: 다이브인그룹
국가: 한국
도시: 서울
산업: hospitality_renovation
설명: 노후 호텔 객실을 예술 경험형 상품으로 전환하는 스타트업. 매출 쉐어 구조.
문제: 노후 호텔 객실의 매출 정체와 리뉴얼 자본 부담
솔루션: 저자본·무철거 예술 경험형 객실 전환, 매출 쉐어
타겟: 노후 객실을 보유한 중소 호텔 오너
판매가치: 매출, 비용
레퍼런스: 성수 Poco Hotel 전환, 한국 부티크 호텔 업셀링 데이터
판매의향: 매우 적극적
"""


def _represent_divein() -> dict:
    res = client.post("/v1/represent", json={
        "assets": [{"type": "text", "content": DIVEIN_ASSET}]})
    assert res.status_code == 200
    return res.json()


def _intent(**kw) -> dict:
    return {"value_props": ["revenue_growth"], "target_region": "베트남", **kw}


def _poll_job(job_id: str) -> dict:
    res = client.get(f"/v1/jobs/{job_id}")
    assert res.status_code == 200
    return res.json()


# ── Represent ────────────────────────────────────────────────────────

class TestRepresent:
    def test_three_form_output(self):
        """REP-01: 한 번의 호출로 3형 출력(프로필+임베딩+온톨로지 앵커)."""
        data = _represent_divein()
        assert data["profile"]["basic"]["name"] == "다이브인그룹"
        assert len(data["embedding"]) == 16
        categories = {a["category"] for a in data["ontology_anchors"]}
        assert {"industry", "region", "stage"} <= categories
        assert data["minimum_met"] is True

    def test_provenance(self):
        """REP-03: 모든 추론 가능 필드에 provenance."""
        p = _represent_divein()["profile"]
        assert p["problem_solved"]["provenance"] == "stated"
        assert p["willingness_sell"] == "very_high"

    def test_below_minimum_409(self):
        """REP-06: 최소 프로필 미달 → 409 + 보강 질문. 풀에 넣지 않는다."""
        res = client.post("/v1/represent", json={
            "assets": [{"type": "text", "content": "이름: 자료빈약컴퍼니\n국가: 한국"}]})
        assert res.status_code == 409
        err = res.json()["error"]
        assert err["code"] == "profile_below_minimum"
        assert len(err["details"]["open_questions"]) >= 3   # 문제·솔루션·타겟·VP

    def test_dialogue_fills_gap(self):
        """REP-07: 보강 대화로 부족 항목만 채워 통과."""
        res = client.post("/v1/represent", json={
            "assets": [{"type": "text",
                        "content": "이름: 미니컴퍼니\n국가: 한국\n산업: saas\n설명: 스타트업"}],
            "dialogue": [
                {"q": "문제", "a": "중소기업 재고 관리 비효율"},
                {"q": "솔루션", "a": "AI 재고 예측 SaaS"},
                {"q": "타겟", "a": "중소 유통사"},
                {"q": "판매가치", "a": "비용"}]})
        assert res.status_code == 200


# ── Retrieve ─────────────────────────────────────────────────────────

class TestRetrieve:
    def test_complementarity_not_similarity(self):
        """RET-02: 보완성 검색 — 동종 경쟁사가 상위에 오지 않는다."""
        profile = _represent_divein()["profile"]
        res = client.post("/v1/retrieve", json={
            "requester_profile": profile, "intent": _intent(),
            "direction": "sell_outreach", "pool": "external", "k": 5})
        assert res.status_code == 200
        data = res.json()
        ids = [c["company_id"] for c in data["candidates"]]
        assert ids[0] == "ext-livi-hanoi"            # 지역 씨앗 + 보완성 1위
        top2 = ids[:2]
        assert "ext-competitor-interior" not in top2  # 경쟁사 강등
        assert "ext-hanoi-luxury-new" not in top2     # 노후 문제 없음 → 배제
        assert data["synthesized_counterpart"]        # 1단 합성 결과 감사용 (RET-01)

    def test_match_points_present(self):
        """RET-03: 후보마다 검색 근거(매칭 포인트) 포함."""
        profile = _represent_divein()["profile"]
        res = client.post("/v1/retrieve", json={
            "requester_profile": profile, "intent": _intent(),
            "direction": "sell_outreach", "pool": "both", "k": 10})
        for c in res.json()["candidates"]:
            assert c["match_points"]
            assert c["pool"] in {"members", "external"}   # RET-04 이중 풀

    def test_no_strong_candidate_422(self):
        """RET-06: 강한 후보 없으면 억지로 채우지 않고 422."""
        res = client.post("/v1/retrieve", json={
            "requester_profile": {
                "basic": {"name": "핀텍스", "country": "한국", "industry": "fintech"},
                "description": "결제 사기 탐지 스타트업",
                "problem_solved": {"value": "온라인 결제 사기 피해", "provenance": "stated"},
                "solution": {"value": "실시간 사기 탐지 API", "provenance": "stated"},
                "target_customer": {"value": "핀테크 PG사", "provenance": "stated"},
                "sell_value_props": ["cost_reduction"]},
            "intent": {"value_props": ["cost_reduction"], "target_region": "일본"},
            "direction": "sell_outreach", "pool": "external"})
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "no_strong_candidate"


# ── Judge (비동기) ───────────────────────────────────────────────────

def _judge_payload(counterpart: dict, vantage="seller",
                   objective="exploration_budget", **kw) -> dict:
    profile = _represent_divein()["profile"]
    return {"vantage": vantage, "objective": objective,
            "self_profile": profile,
            "self_private_state": {"items": [
                {"key": "PMS 접근 권한", "value": "매출 쉐어 정산의 선결 조건",
                 "source": "observed"},
                {"key": "전략 단계", "value": "동남아 레퍼런스 0 — 첫 레퍼런스 확보가 단계 목표",
                 "source": "observed"}]},
            "counterpart_profile": counterpart,
            "intent": _intent(), **kw}


def _livi_profile(willingness=None) -> dict:
    return {"basic": {"name": "리비 하노이 함롱", "country": "베트남", "city": "하노이",
                      "industry": "hospitality"},
            "description": "하노이 호안끼엠 중심부 아파트 호텔. 노후 객실 보유, "
                           "객실 매출 정체, 리뉴얼 자본 부담. 객실 수 적음.",
            "problem_solved": {"value": "노후 객실의 매출 정체 해소",
                               "provenance": "inferred", "confidence": 0.7},
            "solution": {"value": "아파트 호텔 숙박 운영",
                         "provenance": "inferred", "confidence": 0.6},
            "target_customer": {"value": "하노이 방문 관광객",
                                "provenance": "inferred", "confidence": 0.7},
            "willingness_purchase": willingness}


class TestJudge:
    def test_async_202_and_structured_judgment(self):
        """SYS-02 + JDG-01/02: 202 접수 → job 완료 → 구조화 판단(5차원+근거)."""
        res = client.post("/v1/judge", json=_judge_payload(_livi_profile()))
        assert res.status_code == 202
        job = _poll_job(res.json()["job_id"])
        assert job["status"] == "done"
        r = job["result"]
        dims = {d["dimension"] for d in r["category_judgments"]}
        assert dims == {"industry_fit", "purpose_alignment", "resource_complementarity",
                        "stage_compatibility", "demonstrability"}
        for d in r["category_judgments"]:
            assert d["rationale"]                     # 평평한 체크리스트 금지
        assert r["fit_reasons"]                       # 적합근거 ≥ 1
        assert r["decision"] in {"recommend", "conditional", "hold", "terminate"}
        assert r["match_summary"]["reference"]        # JDG-10
        assert "risk_triage" in r["reasoning_moves"]

    def test_buyer_lens_has_seven_dimensions(self):
        """JDG-02/06: buy 렌즈는 +2차원(대체재·기회비용) — 같은 로직, 파라미터 교체."""
        payload = _judge_payload(_livi_profile(), vantage="buyer",
                                 objective="willingness_gate")
        # buyer 렌즈: 나=호텔, 상대=다이브인으로 뒤집기
        payload["self_profile"], payload["counterpart_profile"] = \
            payload["counterpart_profile"], payload["self_profile"]
        res = client.post("/v1/judge", json=payload)
        job = _poll_job(res.json()["job_id"])
        dims = {d["dimension"] for d in job["result"]["category_judgments"]}
        assert {"substitute_comparison", "opportunity_cost"} <= dims
        assert len(dims) == 7

    def test_willingness_changes_decision(self):
        """JDG-08: 동일 쌍에 Willingness만 바꾸면 결정이 달라진다."""
        res_high = client.post("/v1/judge",
                               json=_judge_payload(_livi_profile("high")))
        res_low = client.post("/v1/judge",
                              json=_judge_payload(_livi_profile("very_low")))
        d_high = _poll_job(res_high.json()["job_id"])["result"]["decision"]
        d_low = _poll_job(res_low.json()["job_id"])["result"]["decision"]
        assert d_high == "conditional"
        assert d_low == "hold"
        assert d_high != d_low

    def test_deal_breaker(self):
        """JDG-04: deal-breaker → 결렬·비노출 (423 계약, 비동기라 job error로 수렴)."""
        enterprise = {"basic": {"name": "글로벌 에어로", "country": "미국",
                                "industry": "manufacturing"},
                      "description": "글로벌 대기업 항공기 제조사",
                      "problem_solved": {"value": "부품 공급망", "provenance": "stated"},
                      "solution": {"value": "항공기 제조", "provenance": "stated"},
                      "target_customer": {"value": "항공사", "provenance": "stated"}}
        res = client.post("/v1/judge", json=_judge_payload(enterprise))
        job = _poll_job(res.json()["job_id"])
        assert job["status"] == "error"
        assert job["error"]["code"] == "deal_breaker"
        assert job["error"]["details"]["dimension"] == "stage_compatibility"

    def test_message_body_forbidden(self):
        """JDG-07: 메시지 본문은 판단 입력 금지 — 정의 외 필드는 400."""
        payload = _judge_payload(_livi_profile())
        payload["message_body"] = "잘 쓴 콜드메일 텍스트"
        res = client.post("/v1/judge", json=payload)
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "invalid_input"

    def test_idempotency(self):
        """API §0: 동일 client_request_id 재시도 → 같은 job."""
        payload = _judge_payload(_livi_profile(), client_request_id="req-777")
        j1 = client.post("/v1/judge", json=payload).json()["job_id"]
        j2 = client.post("/v1/judge", json=payload).json()["job_id"]
        assert j1 == j2


# ── Compose ──────────────────────────────────────────────────────────

def _judge_result(counterpart=None) -> dict:
    res = client.post("/v1/judge",
                      json=_judge_payload(counterpart or _livi_profile("high")))
    return _poll_job(res.json()["job_id"])["result"]


class TestCompose:
    def test_outreach_with_claim_trace(self):
        """CMP-01/02/06: 아웃리치 초안 + 주장→근거 추적 + 발송 차단."""
        jr = _judge_result()
        profile = _represent_divein()["profile"]
        res = client.post("/v1/compose", json={
            "mode": "outreach", "judge_result": jr,
            "self_profile": profile, "counterpart_profile": _livi_profile("high"),
            "lens": "sell", "variants": 2})
        assert res.status_code == 200
        data = res.json()
        assert data["send_blocked"] is True           # 사람 승인 게이트
        assert len(data["messages"]) == 2             # CMP-05: sell = A/B 다수
        for msg in data["messages"]:
            assert msg["claim_trace"]                 # 근거 없는 주장 금지
            for ct in msg["claim_trace"]:
                assert ct["fit_reason_ref"].startswith("fit_reasons[")
            assert msg["reference_used"]

    def test_buy_lens_single_variant(self):
        """CMP-05: Purchase = 끌어당기기 — 1안, 톤 없음."""
        jr = _judge_result()
        profile = _represent_divein()["profile"]
        res = client.post("/v1/compose", json={
            "mode": "recommendation_summary", "judge_result": jr,
            "self_profile": _livi_profile("high"), "counterpart_profile": profile,
            "lens": "buy", "variants": 3})
        assert len(res.json()["messages"]) == 1       # buy는 강제 1안


# ── Negotiate (7-A 협상 왕복) ────────────────────────────────────────

def _negotiate_payload(buyer_private=None, max_rounds=3) -> dict:
    seller = _represent_divein()["profile"]
    buyer = _livi_profile("high")
    buyer["purchase_value_props"] = ["revenue_growth"]
    return {"seller_profile": seller,
            "seller_private_state": {"items": [
                {"key": "최저선:share", "value": "8:2", "source": "observed"},
                {"key": "원상 복구", "value": "불만족 시 원상 복구 보장 가능",
                 "source": "observed"}]},
            "buyer_profile": buyer,
            "buyer_private_state": buyer_private or {"items": []},
            "intent": _intent(), "max_rounds": max_rounds}


def _run_negotiation(payload) -> dict:
    res = client.post("/v1/negotiate", json=payload)
    assert res.status_code == 202
    job = _poll_job(res.json()["job_id"])
    assert job["status"] == "done"
    return job["result"]


class TestNegotiate:
    def test_recoverable_rejection_converges(self):
        """NEG-01/03/04: 풀리는 거절 → 손잡이 묶음 조정 → 합의 수렴."""
        result = _run_negotiation(_negotiate_payload())
        assert result["termination"] == "agreement"
        assert result["rounds_used"] <= 3
        counters = [r for r in result["rounds"] if r["response"] == "counter"]
        assert counters, "최소 1회 거절→재제안 왕복이 있어야 협상"
        rej = counters[0]["rejection"]
        assert rej["recoverable"] is True
        assert rej["dimension"]                       # NEG-02: 차원 매핑 구조화 사유
        # NEG-04: 재제안 라운드에 손잡이 묶음(2개 이상) 동시 조정 기록
        adjusted_rounds = [r for r in result["rounds"] if r["knobs_adjusted"]]
        assert adjusted_rounds
        assert len(adjusted_rounds[0]["knobs_adjusted"]) >= 2

    def test_unrecoverable_breakdown(self):
        """NEG-03: 못 푸는 거절(실행 의지 부재)은 재제안 없이 결렬."""
        result = _run_negotiation(_negotiate_payload(
            buyer_private={"items": [{"key": "실행 의지", "value": "없음",
                                      "source": "simulated"}]}))
        assert result["termination"] == "breakdown"
        assert result["rounds_used"] == 1
        assert result["rounds"][0]["rejection"]["recoverable"] is False

    def test_round_limit(self):
        """NEG-05: 라운드 상한 내 반드시 종료 — 무한 협상 0건."""
        result = _run_negotiation(_negotiate_payload(max_rounds=1))
        assert result["termination"] in {"agreement", "breakdown", "round_limit"}
        assert result["rounds_used"] <= 1


# ── E2E: 한 사이클 (기획서 부록 A) ──────────────────────────────────

def test_full_cycle():
    """6월 합격 기준: 입력 → 후보 → 판단 → 초안 생성 흐름이 한 번에 돈다."""
    # 1. Represent
    rep = _represent_divein()
    # 2. Retrieve
    ret = client.post("/v1/retrieve", json={
        "requester_profile": rep["profile"], "intent": _intent(),
        "direction": "sell_outreach", "pool": "external", "k": 5}).json()
    top = ret["candidates"][0]
    assert top["company_id"] == "ext-livi-hanoi"
    # 3. Judge (보내는 쪽 · 탐색 예산)
    res = client.post("/v1/judge", json=_judge_payload(_livi_profile()))
    jr = _poll_job(res.json()["job_id"])["result"]
    assert jr["decision"] == "conditional"            # #01: 조건부 진행 권고
    assert jr["deal_structure"]                       # 소규모 PoC 딜 구조
    # 4. Compose (콜드메일 초안 — 발송은 사람)
    comp = client.post("/v1/compose", json={
        "mode": "outreach", "judge_result": jr,
        "self_profile": rep["profile"], "counterpart_profile": _livi_profile(),
        "lens": "sell", "variants": 1}).json()
    assert comp["send_blocked"] is True
    assert "리비 하노이 함롱" in comp["messages"][0]["body"]
