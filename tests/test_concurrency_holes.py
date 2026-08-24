"""잡과 사용자가 겹칠 때 조용히 지워지던 것들.

이론 스캔(정합성 렌즈)이 잡은 lost update 두 곳 + 에러 분류 한 곳:
- 온보딩 잡이 수십 초 LLM을 도는 사이 사용자가 친 정정·대화가, 잡의
  최종 put(스냅샷 전체 교체)에 덮여 사라졌다.
- refine 잡이 도는 사이 끝난 심층 판독이 같은 방식으로 사라졌다 —
  검색 직후 화면이 판독을 자동으로 돌리므로 이 겹침은 기본 흐름이다.
- deep_read가 비용 캡(402)을 "사이트 미확인"으로 뭉갰다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.saas.router import _graft_readings, _merge_user_input
from app.saas.store import LocalSaasStore


def _store(tmp_path):
    return LocalSaasStore(str(tmp_path / "t.db"))


class TestMergeUserInput:
    def test_corrections_typed_during_job_survive(self, tmp_path):
        """반영 끝난 정정만 비우고, 도중 들어온 것은 남긴다."""
        st = _store(tmp_path)
        st.put("onboarding", "ws", "s1",
               {"corrections": ["a", "b", "c"], "dialogue": [], "assets": []})
        doc = {"corrections": ["a"], "dialogue": [], "assets": []}
        _merge_user_input(st, "ws", "s1", doc, consumed_fixes=1)
        assert doc["corrections"] == ["b", "c"]

    def test_dialogue_appended_during_job_survives(self, tmp_path):
        st = _store(tmp_path)
        st.put("onboarding", "ws", "s1",
               {"corrections": [], "dialogue": [{"q": "1"}, {"q": "2"}],
                "assets": []})
        doc = {"corrections": [], "dialogue": [{"q": "1"}], "assets": []}
        _merge_user_input(st, "ws", "s1", doc)
        assert len(doc["dialogue"]) == 2

    def test_missing_fresh_doc_is_a_noop(self, tmp_path):
        doc = {"corrections": ["x"], "dialogue": []}
        out = _merge_user_input(_store(tmp_path), "ws", "없음", doc)
        assert out["corrections"] == ["x"]


class TestGraftReadings:
    def test_deep_read_done_during_refine_survives(self, tmp_path):
        """스냅샷 put이 판독을 지우면 레이더·접점·LLM 비용이 함께 사라진다."""
        st = _store(tmp_path)
        st.put("lead_request", "ws", "r1", {"candidates": [
            {"company_id": "c1", "ontology": {"axes": {}},
             "hunter": {"status": "ok", "contacts": []},
             "deep_read": {"status": "done"}}], "pool": []})
        doc = {"candidates": [{"company_id": "c1"}],
               "pool": [{"company_id": "c1"}]}
        _graft_readings(st, "ws", "r1", doc)
        assert doc["candidates"][0]["deep_read"]["status"] == "done"
        assert doc["pool"][0]["hunter"]["status"] == "ok"

    def test_fresh_reading_wins_over_stale(self, tmp_path):
        """판독은 항상 최신 문서에 병합되므로 fresh 쪽이 새것이다."""
        st = _store(tmp_path)
        st.put("lead_request", "ws", "r1", {"candidates": [
            {"company_id": "c1", "ontology": {"v": "새것"}}], "pool": []})
        doc = {"candidates": [{"company_id": "c1", "ontology": {"v": "옛것"}}],
               "pool": []}
        _graft_readings(st, "ws", "r1", doc)
        assert doc["candidates"][0]["ontology"]["v"] == "새것"

    def test_unknown_company_untouched(self, tmp_path):
        st = _store(tmp_path)
        st.put("lead_request", "ws", "r1", {"candidates": [
            {"company_id": "c9", "ontology": {"x": 1}}], "pool": []})
        doc = {"candidates": [{"company_id": "c1"}], "pool": []}
        _graft_readings(st, "ws", "r1", doc)
        assert "ontology" not in doc["candidates"][0]


class TestErrorTaxonomy:
    def test_cost_cap_not_swallowed_as_no_site(self):
        """402(예산 결정)를 모델 실패 폴백으로 바꾸면 거짓 상태가 영구 저장된다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.deep_read)
        swallow = src.find("except Exception as e:")
        reraise = src.find("except EngineError:")
        assert reraise != -1 and reraise < swallow


class TestListLimit:
    def test_limit_is_pushed_to_the_store(self):
        """파이썬 [:limit]은 전 문서를 받아온 뒤 자른다 — 읽기 증폭."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.list_requests)
        assert "limit=limit" in src
        assert ")[:limit]" not in src   # 받은 뒤 자르는 옛 패턴
