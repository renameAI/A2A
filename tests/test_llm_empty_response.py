"""빈 LLM 응답 방어 회귀 테스트 — 완전 오프라인 (httpx.post 미호출).

실측(할리케이 PDF 온보딩, 2026-07-27): 깊은 추론(thinking=True) 호출이
finish_reason=stop인데 완료 토큰 5·본문 0자를 반환한 사례(API 비결정적 실패 —
동일 입력 즉시 재시도로는 정상 생성됨). 두 개의 버그가 연쇄됐다:

  A. `_chat`의 length 가드는 finish_reason==length만 잡아 이 stop-빈응답을
     그대로 통과시켰다 → 빈 분석이 구조화 단계로 흘러가 "미상" 프로필.
  B. `_check_minimum` 실패 시 호출하는 `_clarify_questions`는 open_questions가
     비어 있으면 결과가 항상 []로 확정됨에도(순회할 항목이 없음) 174초짜리
     LLM 호출을 그대로 실행했다 — 결과가 예정대로 버려져 순수 낭비.

이 파일은 A(1회 재시도 + 재시도 후에도 비면 명시적 에러)와 B(질문 0건이면
API를 태우지 않음)를 고정한다.
"""
import json

import pytest

from app.engine.llm import _OpenAICompatExtractor
from app.engine.represent import _clarify_questions
from app.errors import EngineError
from app.ingest.chunking import Chunk
from app.ingest.extractor import extract_profile


class _FakeResponse:
    """httpx.Response 대역 — _chat이 쓰는 두 속성만 흉내."""

    def __init__(self, content: str, finish_reason: str = "stop",
                 completion_tokens: int = 5, status_code: int = 200):
        self.status_code = status_code
        self._body = {
            "choices": [{"message": {"content": content},
                        "finish_reason": finish_reason}],
            "usage": {"completion_tokens": completion_tokens},
        }

    def json(self):
        return self._body


def _extractor() -> _OpenAICompatExtractor:
    return _OpenAICompatExtractor(
        "https://fake.example/v1/chat/completions", "tok", "fake-model",
        timeout=5.0, provider_label="Fake", thinking_kwargs=True)


class TestEmptyResponseRetry:
    def test_stop_with_empty_content_retries_once_then_succeeds(self, monkeypatch):
        """finish=stop·본문 0자 → 1회 재시도 → 두 번째 응답이 정상이면 그걸 쓴다."""
        ex = _extractor()
        calls = []

        def fake_post(payload, *, timeout=None):
            calls.append(payload)
            if len(calls) == 1:
                return _FakeResponse("", finish_reason="stop", completion_tokens=5)
            return _FakeResponse("정상 분석 본문", finish_reason="stop",
                                 completion_tokens=120)

        monkeypatch.setattr(ex, "_post", fake_post)
        out = ex._chat("sys", "user", thinking=True, max_tokens=16384)
        assert out == "정상 분석 본문"
        assert len(calls) == 2, "빈 응답이면 정확히 1회만 재시도해야 한다"

    def test_stop_with_empty_content_twice_raises_clear_error(self, monkeypatch):
        """재시도 후에도 비면 침묵하지 않고 명시적 llm_error를 낸다(무한 재시도 아님)."""
        ex = _extractor()
        calls = []

        def fake_post(payload, *, timeout=None):
            calls.append(payload)
            return _FakeResponse("", finish_reason="stop", completion_tokens=5)

        monkeypatch.setattr(ex, "_post", fake_post)
        with pytest.raises(EngineError) as exc_info:
            ex._chat("sys", "user", thinking=True, max_tokens=16384)
        assert exc_info.value.code == "llm_error"
        assert len(calls) == 2, "무한 루프가 아니라 정확히 2회(원 시도+재시도 1회)여야 한다"

    def test_non_thinking_empty_content_not_retried(self, monkeypatch):
        """구조화(thinking=False) 경로는 건드리지 않는다 — _retry_json이 이미 상위에서
        파싱 실패로 재시도하므로 여기서 또 재시도하면 중복이다."""
        ex = _extractor()
        calls = []

        def fake_post(payload, *, timeout=None):
            calls.append(payload)
            return _FakeResponse("", finish_reason="stop", completion_tokens=1)

        monkeypatch.setattr(ex, "_post", fake_post)
        out = ex._chat("sys", "user", thinking=False, max_tokens=16384)
        assert out == ""
        assert len(calls) == 1, "thinking=False는 재시도 가드 대상이 아니다"


_CANNED_PORTRAIT = {k: "x" for k in
                    ("identity", "business_model", "edge", "stage_narrative",
                     "assets", "gaps", "risk_signals")}


def _canned_field(v="x"):
    return {"value": v, "provenance": "stated", "confidence": 1.0,
            "evidence_chunk_ids": []}


class _CannedExtractor:
    """extract_json이 고정 JSON을 돌려주는 스텁 — 실제 API 미호출."""

    def __init__(self, sell_value_props, purchase_value_props):
        self._sell = sell_value_props
        self._purchase = purchase_value_props

    def extract_json(self, system, user, schema, deep=False):
        return {
            "basic": {"name": "테스트사", "country": "한국", "city": None,
                      "founded_year": None, "industry": "unknown"},
            "description": "d", "problem_solved": _canned_field(),
            "solution": _canned_field(), "target_customer": _canned_field(),
            "references": [], "traction": None,
            "sell_value_props": self._sell,
            "purchase_value_props": self._purchase,
            "willingness_sell": None, "willingness_purchase": None,
            "portrait": _CANNED_PORTRAIT, "open_questions": [],
        }

    def complete_text(self, system, user):
        return ""


class TestValuePropsDedup:
    def test_duplicate_value_props_collapsed(self):
        """실측(할리케이 PDF, 2026-07-27): sell_value_props에 'revenue_growth'가
        7회 반복되어 나온 사례 — enum이 4종뿐이라 스키마는 통과하지만 UI(app.js:1318)가
        그대로 렌더링해 데모 화면에 중복 스팸으로 노출된다. 순서는 보존한 채 접는다."""
        ex = _CannedExtractor(
            sell_value_props=["revenue_growth"] * 7,
            purchase_value_props=["impact", "revenue_growth", "impact"])
        chunks = [Chunk(chunk_id="a1#1", source="a1", text="아무 내용")]
        profile, _, _ = extract_profile(chunks, ex)
        assert [v.value for v in profile.sell_value_props] == ["revenue_growth"]
        assert [v.value for v in profile.purchase_value_props] == \
            ["impact", "revenue_growth"]


class TestClarifySkipsEmptyQuestions:
    def test_no_open_questions_skips_llm_call_entirely(self):
        """open_questions=[]이면 결과가 항상 []로 확정돼 있다 — extractor를 아예
        호출하지 않는다.

        주의: extract_json이 예외를 던지는 방식으로만 "호출 안 됨"을 검증하면
        안 된다 — _clarify_questions의 `except Exception:` 폴백이 그 예외를
        삼키고 _mock_clarify([])==[]를 반환해 반환값만으로는 pre-fix 코드도
        우연히 같은 결과가 나와 회귀를 못 잡는다(실제로 겪음). 호출 여부를
        플래그로 직접 관찰한다.
        """

        class _PoisonedExtractor:
            def __init__(self):
                self.called = False

            def extract_json(self, *a, **kw):
                self.called = True
                raise AssertionError("호출되면 안 된다")

        ex = _PoisonedExtractor()
        result = _clarify_questions(ex, profile=None, open_questions=[],
                                    full_text="아무 원문")
        assert result == []
        assert ex.called is False, \
            "open_questions가 비어 있으면 API를 호출하면 안 된다"


class TestDegenerateRepetition:
    """퇴화 반복 차단 — 실측(2026-07-28): 구조화 호출에서 </thihk>(</think> 오타)가
    8,594자 반복되며 예산을 태우고 타임아웃 2회로 끝났다. 모델이 자기 종료 토큰을
    못 만들어 갇힌 상태라 더 기다려도 회복되지 않는다."""

    @staticmethod
    def _run(deltas, monkeypatch):
        import httpx
        from app import progress
        from app.engine.llm import _OpenAICompatExtractor

        frames = [f'data: {json.dumps({"choices": [{"delta": {"content": c}}]})}'
                  for c in deltas] + ["data: [DONE]"]

        class _FakeStream:
            status_code = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def iter_lines(self): return iter(frames)
            def read(self): pass

        monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream())
        ex = _OpenAICompatExtractor("http://x", "t", "m", 60, "TEST",
                                    thinking_kwargs=True)
        progress.bind()
        return ex._post_stream({"messages": [],
                                "response_format": {"type": "json_schema"}}, 60).json()

    def test_typo_close_tag_stripped_before_json(self):
        from app.engine.llm import _OpenAICompatExtractor as E
        assert E._parse_json('<think>고민</thihk>{"a":1}') == {"a": 1}
        assert E._parse_json('<think>안 닫힘\n\n{"a":1}') == {"a": 1}


class TestNoRunawayGuard:
    """폭주 가드 철회(2026-07-28) 고정 — 실수로 되살아나지 않게.

    150초 상한 + 재샘플을 넣었더니 오히려 느려졌다: judge 1건이 추론 23,989자에서
    잘리고 재샘플로 다시 시작해 총 386초(안 끊었으면 한 번에 끝났을 가능성).
    재샘플본은 영어로 사고하고 회사명이 잘리는 등 품질도 나빴다.
    판단이 오래 걸리는 건 정상이다 — 기다리면 끝난다. 체감은 스트리밍으로 푼다.
    """

    def test_thinking_call_has_no_short_cap_by_default(self, monkeypatch):
        ex = _OpenAICompatExtractor(
            "https://fake.example/v1/chat/completions", "tok", "fake-model",
            timeout=600.0, provider_label="Fake", thinking_kwargs=True)
        seen = []

        def fake_post(payload, *, timeout=None):
            seen.append(timeout)
            return _FakeResponse("본문", finish_reason="stop", completion_tokens=10)

        monkeypatch.delenv("LLM_REASON_TIMEOUT", raising=False)
        monkeypatch.setattr(ex, "_post", fake_post)
        ex._chat("sys", "user", thinking=True, max_tokens=16384)
        assert seen == [None], "기본값에서는 추론 호출에 별도 상한을 걸지 않는다"

    def test_timeout_is_raised_not_silently_resampled(self, monkeypatch):
        """상한을 명시했더라도 초과 시 조용히 다시 굴리지 않고 그대로 올린다."""
        ex = _extractor()
        calls = []

        def fake_post(payload, *, timeout=None):
            calls.append(timeout)
            raise EngineError(504, "llm_timeout", "Fake 타임아웃")

        monkeypatch.setenv("LLM_REASON_TIMEOUT", "150")
        monkeypatch.setattr(ex, "_post", fake_post)
        with pytest.raises(EngineError) as exc:
            ex._chat("sys", "user", thinking=True, max_tokens=16384)
        assert exc.value.code == "llm_timeout"
        assert len(calls) == 1, "재샘플 없이 1회 호출로 끝나야 한다"
