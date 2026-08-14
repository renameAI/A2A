"""T10 — 실패를 정직하게 드러내는지 검증 (감사 확정 medium).

배경: Tavily 쿼터가 소진되면 장애가 아니라 "후보 0곳"이라는 정상 응답으로
위장됐고, 그 0건이 24시간 캐시에 굳었다. LLM 429는 백오프 없이 한 번에
포기해 웨이브 중간에 검색 전체가 죽었다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.errors import EngineError


class FakeResp:
    def __init__(self, status=200, headers=None, payload=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload or {"results": []}
        self.request = None
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class TestTavilyHonesty:
    def _run(self, monkeypatch, status, headers=None):
        import app.connectors.tavily as tv
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake")
        monkeypatch.setattr(tv.httpx, "post",
                            lambda *a, **k: FakeResp(status, headers))
        called = {"ddg": False}
        monkeypatch.setattr(tv, "ddg_search",
                            lambda *a, **k: (called.__setitem__("ddg", True), [])[1])

        class S:
            fetch_timeout = 5
        return tv, S(), called

    @pytest.mark.parametrize("status,code", [
        (401, "search_unavailable"), (403, "search_unavailable"),
        (429, "search_rate_limited"),
    ])
    def test_auth_and_quota_failures_raise(self, monkeypatch, status, code):
        """키가 죽었거나 쿼터가 소진된 것은 폴백 대상이 아니다 — 올린다."""
        tv, settings, called = self._run(monkeypatch, status)
        with pytest.raises(EngineError) as ei:
            tv.search("q", settings)
        assert ei.value.code == code
        assert called["ddg"] is False, "인증·쿼터 실패를 DDG로 덮으면 안 된다"

    def test_rate_limit_message_includes_retry_after(self, monkeypatch):
        tv, settings, _ = self._run(monkeypatch, 429, {"Retry-After": "42"})
        with pytest.raises(EngineError) as ei:
            tv.search("q", settings)
        assert "42" in ei.value.message

    def test_server_error_still_falls_back(self, monkeypatch):
        """5xx·타임아웃은 일시적이므로 표기된 폴백을 유지한다."""
        tv, settings, called = self._run(monkeypatch, 503)
        tv.search("q", settings)
        assert called["ddg"] is True


class TestEmptyResultsNotCached:
    def test_empty_hits_are_not_written(self, tmp_path, monkeypatch):
        """일시 장애를 24시간짜리 '후보 0곳'으로 굳히지 않는다."""
        monkeypatch.setenv("A2A_CACHE_DIR", str(tmp_path))
        from app.ingest import websearch as ws
        ws._cache_put("빈 쿼리", [])
        assert ws._cache_get("빈 쿼리") is None
        ws._cache_put("찬 쿼리", [{"title": "t", "url": "u", "snippet": "s"}])
        assert ws._cache_get("찬 쿼리") is not None


class TestLlmBackoff:
    def test_retry_after_header_respected(self):
        from app.engine.llm import _OpenAICompatExtractor as E
        assert E._backoff_seconds(FakeResp(429, {"Retry-After": "7"}), 1) == 7.0

    def test_retry_after_capped(self):
        """서버가 터무니없이 긴 값을 주더라도 job 예산 안에 머문다."""
        from app.engine.llm import _OpenAICompatExtractor as E
        assert E._backoff_seconds(FakeResp(429, {"Retry-After": "99999"}), 1) == 30.0

    def test_exponential_growth_with_jitter(self):
        from app.engine.llm import _OpenAICompatExtractor as E
        r = FakeResp(429)
        waits = [E._backoff_seconds(r, a) for a in (1, 2, 3, 4)]
        assert all(w > 0 for w in waits)
        # 지터가 있으므로 단조 증가를 강제하지 않는다 — 상한만 본다
        assert max(waits) <= 16.0 * 1.5
