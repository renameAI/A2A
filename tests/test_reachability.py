"""문턱(reachability) — "이 회사가 우리에게 답장할 확률"을 랭킹에 접는다.

실측(귤메달): 보완성×실존만 보면 롯데·현대백화점이 1·2위(점수 0.09·0.08).
제주 소규모 브랜드의 콜드메일이 대기업 벤더 등록 절차를 첫 메일로 뚫을 확률은
낮고, 실측으로도 대기업일수록 접점 0건이었다. 닿을 수 없는 후보가 위에 있으면
목록 전체가 안 믿긴다.

판정=모델(read_company가 요청 기업 문맥으로 p·why 산출), 결정=코드
(_rank_pool이 0.5+0.5·reach로 가중 — 순위를 조정하되 후보를 지우지 않는다).
"""
from types import SimpleNamespace as NS

from tests.test_saas_layer import client  # noqa: F401

from app.engine.company_ontology import _clamp_p, read_company


class _Canned:
    def __init__(self, extra=None):
        self.src = None
        self.extra = extra or {}

    def extract_json(self, system, src, schema, **k):
        self.src = src
        from app.engine.company_ontology import AXES
        return {"axes": {a: {"value": "", "status": "unknown", "evidence": ""}
                         for a, _ in AXES},
                "search_keywords": [], "signals": [], "contacts": [],
                "business_language": "", **self.extra}


def test_requester_context_reaches_the_prompt():
    """요청 기업이 누구인지 없이는 문턱을 잴 수 없다."""
    x = _Canned()
    read_company(x, {"name": "롯데백화점", "what": "유통", "signal": "",
                     "url": "https://x.com"},
                 requester="귤메달 — 제주 시트러스 브랜드")
    assert "[요청 기업 — reachability 판정의 기준]" in x.src
    assert "귤메달" in x.src


def test_reachability_is_parsed_and_clamped():
    x = _Canned({"reachability": {"p": 0.15, "why": "대기업 벤더 등록 절차"}})
    ont = read_company(x, {"name": "A", "what": "w", "signal": "", "url": "u"})
    assert ont.reachability == 0.15
    assert ont.reachability_why == "대기업 벤더 등록 절차"
    # 구형·비정상 응답은 판정 없음으로 — 벌점을 주지 않는다
    assert read_company(_Canned(), {"name": "A", "what": "w", "signal": "",
                                    "url": "u"}).reachability is None
    assert _clamp_p(1.7) == 1.0 and _clamp_p("x") is None


def test_rank_pool_sinks_high_threshold_candidates(monkeypatch):
    """보완성이 같아도 문턱 높은 대기업이 아래로 간다. 판정 없으면 벌점 없음."""
    from app.saas import router as R
    class _Res:
        def __init__(self, ids):
            self.candidates = [NS(company_id=i, retrieval_score=0.30,
                                  model_dump=lambda mode=None, i=i: {"company_id": i})
                               for i in ids]
    monkeypatch.setattr(R, "retrieve", lambda req, candidate_records: _Res(
        [r.company_id for r in candidate_records]))
    monkeypatch.setattr(R, "candidate_record_from_profile",
                        lambda cid, prof, url, pain_signal=None: NS(company_id=cid))
    monkeypatch.setattr(R, "RetrieveRequest", lambda **kw: NS(**kw))
    intent = NS(target_region="", target_industry="")

    def mk(cid, reach):
        ont = None if reach == "none" else {"reachability": reach,
                                            "reachability_why": "w"}
        return {"company_id": cid, "name": cid, "what": "w", "signal": "",
                "source_url": "u", "pain_signal": "w", "ontology": ont, "p": 0.8}
    pool = [mk("lotte", 0.1), mk("mid-distributor", 0.8), mk("legacy", "none")]
    ranked = R._rank_pool(profile=None, intent=intent, pool=pool,
                          liked=[], disliked=[], k=10)
    order = [r["company_id"] for r in ranked]
    assert order[0] == "legacy"            # 판정 없음 → 가중 1.0 (벌점 없음)
    assert order[-1] == "lotte"            # 문턱 0.1 → 0.55배로 가라앉는다
    by = {r["company_id"]: r for r in ranked}
    assert by["lotte"]["reach_w"] == 0.55
    assert by["mid-distributor"]["reach_w"] == 0.9
    assert by["legacy"]["reach_w"] == 1.0
    # 지워지지는 않는다 — 셋 다 목록에 남는다
    assert len(ranked) == 3


def test_partial_entries_replace_not_accumulate():
    """부분 결과는 최신 하나만 — 방출 횟수만큼 쌓이면 job 문서가 부푼다."""
    from app.progress import RunLog
    rl = RunLog()
    for n in range(5):
        rl.add("검색", f"p{n}", type="partial", data={"n": n})
        rl.add("검색", f"log{n}")
    partials = [e for e in rl.entries if e["type"] == "partial"]
    assert len(partials) == 1 and partials[0]["data"] == {"n": 4}
    assert sum(1 for e in rl.entries if e["type"] == "log") == 5


def test_progress_partial_is_noop_without_a_job():
    """엔진 단독 사용(테스트·스크립트)에서 죽으면 안 된다."""
    from app import progress
    progress.partial("검색", "m", {"candidates": []})   # 예외 없이 통과


def test_fake_urgency_is_forbidden_when_no_signal():
    from app.engine.compose_lead import _kit_lines
    assert "시의성 표현을 만들지 마라" in _kit_lines({"hook": "h", "why_now": ""})
    assert "만들지 마라" not in _kit_lines({"hook": "h", "why_now": "MS 계약"})


def test_deep_read_rescores_with_updated_reachability(client, monkeypatch):
    """사이트를 읽고 문턱 판정이 갱신되면 순위도 갱신돼야 한다 — 아니면
    판정이 버려진다. 보완성·p·피드백은 저장값 그대로, reach 가중만 새로."""
    import app.saas.router as R
    from app.engine import company_ontology as CO
    from app.ingest import crawler
    from app.schemas import CompanyOntology, OntologyAxis
    from app.engine.company_ontology import AXES
    from tests.test_deep_read import _seed_request, _wait, H

    monkeypatch.setattr(crawler, "crawl_website", lambda url, s: "본문")
    reach_by_name = {"BigCorp": 0.05, "MidCo": 0.9}

    def fake_read(extractor, company, *, region="", purpose="revenue",
                  site_text="", requester=""):
        return CompanyOntology(
            axes={k: OntologyAxis(value="v", status="confirmed", evidence="e")
                  for k, _ in AXES},
            search_keywords=[], signals=[], contacts=[],
            reachability=reach_by_name[company["name"]],
            reachability_why="w")
    monkeypatch.setattr(CO, "read_company", fake_read)
    monkeypatch.setattr(R, "get_extractor", lambda s: object())

    rid = _seed_request(client, monkeypatch, [
        {"company_id": "c1", "name": "BigCorp", "source_url": "https://big.com",
         "source_kind": "own", "what": "w", "signal": "", "pain_signal": "w",
         "ontology": None, "p": 0.8, "complementarity": 0.33,
         "retrieval_score": 0.264, "feedback_bonus": 0},
        {"company_id": "c2", "name": "MidCo", "source_url": "https://mid.com",
         "source_kind": "own", "what": "w", "signal": "", "pain_signal": "w",
         "ontology": None, "p": 0.7, "complementarity": 0.28,
         "retrieval_score": 0.196, "feedback_bonus": 0},
    ])
    res = _wait(client, client.post(f"/saas/lead-requests/{rid}/deep-read",
                                    headers=H, json={}).json()["job_id"])
    order = [c["company_id"] for c in res["candidates"]]
    # BigCorp: 0.33·0.8·0.525=0.139 < MidCo: 0.28·0.7·0.95=0.186 → 역전
    assert order == ["c2", "c1"]
    by = {c["company_id"]: c for c in res["candidates"]}
    assert by["c1"]["reach_w"] == 0.525 and by["c2"]["reach_w"] == 0.95
    # 저장본도 같은 순서
    from app.saas.store import get_saas_store
    doc = get_saas_store().get("lead_request", "ws-boram", rid)
    assert [c["company_id"] for c in doc["candidates"]] == ["c2", "c1"]
