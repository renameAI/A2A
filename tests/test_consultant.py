"""Consultant 모드 테스트 (CON-01~02) — 오프라인 Mock 스크립트 인터뷰."""
from fastapi.testclient import TestClient

from app.main import app
from tests.test_product import DIVEIN_TEXT, _run_job

client = TestClient(app)


def _onboard_id() -> str:
    job = _run_job("/product/onboard", {
        "assets": [{"type": "text", "content": DIVEIN_TEXT}]})
    return job["result"]["company_id"]


def _turn(company_id: str, history: list) -> dict:
    job = _run_job("/product/consult", {"company_id": company_id,
                                        "history": history})
    assert job["status"] == "done", job.get("error")
    return job["result"]


class TestConsultInterview:
    def test_first_turn_has_question_and_options(self):
        """검증된 패턴: 한 번에 하나의 질문 + 4~6지선다(힌트 포함) + 근거."""
        company_id = _onboard_id()
        turn = _turn(company_id, [])
        assert turn["done"] is False
        assert turn["question"]
        assert turn["why"]                        # 왜 지금 이 질문인가
        assert 3 <= len(turn["options"]) <= 6
        for opt in turn["options"]:
            assert opt["label"] and opt["hint"]   # 선택지마다 함의 힌트
        # 첫 질문은 솔루션 좁히기 (좁히기 순서의 시작)
        assert "제품" in turn["question"] or "분야" in turn["question"]

    def test_slots_accumulate_from_history(self):
        """앞 답변이 filled 슬롯에 누적되고 다음 질문이 이어진다."""
        company_id = _onboard_id()
        turn1 = _turn(company_id, [])
        history = [{"question": turn1["question"], "answer": "부품/모듈/원료 공급"}]
        turn2 = _turn(company_id, history)
        assert turn2["done"] is False
        assert turn2["filled"]["solution"] == "부품/모듈/원료 공급"
        assert turn2["question"] != turn1["question"]   # 같은 질문 반복 금지

    def test_full_interview_ends_with_hypothesis(self):
        """10개 슬롯이 다 차면 done=true + 최종 아웃리치 가설."""
        company_id = _onboard_id()
        history = []
        for _ in range(12):                        # 상한 여유
            turn = _turn(company_id, history)
            if turn["done"]:
                break
            history.append({"question": turn["question"],
                            "answer": turn["options"][0]["label"]})
        assert turn["done"] is True
        assert turn["hypothesis"]
        filled = [v for v in turn["filled"].values() if v]
        assert len(filled) == 10                   # 전 슬롯 확정
        assert len(history) == 10                  # 슬롯당 질문 1개 (중복 없음)

    def test_consult_pipeline_and_audit(self, tmp_path, monkeypatch):
        """파이프라인 노드 이벤트 + 감사 로그(인터뷰=데이터 자산) 기록."""
        import json
        monkeypatch.setenv("A2A_AUDIT_DIR", str(tmp_path / "audit"))
        company_id = _onboard_id()
        job = _run_job("/product/consult", {"company_id": company_id,
                                            "history": []})
        nodes = {e["node"] for e in job["logs"] if e.get("type") == "node_start"}
        assert "consult" in nodes
        files = list((tmp_path / "audit").glob("*.jsonl"))
        entries = [json.loads(line) for f in files for line in f.read_text().splitlines()]
        assert any(e["kind"] == "consult" for e in entries)

    def test_unknown_company_404(self):
        res = client.post("/product/consult", json={"company_id": "co-없음",
                                                    "history": []})
        assert res.status_code == 404
