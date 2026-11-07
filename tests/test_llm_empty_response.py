"""빈 LLM 응답 방어 회귀 테스트 — 완전 오프라인 (httpx.post 미호출).

실측(할리케이 PDF 온보딩, 2026-07-27): 깊은 추론(thinking=True) 호출이
finish_reason=stop인데 완료 토큰 5·본문 0자를 반환한 사례(API 비결정적 실패 —
동일 입력 즉시 재시도로는 정상 생성됨). 두 개의 버그가 연쇄됐다:

  A. `_chat`의 length 가드는 finish_reason==length만 잡아 이 stop-빈응답을
     그대로 통과시켰다 → 빈 분석이 구조화 단계로 흘러가 "미상" 프로필.
  B. `_check_minimum` 실패 시 호출하는 `_clarify_questions`는 open_questions가
     비어 있으면 결과가 항상 []로 확정됨에도(순회할 항목이 없음) 174초짜리
     LLM 호출을 그대로 실행했다 — 결과가 예정대로 버려져 순수 낭비.

이 파일은 B(질문 0건이면 API를 태우지 않음)를 고정한다. A는 OpenAI 호환 어댑터
(_chat)의 방어였고, 그 어댑터는 Claude 통일(2026-10)로 지웠다 — Claude 경로는 빈
본문이면 json 파싱에서 llm_error로 명시적으로 실패한다(tests/test_claude_provider.py).
"""
import json

import pytest

from app.engine.represent import _clarify_questions
from app.errors import EngineError
from app.ingest.chunking import Chunk
from app.ingest.extractor import extract_profile


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
