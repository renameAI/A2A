"""끊긴 검색을 이어받는다 — 서버리스에서 인스턴스는 조용히 사라진다.

배경: job은 BackgroundTasks로 인스턴스 안에서 돈다. 인스턴스가 재활용되면
job이 소리 없이 죽고(600초 뒤 좀비 수확), 그때까지의 검색·판독이 통째로
버려졌다 — 재시도는 처음부터 다시 돌고 다시 지불했다. 웨이브1이 100~180초라
데모 중 이 창에 걸릴 확률이 낮지 않다.
"""
from tests.test_saas_layer import client, H  # noqa: F401


def _seed(client, monkeypatch, pool=None, segments=None):
    from app.saas.store import get_saas_store
    store = get_saas_store()
    pv = store.new_id("pv")
    store.put("profile_version", "ws-boram", pv, {"version_id": pv, "profile": {
        "basic": {"name": "A", "country": "한국", "industry": "x"},
        "description": "d",
        "problem_solved": {"value": "p", "provenance": "stated", "confidence": 0.9},
        "solution": {"value": "s", "provenance": "stated", "confidence": 0.9},
        "target_customer": {"value": "t", "provenance": "stated", "confidence": 0.9}}})
    rid = "lr-resume"
    store.put("lead_request", "ws-boram", rid, {
        "request_id": rid, "profile_version_id": pv, "title": "t",
        "intent": {"value_props": ["revenue_growth"], "lead_count": 5},
        "search_brief": {"synthesized_counterpart": "상대",
                         "query_hypotheses": ["q"], "deterministic_anchor": "a",
                         "must_have": [], "exclusions": []},
        "pool": pool or [], "segments_selected": segments, "asked": [],
        "extra_queries_used": [] if segments is not None else None})
    return store, rid


def _stub_discover(monkeypatch, found):
    """_discover를 대신해 '이번 시도에서 새로 찾은 것'만 돌려준다."""
    import app.engine.clarify as clarify
    import app.engine.keywords as kw
    import app.saas.router as R
    monkeypatch.setattr(R, "_discover",
                        lambda *a, **k: [dict(c) for c in found])
    monkeypatch.setattr(R, "_rank_pool",
                        lambda profile, intent, pool, liked, disliked, k,
                        reach_facts=None: list(pool))
    # 지역 import(`from ..engine.clarify import generate_questions`)는
    # 함수가 실행될 때 모듈에서 이름을 가져오므로 원본 모듈을 패치한다.
    monkeypatch.setattr(clarify, "generate_questions",
                        lambda extractor, cands, cp, asked: [])
    monkeypatch.setattr(kw, "recommend", lambda *a, **k: [])
    monkeypatch.setattr(kw, "record_run", lambda *a, **k: None)
    # 세그먼트별 브리프도 LLM을 탄다 — 이 테스트가 재는 것은 재개 로직이다
    from app.schemas import SearchBrief
    monkeypatch.setattr(R, "build_search_brief", lambda req, segment=None:
                        SearchBrief(deterministic_anchor="a",
                                    synthesized_counterpart="상대",
                                    query_hypotheses=["q"], must_have=[],
                                    exclusions=[]))
    monkeypatch.setattr(R, "get_extractor", lambda s: object())
    import app.engine.llm as llm
    monkeypatch.setattr(llm, "get_extractor", lambda s: object())


def _cand(cid, name, site):
    return {"company_id": cid, "name": name, "what": "w", "signal": "",
            "source_url": f"https://{site}/", "pain_signal": "w",
            "ontology": None, "p": 0.7, "segment": "s"}


def _wait(client, job):
    import time
    for _ in range(60):
        d = client.get(f"/saas/jobs/{job}", headers=H).json()
        if d["status"] in ("done", "error"):
            assert d["status"] == "done", d.get("error")
            return d["result"]
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_same_conditions_resume_keeps_the_earlier_pool(client, monkeypatch):
    """끊긴 뒤 같은 조건으로 다시 누르면 이어받는다 — 이것이 사용자의 행동이다."""
    store, rid = _seed(client, monkeypatch,
                       pool=[_cand("c1", "이전후보", "old.com")],
                       segments=["세그A"])
    _stub_discover(monkeypatch, [_cand("c2", "새후보", "new.com")])
    _wait(client, client.post(f"/saas/lead-requests/{rid}/search", headers=H,
                              json={"segments": ["세그A"], "extra_queries": []}
                              ).json()["job_id"])
    pool = store.get("lead_request", "ws-boram", rid)["pool"]
    assert {c["name"] for c in pool} == {"이전후보", "새후보"}


def test_changed_conditions_start_over(client, monkeypatch):
    """업종을 바꿨으면 새 검색이다 — 옛 후보를 끌고 가면 조건과 안 맞는다."""
    store, rid = _seed(client, monkeypatch,
                       pool=[_cand("c1", "이전후보", "old.com")],
                       segments=["세그A"])
    _stub_discover(monkeypatch, [_cand("c2", "새후보", "new.com")])
    _wait(client, client.post(f"/saas/lead-requests/{rid}/search", headers=H,
                              json={"segments": ["세그B"], "extra_queries": []}
                              ).json()["job_id"])
    pool = store.get("lead_request", "ws-boram", rid)["pool"]
    assert {c["name"] for c in pool} == {"새후보"}


def test_resume_does_not_duplicate_the_same_company(client, monkeypatch):
    """이어받기가 중복을 만들면 안 된다 — 이름+사이트로 합쳐진다."""
    store, rid = _seed(client, monkeypatch,
                       pool=[_cand("c1", "같은회사", "same.com")],
                       segments=["세그A"])
    _stub_discover(monkeypatch, [_cand("c2", "같은회사", "same.com")])
    _wait(client, client.post(f"/saas/lead-requests/{rid}/search", headers=H,
                              json={"segments": ["세그A"], "extra_queries": []}
                              ).json()["job_id"])
    pool = store.get("lead_request", "ws-boram", rid)["pool"]
    assert len(pool) == 1


def test_stale_running_job_is_reaped_with_a_resumable_message(client):
    """좀비 수확 메시지가 '재개하면 된다'로 이어져야 한다."""
    import time

    from app.jobs import store as job_store
    from app.saas.store import get_saas_store
    job, _ = job_store.create(ws="ws-boram")
    job.status = job.status.__class__.running
    job_store._put(job)
    # 갱신 시각을 과거로 밀어 stale 조건을 만든다
    st = get_saas_store()
    raw = st.get("job", "ws-boram", job.job_id)
    raw["updated"] = time.time() - 10_000
    st.put("job", "ws-boram", job.job_id, raw)
    job_store._jobs.pop(job.job_id, None)      # 메모리 캐시가 가리지 않게
    got = job_store.get(job.job_id, "ws-boram")
    assert got.status.value == "error"
    assert "중단되었습니다" in got.error["message"]
