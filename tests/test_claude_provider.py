"""Claude 어댑터(AnthropicExtractor) — 요청 모양과 응답 해석을 스텁 클라이언트로 고정한다.

실 API는 부르지 않는다(키·비용·비결정). 실호출 검증은 ANTHROPIC_API_KEY를 넣고
scripts 쪽에서 따로 한다. 여기서 지키는 것:
  - 모델·사고(adaptive)·effort(기본 medium / deep high)·구조화 출력·캐시·거절 대비
  - 사고 블록이 앞에 와도 본문(text)만 모은다 (content[0] 가정 금지)
  - 거절·잘림·JSON 아님 → 조용히 넘어가지 않고 EngineError
"""
import json
from types import SimpleNamespace as NS

import pytest

from app.config import Settings
from app.engine.llm import AnthropicExtractor, get_extractor
from app.errors import EngineError


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def _msg(text, *, stop="end_turn", thinking=True, model="claude-opus-5-5"):
    blocks = ([NS(type="thinking", thinking="")] if thinking else []) + \
             [NS(type="text", text=text)]
    return NS(content=blocks, stop_reason=stop, model=model, stop_details=None,
              usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0))


@pytest.fixture()
def make(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "_load_dotenv", lambda: None)

    def _make(env=None, reply=None, tier="default"):
        for k in ("ANTHROPIC_MODEL", "ANTHROPIC_MODEL_FAST", "ANTHROPIC_EFFORT",
                  "ANTHROPIC_EFFORT_DEEP"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        ex = AnthropicExtractor(Settings(), tier=tier)
        calls = []

        def stream(**kw):
            calls.append(kw)
            return _Stream(reply or _msg('{"ok": true}'))
        ex._client = NS(beta=NS(messages=NS(stream=stream)))
        return ex, calls
    return _make


def test_request_shape_default(make):
    ex, calls = make()
    assert ex.extract_json("S", "U", {"type": "object"}) == {"ok": True}
    kw = calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["effort"] == "medium"
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert kw["cache_control"] == {"type": "ephemeral"}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["max_tokens"] >= 32000                       # 스트리밍이라 넉넉히
    assert "budget_tokens" not in json.dumps(kw)


def test_deep_raises_effort(make):
    ex, calls = make()
    ex.extract_json("S", "U", {"type": "object"}, deep=True)
    assert calls[0]["output_config"]["effort"] == "high"


def test_text_ignores_thinking_blocks(make):
    ex, _ = make(reply=_msg("안녕하세요", thinking=True))
    assert ex.complete_text("S", "U") == "안녕하세요"


def test_complete_text_has_no_format(make):
    ex, calls = make(reply=_msg("hi"))
    ex.complete_text("S", "U")
    assert "format" not in calls[0]["output_config"]
    assert calls[0]["max_tokens"] >= 8000                  # 사고까지 담을 여유


def test_fast_tier_and_no_fallback_on_haiku(make):
    ex, calls = make(env={"ANTHROPIC_MODEL_FAST": "claude-haiku-5-5"}, tier="fast")
    ex.complete_text("S", "U")
    assert calls[0]["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in calls[0]                     # Haiku 5.5는 서버 측 대비 없음


def test_fast_tier_defaults_to_main_model(make):
    ex, calls = make(tier="fast")
    ex.complete_text("S", "U")
    assert calls[0]["model"] == "claude-opus-5-5"


@pytest.mark.parametrize("stop,code", [("refusal", "llm_refusal"), ("max_tokens", "llm_error")])
def test_bad_stops_fail_loudly(make, stop, code):
    ex, _ = make(reply=_msg("{}", stop=stop))
    with pytest.raises(EngineError) as e:
        ex.extract_json("S", "U", {"type": "object"})
    assert e.value.code == code


def test_non_json_fails_loudly(make):
    ex, _ = make(reply=_msg("not json"))
    with pytest.raises(EngineError):
        ex.extract_json("S", "U", {"type": "object"})


def test_default_provider_is_claude(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "_load_dotenv", lambda: None)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings()
    assert s.llm_provider == "anthropic"
    with pytest.raises(EngineError) as e:                  # 키 없으면 조용히 대체하지 않는다
        get_extractor(s)
    assert "ANTHROPIC_API_KEY" in e.value.message
