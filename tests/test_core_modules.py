"""T12 — 깨져도 아무도 모를 경로를 게이트로 덮는다 (감사 확정 medium).

세 구멍:
1. /refine에 통합 테스트가 0건이었다 — 멀티턴이 통째로 죽어도 화면상
   "오늘은 갈림이 없나 보다"로 보인다.
2. read_company·confirmed_ratio에 테스트가 0건인데, 실패는 로그 한 줄로
   삼켜져 "오늘은 신호가 없네"처럼 보인다.
3. 세 SaasStore가 같은 계약을 지키는지 검증하는 공통 테스트가 없었다 —
   하나만 고치고 다른 하나를 잊는 전형적 사고.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import (AxisStatus, BasicInfo, CompanyOntology, Intent,
                         OntologyAxis, Profile, ProvField, Provenance, ValueProp)

H = {"X-Dev-User": "boram"}


# ── 1. /refine 통합 ────────────────────────────────────────────────

@pytest.fixture()
def refine_client(tmp_path, monkeypatch):
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "_load_dotenv", lambda: None)
    for k, v in [("SAAS_AUTH", "dev"), ("SAAS_STORE", "local"),
                 ("SAAS_ALLOWED_USERS", "boram")]:
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SAAS_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("A2A_DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.delenv("SNIPPET_LOG_PATH", raising=False)
    import app.saas.store as store_mod
    store_mod._store = None

    import app.connectors.tavily as tv
    import app.engine.company_ontology as co
    import app.engine.scorer_client as sc
    import app.saas.router as r

    # 오프라인 계약 — 네트워크·LLM에 닿지 않는다
    monkeypatch.setattr(sc, "score_batch_timed", lambda p: (None, None))
    monkeypatch.setattr(sc, "api_score_batch", lambda p: (None, None))
    monkeypatch.setattr(sc, "api_rank_listwise", lambda *a, **k: (None, None))
    import app.engine.retrieve as ret
    monkeypatch.setattr(ret, "synthesize_counterpart", lambda req: "상대상")

    state = {"wave": 0}

    def fake_search(q, settings, max_results=8):
        # 웨이브마다 다른 URL — 재수집 금지 계약을 검증할 수 있게
        return [{"url": f"https://ex.jp/w{state['wave']}-{i}",
                 "title": f"회사{i}", "snippet": "s"} for i in range(3)]

    monkeypatch.setattr(tv, "search", fake_search)
    monkeypatch.setattr(r, "filter_company_hits", lambda hits: (hits, 0))

    def fake_extract(extractor, hits, counterpart, requester_name=""):
        return [{"name": f"사{h['url'][-4:]}", "name_ko": "", "what": "유통",
                 "signal": "", "url": h["url"]} for h in hits]

    monkeypatch.setattr(r, "extract_companies", fake_extract)
    monkeypatch.setattr(co, "read_company", lambda e, c, **k: CompanyOntology(
        axes={"offering": OntologyAxis(value="식품 유통", status=AxisStatus.confirmed)},
        search_keywords=["kw"], source_url=c["url"]))

    class Canned:
        def extract_json(self, system, user, schema, deep=False, allow_foreign=False):
            req = set(schema.get("required", []))
            if "queries" in req:
                state["wave"] += 1
                return {"queries": [f"q-wave{state['wave']}"]}
            if "questions" in req:
                return {"questions": []}
            return {}

    monkeypatch.setattr(r, "get_extractor", lambda s: Canned())

    import app.main as main_mod
    client = TestClient(main_mod.app)

    store = store_mod.get_saas_store()
    store.put("profile_version", "ws-boram", "pv-1", {
        "version_id": "pv-1",
        "profile": Profile(
            basic=BasicInfo(name="귤메달", country="한국", industry="food"),
            description="건강음료",
            problem_solved=ProvField(value="p", provenance=Provenance.stated),
            solution=ProvField(value="s", provenance=Provenance.stated),
            target_customer=ProvField(value="t", provenance=Provenance.stated),
        ).model_dump(mode="json")})
    rid = client.post("/saas/lead-requests", headers=H, json={
        "title": "t", "profile_version_id": "pv-1",
        "intent": {"value_props": ["revenue_growth"], "target_region": "일본",
                   "lead_count": 10}}).json()["request_id"]
    doc = store.get("lead_request", "ws-boram", rid)
    doc["search_brief"] = {"synthesized_counterpart": "상대상",
                           "query_hypotheses": ["q0"], "must_have": [],
                           "exclusions": [], "deterministic_anchor": "a"}
    store.put("lead_request", "ws-boram", rid, doc)
    return client, rid, store


def _poll(client, job_id):
    for _ in range(60):
        j = client.get(f"/saas/jobs/{job_id}", headers=H).json()
        if j["status"] != "running":
            assert j["status"] == "done", f"job 실패: {j.get('error')}"
            return j["result"]
    raise AssertionError("job 미완료")


def test_refine_adds_wave_without_recollecting_seen_urls(refine_client):
    """2차 웨이브는 새 URL만 모은다 — 같은 URL을 다시 추출하면 중복 후보가 된다."""
    client, rid, store = refine_client
    r1 = _poll(client, client.post(f"/saas/lead-requests/{rid}/search",
                                   headers=H, json={"segments": []}).json()["job_id"])
    n1 = len(r1["candidates"])
    assert n1 == 3
    r2 = _poll(client, client.post(f"/saas/lead-requests/{rid}/refine", headers=H,
               json={"answers": ["더 좁게"], "liked": [], "disliked": [],
                     "done": False}).json()["job_id"])
    assert r2["final"] is False and r2["wave"] == 2
    assert r2["new_found"] == 3
    urls = [c["source_url"] for c in r2["candidates"]]
    assert len(set(urls)) == len(urls), "URL 중복 — 재수집 금지 계약 위반"


def test_refine_done_finalizes_without_new_search(refine_client):
    client, rid, store = refine_client
    _poll(client, client.post(f"/saas/lead-requests/{rid}/search",
                              headers=H, json={"segments": []}).json()["job_id"])
    before = store.get("lead_request", "ws-boram", rid)["pool"]
    res = _poll(client, client.post(f"/saas/lead-requests/{rid}/refine", headers=H,
                json={"answers": [], "liked": [], "disliked": [],
                      "done": True}).json()["job_id"])
    assert res["final"] is True
    after = store.get("lead_request", "ws-boram", rid)
    assert len(after["pool"]) == len(before), "확정은 새로 찾지 않는다"
    assert after["status"] == "candidates_ready"


def test_refine_disliked_removed_from_results(refine_client):
    client, rid, store = refine_client
    r1 = _poll(client, client.post(f"/saas/lead-requests/{rid}/search",
                                   headers=H, json={"segments": []}).json()["job_id"])
    drop = r1["candidates"][0]["company_id"]
    res = _poll(client, client.post(f"/saas/lead-requests/{rid}/refine", headers=H,
                json={"answers": [], "liked": [], "disliked": [drop],
                      "done": True}).json()["job_id"])
    assert drop not in [c["company_id"] for c in res["candidates"]]


def test_refine_falls_back_to_rerank_when_query_generation_fails(
        refine_client, monkeypatch):
    """재검색어 생성이 죽어도 반응만 반영해 다시 정렬한다 — 막다른 길이 아니다."""
    client, rid, store = refine_client
    _poll(client, client.post(f"/saas/lead-requests/{rid}/search",
                              headers=H, json={"segments": []}).json()["job_id"])

    import app.saas.router as r

    class Broken:
        def extract_json(self, *a, **k):
            raise RuntimeError("LLM 다운")

    monkeypatch.setattr(r, "get_extractor", lambda s: Broken())
    res = _poll(client, client.post(f"/saas/lead-requests/{rid}/refine", headers=H,
                json={"answers": [], "liked": [], "disliked": [],
                      "done": False}).json()["job_id"])
    assert res["final"] is False and "note" in res
    assert res["candidates"], "재랭킹 결과는 남아야 한다"


# ── 2. 온톨로지 판독 ───────────────────────────────────────────────

class _Ext:
    def __init__(self, payload):
        self.payload = payload

    def extract_json(self, system, user, schema, deep=False, allow_foreign=False):
        return self.payload


def _full_axes(**over):
    from app.engine.company_ontology import AXES
    axes = {k: {"value": f"{k} 값", "status": "assumed"} for k, _ in AXES}
    axes.update(over)
    return axes


class TestReadCompany:
    def _company(self):
        return {"name": "㈜테스트", "name_ko": "테스트", "what": "유통",
                "signal": "", "url": "https://ex.jp/a"}

    def test_unknown_axis_value_is_emptied(self):
        """status=unknown이면 value는 빈 문자열 — 모르는 것을 그럴듯하게
        채운 값이 화면에 남으면 안 된다."""
        from app.engine.company_ontology import read_company
        ext = _Ext({"axes": _full_axes(
            offering={"value": "지어낸 값", "status": "unknown"}),
            "search_keywords": ["kw"], "signals": [], "contacts": []})
        ont = read_company(ext, self._company())
        assert ont.axes["offering"].value == ""
        assert ont.axes["offering"].status == AxisStatus.unknown

    def test_missing_axis_raises_not_silently_empty(self):
        """스키마가 바뀌어 축이 빠지면 조용히 빈 판독을 만들지 않고 올린다 —
        호출자(_discover)가 '판독 실패'로 세야 정직한 집계가 된다."""
        from app.engine.company_ontology import read_company
        axes = _full_axes()
        axes.pop("entry_path")
        ext = _Ext({"axes": axes, "search_keywords": [], "signals": [],
                    "contacts": []})
        with pytest.raises(KeyError):
            read_company(ext, self._company())

    def test_signals_and_contacts_mapped(self):
        from app.engine.company_ontology import read_company
        ext = _Ext({"axes": _full_axes(), "search_keywords": ["kw"],
                    "signals": [{"category": "procurement", "evidence": "모집 공고",
                                 "observed_at": "2026년 4월"}],
                    "contacts": [{"channel": "메일", "value": "a@b.jp",
                                  "role_hint": "구매팀"}]})
        ont = read_company(ext, self._company())
        assert ont.signals[0].category.value == "procurement"
        assert ont.contacts[0].role_hint == "구매팀"

    def test_empty_evidence_signal_dropped(self):
        """근거 없는 신호는 버린다 — evidence가 인용 계약의 본체다."""
        from app.engine.company_ontology import read_company
        ext = _Ext({"axes": _full_axes(), "search_keywords": [],
                    "signals": [{"category": "expansion", "evidence": "   ",
                                 "observed_at": ""}],
                    "contacts": [{"channel": "메일", "value": "  ",
                                  "role_hint": ""}]})
        ont = read_company(ext, self._company())
        assert ont.signals == [] and ont.contacts == []

    def test_confirmed_ratio(self):
        from app.engine.company_ontology import confirmed_ratio
        ont = CompanyOntology(axes={
            "a": OntologyAxis(value="x", status=AxisStatus.confirmed),
            "b": OntologyAxis(value="y", status=AxisStatus.assumed),
            "c": OntologyAxis(value="", status=AxisStatus.unknown),
            "d": OntologyAxis(value="z", status=AxisStatus.confirmed)})
        assert confirmed_ratio(ont) == 0.5
        assert confirmed_ratio(CompanyOntology()) == 0.0


# ── 3. 저장 계층 공통 계약 ─────────────────────────────────────────

class TestStoreContractParity:
    """세 백엔드가 같은 메서드·같은 시그니처를 갖는지 본다.

    Firestore·Supabase는 실 서버 없이 동작을 못 재현하므로, 최소한 계약이
    어긋나는 것(하나만 고치고 다른 하나를 잊는 사고)은 정적으로 잡는다.
    """
    METHODS = ["put", "get", "list", "delete", "delete_prefix",
               "delete_workspace", "new_id", "reserve_cost"]

    def _classes(self):
        from app.saas.store import (FirestoreSaasStore, LocalSaasStore,
                                    SupabaseSaasStore)
        return [LocalSaasStore, FirestoreSaasStore, SupabaseSaasStore]

    @pytest.mark.parametrize("name", METHODS)
    def test_all_backends_expose_method(self, name):
        for cls in self._classes():
            assert callable(getattr(cls, name, None)), \
                f"{cls.__name__}에 {name}이 없다"

    def test_signatures_match(self):
        import inspect
        classes = self._classes()
        for name in self.METHODS:
            sigs = {cls.__name__: list(
                inspect.signature(getattr(cls, name)).parameters)
                for cls in classes}
            first = next(iter(sigs.values()))
            assert all(v == first for v in sigs.values()), \
                f"{name} 시그니처 불일치: {sigs}"


# ── 4. 원장 왕복 ───────────────────────────────────────────────────

def test_record_run_to_recommend_roundtrip(tmp_path, monkeypatch):
    """record_run이 쓰는 키와 recommend가 읽는 키가 어긋나면 추천이 조용히
    0건이 된다 — 기존 테스트는 문서를 손으로 만들어 써서 못 잡았다."""
    monkeypatch.setenv("SAAS_DB_PATH", str(tmp_path / "s.db"))
    from app.engine import keywords as kw
    from app.saas.store import LocalSaasStore
    st = LocalSaasStore(str(tmp_path / "s.db"))
    ont = CompanyOntology(
        axes={"offering": OntologyAxis(value="일본 식품 수입 유통",
                                       status=AxisStatus.confirmed)},
        search_keywords=["食品輸入"], source_url="u").model_dump(mode="json")
    kw.record_run(st, "ws-1", "lr-old-w1", segment="수입사",
                  queries=["일본 식품 수입사 회사소개"],
                  yield_by_query={"일본 식품 수입사 회사소개": 3},
                  kept=3, ontologies=[ont])
    recs = kw.recommend(st, "ws-1", ["다른 질의"], current_ontologies=[ont],
                        exclude_rid="lr-new")
    assert recs, "왕복이 끊겼다 — 쓴 키를 읽지 못한다"
    assert recs[0]["query"] == "일본 식품 수입사 회사소개"


# ── 5. job 원장이 인스턴스를 넘는가 (V1 — 서버리스 대응) ──────────────

class TestJobLedgerCrossInstance:
    """서버리스에서는 /search를 처리한 인스턴스와 폴링을 받는 인스턴스가 다르다.
    job 원장이 프로세스 메모리·인스턴스 로컬 파일에 있으면 폴링이 아예 못 찾는다
    — 기능이 성립하지 않는다. 별도 JobStore 인스턴스로 그 상황을 재현한다."""

    def _stores(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAAS_STORE", "local")
        monkeypatch.setenv("SAAS_DB_PATH", str(tmp_path / "s.db"))
        import app.saas.store as sm
        sm._store = None
        from app.jobs import JobStore
        return JobStore(), JobStore()      # 서로 다른 '인스턴스'

    def test_other_instance_sees_completed_job(self, tmp_path, monkeypatch):
        a, b = self._stores(tmp_path, monkeypatch)
        job, _ = a.create(ws="ws-1")
        a.run(job, lambda: {"candidates": ["A사"]})
        seen = b.get(job.job_id, "ws-1")
        assert seen is not None, "다른 인스턴스가 job을 못 본다"
        assert seen.status.value == "done"
        assert seen.result == {"candidates": ["A사"]}

    def test_other_instance_sees_progress_mid_run(self, tmp_path, monkeypatch):
        """실행 중 진행 로그가 원장에 흘러야 폴링이 '작업 중'만 보지 않는다."""
        import app.jobs as jm
        monkeypatch.setattr(jm, "_FLUSH_EVERY", 0.0)   # 매 줄 flush(테스트)
        a, b = self._stores(tmp_path, monkeypatch)
        job, _ = a.create(ws="ws-1")
        observed = {}

        def work():
            from app import progress
            progress.log("검색", "웹 수집 시작")
            progress.log("검색", "실존 기업 3곳 추출")
            other = b.get(job.job_id, "ws-1")
            observed["status"] = other.status.value
            observed["msgs"] = [e.get("message") for e in other.log.entries]
            return {"ok": True}

        a.run(job, work)
        assert observed["status"] == "running"
        assert any("3곳 추출" in (m or "") for m in observed["msgs"]), \
            f"진행 로그가 원장에 없다: {observed['msgs']}"

    def test_workspace_isolation_across_instances(self, tmp_path, monkeypatch):
        a, b = self._stores(tmp_path, monkeypatch)
        job, _ = a.create(ws="ws-1")
        a.run(job, lambda: {"secret": "보람의 후보"})
        assert b.get(job.job_id, "ws-2") is None, "남의 워크스페이스에서 보인다"
        assert b.get(job.job_id, "ws-1") is not None

    def test_stale_running_job_reaped_on_read(self, tmp_path, monkeypatch):
        """인스턴스가 조용히 사라지면 '영원한 running'이 남는다 — 조회 시점에
        수확한다(서버리스엔 '재시작'이라는 시점이 없다)."""
        import time

        import app.jobs as jm
        a, b = self._stores(tmp_path, monkeypatch)
        job, _ = a.create(ws="ws-1")
        job.status = job.status.__class__.running
        a._put(job)
        # 갱신 시각을 과거로 밀어 죽은 실행을 흉내낸다
        st = jm._store()
        d = st.get("job", "ws-1", job.job_id)
        d["updated"] = time.time() - jm._STALE_AFTER - 10
        st.put("job", "ws-1", job.job_id, d)
        seen = b.get(job.job_id, "ws-1")
        assert seen.status.value == "error"
        assert "중단" in seen.error["message"]
