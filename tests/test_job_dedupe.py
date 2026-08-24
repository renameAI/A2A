"""잡 중복 제출 흡수 — 실측이 근거다.

프로덕션 잡 156건(10일) 분석: 같은 브리프가 2초 안에 5번 제출돼 LLM을 5번
결제했고(유령 요청 4개 동반), 같은 판독이 18초 간격으로 겹쳐 돌았다.
멱등 create는 원래 있었지만 아무도 서명을 안 넘겨서 0/156건 — 이 테스트는
배선(서명 부착)과 의미('활성 잡만' 재사용)를 고정한다.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.jobs import store as job_store
from app.saas.router import _job_sig
from app.saas.store import LocalSaasStore


@pytest.fixture()
def _iso_store(tmp_path, monkeypatch):
    st = LocalSaasStore(str(tmp_path / "j.db"))
    monkeypatch.setattr("app.jobs._SAAS_STORE", st, raising=False)
    monkeypatch.setattr("app.jobs._store", lambda: st)
    job_store._jobs.clear()
    return st


class TestSingleFlight:
    def test_active_job_absorbs_duplicate(self, _iso_store):
        """돌고 있는 같은 서명 → 새 잡을 만들지 않는다."""
        a, existed_a = job_store.create("search_brief:abc", ws="ws1")
        assert not existed_a
        b, existed_b = job_store.create("search_brief:abc", ws="ws1")
        assert existed_b and b.job_id == a.job_id

    def test_done_job_is_not_a_result_cache(self, _iso_store):
        """끝난 잡 재사용은 정당한 재실행에 옛 결과를 돌려준다 — 금지."""
        from app.schemas import JobStatus
        a, _ = job_store.create("search_brief:abc", ws="ws1")
        a.status = JobStatus.done
        job_store._put(a, "search_brief:abc")
        b, existed = job_store.create("search_brief:abc", ws="ws1")
        assert not existed and b.job_id != a.job_id

    def test_different_signature_not_coalesced(self, _iso_store):
        """payload가 다른 refine 두 건은 다른 작업이다."""
        s1 = _job_sig("refine", "r1", ["like-a"], [], [], False)
        s2 = _job_sig("refine", "r1", ["like-b"], [], [], False)
        assert s1 != s2
        a, _ = job_store.create(s1, ws="ws1")
        b, existed = job_store.create(s2, ws="ws1")
        assert not existed and b.job_id != a.job_id

    def test_stale_running_job_does_not_block(self, _iso_store):
        """좀비(오래 정지한 running)가 10분간 새 실행을 막으면 안 된다."""
        a, _ = job_store.create("deep_read:xyz", ws="ws1")
        d = _iso_store.get("job", "ws1", a.job_id)
        d["updated"] = time.time() - 700          # _STALE_AFTER(600s) 초과
        _iso_store.put("job", "ws1", a.job_id, d)
        job_store._jobs.clear()                   # 다른 인스턴스 시뮬레이션
        b, existed = job_store.create("deep_read:xyz", ws="ws1")
        assert not existed and b.job_id != a.job_id

    def test_signature_is_readable(self):
        """op가 사람이 읽게 남는다 — 이번 측정이 result 모양 추측에 의존했다."""
        sig = _job_sig("deep_read", "lr-1", ["c1", "c2"])
        assert sig.startswith("deep_read:")

    def test_coalesced_submit_does_not_rerun(self, _iso_store):
        """합류 시 실행을 다시 걸지 않는다 — 두 번 걸면 그게 중복이다."""
        from app.saas.router import _submit

        class BG:
            def __init__(self): self.tasks = []
            def add_task(self, *a): self.tasks.append(a)

        class U:
            workspace_id = "ws1"

        bg1, bg2 = BG(), BG()
        r1 = _submit(bg1, lambda: {}, U(), sig="insight:aa")
        r2 = _submit(bg2, lambda: {}, U(), sig="insight:aa")
        assert r2["job_id"] == r1["job_id"]
        assert r2.get("coalesced") is True
        assert len(bg1.tasks) == 1 and len(bg2.tasks) == 0
