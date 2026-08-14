"""SaaS API (이슈 #6-B~E) — 기획서 §11의 MVP 부분집합.

설계: 엔진은 무상태 계산기, 이 계층이 Firestore(또는 local)에 보존 책임을 진다.
장시간 작업은 기존 JobStore+폴링을 그대로 쓴다 (/product/jobs/{id} 공유).
Judge는 어떤 경로에서도 호출하지 않는다 (§2.3).
"""
from fastapi import APIRouter, BackgroundTasks, Depends
from typing import Literal

from pydantic import BaseModel, Field

from ..jobs import store as job_store
from ..config import get_settings
from ..engine.candidate_adapter import candidate_record_from_profile
from ..engine.candidate_extract import extract_companies, filter_company_hits
from ..engine import keywords as kw
from ..engine.candidate_insight import build_insight
from ..engine.compose_lead import compose_lead
from ..engine.llm import get_extractor
from ..engine.represent import represent
from ..engine.retrieve import build_search_brief, retrieve, propose_segments
from ..errors import EngineError, ProfileBelowMinimum
from ..schemas import (Asset, BasicInfo, CandidateInsight, ComposeLeadRequest,
                       DialogueTurn, Intent, PoolChoice, Profile, ProvField,
                       Provenance, RepresentRequest, RetrieveDirection,
                       RetrieveRequest)
from . import cost
from .auth import SaasUser, current_user
from .store import get_saas_store

router = APIRouter(prefix="/saas", tags=["saas"])


def _submit(background: BackgroundTasks, fn) -> dict:
    job, _ = job_store.create()
    background.add_task(job_store.run, job, fn)
    return {"job_id": job.job_id}


# ── LLM 프로바이더 토글 (EXAONE 로컬 ↔ GPT Luna) ────────────────────
# 프로세스 전역 전환 — Settings()가 매 호출 env를 읽으므로 env 교체가 곧 반영이다.
# MVP 운영 도구: 키가 없는 쪽으로는 전환을 거부한다(조용한 대체 없음 — 전환해 놓고
# 첫 호출에서 터지는 것보다 전환 시점에 거부하는 것이 정직하다).

import os as _os


class LlmToggle(BaseModel):
    provider: Literal["local", "openai"]


def _llm_state() -> dict:
    s = get_settings()
    return {
        "provider": s.llm_provider,
        "model": s.openai_model if s.llm_provider == "openai" else s.local_model,
        "label": "GPT Luna" if s.llm_provider == "openai" else "EXAONE 로컬",
        "ready": {"local": bool(s.local_base_url and s.local_model),
                  "openai": bool(s.openai_api_key)},
    }


@router.get("/settings/llm")
def llm_settings(user: SaasUser = Depends(current_user)):
    return _llm_state()


class OpenAIKey(BaseModel):
    key: str = Field(min_length=20)


@router.post("/settings/openai-key")
def set_openai_key(req: OpenAIKey, user: SaasUser = Depends(current_user)):
    """사용자가 브라우저에서 직접 붙여넣은 키를 받는다 — 채팅·로그 미경유.

    처리: ① 형식 검사 ② 프로세스 env 반영(즉시 사용 가능) ③ .env에 영속
    (재시작 생존 — .env는 gitignore라 커밋 불가). 키 값은 어떤 로그·응답에도
    전문을 남기지 않는다(마스킹만 반환)."""
    key = req.key.strip()
    if not key.startswith("sk-"):
        raise EngineError(400, "invalid_input",
                          "OpenAI 키 형식이 아닙니다 (sk- 로 시작해야 해요)")
    _os.environ["OPENAI_API_KEY"] = key
    env_path = _os.environ.get("A2A_ENV_FILE", ".env")
    try:
        from pathlib import Path
        p = Path(env_path)
        lines = p.read_text().splitlines() if p.exists() else []
        lines = [l for l in lines if not l.startswith("OPENAI_API_KEY=")]
        lines.append(f"OPENAI_API_KEY={key}")
        p.write_text("\n".join(lines) + "\n")
        persisted = True
    except OSError:
        persisted = False   # 읽기 전용 컨테이너 등 — 프로세스 env로는 이미 유효
    return {"saved": True, "persisted": persisted,
            "masked": f"sk-****{key[-4:]}", **_llm_state()}


@router.post("/settings/llm")
def set_llm(req: LlmToggle, user: SaasUser = Depends(current_user)):
    s = get_settings()
    if req.provider == "openai" and not s.openai_api_key:
        raise EngineError(409, "config_error",
                          "OPENAI_API_KEY가 없어 GPT로 전환할 수 없습니다 — "
                          "키를 설정한 뒤 다시 시도하세요.")
    if req.provider == "local" and not (s.local_base_url and s.local_model):
        raise EngineError(409, "config_error",
                          "LOCAL_LLM_BASE_URL·LOCAL_LLM_MODEL이 없어 로컬로 "
                          "전환할 수 없습니다.")
    _os.environ["LLM_PROVIDER"] = req.provider
    return _llm_state()


# ── 사용자·워크스페이스 ─────────────────────────────────────────────

@router.get("/me")
def me(user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    ws = store.get("workspace", user.workspace_id, "root")
    if ws is None:
        ws = {"workspace_id": user.workspace_id, "owner": user.uid,
              "email": user.email}
        store.put("workspace", user.workspace_id, "root", ws)
    return {"user": {"uid": user.uid, "email": user.email}, "workspace": ws}


# ── 온보딩 (이슈 #6-C, 기획서 §5) ───────────────────────────────────

class OnboardingCreate(BaseModel):
    assets: list[Asset] = Field(min_length=1)


class OnboardingAnswer(BaseModel):
    answer: str


@router.post("/onboarding-sessions", status_code=201)
def create_session(req: OnboardingCreate,
                   user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    sid = store.new_id("ob")
    doc = {"session_id": sid, "status": "collecting",
           "assets": [a.model_dump(mode="json") for a in req.assets],
           "dialogue": [], "current_questions": [], "profile": None}
    store.put("onboarding", user.workspace_id, sid, doc)
    return doc


@router.post("/onboarding-sessions/{sid}/run", status_code=202)
def run_session(sid: str, background: BackgroundTasks,
                user: SaasUser = Depends(current_user)):
    """Represent 실행 — 미달이어도 세션은 유실되지 않는다 (§5.2·§5.3)."""
    store = get_saas_store()
    doc = store.get("onboarding", user.workspace_id, sid)
    if doc is None:
        raise EngineError(404, "not_found", f"온보딩 세션 {sid} 없음")

    def _run() -> dict:
        doc["status"] = "representing"
        store.put("onboarding", user.workspace_id, sid, doc)
        try:
            rep = represent(RepresentRequest(
                assets=[Asset(**a) for a in doc["assets"]],
                dialogue=[DialogueTurn(**t) for t in doc["dialogue"]]))
        except ProfileBelowMinimum as e:
            # 오류로 끝내지 않고 질문을 세션에 보존 → 채팅이 이어받는다 (§5.3)
            doc["status"] = "clarifying"
            doc["current_questions"] = (e.details or {}).get("open_questions", [])
            store.put("onboarding", user.workspace_id, sid, doc)
            return {"session": doc, "needs_answers": True}
        doc["status"] = "review_required"
        doc["profile"] = rep.profile.model_dump(mode="json")
        doc["current_questions"] = rep.open_questions
        store.put("onboarding", user.workspace_id, sid, doc)
        return {"session": doc, "needs_answers": False,
                "minimum_met": rep.minimum_met}

    return _submit(background, _run)


@router.post("/onboarding-sessions/{sid}/messages")
def answer_session(sid: str, req: OnboardingAnswer,
                   user: SaasUser = Depends(current_user)):
    """질문 1개에 대한 답 → DialogueTurn 누적 (재실행은 /run 재호출)."""
    store = get_saas_store()
    doc = store.get("onboarding", user.workspace_id, sid)
    if doc is None:
        raise EngineError(404, "not_found", f"온보딩 세션 {sid} 없음")
    q = (doc.get("current_questions") or ["회사에 대해 자유롭게 알려주세요"])[0]
    doc["dialogue"].append({"q": q, "a": req.answer})
    doc["current_questions"] = doc.get("current_questions", [])[1:]
    store.put("onboarding", user.workspace_id, sid, doc)
    return doc


@router.post("/onboarding-sessions/{sid}/approve")
def approve_profile(sid: str, user: SaasUser = Depends(current_user)):
    """프로필 승인 → 버전 생성 (§3.3). 이후 Lead Request가 이 버전을 참조."""
    store = get_saas_store()
    doc = store.get("onboarding", user.workspace_id, sid)
    if not doc or not doc.get("profile"):
        raise EngineError(409, "invalid_state", "승인할 프로필이 없습니다 — run 먼저")
    vid = store.new_id("pv")
    store.put("profile_version", user.workspace_id, vid,
              {"version_id": vid, "profile": doc["profile"],
               "approved_by": user.uid})
    doc["status"] = "completed"
    store.put("onboarding", user.workspace_id, sid, doc)
    return {"version_id": vid}


# ── Lead Request (이슈 #6-D, 기획서 §6) ─────────────────────────────

class LeadRequestCreate(BaseModel):
    title: str
    profile_version_id: str
    intent: Intent


@router.post("/lead-requests", status_code=201)
def create_request(req: LeadRequestCreate,
                   user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    if store.get("profile_version", user.workspace_id,
                 req.profile_version_id) is None:
        raise EngineError(404, "not_found", "승인된 프로필 버전이 없습니다")
    rid = store.new_id("lr")
    doc = {"request_id": rid, "title": req.title, "status": "draft",
           "profile_version_id": req.profile_version_id,
           "intent": req.intent.model_dump(mode="json"),
           "search_brief": None, "candidates": [], "cost_usd": 0.0}
    store.put("lead_request", user.workspace_id, rid, doc)
    return doc


@router.get("/lead-requests")
def list_requests(user: SaasUser = Depends(current_user)):
    return {"requests": get_saas_store().list("lead_request", user.workspace_id)}


@router.get("/lead-requests/{rid}")
def get_request(rid: str, user: SaasUser = Depends(current_user)):
    doc = get_saas_store().get("lead_request", user.workspace_id, rid)
    if doc is None:
        raise EngineError(404, "not_found", f"Request {rid} 없음")
    return doc


def _load_request(store, user, rid) -> "tuple[dict, Profile, Intent]":
    doc = store.get("lead_request", user.workspace_id, rid)
    if doc is None:
        raise EngineError(404, "not_found", f"Request {rid} 없음")
    pv = store.get("profile_version", user.workspace_id,
                   doc["profile_version_id"])
    return doc, Profile.model_validate(pv["profile"]), \
        Intent.model_validate(doc["intent"])


@router.post("/lead-requests/{rid}/search-brief", status_code=202)
def make_brief(rid: str, background: BackgroundTasks,
               user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    doc, profile, intent = _load_request(store, user, rid)

    def _run() -> dict:
        cost.reserve(store, user.workspace_id, rid, "synth")
        brief = build_search_brief(RetrieveRequest(
            requester_profile=profile, intent=intent,
            direction=RetrieveDirection.sell_outreach,
            pool=PoolChoice.both, k=intent.lead_count or 30))
        doc["search_brief"] = brief.model_dump(mode="json")
        doc["status"] = "target_review"
        store.put("lead_request", user.workspace_id, rid, doc)
        return {"search_brief": doc["search_brief"]}

    return _submit(background, _run)


@router.post("/lead-requests/{rid}/segments", status_code=202)
def suggest_segments(rid: str, background: BackgroundTasks,
                     user: SaasUser = Depends(current_user)):
    """상대 업종 후보를 제안한다 — 사용자가 고른다.

    왜 되묻는가: 상대 업종을 엔진이 혼자 정하면 그 추측이 검색 전체의 전제가 되고,
    빗나가도 사용자는 왜 엉뚱한 게 나왔는지 모른다. 게다가 한 회사가 노릴 상대
    업종은 원래 여러 개다. 추측을 선택으로 바꾼다.
    """
    store = get_saas_store()
    doc, profile, intent = _load_request(store, user, rid)

    def _run() -> dict:
        cost.reserve(store, user.workspace_id, rid, "synth")
        segs = propose_segments(RetrieveRequest(
            requester_profile=profile, intent=intent,
            direction=RetrieveDirection.sell_outreach,
            pool=PoolChoice.both, k=intent.lead_count or 30))
        doc["segments"] = segs
        store.put("lead_request", user.workspace_id, rid, doc)
        # 과거 실적 기반 키워드 추천 — 이력이 없으면 빈 목록(지어내지 않는다)
        recs = kw.recommend(store, user.workspace_id,
                            doc.get("search_brief", {}).get("query_hypotheses", []),
                            exclude_rid=rid)
        return {"segments": segs, "keyword_recommendations": recs}

    return _submit(background, _run)


class SearchIn(BaseModel):
    """사용자가 고른 상대 업종. 비우면 업종을 나누지 않고 한 번만 검색한다."""
    segments: list[str] = []
    extra_queries: list[str] = []   # 추천 키워드 중 사용자가 채택한 것


def _discover(store, user, rid, doc, profile, intent, settings, extractor,
              plans, wave: int) -> list[dict]:
    """한 웨이브의 수집: 검색 → 도메인 필터 → 기업 추출 → 온톨로지 판독.

    /search(1차)와 /refine(2차+)이 같은 기계를 쓴다 — 대화형 검색의 각 턴은
    같은 수집이고, 다른 것은 검색어(사용자의 답이 만든)뿐이어야 한다.
    """
    import json as _json
    from pathlib import Path as _P
    from ..connectors.tavily import search as web_search
    from ..engine.company_ontology import confirmed_ratio, read_company
    from .. import progress

    hits, seen, src_of = [], set(), {}
    known_urls = {c.get("source_url") for c in doc.get("pool", [])}
    total_q = sum(len(qs) for _, qs in plans)
    cost.reserve(store, user.workspace_id, rid, "tavily", count=total_q)
    for seg, qs in plans:
        for q in qs:
            for h in web_search(q, settings):
                if h["url"] in seen or h["url"] in known_urls:
                    continue        # 이전 웨이브에서 본 URL 재수집 금지
                seen.add(h["url"])
                src_of[h["url"]] = (seg, q)
                hits.append(h)
        progress.log("검색", f"{seg or '기본'} — 검색어 {len(qs)}개")
    progress.log("검색", f"웨이브 {wave}: 웹 수집 {len(hits)}건 (중복 제거)")

    hits, dropped = filter_company_hits(hits)
    if dropped:
        progress.log("검색", f"비기업 도메인 {dropped}건 제외 (블로그·위키·SNS 등)")
    cost.reserve(store, user.workspace_id, rid, "insight")
    companies = extract_companies(
        extractor, hits, doc["search_brief"]["synthesized_counterpart"],
        requester_name=profile.basic.name)
    progress.log("검색", f"실존 기업 {len(companies)}곳 추출 (히트 {len(hits)}건 중)")

    cost.reserve(store, user.workspace_id, rid, "insight", count=len(companies))
    onts: dict[str, dict] = {}
    for c in companies:
        try:
            ont = read_company(extractor, c, region=intent.target_region or "",
                               purpose=intent.purpose)
        except Exception as e:
            progress.log("검색", f"⚠ {c['name']} 온톨로지 판독 실패({type(e).__name__})")
            continue
        d = ont.model_dump(mode="json")
        d["confirmed_ratio"] = confirmed_ratio(ont)
        onts[c["url"]] = d
        store.put("company_ontology", user.workspace_id,
                  f"{rid}::{c['url']}", {**d, "name": c["name"],
                                         "name_ko": c.get("name_ko", ""),
                                         "request_id": rid})
    progress.log("검색", f"온톨로지 판독 {len(onts)}곳 "
                         f"({len(companies) - len(onts)}곳 실패)")

    # 실전 스니펫 캡처 (B5) — 라벨은 사람이 확정한다(모델 출력을 골드로 쓰면
    # 순환 논증). 실패해도 검색은 계속.
    try:
        log_p = _P("dataset/snippet_log.jsonl")
        log_p.parent.mkdir(exist_ok=True)
        kept_by_url = {c["url"]: c for c in companies}
        with log_p.open("a", encoding="utf-8") as f:
            for h in hits:
                seg, q = src_of.get(h["url"], ("", ""))
                c = kept_by_url.get(h["url"])
                f.write(_json.dumps({
                    "request_id": rid, "segment": seg, "query": q,
                    "url": h.get("url", ""), "title": h.get("title", ""),
                    "snippet": (h.get("content") or h.get("snippet") or "")[:800],
                    "extracted": bool(c),
                    "extraction": ({"name": c["name"], "what": c["what"],
                                    "signal": c["signal"]} if c else None),
                    "ontology_signals": [
                        {"category": x["category"], "evidence": x["evidence"]}
                        for x in (onts.get(h["url"], {}).get("signals") or [])],
                }, ensure_ascii=False) + "\n")
    except OSError as e:
        progress.log("검색", f"⚠ 스니펫 로그 실패({type(e).__name__}) — 검색은 계속")

    # 키워드 원장 — 업종이 데려온 기업의 온톨로지만 그 업종에 기록
    kept_urls = {c["url"] for c in companies}
    for seg, qs in plans:
        y = {q: sum(1 for u, (sg, qq) in src_of.items()
                    if qq == q and u in kept_urls) for q in qs}
        seg_urls = [u for u in kept_urls
                    if u in onts and src_of.get(u, ("", ""))[0] == seg]
        kw.record_run(store, user.workspace_id, f"{rid}-w{wave}", segment=seg,
                      queries=qs, yield_by_query=y, kept=sum(y.values()),
                      ontologies=[onts[u] for u in seg_urls])

    base = len(doc.get("pool", []))
    out = []
    for i, c in enumerate(companies):
        seg, q = src_of.get(c["url"], ("", ""))
        out.append({
            "company_id": f"web-{rid}-{base + i + 1:02d}",
            "name": c["name"], "name_ko": c.get("name_ko", ""),
            "what": c["what"], "signal": c["signal"],
            "source_url": c["url"], "segment": seg, "found_by": q,
            "wave": wave, "ontology": onts.get(c["url"]),
            "pain_signal": " ".join(x for x in (c["what"], c["signal"]) if x)})
    return out


def _rank_pool(profile, intent, pool: list[dict],
               liked: list[str], disliked: list[str], k: int) -> list[dict]:
    """풀 전체 재랭킹 + 피드백 보정 (결정=코드).

    retrieve의 보완성 점수에, '이런 곳 더/아니에요' 반응과의 온톨로지 축
    겹침(Rocchio식)을 가감한다. 아니에요 후보 자신은 목록에서 뺀다 —
    사용자가 거른 것을 다시 보여주는 것은 반응을 무시하는 것이다.
    """
    from ..engine.clarify import feedback_bonus
    from ..engine.keywords import axis_tokens
    records, by_cid = [], {}
    for c in pool:
        if c["company_id"] in disliked:
            continue
        by_cid[c["company_id"]] = c
        desc = c["what"] or c["signal"]
        thin = Profile(
            basic=BasicInfo(name=c["name"],
                            country=intent.target_region or "미상",
                            industry=intent.target_industry or "unknown"),
            description=desc,
            problem_solved=ProvField(value=(c["signal"] or desc)[:200],
                                     provenance=Provenance.inferred,
                                     confidence=0.4),
            solution=ProvField(value="", provenance=Provenance.ask),
            target_customer=ProvField(value="", provenance=Provenance.ask))
        records.append(candidate_record_from_profile(
            c["company_id"], thin, c["source_url"],
            pain_signal=c["pain_signal"]))
    if not records:
        return []
    result = retrieve(RetrieveRequest(
        requester_profile=profile, intent=intent,
        direction=RetrieveDirection.sell_outreach, pool=PoolChoice.both,
        k=min(max(k, len(records)), 50), allow_weak=True),
        candidate_records=records)
    liked_toks = axis_tokens([by_cid[c]["ontology"] for c in liked
                              if c in by_cid and by_cid[c].get("ontology")])
    dis_toks = axis_tokens([c["ontology"] for c in pool
                            if c["company_id"] in disliked and c.get("ontology")])
    ranked = []
    for r in result.candidates:
        c = by_cid.get(r.company_id)
        if c is None:
            continue
        bonus = feedback_bonus(c, liked_toks, dis_toks)
        ranked.append({**r.model_dump(mode="json"), **c,
                       "retrieval_score": round(r.retrieval_score + bonus, 4),
                       "feedback_bonus": bonus})
    ranked.sort(key=lambda x: (-x["retrieval_score"], x["company_id"]))
    return ranked[:k]


@router.post("/lead-requests/{rid}/search", status_code=202)
def run_search(rid: str, body: SearchIn | None = None,
               background: BackgroundTasks = None,
               user: SaasUser = Depends(current_user)):
    """1차 웨이브 — 수집 후 top 후보와 함께 **명확화 질문**을 돌려준다.

    한 번에 top-10을 확정하지 않는다: 후보 풀이 갈리는 지점(facet)에서 질문을
    만들어 사용자에게 던지고(/refine), 답이 다음 웨이브의 검색어가 된다.
    메신저가 곧 검색 인터페이스다 — 모호함은 라벨이 아니라 대화로 푼다.
    """
    store = get_saas_store()
    doc, profile, intent = _load_request(store, user, rid)
    if not doc.get("search_brief"):
        raise EngineError(409, "invalid_state", "search-brief 먼저 확정하세요")
    settings = get_settings()
    segments = [s.strip() for s in (body.segments if body else []) if s.strip()]
    extra = [q.strip() for q in (body.extra_queries if body else []) if q.strip()]

    def _run() -> dict:
        from ..engine.clarify import generate_questions
        doc["status"] = "discovering"
        doc["segments_selected"] = segments
        doc["pool"] = []
        doc["feedback"] = {"liked": [], "disliked": [], "answers": []}
        doc["asked"] = []
        store.put("lead_request", user.workspace_id, rid, doc)
        extractor = get_extractor(settings)

        plans: list[tuple[str, list[str]]] = []
        if segments:
            cost.reserve(store, user.workspace_id, rid, "synth", count=len(segments))
            for seg in segments:
                b = build_search_brief(RetrieveRequest(
                    requester_profile=profile, intent=intent,
                    direction=RetrieveDirection.sell_outreach,
                    pool=PoolChoice.both, k=intent.lead_count or 30), segment=seg)
                plans.append((seg, b.query_hypotheses))
        else:
            plans.append(("", doc["search_brief"]["query_hypotheses"]))
        if extra:
            plans.append(("추천 키워드", extra))

        doc["pool"] = _discover(store, user, rid, doc, profile, intent,
                                settings, extractor, plans, wave=1)
        doc["candidates"] = _rank_pool(profile, intent, doc["pool"], [], [],
                                       k=min(intent.lead_count or 10, 30))
        questions = generate_questions(
            extractor, doc["candidates"],
            doc["search_brief"]["synthesized_counterpart"], doc["asked"])
        doc["asked"] += [q["question"] for q in questions]
        doc["clarify"] = questions
        doc["status"] = "clarifying" if questions else "candidates_ready"
        store.put("lead_request", user.workspace_id, rid, doc)
        return {"candidates": doc["candidates"], "clarify": questions,
                "wave": 1,
                "keyword_recommendations": kw.recommend(
                    store, user.workspace_id,
                    [q for _, qs in plans for q in qs],
                    current_ontologies=[c["ontology"] for c in doc["pool"]
                                        if c.get("ontology")],
                    exclude_rid=rid)}

    return _submit(background, _run)


class RefineIn(BaseModel):
    """사용자의 답과 반응 — 다음 웨이브의 재료.

    answers: 명확화 질문에 고른 선택지 라벨들.
    liked/disliked: 후보별 '이런 곳 더 / 아니에요' 반응 (company_id).
    done=True면 더 찾지 않고 현재 풀에서 top-k를 확정한다.
    """
    answers: list[str] = []
    liked: list[str] = []
    disliked: list[str] = []
    done: bool = False


REFINE_QUERY_SYSTEM_SUFFIX = """

[추가 규율 — 재검색]
지금은 1차 검색 뒤의 재검색이다. 사용자의 답과 반응이 아래에 있다.
- 사용자가 고른 방향에 **한정**한 검색어를 만든다. 거른 방향의 어휘는 빼라.
- '좋다'고 반응한 기업들의 판독 어휘를 씨앗으로 쓴다 — 그런 회사가 더 있는
  곳을 찾는 검색어를 만들어라.
- 1차에 쓴 검색어를 그대로 반복하지 마라 — 같은 검색어는 같은 결과를 낳는다."""


@router.post("/lead-requests/{rid}/refine", status_code=202)
def refine_search(rid: str, body: RefineIn,
                  background: BackgroundTasks = None,
                  user: SaasUser = Depends(current_user)):
    """멀티턴 좁히기 — 답·반응을 받아 다음 웨이브를 돌거나 top-k를 확정한다."""
    store = get_saas_store()
    doc, profile, intent = _load_request(store, user, rid)
    if not doc.get("pool"):
        raise EngineError(409, "invalid_state", "1차 검색(/search)이 먼저입니다")
    settings = get_settings()

    def _run() -> dict:
        from ..engine.clarify import generate_questions
        from ..engine.keywords import axis_tokens
        from ..engine.retrieve import QUERY_SCHEMA, QUERY_SYSTEM
        extractor = get_extractor(settings)
        fb = doc.get("feedback") or {"liked": [], "disliked": [], "answers": []}
        fb["liked"] = sorted(set(fb["liked"]) | set(body.liked))
        fb["disliked"] = sorted(set(fb["disliked"]) | set(body.disliked))
        fb["answers"] += [a for a in body.answers if a.strip()]
        doc["feedback"] = fb
        k = min(intent.lead_count or 10, 30)

        if body.done:
            doc["candidates"] = _rank_pool(profile, intent, doc["pool"],
                                           fb["liked"], fb["disliked"], k)
            doc["status"] = "candidates_ready"
            doc["clarify"] = []
            store.put("lead_request", user.workspace_id, rid, doc)
            return {"candidates": doc["candidates"], "clarify": [],
                    "final": True, "wave": doc.get("wave", 1)}

        wave = doc.get("wave", 1) + 1
        doc["wave"] = wave
        by_cid = {c["company_id"]: c for c in doc["pool"]}
        liked_kw = sorted({
            q for cid in fb["liked"] if by_cid.get(cid, {}).get("ontology")
            for q in by_cid[cid]["ontology"].get("search_keywords", [])})[:8]
        disliked_desc = [by_cid[cid]["what"] for cid in fb["disliked"]
                         if cid in by_cid][:5]
        cost.reserve(store, user.workspace_id, rid, "synth")
        try:
            data = extractor.extract_json(
                QUERY_SYSTEM + REFINE_QUERY_SYSTEM_SUFFIX,
                f"[이상적 상대의 상]\n"
                f"{doc['search_brief']['synthesized_counterpart'][:600]}\n"
                f"[지역] {intent.target_region or '미지정'}\n"
                f"[사용자가 고른 방향] {'; '.join(fb['answers']) or '없음'}\n"
                f"[좋다고 한 기업들의 판독 어휘] {'; '.join(liked_kw) or '없음'}\n"
                f"[거른 기업들 — 이런 곳은 빼라] "
                f"{'; '.join(disliked_desc) or '없음'}\n"
                f"[1차에 쓴 검색어 — 반복 금지]\n"
                + "\n".join(f"- {c['found_by']}" for c in doc["pool"][:12]
                             if c.get("found_by")),
                QUERY_SCHEMA, deep=False, allow_foreign=True)
            queries = [q for q in data.get("queries", []) if q.strip()]
        except Exception as e:
            from .. import progress
            progress.log("검색", f"⚠ 재검색어 생성 실패({type(e).__name__})")
            queries = []
        if not queries:                     # 새 검색어가 없으면 풀 재랭킹만
            doc["candidates"] = _rank_pool(profile, intent, doc["pool"],
                                           fb["liked"], fb["disliked"], k)
            store.put("lead_request", user.workspace_id, rid, doc)
            return {"candidates": doc["candidates"], "clarify": [],
                    "final": False, "wave": wave,
                    "note": "재검색어 생성 실패 — 반응만 반영해 다시 정렬했어요"}

        new_pool = _discover(store, user, rid, doc, profile, intent,
                             settings, extractor,
                             [(f"웨이브{wave}", queries)], wave=wave)
        doc["pool"] += new_pool
        doc["candidates"] = _rank_pool(profile, intent, doc["pool"],
                                       fb["liked"], fb["disliked"], k)
        questions = generate_questions(
            extractor, doc["candidates"],
            doc["search_brief"]["synthesized_counterpart"], doc["asked"])
        doc["asked"] += [q["question"] for q in questions]
        doc["clarify"] = questions
        doc["status"] = "clarifying" if questions else "candidates_ready"
        store.put("lead_request", user.workspace_id, rid, doc)
        return {"candidates": doc["candidates"], "clarify": questions,
                "final": False, "wave": wave,
                "new_found": len(new_pool)}

    return _submit(background, _run)


# ── Insight · Compose V2 (이슈 #6-E) ────────────────────────────────

def _record_outcome(store, ws: str, rid: str, cid: str, cand: dict,
                    **fields) -> dict:
    """결과 원장 upsert — 어떤 검색어(found_by)·업종(segment)이 이 회사를
    데려왔는지를 결과와 함께 남긴다. 이 연결이 없으면 '통한 검색어'를 알 수 없다."""
    key = f"{rid}::{cid}"
    o = store.get("outcome", ws, key) or {
        "request_id": rid, "company_id": cid,
        "segment": cand.get("segment", ""),
        "found_by": cand.get("found_by", ""),
        "saved": False, "drafted": False, "replied": ""}
    o.update(fields)
    store.put("outcome", ws, key, o)
    return o


class OutcomeIn(BaseModel):
    """사용자가 명시하는 결과. drafted는 받지 않는다 — compose 성공이 곧
    사실이므로 서버가 자동 기록한다(사용자 신고보다 정확하다)."""
    saved: "bool | None" = None
    replied: "Literal['yes', 'no', ''] | None" = None


@router.post("/lead-requests/{rid}/candidates/{cid}/outcome")
def set_outcome(rid: str, cid: str, body: OutcomeIn,
                user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    doc, _, _ = _load_request(store, user, rid)
    cand = next((c for c in doc.get("candidates", [])
                 if c["company_id"] == cid), None)
    if cand is None:
        raise EngineError(404, "not_found", f"후보 {cid} 없음")
    fields = {}
    if body.saved is not None:
        fields["saved"] = body.saved
    if body.replied is not None:
        fields["replied"] = body.replied
    return _record_outcome(store, user.workspace_id, rid, cid, cand, **fields)


@router.post("/lead-requests/{rid}/candidates/{cid}/insight", status_code=202)
def make_insight(rid: str, cid: str, background: BackgroundTasks,
                 user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    doc, profile, intent = _load_request(store, user, rid)
    cand = next((c for c in doc["candidates"] if c["company_id"] == cid), None)
    if cand is None:
        raise EngineError(404, "not_found", f"후보 {cid} 없음")
    settings = get_settings()

    def _run() -> dict:
        cost.reserve(store, user.workspace_id, rid, "insight")
        thin = Profile(
            basic=BasicInfo(name=cand["name"],
                            country=intent.target_region or "미상",
                            industry=intent.target_industry or "unknown"),
            description=cand.get("pain_signal", ""),
            problem_solved=ProvField(value=cand.get("pain_signal", "")[:200],
                                     provenance=Provenance.inferred,
                                     confidence=0.4),
            solution=ProvField(value="", provenance=Provenance.ask),
            target_customer=ProvField(value="", provenance=Provenance.ask))
        ins = build_insight(
            get_extractor(settings), cid, profile, intent, thin,
            pain_signal=cand.get("pain_signal", ""),
            source_urls=[u for u in [cand.get("source_url", "")] if u])
        store.put("insight", user.workspace_id, cid,
                  ins.model_dump(mode="json"))
        return {"insight": ins.model_dump(mode="json")}

    return _submit(background, _run)


@router.post("/lead-requests/{rid}/candidates/{cid}/compose", status_code=202)
def make_drafts(rid: str, cid: str, background: BackgroundTasks,
                user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    doc, profile, intent = _load_request(store, user, rid)
    cand = next((c for c in doc["candidates"] if c["company_id"] == cid), None)
    if cand is None:
        raise EngineError(404, "not_found", f"후보 {cid} 없음")
    ins_doc = store.get("insight", user.workspace_id, cid)
    if ins_doc is None:
        raise EngineError(409, "invalid_state", "insight 먼저 생성하세요")
    settings = get_settings()

    def _run() -> dict:
        cost.reserve(store, user.workspace_id, rid, "compose")
        thin = Profile(
            basic=BasicInfo(name=cand["name"],
                            country=intent.target_region or "미상",
                            industry=intent.target_industry or "unknown"),
            description=cand.get("pain_signal", ""),
            problem_solved=ProvField(value=cand.get("pain_signal", "")[:200],
                                     provenance=Provenance.inferred,
                                     confidence=0.4),
            solution=ProvField(value="", provenance=Provenance.ask),
            target_customer=ProvField(value="", provenance=Provenance.ask))
        res = compose_lead(get_extractor(settings), ComposeLeadRequest(
            requester_profile=profile, intent=intent, candidate_profile=thin,
            candidate_insight=CandidateInsight.model_validate(ins_doc),
            language=intent.outreach_language or "ko"))
        store.put("email_draft", user.workspace_id, cid,
                  res.model_dump(mode="json"))
        # 초안 생성은 서버가 아는 사실 — 결과 원장에 자동 기록 (B4)
        _record_outcome(store, user.workspace_id, rid, cid, cand, drafted=True)
        return res.model_dump(mode="json")

    return _submit(background, _run)
