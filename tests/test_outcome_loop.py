"""B4 — 결과 학습 루프 속성 테스트.

추천이 '많이 찾힌 검색어'에서 '실제로 통한 검색어'로 이동하는지를 결정적으로
검증한다. 근거: Hu-Koren-Volinsky(2008) — 암묵 신호는 신뢰도 등급이고,
미관측은 부정이 아니다. LLM이 없어 CI 게이트로 상시 돈다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.keywords import outcome_weight, recommend
from tests.test_saas_layer import client  # noqa: F401


class FakeStore:
    def __init__(self):
        self.docs = {}

    def put(self, kind, ws, doc_id, body):
        self.docs[(kind, ws, doc_id)] = dict(body)

    def get(self, kind, ws, doc_id):
        return self.docs.get((kind, ws, doc_id))

    def list(self, kind, ws, limit=None):
        # 실 계약과 정렬 — limit을 안 받으면 recommend()가 TypeError로 죽는다
        # (계약 변경을 이 테스트가 잡아냈다).
        rows = [v for (k, w, _), v in self.docs.items()
                if k == kind and w == ws]
        return rows[:limit] if limit is not None else rows


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


class TestDerivedOnGet:
    """GET /lead-requests/{rid}가 인사이트·초안·결과를 후보별로 실어준다.

    실측: 세 가지 다 저장은 되고 있었는데 조회에 없어, 새로고침하면 화면이
    '초안이 사라졌다'고 보였다. 저장은 되고 복원이 안 되면 도구가 아니다.
    """
    def test_get_carries_derived_by_candidate(self, client):
        from app.saas.store import get_saas_store
        from app.saas.router import _derived_key
        H = {"X-Dev-User": "boram"}
        store = get_saas_store()
        pv = store.new_id("pv")
        store.put("profile_version", "ws-boram", pv, {"version_id": pv, "profile": {}})
        rid = "lr-derived"
        doc = {"request_id": rid, "profile_version_id": pv, "generation": 2,
               "candidates": [{"company_id": "c1", "name": "A"},
                              {"company_id": "c2", "name": "B"}]}
        store.put("lead_request", "ws-boram", rid, doc)
        store.put("email_draft", "ws-boram", _derived_key(doc, rid, "c1"),
                  {"drafts": [{"subject": "s", "body": "b", "warnings": []}]})
        store.put("outcome", "ws-boram", f"{rid}::c1",
                  {"saved": True, "drafted": True, "replied": "yes"})
        # 다른 세대의 초안은 실리면 안 된다 — 같은 company_id에 다른 회사였을 수 있다
        store.put("email_draft", "ws-boram", f"{rid}::g1::c2",
                  {"drafts": [{"subject": "old", "body": "old", "warnings": []}]})

        r = client.get(f"/saas/lead-requests/{rid}", headers=H).json()
        d = r["derived"]
        assert d["c1"]["draft"]["drafts"][0]["subject"] == "s"
        # 추적 사실(opened·stage)도 함께 실린다 — 빠뜨렸더니 열람이 원장에만
        # 남고 화면에는 영영 안 보였다(실측).
        assert d["c1"]["outcome"] == {"saved": True, "drafted": True,
                                      "replied": "yes", "opened": False,
                                      "stage": ""}
        assert d["c1"]["has_insight"] is False
        assert "c2" not in d                       # 옛 세대 초안은 무시


class TestPipeline:
    """저장한 리드를 요청 넘어 단계별로 — 원장에 있는 것을 모을 뿐이다."""
    def _seed(self, client):
        from app.saas.store import get_saas_store
        store = get_saas_store()
        for rid, title in (("lr-a", "A요청"), ("lr-b", "B요청")):
            store.put("lead_request", "ws-boram", rid, {"request_id": rid, "title": title})
        return store

    def test_saved_leads_are_grouped_by_stage(self, client):
        store = self._seed(client)
        store.put("outcome", "ws-boram", "lr-a::c1", {"request_id": "lr-a", "company_id": "c1",
                  "name": "UNDO", "saved": True, "drafted": True, "replied": "", "stage": "contacted"})
        store.put("outcome", "ws-boram", "lr-b::c2", {"request_id": "lr-b", "company_id": "c2",
                  "name": "Varaha", "saved": True, "drafted": False, "replied": "yes", "stage": "replied"})
        store.put("outcome", "ws-boram", "lr-b::c3", {"request_id": "lr-b", "company_id": "c3",
                  "name": "NotSaved", "saved": False})
        r = client.get("/saas/pipeline", headers={"X-Dev-User": "boram"}).json()
        assert r["total"] == 2
        assert [x["name"] for x in r["board"]["contacted"]] == ["UNDO"]
        assert r["board"]["replied"][0]["request_title"] == "B요청"
        assert all(x["name"] != "NotSaved" for st in r["board"].values() for x in st)

    def test_stage_moves_and_replied_promotes(self, client):
        from app.saas.store import get_saas_store
        store = self._seed(client)
        pv = store.new_id("pv")
        store.put("profile_version", "ws-boram", pv, {"version_id": pv, "profile": {
            "basic": {"name": "A", "country": "한국", "industry": "x"}, "description": "d",
            "problem_solved": {"value": "p", "provenance": "stated", "confidence": 0.9},
            "solution": {"value": "s", "provenance": "stated", "confidence": 0.9},
            "target_customer": {"value": "t", "provenance": "stated", "confidence": 0.9}}})
        store.put("lead_request", "ws-boram", "lr-a", {"request_id": "lr-a", "title": "A",
                  "profile_version_id": pv,
                  "intent": {"value_props": ["revenue_growth"], "lead_count": 5},
                  "candidates": [{"company_id": "c1", "name": "UNDO", "source_url": "https://un-do.com"}]})
        H = {"X-Dev-User": "boram"}
        client.post("/saas/lead-requests/lr-a/candidates/c1/outcome", headers=H, json={"saved": True})
        o = store.get("outcome", "ws-boram", "lr-a::c1")
        assert o["stage"] == "saved" and o["name"] == "UNDO"
        client.post("/saas/lead-requests/lr-a/candidates/c1/outcome", headers=H, json={"replied": "yes"})
        assert store.get("outcome", "ws-boram", "lr-a::c1")["stage"] == "replied"
        client.post("/saas/lead-requests/lr-a/candidates/c1/outcome", headers=H, json={"stage": "meeting", "note": "9/2 콜"})
        o = store.get("outcome", "ws-boram", "lr-a::c1")
        assert o["stage"] == "meeting" and o["note"] == "9/2 콜"
        assert client.post("/saas/lead-requests/lr-a/candidates/c1/outcome", headers=H,
                           json={"stage": "bogus"}).status_code in (400, 422)
