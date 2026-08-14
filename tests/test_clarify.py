"""B6 — 대화형 검색 루프 속성 테스트 (결정적, CI 게이트).

핵심 계약: 질문은 관측된 후보를 실제로 가르는 것만 살아남는다. LLM이 지어낸
선택지(존재하지 않는 후보 인용), 풀을 못 가르는 질문, 이미 물은 질문은
코드가 버린다 — 사용자의 시간을 라벨링 노동으로 바꾸지 않기 위한 집행이다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.clarify import feedback_bonus, validate_questions

CANDS = [
    {"company_id": "web-1", "name": "A상사",
     "ontology": {"axes": {"value_chain_position":
                           {"value": "종합상사로 광범위 취급", "status": "confirmed"}},
                  "search_keywords": []}},
    {"company_id": "web-2", "name": "B수입",
     "ontology": {"axes": {"value_chain_position":
                           {"value": "음료 전문 수입사", "status": "confirmed"}},
                  "search_keywords": []}},
    {"company_id": "web-3", "name": "C도매", "ontology": None},
]


def _q(question="종합상사와 전문 수입사 중 어느 쪽인가요?",
       options=None):
    return {"question": question, "axis": "value_chain_position", "why": "w",
            "options": options or [
                {"label": "종합상사", "company_ids": ["web-1"]},
                {"label": "전문 수입사", "company_ids": ["web-2"]}]}


def test_valid_question_survives():
    out = validate_questions([_q()], CANDS, asked=[])
    assert len(out) == 1 and out[0]["id"] == "q1"


def test_hallucinated_citation_dropped():
    """존재하지 않는 후보를 인용한 선택지는 버려지고, 그래서 선택지가 1개만
    남으면 질문 자체가 죽는다 — 인용 계약."""
    out = validate_questions([_q(options=[
        {"label": "종합상사", "company_ids": ["web-999"]},
        {"label": "전문 수입사", "company_ids": ["web-2"]}])], CANDS, asked=[])
    assert out == []


def test_non_splitting_question_dropped():
    """모든 선택지가 같은 후보 집합이면 가르는 질문이 아니다."""
    out = validate_questions([_q(options=[
        {"label": "옵션갑", "company_ids": ["web-1"]},
        {"label": "옵션을", "company_ids": ["web-1"]}])], CANDS, asked=[])
    assert out == []


def test_already_asked_not_repeated():
    out = validate_questions([_q()], CANDS,
                             asked=["종합상사와 전문 수입사 중 어느 쪽인가요?"])
    assert out == []


def test_question_cap_three():
    qs = [_q(question=f"질문 {i}?") for i in range(5)]
    out = validate_questions(qs, CANDS, asked=[])
    assert len(out) == 3


def test_feedback_bonus_directions():
    """좋아요 겹침은 가산, 아니에요 겹침은 감산 — 그리고 감산이 더 크다
    (부정이 더 확실한 신호: 사용자가 명시적으로 걸렀다).

    토큰은 프로덕션 경로(axis_tokens) 그대로 만든다 — 손으로 2-gram을 넣었다가
    한국어는 단어 단위라 겹침 0이 나온 실측 오류의 재발 방지."""
    from app.engine.keywords import axis_tokens
    liked = axis_tokens([CANDS[0]["ontology"]])   # 같은 성격의 회사를 좋아함
    assert liked, "픽스처 토큰이 비면 테스트가 무의미하다"
    cand = CANDS[0]
    up = feedback_bonus(cand, liked, set())
    down = feedback_bonus(cand, set(), liked)   # 같은 겹침을 부정으로
    assert up > 0 > down
    assert abs(down) > abs(up)


def test_feedback_zero_without_feedback_or_ontology():
    assert feedback_bonus(CANDS[0], set(), set()) == 0.0
    assert feedback_bonus(CANDS[2], {"토큰"}, set()) == 0.0   # 온톨로지 없음
