"""LLM_PROVIDER=openai 별칭 회귀 테스트 — 완전 오프라인 (이슈 #6, 이슈 A).

계약: 키가 있으면 OpenAIExtractor(gpt-5.6-luna 기본), 없으면 조용한 대체 없이
config_error 즉시 실패 — friendli·local·anthropic과 동일한 정직성 규칙.
"""
import pytest

from app.config import Settings
from app.engine.llm import OpenAIExtractor, get_extractor
from app.errors import EngineError


def _settings(monkeypatch, **env) -> Settings:
    for k in ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
              "FRIENDLI_TOKEN", "FRIENDLI_ENDPOINT_ID"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings()


class TestOpenAIProvider:
    def test_key_present_returns_openai_extractor(self, monkeypatch):
        s = _settings(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test")
        ex = get_extractor(s)
        assert isinstance(ex, OpenAIExtractor)
        assert s.openai_model == "gpt-5.6-luna", "기본 모델은 Luna (스펙 확정값)"
        assert s.openai_base_url.endswith("/v1/chat/completions")
        assert ex._thinking_kwargs is False, \
            "OpenAI 모델엔 EXAONE reasoning 토글이 없다"

    def test_missing_key_fails_loud(self, monkeypatch):
        s = _settings(monkeypatch, LLM_PROVIDER="openai")
        with pytest.raises(EngineError) as exc:
            get_extractor(s)
        assert exc.value.code == "config_error"

    def test_llm_enabled_reflects_openai_key(self, monkeypatch):
        assert _settings(monkeypatch, LLM_PROVIDER="openai",
                         OPENAI_API_KEY="sk-x").llm_enabled is True
        assert _settings(monkeypatch, LLM_PROVIDER="openai").llm_enabled is False

    def test_model_and_url_overridable(self, monkeypatch):
        s = _settings(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x",
                      OPENAI_MODEL="gpt-5.6-terra",
                      OPENAI_BASE_URL="https://proxy.internal/v1/chat/completions")
        assert s.openai_model == "gpt-5.6-terra"
        assert s.openai_base_url == "https://proxy.internal/v1/chat/completions"
