"""Claude 비전 어댑터(ClaudeBBoxExtractor) 단위 테스트 — 전부 오프라인.

Anthropic 클라이언트를 가짜로 바꿔 끼워 요청 형태(이미지 base64 블록·라벨·구조화
출력 스키마·사고·거절 대비)와 실패 의미(거절·잘림·파싱 실패·API 오류 → 배치만
건너뛰고 빈 리스트)를 검증한다. 실 API는 부르지 않는다(키·비용·비결정).
Claude의 실제 박스 정밀도는 여기서 재지 않는다 — 실측 전이다.
"""
import base64
import json
from types import SimpleNamespace

import anthropic
import fitz
import httpx
import pytest

from app.config import Settings
from app.engine import vision
from app.engine.prompts import BBOX_SCHEMA, BBOX_SYSTEM


def _png(w: int = 40, h: int = 20) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    return pix.tobytes("png")


def _msg(text: str = "", stop_reason: str = "end_turn", category=None,
         input_tokens: int = 100, output_tokens: int = 20):
    content = [SimpleNamespace(type="thinking", thinking=""),   # 사고 블록이 앞에 온다
               SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(
        content=content, stop_reason=stop_reason, model="claude-opus-5-5",
        stop_details=SimpleNamespace(category=category) if category else None,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                              cache_read_input_tokens=0, cache_creation_input_tokens=0))


class _Stream:
    def __init__(self, result):
        self._result = result

    def __enter__(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._result


class FakeClient:
    """client.beta.messages.stream(**kwargs)만 흉내 — 호출 인자를 기록한다."""
    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return _Stream(self.results.pop(0))


def _ex(client, **kw) -> vision.ClaudeBBoxExtractor:
    return vision.ClaudeBBoxExtractor("sk-ant-test", kw.pop("model", "claude-opus-5-5"),
                                      client=client, **kw)


LOC = {"question_index": 0, "page": 3, "quote": "매출 쉐어 구조",
       "box_2d": [300, 100, 400, 500], "relevance": 0.9}


class TestRequestShape:
    def test_images_labels_schema_and_thinking(self):
        img = _png(40, 20)
        client = FakeClient(_msg(json.dumps({"locations": [LOC]})))
        ex = _ex(client)
        out = ex.locate_batch([(3, img, "image/png"), (4, img, "image/png")], ["질문 A"])
        assert out == [LOC]

        kw = client.calls[0]
        assert kw["model"] == "claude-opus-5-5"
        assert kw["system"] == BBOX_SYSTEM
        assert kw["thinking"] == {"type": "adaptive"}
        assert kw["output_config"]["format"] == {"type": "json_schema", "schema": BBOX_SCHEMA}
        assert kw["output_config"]["effort"] == "medium"
        assert kw["fallbacks"] == "default"
        assert kw["betas"] == ["server-side-fallback-2026-07-01"]

        content = kw["messages"][0]["content"]
        # [라벨, 이미지] × 2 + 지시문(질문 목록) 마지막
        assert [b["type"] for b in content] == ["text", "image", "text", "image", "text"]
        assert content[0]["text"].startswith("[PAGE 3]") and "40×20px" in content[0]["text"]
        assert content[2]["text"].startswith("[PAGE 4]")
        src = content[1]["source"]
        assert src["type"] == "base64" and src["media_type"] == "image/png"
        assert base64.standard_b64decode(src["data"]) == img
        assert "질문 A" in content[-1]["text"]

    def test_no_fallback_for_unsupported_model(self):
        client = FakeClient(_msg(json.dumps({"locations": []})))
        _ex(client, model="claude-haiku-5-5").locate_batch([(1, _png(), "image/png")], ["q"])
        assert "fallbacks" not in client.calls[0] and "betas" not in client.calls[0]

    def test_unreadable_image_label_omits_size(self):
        client = FakeClient(_msg(json.dumps({"locations": []})))
        _ex(client).locate_batch([(1, b"not-an-image", "image/png")], ["q"])
        assert client.calls[0]["messages"][0]["content"][0]["text"] == "[PAGE 1]"


class TestSchemaFormat:
    def test_claude_json_schema_rules(self):
        """Claude 구조화 출력 규격 — 소문자 타입, 모든 object에 additionalProperties: false,
        지원되지 않는 수치·길이 제약 없음."""
        def walk(node):
            t = node.get("type")
            assert t == t.lower()
            for bad in ("minimum", "maximum", "minItems", "maxItems", "minLength"):
                assert bad not in node
            if t == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
                for child in node["properties"].values():
                    walk(child)
            if t == "array":
                walk(node["items"])
        walk(BBOX_SCHEMA)


class TestAccountingAndBudget:
    def test_tokens_accumulate_and_budget_stops_calls(self):
        client = FakeClient(_msg(json.dumps({"locations": []}), input_tokens=900,
                                 output_tokens=200))
        ex = _ex(client, token_budget=1000)
        ex.locate_batch([(1, _png(), "image/png")], ["q"])
        assert (ex.calls, ex.tokens_used) == (1, 1100)
        assert ex.budget_exhausted
        assert ex.locate_batch([(2, _png(), "image/png")], ["q"]) == []
        assert len(client.calls) == 1     # 예산 소진 후엔 전송하지 않는다


def _req():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class TestHonestFailure:
    """실패는 이 배치만 빈 리스트 — 가짜 위치를 만들지 않고, 예외로 온보딩을 막지 않는다."""

    @pytest.mark.parametrize("msg", [
        _msg("", stop_reason="refusal", category="cyber"),
        _msg('{"locations": [', stop_reason="max_tokens"),
        _msg("JSON 아님"),
        _msg(json.dumps({"locations": "nope"})),
    ])
    def test_bad_responses_skip_batch(self, msg):
        ex = _ex(FakeClient(msg))
        assert ex.locate_batch([(1, _png(), "image/png")], ["q"]) == []
        assert ex.calls == 1             # 응답은 받았으니 토큰은 계측된다

    @pytest.mark.parametrize("err", [
        anthropic.APIStatusError("overloaded", response=httpx.Response(529, request=_req()),
                                 body=None),
        anthropic.APIConnectionError(request=_req()),
    ])
    def test_api_errors_skip_batch(self, err):
        ex = _ex(FakeClient(err))
        assert ex.locate_batch([(1, _png(), "image/png")], ["q"]) == []
        assert (ex.calls, ex.tokens_used) == (0, 0)


class TestFactory:
    def _settings(self, monkeypatch, **env):
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_VISION_MODEL",
                  "ANTHROPIC_EFFORT"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        s = Settings()
        if "ANTHROPIC_API_KEY" not in env:
            s.anthropic_api_key = ""      # .env 자동 로드와 무관하게 '키 없음' 고정
        return s

    def test_no_key_means_disabled(self, monkeypatch):
        assert vision.get_vision_extractor(self._settings(monkeypatch)) is None

    def test_key_enables_with_default_model_and_sdk_retries(self, monkeypatch):
        s = self._settings(monkeypatch, ANTHROPIC_API_KEY="sk-ant-test")
        s.anthropic_model = "claude-opus-5-5"
        if hasattr(s, "anthropic_vision_model"):
            s.anthropic_vision_model = ""
        ex = vision.get_vision_extractor(s)
        assert isinstance(ex, vision.ClaudeBBoxExtractor)
        assert ex._model == "claude-opus-5-5"
        assert ex._client.max_retries == 2          # 총 3회 시도 (예전 계약과 동일)
        assert ex.token_budget == s.vision_token_budget

    def test_vision_model_override(self, monkeypatch):
        s = self._settings(monkeypatch, ANTHROPIC_API_KEY="sk-ant-test",
                           ANTHROPIC_VISION_MODEL="claude-sonnet-5-5")
        if hasattr(s, "anthropic_vision_model"):
            s.anthropic_vision_model = "claude-sonnet-5-5"
        assert vision.get_vision_extractor(s)._model == "claude-sonnet-5-5"
