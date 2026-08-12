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
from ..engine.candidate_insight import build_insight
from ..engine.compose_lead import compose_lead
from ..engine.llm import get_extractor
from ..engine.represent import represent
from ..engine.retrieve import build_search_brief, retrieve
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


@router.post("/lead-requests/{rid}/search", status_code=202)
def run_search(rid: str, background: BackgroundTasks,
               user: SaasUser = Depends(current_user)):
    """웹 수집 → 얇은 후보 → 기존 Retrieve 재랭킹 (§6.2 순서 전환).

    수집 단계 후보는 검색 스니펫 기반 얇은 프로필이다 — prospect Represent
    (LLM 리서치)는 사용자가 고른 상위 후보의 /research에서만 수행해 비용을
    아낀다 (§6.4 '연락처·리서치는 상위 후보 확정 이후').
    """
    store = get_saas_store()
    doc, profile, intent = _load_request(store, user, rid)
    if not doc.get("search_brief"):
        raise EngineError(409, "invalid_state", "search-brief 먼저 확정하세요")
    settings = get_settings()

    def _run() -> dict:
        from ..connectors.tavily import search as web_search
        from .. import progress
        doc["status"] = "discovering"
        store.put("lead_request", user.workspace_id, rid, doc)
        queries = doc["search_brief"]["query_hypotheses"]
        cost.reserve(store, user.workspace_id, rid, "tavily", count=len(queries))
        hits, seen = [], set()
        for q in queries:
            for h in web_search(q, settings):
                if h["url"] not in seen:
                    seen.add(h["url"])
                    hits.append(h)
        progress.log("검색", f"웹 수집 {len(hits)}건 (쿼리 {len(queries)}개, 중복 제거)")
        records = []
        for i, h in enumerate(hits):
            thin = Profile(
                basic=BasicInfo(name=h["title"][:80] or f"후보{i+1}",
                                country=intent.target_region or "미상",
                                industry=intent.target_industry or "unknown"),
                description=h["snippet"],
                problem_solved=ProvField(value=h["snippet"][:200],
                                         provenance=Provenance.inferred,
                                         confidence=0.4),
                solution=ProvField(value="", provenance=Provenance.ask),
                target_customer=ProvField(value="", provenance=Provenance.ask))
            records.append(candidate_record_from_profile(
                f"web-{rid}-{i+1:02d}", thin, h["url"],
                pain_signal=h["snippet"]))
        result = retrieve(RetrieveRequest(
            requester_profile=profile, intent=intent,
            direction=RetrieveDirection.sell_outreach, pool=PoolChoice.both,
            k=min(intent.lead_count or 30, 30), allow_weak=True),
            candidate_records=records)
        by_id = {r.company_id: r for r in records}
        doc["candidates"] = [
            {**c.model_dump(mode="json"),
             "name": by_id[c.company_id].profile.basic.name
             if c.company_id in by_id else c.company_id,
             "source_url": next((t for t in by_id[c.company_id].tags
                                 if t.startswith("http")), "")
             if c.company_id in by_id else "",
             "pain_signal": by_id[c.company_id].pain_points
             if c.company_id in by_id else ""}
            for c in result.candidates]
        doc["status"] = "candidates_ready"
        store.put("lead_request", user.workspace_id, rid, doc)
        return {"candidates": doc["candidates"],
                "synthesized_counterpart": result.synthesized_counterpart}

    return _submit(background, _run)


# ── Insight · Compose V2 (이슈 #6-E) ────────────────────────────────

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
        return res.model_dump(mode="json")

    return _submit(background, _run)
