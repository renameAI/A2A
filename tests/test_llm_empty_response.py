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


class TestReasoningRunawayTimeout:
    """추론 폭주(사고 사슬이 루프에 갇혀 예산을 다 태우는 것) 차단.

    실측(할리케이 PDF, 2026-07-27): 같은 입력·같은 파라미터로 한 번은 61초에
    finish=stop 정상 종료, 다른 한 번은 458초 동안 16,384토큰을 전부 태우고
    finish=length·본문 0자. 기존 가드는 폭주를 막는 게 아니라 **끝난 뒤 수습**해서
    458초는 이미 날아간 뒤였다(총 496초). 비결정적 실패라 재샘플이면 회복된다.
    """

    def test_runaway_is_cut_and_resampled_not_surfaced_as_error(self, monkeypatch):
        """1차가 상한 초과 → 끊고 재샘플 → 성공하면 사용자에겐 에러가 안 보인다."""
        ex = _extractor()
        calls = []

        def fake_post(payload, *, timeout=None):
            calls.append(timeout)
            if len(calls) == 1:
                raise EngineError(504, "llm_timeout", "Fake 타임아웃")
            return _FakeResponse("재샘플 분석 본문", finish_reason="stop",
                                 completion_tokens=2192)

        monkeypatch.setattr(ex, "_post", fake_post)
        out = ex._chat("sys", "user", thinking=True, max_tokens=16384)
        assert out == "재샘플 분석 본문"
        assert len(calls) == 2, "폭주 1회면 재샘플 1회로 끝나야 한다"

    def test_thinking_call_uses_short_cap_not_full_timeout(self, monkeypatch):
        """추론 호출은 기본 상한(600초)이 아니라 짧은 상한을 써야 폭주가 잘린다."""
        ex = _OpenAICompatExtractor(
            "https://fake.example/v1/chat/completions", "tok", "fake-model",
            timeout=600.0, provider_label="Fake", thinking_kwargs=True)
        seen = []

        def fake_post(payload, *, timeout=None):
            seen.append(timeout)
            return _FakeResponse("본문", finish_reason="stop", completion_tokens=10)

        monkeypatch.setattr(ex, "_post", fake_post)
        ex._chat("sys", "user", thinking=True, max_tokens=16384)
        assert seen == [150.0], "추론 호출엔 짧은 벽시계 상한이 걸려야 한다"

    def test_structuring_call_keeps_full_timeout(self, monkeypatch):
        """구조화(thinking=False)는 폭주 대상이 아니므로 기본 상한을 유지한다."""
        ex = _OpenAICompatExtractor(
            "https://fake.example/v1/chat/completions", "tok", "fake-model",
            timeout=600.0, provider_label="Fake", thinking_kwargs=True)
        seen = []

        def fake_post(payload, *, timeout=None):
            seen.append(timeout)
            return _FakeResponse('{"a":1}', finish_reason="stop", completion_tokens=10)

        monkeypatch.setattr(ex, "_post", fake_post)
        ex._chat("sys", "user", thinking=False, max_tokens=16384)
        assert seen == [None], "구조화 호출은 상한을 덮어쓰지 않는다(기본값 사용)"

    def test_fallback_is_bounded_so_worst_case_cannot_exceed_original(
            self, monkeypatch):
        """폭주 2회 후 thinking OFF 폴백에도 상한이 걸린다.

        상한을 안 넘기면 _reason_timeout(False)=None이라 기본 600초로 돌아가
        최악이 150+150+600=900초가 된다 — 고치려던 496초보다 나쁘다.
        """
        ex = _extractor()
        seen = []

        def fake_post(payload, *, timeout=None):
            seen.append((payload.get("chat_template_kwargs"), timeout))
            if len(seen) <= 2:
                raise EngineError(504, "llm_timeout", "Fake 타임아웃")
            return _FakeResponse("폴백 본문", finish_reason="stop",
                                 completion_tokens=1330)

        monkeypatch.setattr(ex, "_post", fake_post)
        out = ex._chat("sys", "user", thinking=True, max_tokens=16384)
        assert out == "폴백 본문", "폴백까지 가도 사용자에겐 에러가 아니라 결과가 간다"
        assert len(seen) == 3
        assert seen[2][0] == {"enable_thinking": False}, "3번째는 thinking OFF"
        assert seen[2][1] is not None, "폴백에도 상한이 걸려야 총 시간이 묶인다"


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
