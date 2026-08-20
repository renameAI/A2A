"""모델 티어링·판독 캐시·실측 비용.

셋 다 같은 문제를 다룬다: 웨이브1이 판정용 모델로 정리 작업까지 돌리고,
같은 회사를 요청마다 다시 읽고, 그 비용을 추정치로만 알고 있었다.
"""
import pytest

from app.config import Settings
from app.engine.company_ontology import AXES, _ont_cache, read_company
from app.engine.llm import get_extractor


class _Spy:
    def __init__(self): self.n = 0
    def extract_json(self, *a, **k):
        self.n += 1
        return {"axes": {x: {"value": "", "status": "unknown", "evidence": ""}
                         for x, _ in AXES},
                "search_keywords": [], "signals": [], "contacts": [],
                "business_language": "en",
                "reachability": {"p": 0.5, "why": "w"}}


def _co(name="A"):
    return {"name": name, "what": "w", "signal": "", "url": f"https://{name}.com"}


class TestTiering:
    def _env(self, monkeypatch, fast=None):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        monkeypatch.setenv("OPENAI_MODEL", "big")
        if fast is None:
            monkeypatch.delenv("OPENAI_MODEL_FAST", raising=False)
        else:
            monkeypatch.setenv("OPENAI_MODEL_FAST", fast)
        return Settings()

    def _model(self, ex):
        return next(getattr(ex, a) for a in vars(ex) if "model" in a.lower())

    def test_fast_tier_uses_the_light_model(self, monkeypatch):
        s = self._env(monkeypatch, fast="small")
        assert self._model(get_extractor(s)) == "big"
        assert self._model(get_extractor(s, tier="fast")) == "small"

    def test_unset_fast_model_changes_nothing(self, monkeypatch):
        """설정을 안 하면 동작이 그대로여야 한다 — 조용한 성능 저하 없음."""
        s = self._env(monkeypatch)
        assert self._model(get_extractor(s, tier="fast")) == "big"


class TestOntologyCache:
    def test_same_company_is_read_once(self):
        spy = _Spy()
        a = read_company(spy, _co("UNDO"), requester="귤메달")
        b = read_company(spy, _co("UNDO"), requester="귤메달")
        assert spy.n == 1 and a is b

    def test_requester_is_part_of_the_key(self):
        """문턱은 '누가 묻는가'에 달린 판정이라 요청 기업이 다르면 다시 읽는다."""
        spy = _Spy()
        read_company(spy, _co("UNDO"), requester="귤메달")
        read_company(spy, _co("UNDO"), requester="할리케이")
        assert spy.n == 2

    def test_deep_read_is_not_served_from_the_snippet_entry(self):
        """사이트 본문을 읽는 판독은 스니펫 판독과 다른 결과다."""
        spy = _Spy()
        read_company(spy, _co("UNDO"), requester="r")
        read_company(spy, _co("UNDO"), requester="r", site_text="본문")
        assert spy.n == 2

    def test_purpose_and_region_are_part_of_the_key(self):
        spy = _Spy()
        read_company(spy, _co("X"), requester="r", purpose="revenue")
        read_company(spy, _co("X"), requester="r", purpose="poc")
        read_company(spy, _co("X"), requester="r", purpose="poc", region="일본")
        assert spy.n == 3

    def test_cache_is_bounded(self):
        spy = _Spy()
        for i in range(520):
            read_company(spy, _co(f"C{i}"), requester="r")
        assert len(_ont_cache) <= 501


class TestMeasuredSpend:
    def test_tokens_accumulate_and_land_on_the_result(self):
        from app import progress
        from app.jobs import Job, JobStatus, store as job_store

        job, _ = job_store.create(ws="ws-boram")

        def fn():
            progress.add_tokens(1000, 500)
            progress.add_tokens(2000, 250)
            return {"ok": True}

        job_store.run(job, fn)
        assert job.status == JobStatus.done
        spend = job.result["spend"]
        assert spend["calls"] == 2
        assert spend["tokens_in"] == 3000 and spend["tokens_out"] == 750
        assert spend["usd"] > 0

    def test_no_model_calls_means_no_spend_field(self):
        """호출이 없으면 비용 줄을 만들지 않는다 — 0.0을 적으면 잰 것처럼 보인다."""
        from app.jobs import store as job_store
        job, _ = job_store.create(ws="ws-boram")
        job_store.run(job, lambda: {"ok": True})
        assert "spend" not in job.result

    def test_add_tokens_outside_a_job_is_a_noop(self):
        from app import progress
        progress.add_tokens(10, 10)     # 예외 없이 통과해야 한다
