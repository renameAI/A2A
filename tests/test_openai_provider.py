"""LLM_PROVIDER=openai 별칭 회귀 테스트 — 완전 오프라인 (이슈 #6, 이슈 A).

계약: 키가 있으면 OpenAIExtractor(gpt-5.6-luna 기본), 없으면 조용한 대체 없이
config_error 즉시 실패 — friendli·local·anthropic과 동일한 정직성 규칙.
"""
import pytest

from app.config import Settings
from app.engine.llm import OpenAIExtractor, get_extractor
from app.errors import EngineError


def _settings(monkeypatch, **env) -> Settings:
    """오프라인 테스트용 Settings — 실제 .env에서 격리한다.

    _load_dotenv가 os.environ.setdefault라 delenv한 키를 .env가 되살린다.
    개발 머신에 실키가 생기자 '키 없음' 테스트가 깨졌다(실측) — 테스트가
    로컬 파일 상태에 의존하면 안 되므로 로더 자체를 무력화한다.
    """
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "_load_dotenv", lambda: None)
    for k in ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
              "FRIENDLI_TOKEN", "FRIENDLI_ENDPOINT_ID",
              "LOCAL_LLM_BASE_URL", "LOCAL_LLM_MODEL"):
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


class TestMaxTokensField:
    """GPT-5.6 계열은 max_tokens를 거부한다 (실측 400 unsupported_parameter).
    페이로드가 max_completion_tokens를 쓰는지 오프라인으로 고정한다."""

    def test_openai_uses_max_completion_tokens(self, monkeypatch):
        s = _settings(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x")
        ex = get_extractor(s)
        seen = {}

        class _Resp:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": "ok"},
                                     "finish_reason": "stop"}],
                        "usage": {"completion_tokens": 3}}

        def fake_post(payload, *, timeout=None):
            seen.update(payload)
            return _Resp()

        monkeypatch.setattr(ex, "_post", fake_post)
        ex._chat("sys", "user", max_tokens=1234)
        assert "max_completion_tokens" in seen and seen["max_completion_tokens"] == 1234
        assert "max_tokens" not in seen, "GPT-5.6은 max_tokens를 거부한다"

    def test_local_keeps_max_tokens(self, monkeypatch):
        """Ollama 등 기존 OpenAI 호환 서버는 max_tokens 그대로 — 회귀 방지."""
        s = _settings(monkeypatch, LLM_PROVIDER="local",
                      LOCAL_LLM_BASE_URL="http://x/v1/chat/completions",
                      LOCAL_LLM_MODEL="exaone3.5:7.8b")
        assert get_extractor(s)._max_tokens_field == "max_tokens"


class TestTemperature:
    """GPT-5.6은 temperature 커스텀을 거부한다 (400 unsupported_value).
    우리 0.5는 EXAONE 반복 루프 방어용이라, 안 받는 모델엔 빼고 보낸다."""

    def _capture(self, monkeypatch, ex) -> dict:
        seen = {}

        class _Resp:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": "ok"},
                                     "finish_reason": "stop"}],
                        "usage": {"completion_tokens": 3}}

        monkeypatch.setattr(ex, "_post",
                            lambda payload, *, timeout=None: (seen.update(payload),
                                                              _Resp())[1])
        ex._chat("sys", "user", max_tokens=100, temperature=0.5)
        return seen

    def test_openai_omits_temperature(self, monkeypatch):
        s = _settings(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-x")
        seen = self._capture(monkeypatch, get_extractor(s))
        assert "temperature" not in seen, "GPT-5.6은 temperature를 거부한다"

    def test_local_keeps_temperature(self, monkeypatch):
        s = _settings(monkeypatch, LLM_PROVIDER="local",
                      LOCAL_LLM_BASE_URL="http://x/v1/chat/completions",
                      LOCAL_LLM_MODEL="exaone3.5:7.8b")
        seen = self._capture(monkeypatch, get_extractor(s))
        assert seen["temperature"] == 0.5, "EXAONE 반복 루프 방어값은 유지"
