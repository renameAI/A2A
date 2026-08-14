"""B4 — 결과 학습 루프 속성 테스트.

추천이 '많이 찾힌 검색어'에서 '실제로 통한 검색어'로 이동하는지를 결정적으로
검증한다. 근거: Hu-Koren-Volinsky(2008) — 암묵 신호는 신뢰도 등급이고,
미관측은 부정이 아니다. LLM이 없어 CI 게이트로 상시 돈다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.keywords import outcome_weight, recommend


class FakeStore:
    def __init__(self):
        self.docs = {}

    def put(self, kind, ws, doc_id, body):
        self.docs[(kind, ws, doc_id)] = dict(body)

    def get(self, kind, ws, doc_id):
        return self.docs.get((kind, ws, doc_id))

    def list(self, kind, ws):
        return [v for (k, w, _), v in self.docs.items()
                if k == kind and w == ws]


WS = "ws-test"
# 두 검색어가 같은 수확(3곳) — 결과가 없으면 동률이어야 한다
RUN = {
    "request_id": "lr-old", "segment": "일본 음료 수입사",
    "queries": ["Q1 수입사", "Q2 수입사"],
    "yield_by_query": {"Q1 수입사": 3, "Q2 수입사": 3},
    "companies_kept": 6,
    "tokens": ["수입사", "일본"], "axis_tokens": [], "derived_keywords": [],
}


def _store_with(outcomes):
    st = FakeStore()
    st.put("keyword_run", WS, "lr-old::일본 음료 수입사", RUN)
    for i, o in enumerate(outcomes):
        st.put("outcome", WS, f"lr-old::web-{i}", {
            "request_id": "lr-old", "company_id": f"web-{i}",
            "segment": "일본 음료 수입사", "found_by": o.get("found_by", ""),
            "saved": o.get("saved", False), "drafted": o.get("drafted", False),
            "replied": o.get("replied", "")})
    return st


def _rank(recs):
    return [r["query"] for r in recs]


def test_cold_start_ties_without_outcomes():
    """결과가 없으면 수확만으로 — 동률이어야 하고, 점수를 지어내면 안 된다."""
    recs = recommend(_store_with([]), WS, ["일본 수입사 회사소개"])
    scores = {r["query"]: r["score"] for r in recs}
    assert scores["Q1 수입사"] == scores["Q2 수입사"]


def test_reply_outranks_equal_yield():
    """답장 1건이 동률을 깬다 — 시장의 확인이 최상위 신호다."""
    recs = recommend(_store_with(
        [{"found_by": "Q2 수입사", "saved": True, "drafted": True,
          "replied": "yes"}]),
        WS, ["일본 수입사 회사소개"])
    assert _rank(recs)[0] == "Q2 수입사"


def test_tier_monotonicity():
    """저장 < 초안 < 답장 — 계층이 단조여야 한다."""
    w_found = outcome_weight({})
    w_saved = outcome_weight({"saved": True})
    w_draft = outcome_weight({"saved": True, "drafted": True})
    w_reply = outcome_weight({"saved": True, "drafted": True, "replied": "yes"})
    assert w_found < w_saved < w_draft < w_reply


def test_no_reply_is_not_negative():
    """'답장 없음'은 감점이 아니다 — 지연 피드백과 부정을 구분할 수 없다(HKV)."""
    base = recommend(_store_with([]), WS, ["일본 수입사 회사소개"])
    with_no = recommend(_store_with(
        [{"found_by": "Q1 수입사", "drafted": True, "replied": "no"}]),
        WS, ["일본 수입사 회사소개"])
    s_base = {r["query"]: r["score"] for r in base}
    s_no = {r["query"]: r["score"] for r in with_no}
    # 초안 가중은 남고(노력 투자는 사실), 답장 없음으로 인한 추가 감점은 없다
    assert s_no["Q1 수입사"] >= s_base["Q1 수입사"]


def test_excluded_request_outcomes_ignored():
    """현재 요청 자신의 결과는 추천 근거에서 제외 — 자기 참조 부스트 방지."""
    st = _store_with(
        [{"found_by": "Q2 수입사", "replied": "yes"}])
    # 같은 결과를 현재 요청(lr-now)에도 넣는다
    st.put("outcome", WS, "lr-now::web-9", {
        "request_id": "lr-now", "company_id": "web-9",
        "segment": "x", "found_by": "Q1 수입사",
        "saved": True, "drafted": True, "replied": "yes"})
    recs = recommend(st, WS, ["일본 수입사 회사소개"], exclude_rid="lr-now")
    assert _rank(recs)[0] == "Q2 수입사"


def test_reply_beats_higher_yield():
    """답장 1건(수확 1)이 수확 3의 무결과 검색어를 이긴다 — 이것이 '많이
    찾힌 것'에서 '통한 것'으로의 이동이다. 8(답장)+4(초안)+2(저장) > 3-1=2."""
    st = FakeStore()
    st.put("keyword_run", WS, "lr-old::seg", {**RUN,
        "yield_by_query": {"Q1 수입사": 3, "Q2 수입사": 1}})
    st.put("outcome", WS, "lr-old::web-1", {
        "request_id": "lr-old", "company_id": "web-1", "segment": "seg",
        "found_by": "Q2 수입사", "saved": True, "drafted": True,
        "replied": "yes"})
    recs = recommend(st, WS, ["일본 수입사 회사소개"])
    assert _rank(recs)[0] == "Q2 수입사"


def test_why_explains_reply():
    """추천 근거에 답장이 드러난다 — 이유를 감추지 않는다."""
    recs = recommend(_store_with(
        [{"found_by": "Q2 수입사", "drafted": True, "replied": "yes"}]),
        WS, ["일본 수입사 회사소개"])
    top = next(r for r in recs if r["query"] == "Q2 수입사")
    assert "답장 1건" in top["why"]
