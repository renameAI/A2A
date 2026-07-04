"""파이프라인 노드 이벤트 테스트 — DAG 시각화의 데이터 계약 (오프라인)."""
from fastapi.testclient import TestClient

from app.main import app
from tests.test_product import DIVEIN_TEXT, INTENT, _run_job

client = TestClient(app)


def _node_events(logs: list[dict]) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {}
    for e in logs:
        if e.get("type") in ("node_start", "node_end"):
            events.setdefault(e["node"], []).append(e)
    return events


def _onboard_id() -> str:
    job = _run_job("/product/onboard", {
        "assets": [{"type": "text", "content": DIVEIN_TEXT}]})
    return job["result"]["company_id"]


class TestNodeEvents:
    def test_onboard_pipeline_nodes(self):
        """Represent 파이프라인: fetch → mock.parse → gate → audit 수명주기."""
        job = _run_job("/product/onboard", {
            "assets": [{"type": "text", "content": DIVEIN_TEXT}]})
        events = _node_events(job["logs"])
        for node in ("fetch", "mock.parse", "gate", "audit"):
            assert node in events, f"{node} 노드 이벤트 누락"
            types = [e["type"] for e in events[node]]
            assert types == ["node_start", "node_end"], f"{node} 수명주기 불완전"
            assert events[node][1]["status"] == "ok"
            # 소요 시간 계산 가능 (end.t >= start.t)
            assert events[node][1]["t"] >= events[node][0]["t"]
        assert "elapsed" in job

    def test_gate_failure_marks_node_error(self):
        """실패 지점이 DAG에서 빨간 노드로 보이도록 — gate 노드 error 마킹."""
        job = _run_job("/product/onboard", {
            "assets": [{"type": "text", "content": "이름: 빈약\n국가: 한국\n산업: saas"}]})
        assert job["status"] == "error"
        events = _node_events(job["logs"])
        assert events["gate"][1]["status"] == "error"     # 409 지점이 노드에 표시
        assert events["fetch"][1]["status"] == "ok"       # 앞 단계는 정상

    def test_judge_and_match_nodes(self):
        company_id = _onboard_id()
        job = _run_job("/product/match", {
            "company_id": company_id, "intent": INTENT, "pool": "external"})
        events = _node_events(job["logs"])
        assert {"synth", "search"} <= set(events)

        job = _run_job("/product/judge", {
            "company_id": company_id, "candidate_id": "ext-livi-hanoi",
            "intent": INTENT})
        events = _node_events(job["logs"])
        assert {"gate.dealbreaker", "rules.judge", "audit"} <= set(events)

    def test_negotiate_dynamic_round_nodes(self):
        """협상: 라운드별 동적 노드 + depth(중첩) + 종료 노드."""
        company_id = _onboard_id()
        job = _run_job("/product/negotiate", {
            "company_id": company_id, "candidate_id": "ext-livi-hanoi",
            "intent": INTENT})
        events = _node_events(job["logs"])
        assert "round1.review" in events
        assert "termination" in events
        # 라운드 내부의 judge 노드는 depth 2로 중첩 표시
        inner = [e for e in job["logs"]
                 if e.get("type") == "node_start"
                 and e["node"] == "gate.dealbreaker"]
        assert inner and inner[0]["depth"] == 2

    def test_compose_sendgate_node(self):
        company_id = _onboard_id()
        judge_job = _run_job("/product/judge", {
            "company_id": company_id, "candidate_id": "ext-livi-hanoi",
            "intent": INTENT})
        job = _run_job("/product/compose", {
            "company_id": company_id, "candidate_id": "ext-livi-hanoi",
            "judge_result": judge_job["result"]["judge_result"], "variants": 2})
        events = _node_events(job["logs"])
        assert {"compose.template", "sendgate"} <= set(events)
