"""SaaS API (이슈 #6-B~E) — 기획서 §11의 MVP 부분집합.

설계: 엔진은 무상태 계산기, 이 계층이 Firestore(또는 local)에 보존 책임을 진다.
장시간 작업은 기존 JobStore+폴링을 그대로 쓴다 (/product/jobs/{id} 공유).
Judge는 어떤 경로에서도 호출하지 않는다 (§2.3).
"""
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends
from typing import Literal

from pydantic import BaseModel, Field

from ..jobs import store as job_store
from ..config import get_settings
from ..engine.candidate_adapter import candidate_record_from_profile
from ..engine.candidate_extract import (dedupe_pool, extract_companies,
                                        filter_company_hits)
from ..engine import keywords as kw
from ..engine.candidate_insight import build_insight
from ..engine.compose_lead import compose_lead
from ..engine.llm import get_extractor
from ..engine.represent import represent
from ..engine.retrieve import (build_search_brief, propose_brief,
                              propose_segments, retrieve)
from ..errors import EngineError, ProfileBelowMinimum
from ..schemas import (Asset, BasicInfo, CandidateInsight, ComposeLeadRequest,
                       DialogueTurn, Intent, PoolChoice, Profile, ProvField,
                       Provenance, RepresentRequest, RetrieveDirection,
                       RetrieveRequest)
from . import cost, storage
from .auth import SaasUser, current_user
from .store import get_saas_store

router = APIRouter(prefix="/saas", tags=["saas"])

class _SkipSnippetLog(Exception):
    """스니펫 로그가 꺼져 있다는 내부 신호 — 오류가 아니다."""


def _submit(background: BackgroundTasks, fn, user: "SaasUser | None" = None) -> dict:
    """job 생성 + **소유자 기록**.

    job_id는 uuid4().hex[:12](48비트)라 추측이 어렵지만, 추측 난이도는 접근
    제어가 아니다. 결과에는 후보 목록·인사이트·메일 초안이 들어 있으므로
    소유 워크스페이스를 남기고 조회 시 대조한다.
    """
    # job 문서를 소유 워크스페이스 아래 만든다 — 조회에 ws가 필요하므로
    # 남의 job은 구조적으로 보이지 않는다(별도 소유권 대조 문서가 불필요).
    ws = user.workspace_id if user is not None else "__legacy__"
    job, _ = job_store.create(ws=ws)
    background.add_task(job_store.run, job, fn)
    return {"job_id": job.job_id}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: SaasUser = Depends(current_user)):
    """SaaS job 조회 — 자기 워크스페이스가 만든 job만 보인다.

    프론트는 이 경로만 쓴다. /product/jobs/{id}는 인증이 없어 공개 프록시로
    노출되면 남의 검색 결과가 읽힌다(감사 확정 발견).
    """
    job = job_store.get(job_id, user.workspace_id)
    if job is None:
        raise EngineError(404, "not_found", f"job {job_id} 없음")
    return {"job_id": job.job_id, "status": job.status,
            "result": job.result, "error": job.error,
            "logs": job.log.entries, "elapsed": job.log.elapsed}


# ── LLM 프로바이더 토글 (EXAONE 로컬 ↔ GPT Luna) ────────────────────
# 프로세스 전역 전환 — Settings()가 매 호출 env를 읽으므로 env 교체가 곧 반영이다.
# MVP 운영 도구: 키가 없는 쪽으로는 전환을 거부한다(조용한 대체 없음 — 전환해 놓고
# 첫 호출에서 터지는 것보다 전환 시점에 거부하는 것이 정직하다).

_os = os


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


@router.get("/usage")
def usage(user: SaasUser = Depends(current_user)):
    """이번 달 예약액과 캡 잔여 — 아무도 볼 수 없던 것을 보이게 한다.

    정직 표기: 이 수치는 **선예약 추정치**의 합이지 실제 청구액이 아니다.
    ESTIMATE_USD는 보수적 상한이라 실제보다 크게 잡힌다. 정산(실 토큰 수
    반영)은 미구현이므로 화면도 '추정'이라고 말해야 한다.
    """
    import time

    store = get_saas_store()
    mk = time.strftime("%Y-%m")
    mine = (store.get("cost_month", user.workspace_id, mk) or {}).get("usd", 0.0)
    total = (store.get("cost_month", cost.GLOBAL_WS, mk) or {}).get("usd", 0.0)
    return {
        "month": mk,
        "workspace_usd": round(float(mine), 4),
        "workspace_cap_usd": cost.month_cap(),
        "global_usd": round(float(total), 4),
        "global_cap_usd": cost.global_month_cap(),
        "request_cap_usd": cost.req_cap(),
        "estimated": True,   # 선예약 추정 — 실 청구액 아님
    }


# ── 업로드 (IR덱 PDF) — 인증·크기·형식 검증 ─────────────────────────
# /product/upload를 대체한다. 그쪽은 인증도, 크기 상한도, 형식 검증도 없어
# 공개 프록시로 노출되면 누구나 서버 디스크를 채울 수 있었다(감사 확정 발견).



class UploadSignIn(BaseModel):
    filename: str


@router.post("/uploads/sign")
def sign_upload_url(body: UploadSignIn,
                    user: SaasUser = Depends(current_user)):
    """브라우저가 스토리지로 **직접** 올릴 서명 URL을 발급한다.

    파일이 이 함수를 통과하지 않는다. 통과하면 Vercel의 요청 본문 4.5MB
    상한에 걸리는데(413, 요금제와 무관), 그건 IR덱에 턱없이 부족하다.

    경로는 서버가 정한다 — 워크스페이스 접두사와 난수 이름을 여기서 붙이므로
    클라이언트가 남의 워크스페이스에 쓰거나 남의 파일을 덮어쓸 수 없다.
    원본 파일명은 경로에 넣지 않는다(고객사 실명이 경로에 남고 경로 순회의
    입구가 된다) — 응답으로만 돌려준다.
    """
    name = (body.filename or "").strip()
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in storage.CONTENT_TYPES:
        raise EngineError(
            400, "invalid_input",
            "PDF 또는 Word(.docx) 파일만 올릴 수 있습니다. "
            "구형 .doc은 .docx로 저장한 뒤 올려주세요.")
    obj = f"{user.workspace_id}/{uuid.uuid4().hex}{ext}"
    signed = storage.sign_upload(obj)
    return {"path": obj, "token": signed["token"], "bucket": storage.BUCKET,
            "content_type": storage.CONTENT_TYPES[ext], "filename": name}


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

# 회사명을 대화 턴으로 전달할 때 쓰는 질문 문구. represent의 mock 파서가
# 이 문구를 '이름' 필드로 읽는다(_QUESTION_TO_FIELD) — 두 곳이 같아야 한다.
NAME_QUESTION = "회사 이름은 무엇인가요?"


class OnboardingCreate(BaseModel):
    assets: list[Asset] = Field(min_length=1)
    # 회사명은 사용자가 아는 사실이다 — 자료에서 추론할 이유가 없다. 있으면
    # 프로필의 basic.name을 코드가 확정한다(LLM이 '뉴턴/뉴톤'을 오가던 근원).
    company_name: str | None = None


class OnboardingAnswer(BaseModel):
    answer: str


@router.post("/onboarding-sessions", status_code=201)
def create_session(req: OnboardingCreate,
                   user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    sid = store.new_id("ob")
    doc = {"session_id": sid, "status": "collecting",
           "assets": [a.model_dump(mode="json") for a in req.assets],
           "dialogue": [], "current_questions": [], "profile": None,
           "company_name": (req.company_name or "").strip() or None}
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

        # 정정이 쌓여 있으면 **고친다**. 자료를 다시 읽어 프로필을 새로 만들면
        # 사용자가 건드리지 않은 필드까지 매번 흔들리고, 그게 '갑자기 바보가
        # 됐다'로 보인다. 정정은 편집이지 재해석이 아니다.
        pending_fix = doc.get("corrections") or []
        if pending_fix and doc.get("profile"):
            from ..engine.represent import revise_profile
            cost.reserve(store, user.workspace_id, sid, "brief")
            revised, changed, unclear = revise_profile(
                Profile.model_validate(doc["profile"]), pending_fix)
            doc["profile"] = revised.model_dump(mode="json")
            if changed and "name" in changed:
                doc["company_name"] = revised.basic.name   # 정정된 이름이 새 사실
            doc["corrections"] = []          # 반영했으므로 비운다
            doc["status"] = "review_required"
            # 모호해서 못 고쳤으면 그 사실을 질문으로 돌려준다 — 조용히
            # 아무것도 안 바뀌면 사용자는 정정이 먹혔다고 오해한다.
            doc["current_questions"] = [unclear] if (unclear and not changed) else []
            store.put("onboarding", user.workspace_id, sid, doc)
            return {"session": doc, "needs_answers": bool(unclear and not changed),
                    "changed": changed}
        known_name = (doc.get("company_name") or "").strip()
        dialogue = [DialogueTurn(**t) for t in doc["dialogue"]]
        if known_name:
            # 모델에게도 알려준다 — 서술 필드가 다른 표기로 회사를 부르면 어색하다.
            dialogue.insert(0, DialogueTurn(q=NAME_QUESTION, a=known_name))
        try:
            rep = represent(RepresentRequest(
                assets=[Asset(**a) for a in doc["assets"]], dialogue=dialogue))
        except ProfileBelowMinimum as e:
            # 오류로 끝내지 않고 질문을 세션에 보존 → 채팅이 이어받는다 (§5.3)
            doc["status"] = "clarifying"
            doc["current_questions"] = (e.details or {}).get("open_questions", [])
            store.put("onboarding", user.workspace_id, sid, doc)
            return {"session": doc, "needs_answers": True}
        if known_name:
            # 판정은 모델, 결정은 코드 — 사용자가 준 이름을 추론값이 이길 수 없다.
            rep.profile.basic.name = known_name
        doc["status"] = "review_required"
        doc["profile"] = rep.profile.model_dump(mode="json")
        doc["current_questions"] = rep.open_questions
        store.put("onboarding", user.workspace_id, sid, doc)
        return {"session": doc, "needs_answers": False,
                "minimum_met": rep.minimum_met}

    return _submit(background, _run, user)


@router.post("/onboarding-sessions/{sid}/messages")
def answer_session(sid: str, req: OnboardingAnswer,
                   user: SaasUser = Depends(current_user)):
    """질문 1개에 대한 답 → DialogueTurn 누적 (재실행은 /run 재호출)."""
    store = get_saas_store()
    doc = store.get("onboarding", user.workspace_id, sid)
    if doc is None:
        raise EngineError(404, "not_found", f"온보딩 세션 {sid} 없음")
    # 채팅 입력의 의미는 세션 단계가 정한다. 세 가지뿐이다:
    #   프로필 있음            → 정정 (성공한 /run이 보강 질문을 남겨도 화면엔
    #                            안 보인다 — 본 적 없는 질문의 답으로 기록하면
    #                            "뉴톤이야 기업명이"가 '푸는 문제'의 최우선 신뢰
    #                            답이 된다. 실측.)
    #   프로필 없음·질문 대기  → 그 질문의 답 (clarifying)
    #   프로필 없음·질문 없음  → 추가 자료 (소개를 두 번에 나눠 붙여넣는 경우)
    # 질문을 지어내지 않는다 — 물은 적 없는 질문에 답이 달리면 답의 의미가 바뀐다.
    pending = doc.get("current_questions") or []
    if doc.get("profile"):
        doc.setdefault("corrections", []).append(req.answer)
    elif pending:
        doc["dialogue"].append({"q": pending[0], "a": req.answer})
        doc["current_questions"] = pending[1:]
    else:
        doc["assets"].append(Asset(type="text", content=req.answer)
                             .model_dump(mode="json"))
    store.put("onboarding", user.workspace_id, sid, doc)
    return doc


class OnboardingAssets(BaseModel):
    assets: list[Asset] = Field(min_length=1)


@router.post("/onboarding-sessions/{sid}/assets")
def add_assets(sid: str, req: OnboardingAssets,
               user: SaasUser = Depends(current_user)):
    """세션에 자료를 **추가**한다 (교체가 아니다).

    없던 API라 클라이언트가 파일이 더 오면 새 세션을 만들었고, 그러면 앞서
    붙여넣은 소개 텍스트가 버려졌다 — 정정 사고와 같은 계열(멀티턴 약함).
    프로필이 이미 있으면 추가 자료로 다시 만든다(다음 /run이 재생성). 승인된
    뒤에는 붙일 수 없다 — 그 프로필은 이미 버전으로 굳었다.
    """
    store = get_saas_store()
    doc = store.get("onboarding", user.workspace_id, sid)
    if doc is None:
        raise EngineError(404, "not_found", f"온보딩 세션 {sid} 없음")
    if doc.get("status") == "completed":
        raise EngineError(409, "invalid_state",
                          "이미 승인된 프로필입니다 — 새 요청으로 시작해 주세요.")
    doc["assets"].extend(a.model_dump(mode="json") for a in req.assets)
    if doc.get("profile"):
        # 자료가 늘었으니 프로필은 다시 만들어야 한다. 정정(revise)이 아니라
        # 재생성이 맞다 — 새 자료가 어느 필드를 바꿀지 사용자도 모른다.
        doc["profile"] = None
        doc["corrections"] = []
        doc["current_questions"] = []
    store.put("onboarding", user.workspace_id, sid, doc)
    return doc


class ProfileCorrection(BaseModel):
    note: str


@router.post("/onboarding-sessions/{sid}/corrections")
def correct_profile(sid: str, req: ProfileCorrection,
                    user: SaasUser = Depends(current_user)):
    """프로필 정정 요청을 세션에 붙인다 (자료를 대체하지 않는다).

    별도 경로를 두는 이유: 정정을 '질문에 대한 답'으로 보내면 답이 자료가
    된다. 실측(프로덕션 저장 세션) — 정정문 9글자가 세션의 **유일한 자료**가
    되어, 15,559자로 만든 프로필이 버려지고 회사를 처음부터 다시 파악했다.
    정정은 자료가 아니라 이미 만든 프로필에 대한 지시다.
    """
    store = get_saas_store()
    doc = store.get("onboarding", user.workspace_id, sid)
    if doc is None:
        raise EngineError(404, "not_found", f"온보딩 세션 {sid} 없음")
    if not doc.get("profile"):
        raise EngineError(409, "invalid_state",
                          "고칠 프로필이 아직 없습니다 — 먼저 자료를 넣어주세요.")
    note = (req.note or "").strip()
    if not note:
        raise EngineError(400, "invalid_input", "무엇을 고칠지 적어주세요.")
    doc.setdefault("corrections", []).append(note)
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
    # Lead Request 폼 초안을 같이 실어 보낸다. 별도 왕복을 만들면 화면이
    # 빈 폼을 먼저 보여줬다가 값이 나중에 채워지는데, 사용자는 그 사이에
    # 이미 타이핑을 시작한다. 승인은 의도적인 클릭이라 몇 초는 견딘다.
    #
    # 초안 생성이 실패해도 승인 자체는 성공해야 한다 — propose_brief가 빈
    # 초안을 돌려주므로 폼은 지금까지처럼 빈칸으로 열린다.
    brief = {"region": "", "target_type": "", "notes": "",
             "purpose": "revenue", "why": ""}
    try:
        cost.reserve(store, user.workspace_id, vid, "brief")
        brief = propose_brief(Profile.model_validate(doc["profile"]))
    except EngineError:
        raise                      # 캡 초과(402)는 삼키지 않는다
    except Exception as e:         # noqa: BLE001
        from .. import progress
        progress.log("온보딩", f"⚠ 폼 초안 생략({type(e).__name__})")
    return {"version_id": vid, "brief": brief}


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
def list_requests(limit: int = 50, user: SaasUser = Depends(current_user)):
    """사이드바용 **요약** 목록.

    전문을 실어 보내지 않는다: 요청 하나에 pool(누적 후보)과 candidates가
    통째로 들어 있어, 요청이 쌓이면 목록 한 번에 수 MB가 나간다. 목록에
    필요한 것은 제목·상태·개수뿐이고, 상세는 /lead-requests/{rid}가 준다.
    """
    docs = get_saas_store().list("lead_request", user.workspace_id)[:limit]
    return {"requests": [{
        "request_id": d.get("request_id"),
        "title": d.get("title", ""),
        "status": d.get("status", ""),
        "candidate_count": len(d.get("candidates") or []),
        "wave": d.get("wave", 1),
        "target_region": (d.get("intent") or {}).get("target_region", ""),
        "purpose": (d.get("intent") or {}).get("purpose", "revenue"),
    } for d in docs]}


@router.get("/lead-requests/{rid}")
def get_request(rid: str, user: SaasUser = Depends(current_user)):
    doc = get_saas_store().get("lead_request", user.workspace_id, rid)
    if doc is None:
        raise EngineError(404, "not_found", f"Request {rid} 없음")
    return doc


@router.delete("/lead-requests/{rid}")
def delete_request(rid: str, user: SaasUser = Depends(current_user)):
    """요청과 그 파생물을 전부 지운다.

    연쇄가 필요한 이유: 요청 문서만 지우면 온톨로지·키워드 원장·결과·인사이트·
    메일 초안이 고아로 남아 계속 추천 가중에 반영된다. "지워달라"는 요청에
    응하려면 파생물까지 따라가야 한다(감사 확정 medium — 삭제 경로가 앱
    전체에 0개였다).
    """
    store = get_saas_store()
    if store.get("lead_request", user.workspace_id, rid) is None:
        raise EngineError(404, "not_found", f"Request {rid} 없음")
    ws = user.workspace_id
    removed = {"lead_request": int(store.delete("lead_request", ws, rid))}
    # 합성 키(f"{rid}::...")를 쓰는 파생물은 접두어로 지운다
    for kind in ("company_ontology", "insight", "email_draft", "outcome",
                 "cost_request"):
        removed[kind] = store.delete_prefix(kind, ws, f"{rid}::")
    # 키워드 원장은 f"{rid}-w{wave}" 형태
    removed["keyword_run"] = store.delete_prefix("keyword_run", ws, f"{rid}-w")
    removed["cost_request_self"] = int(store.delete("cost_request", ws, rid))
    return {"deleted": rid, "removed": removed}


class DeleteMe(BaseModel):
    """워크스페이스 파기는 되돌릴 수 없다 — 오타 한 번으로 지워지지 않게
    사용자가 자기 식별자를 그대로 다시 입력하게 한다."""
    confirm: str


@router.post("/me/delete")
def delete_me(body: DeleteMe, user: SaasUser = Depends(current_user)):
    """내 워크스페이스의 모든 문서를 지운다. 업로드 파일도 함께.

    POST인 이유: 확인 문구를 본문으로 받아야 하고, DELETE에 본문을 싣는 것은
    프록시·클라이언트마다 취급이 다르다.
    """
    expected = user.email or user.uid
    if body.confirm.strip() != expected:
        raise EngineError(400, "invalid_input",
                          f"확인 문구가 다릅니다 — '{expected}' 를 그대로 입력하세요.")
    store = get_saas_store()
    n = store.delete_workspace(user.workspace_id)
    # 업로드한 원본 자료도 지운다 — 문서만 지우면 IR덱이 스토리지에 남는다
    files = 0
    try:
        files = storage.remove_prefix(user.workspace_id)
    except Exception as e:          # noqa: BLE001 — 문서 삭제까지 되돌리지 않는다
        from .. import progress
        progress.log("삭제", f"⚠ 스토리지 정리 실패({type(e).__name__}) — "
                             f"자료가 남았을 수 있습니다")
    return {"deleted_workspace": user.workspace_id,
            "documents": n, "files": files}


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

    return _submit(background, _run, user)


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
    # search-brief 없이 부르면 아래 doc["search_brief"]["query_hypotheses"]가
    # None을 역참조해 500으로 죽었다 — 계약 위반은 409로 말한다.
    if not doc.get("search_brief"):
        raise EngineError(409, "invalid_state", "search-brief 먼저 확정하세요")

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

    return _submit(background, _run, user)


class SearchIn(BaseModel):
    """사용자가 고른 상대 업종. 비우면 업종을 나누지 않고 한 번만 검색한다."""
    segments: list[str] = []
    extra_queries: list[str] = []   # 추천 키워드 중 사용자가 채택한 것


def _derived_key(doc: dict, rid: str, cid: str) -> str:
    """후보에서 파생된 문서(인사이트·메일 초안)의 저장 키.

    세대(generation)를 포함한다: /search를 다시 돌리면 company_id가 web-{rid}-01
    부터 재발급되므로, 세대가 없으면 같은 키에 다른 회사의 인사이트가 남아
    메일 초안이 엉뚱한 근거로 작성된다.
    """
    return f"{rid}::g{doc.get('generation', 1)}::{cid}"


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

    # src_of: url → 그 URL을 찾아낸 (업종, 검색어) 전부. 예전엔 딕셔너리
    # 덮어쓰기라 '먼저 본 검색어가 독식'했다 — 뒤 업종의 검색어가 같은 URL을
    # 다시 찾아내도 그 사실이 사라져 원장에 성과 0으로 기록됐다(감사 확정 low).
    hits, seen, src_of = [], set(), {}
    known_urls = {c.get("source_url") for c in doc.get("pool", [])}
    total_q = sum(len(qs) for _, qs in plans)
    cost.reserve(store, user.workspace_id, rid, "tavily", count=total_q)
    for seg, qs in plans:
        for q in qs:
            for h in web_search(q, settings):
                url = h["url"]
                src_of.setdefault(url, []).append((seg, q))
                if url in seen or url in known_urls:
                    continue        # 이전 웨이브에서 본 URL 재수집 금지 (추출은 1회)
                seen.add(url)
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

    # 회사 식별자를 여기서 확정한다 — 이후 온톨로지·원장·스니펫 로그 전부
    # 이 식별자로 귀속시킨다. **URL을 키로 쓰지 않는다**: extract_companies는
    # 회사명으로만 중복을 거르므로(candidate_extract.py), 디렉터리·회원사
    # 목록 페이지 하나에서 회사 여러 곳을 뽑는 것이 정상 동작이다. URL을 키로
    # 쓰면 뒤 회사가 앞 회사의 온톨로지·연락처를 덮어써, 사용자가 A사 카드에서
    # B사의 메일 주소를 보게 된다(감사 확정 high — 아웃리치 제품의 무성 오염).
    # 발급 번호는 **누적 발급 수**로 센다. len(pool)로 세면 병합으로 풀이
    # 줄어든 만큼 다음 웨이브가 이미 쓴 번호를 다시 발급해, 서로 다른 회사가
    # 같은 company_id를 갖는다(저장 스냅샷·명확화 인용·반응이 엉뚱한 회사에
    # 붙는다). 옛 문서에는 이 값이 없으므로 len(pool)로 되돌아간다 — 그때는
    # 병합이 없었으니 두 값이 같다.
    base = doc.get("cid_seq", len(doc.get("pool", [])))
    for i, c in enumerate(companies):
        c["_cid"] = f"web-{rid}-{base + i + 1:02d}"
        c["_seg"], c["_q"] = (src_of.get(c["url"]) or [("", "")])[0]
    doc["cid_seq"] = base + len(companies)

    cost.reserve(store, user.workspace_id, rid, "insight", count=len(companies))
    onts: dict[str, dict] = {}
    ontology_failures = 0
    for c in companies:
        try:
            ont = read_company(extractor, c, region=intent.target_region or "",
                               purpose=intent.purpose)
        except Exception as e:
            ontology_failures += 1
            progress.log("검색", f"⚠ {c['name']} 온톨로지 판독 실패({type(e).__name__})")
            continue
        d = ont.model_dump(mode="json")
        d["confirmed_ratio"] = confirmed_ratio(ont)
        onts[c["_cid"]] = d
        store.put("company_ontology", user.workspace_id, f"{rid}::{c['_cid']}",
                  {**d, "name": c["name"], "name_ko": c.get("name_ko", ""),
                   "source_url": c["url"], "request_id": rid})
    progress.log("검색", f"온톨로지 판독 {len(onts)}곳 ({ontology_failures}곳 실패)")

    # 실전 스니펫 캡처 (B5) — 라벨은 사람이 확정한다(모델 출력을 골드로 쓰면
    # 순환 논증). 한 URL에서 회사가 여럿 나올 수 있으므로 리스트로 담는다.
    # 실패해도 검색은 계속.
    # 기본은 끔. Cloud Run의 파일시스템은 인스턴스 메모리라 이 쓰기는 재시작마다
    # 증발하면서 그때까지는 메모리를 먹는다(감사 확정 low). 골든셋 증강이
    # 필요한 로컬·볼륨 환경에서만 SNIPPET_LOG_PATH로 켠다.
    snippet_path = os.environ.get("SNIPPET_LOG_PATH", "")
    try:
        if not snippet_path:
            raise _SkipSnippetLog
        log_p = _P(snippet_path)
        log_p.parent.mkdir(parents=True, exist_ok=True)
        by_url: dict[str, list[dict]] = {}
        for c in companies:
            by_url.setdefault(c["url"], []).append(c)
        with log_p.open("a", encoding="utf-8") as f:
            for h in hits:
                cs = by_url.get(h["url"], [])
                pairs = src_of.get(h["url"]) or [("", "")]
                f.write(_json.dumps({
                    "request_id": rid, "segment": pairs[0][0], "query": pairs[0][1],
                    "found_by_queries": [q for _, q in pairs],
                    "url": h.get("url", ""), "title": h.get("title", ""),
                    "snippet": (h.get("content") or h.get("snippet") or "")[:800],
                    "extracted": bool(cs),
                    "extractions": [{"name": c["name"], "what": c["what"],
                                     "signal": c["signal"]} for c in cs],
                    "ontology_signals": [
                        {"category": x["category"], "evidence": x["evidence"]}
                        for c in cs
                        for x in (onts.get(c["_cid"], {}).get("signals") or [])],
                }, ensure_ascii=False) + "\n")
    except _SkipSnippetLog:
        pass
    except OSError as e:
        progress.log("검색", f"⚠ 스니펫 로그 실패({type(e).__name__}) — 검색은 계속")

    # 키워드 원장 — 그 URL을 찾아낸 **모든** 검색어에 그 URL의 회사 수만큼
    # 수확을 인정한다(먼저 찾은 검색어만 독식하지 않는다). 온톨로지는 그
    # 업종이 데려온 기업의 것만 그 업종에 기록한다(교차 오염 방지, 이전 실측:
    # 두 업종 축토큰이 동일하게 85개였다).
    for seg, qs in plans:
        y = {}
        for q in qs:
            n = 0
            for c in companies:
                if (seg, q) in (src_of.get(c["url"]) or []):
                    n += 1
            y[q] = n
        seg_cids = [c["_cid"] for c in companies
                    if c["_cid"] in onts and c["_seg"] == seg]
        kw.record_run(store, user.workspace_id, f"{rid}-w{wave}", segment=seg,
                      queries=qs, yield_by_query=y, kept=len(seg_cids),
                      ontologies=[onts[cid] for cid in seg_cids])

    return [{
        "company_id": c["_cid"], "name": c["name"], "name_ko": c.get("name_ko", ""),
        "what": c["what"], "signal": c["signal"], "source_url": c["url"],
        "segment": c["_seg"], "found_by": c["_q"], "wave": wave,
        "ontology": onts.get(c["_cid"]),
        "pain_signal": " ".join(x for x in (c["what"], c["signal"]) if x),
        # 스카우트 판정 — p_eff = p·w(출처). 재랭킹이 이 값을 쓴다.
        "p": c.get("p", 0.7), "p_raw": c.get("p_raw", c.get("p", 0.7)),
        "source_kind": c.get("source_kind", "unknown"),
    } for c in companies]


def _merge_pool(pool: list[dict]) -> list[dict]:
    """풀을 중복 없는 상태로 유지한다. 웨이브를 넘나드는 중복은 여기서만 잡힌다."""
    from .. import progress
    kept, merged = dedupe_pool(pool)
    if merged:
        progress.log("검색", f"같은 회사 {merged}건 병합 (같은 사이트의 다른 페이지)")
    return kept


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
        # 상한을 두지 않는다 — retrieve가 자기 k로 먼저 자르고 그 뒤에
        # feedback_bonus를 적용하므로, 상한 밖 후보에게는 '이런 곳 더/
        # 아니에요' 반응이 아예 닿지 않는다(풀 전체 재랭킹 의도와 모순).
        k=max(k, len(records)), allow_weak=True),
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
        # 기대 보완성 = P(실존·부합) × 보완성 점수. 보완성만 보면 기사에서
        # 스쳐 언급된 대형 유통사(mention, p_eff≈0.3)가 회사 자신의 페이지에서
        # 발굴된 소규모 적격 후보(own, p_eff≈0.8)와 같은 줄에 선다 — 할리케이
        # 프로덕션 실측(하위 5곳이 뉴스·채용공고 출처). 사용자 피드백은 곱이
        # 아니라 합으로 얹는다(출처와 무관한 별개 증거).
        p = float(c.get("p", 0.7))
        ranked.append({**r.model_dump(mode="json"), **c,
                       "retrieval_score": round(r.retrieval_score * p + bonus, 4),
                       "complementarity": round(r.retrieval_score, 4),
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
        doc["wave"] = 1
        # 재실행은 company_id를 처음부터 다시 발급한다(web-{rid}-01…).
        # 이전 실행의 파생 문서를 남겨두면 같은 cid에 **다른 회사**의 인사이트가
        # 붙어, 메일 초안이 엉뚱한 회사 근거로 작성된다. 세대 표식을 올려
        # 옛 파생물이 조회되지 않게 한다.
        doc["generation"] = int(doc.get("generation", 0)) + 1
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

        doc["pool"] = _merge_pool(_discover(
            store, user, rid, doc, profile, intent,
            settings, extractor, plans, wave=1))
        doc["searched"] = True        # 0곳이어도 '돌렸다'는 사실은 남는다
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

    return _submit(background, _run, user)


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
    # 가드는 pool 진리값이 아니라 **검색을 돌린 적이 있는가**로 판정한다.
    # 웨이브1이 0곳으로 끝나면 pool == []이고, 그때가 사용자가 가장 다시
    # 시도하고 싶은 순간인데 "1차 검색이 먼저입니다"라는 자기모순으로
    # 막혔다(감사 확정). 화면의 유일한 재시도 버튼이 죽는 막다른 길이었다.
    if not doc.get("searched"):
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
        # '새로 N곳'은 병합 뒤 실제로 늘어난 수여야 한다. 추출 수를 그대로
        # 쓰면 이미 아는 회사를 다시 찾은 것도 새로 찾았다고 보고한다.
        before = len(doc["pool"])
        doc["pool"] = _merge_pool(doc["pool"] + new_pool)
        gained = len(doc["pool"]) - before
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
                "new_found": gained}

    return _submit(background, _run, user)


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


# ── 심층 판독 — '닿기'의 마지막 1마일 ─────────────────────────────────
#
# 발굴 직후의 온톨로지는 검색 스니펫만 읽는다. 실측(프로덕션 5건 전부): 1위
# 후보 접점 0건·타이밍 신호 0건. 200자 안에 이메일·담당·채용·뉴스가 있을 리
# 없다. 여기서 후보 회사의 사이트를 실제로 가져와 다시 읽는다.
#
# 별도 job인 이유: 웨이브1이 이미 300초 근처(실측 303s)라 같은 job에 넣으면
# Vercel 상한을 넘긴다. 목록은 먼저 보여주고 뒤에서 채운다.
#
# 출처가 own인 후보만 읽는다: 기사·디렉터리 URL은 그 회사 사이트가 아니다 —
# 크롤하면 엉뚱한 페이지를 그 회사의 것으로 읽는다. 그런 후보는 "사이트
# 미확인"으로 정직하게 남긴다(사이트 발견은 다음 단계).
_DEEP_READ_TOP = 10
_DEEP_READ_WORKERS = 4         # 크롤+판독은 IO 대기가 대부분 — 병렬이 이득


class DeepReadIn(BaseModel):
    company_ids: list[str] | None = None     # 비우면 상위 _DEEP_READ_TOP


@router.post("/lead-requests/{rid}/deep-read", status_code=202)
def deep_read(rid: str, background: BackgroundTasks,
              body: DeepReadIn | None = None,
              user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    doc, _profile, intent = _load_request(store, user, rid)
    if not doc.get("candidates"):
        raise EngineError(409, "invalid_state", "후보가 아직 없습니다 — 검색 먼저")
    settings = get_settings()
    want = set((body.company_ids if body and body.company_ids else
                [c["company_id"] for c in doc["candidates"][:_DEEP_READ_TOP]]))
    targets = [c for c in doc["candidates"] if c["company_id"] in want]

    def _one(c: dict) -> tuple[str, dict]:
        """(company_id, 갱신 필드). 실패는 상태로 남긴다 — 조용히 넘기지 않는다."""
        from ..engine.company_ontology import confirmed_ratio, read_company
        from ..ingest.crawler import crawl_website
        cid = c["company_id"]
        site = c["source_url"]
        found_via = ""
        if c.get("source_kind") != "own":
            # 기사·디렉터리 URL은 그 회사 사이트가 아니다 — 이름으로 찾는다.
            # 실측: 상위 10곳 중 5곳이 이 경우라, 이게 없으면 절반을 못 읽는다.
            from ..engine.candidate_extract import _site_of
            from ..engine.site_discovery import find_official_site
            try:
                cost.reserve(store, user.workspace_id, rid, "brief")
                site, p_site = find_official_site(
                    get_extractor(settings), settings, c["name"],
                    c.get("what", ""), exclude_site=_site_of(c["source_url"]))
            except Exception as e:                   # noqa: BLE001
                site, p_site = "", 0.0
            if not site:
                return cid, {"deep_read": {"status": "no_site",
                                           "note": "공식 사이트를 확신 있게 찾지 못함"}}
            found_via = f"검색으로 발견 (p={p_site:.2f})"
        text = ""
        try:
            text = crawl_website(site, settings)
        except Exception as e:                       # noqa: BLE001
            crawl_err = type(e).__name__
            # 정적 크롤이 못 읽으면(SPA·본문 부족) 렌더링 폴백 — 접점은 보통
            # 루트와 /contact·/about에 있다. 실측: 찾은 사이트 3곳이 전부 SPA였다.
            from ..connectors.tavily import extract as tavily_extract
            from urllib.parse import urljoin
            pages = tavily_extract([site, urljoin(site, "/contact"),
                                    urljoin(site, "/about")], settings)
            text = "\n\n".join(f"[{u}]\n{t}" for u, t in pages.items())
            if not text.strip():
                return cid, {"deep_read": {"status": "fetch_failed",
                                           "note": crawl_err, "site": site}}
            found_via = (found_via + " · " if found_via else "") + "렌더링 폴백"
        if not text.strip():
            return cid, {"deep_read": {"status": "empty",
                                       "note": "본문을 읽지 못함(JS 렌더링 등)"}}
        cost.reserve(store, user.workspace_id, rid, "deep_read")
        try:
            ont = read_company(
                get_extractor(settings),
                {"name": c["name"], "name_ko": c.get("name_ko", ""),
                 "what": c.get("what", ""), "signal": c.get("signal", ""),
                 "url": site},
                region=intent.target_region or "", purpose=intent.purpose,
                site_text=text)
        except Exception as e:                       # noqa: BLE001
            return cid, {"deep_read": {"status": "read_failed",
                                       "note": type(e).__name__}}
        d = ont.model_dump(mode="json")
        d["confirmed_ratio"] = confirmed_ratio(ont)
        return cid, {"ontology": d,
                     "deep_read": {"status": "done", "chars": len(text),
                                   "contacts": len(ont.contacts),
                                   "signals": len(ont.signals),
                                   "site": site, "note": found_via}}

    def _run() -> dict:
        from concurrent.futures import ThreadPoolExecutor
        from .. import progress
        progress.log("판독", f"상위 {len(targets)}곳 사이트 심층 판독 시작")
        with ThreadPoolExecutor(max_workers=_DEEP_READ_WORKERS) as ex:
            results = dict(ex.map(_one, targets))
        # 최신 문서에 병합한다 — 판독 중 사용자가 반응(👍/👋)을 남겼을 수 있다.
        fresh = store.get("lead_request", user.workspace_id, rid) or doc
        for coll in ("candidates", "pool"):
            for c in fresh.get(coll) or []:
                upd = results.get(c["company_id"])
                if upd:
                    c.update(upd)
        store.put("lead_request", user.workspace_id, rid, fresh)
        done = sum(1 for r in results.values()
                   if r["deep_read"]["status"] == "done")
        contacts = sum(r["deep_read"].get("contacts", 0) for r in results.values())
        progress.log("판독", f"완료 — 판독 {done}/{len(targets)}곳, 접점 {contacts}건")
        return {"candidates": fresh["candidates"],
                "read": done, "total": len(targets)}

    return _submit(background, _run, user)


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
        store.put("insight", user.workspace_id, _derived_key(doc, rid, cid),
                  ins.model_dump(mode="json"))
        return {"insight": ins.model_dump(mode="json")}

    return _submit(background, _run, user)


@router.post("/lead-requests/{rid}/candidates/{cid}/compose", status_code=202)
def make_drafts(rid: str, cid: str, background: BackgroundTasks,
                user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    doc, profile, intent = _load_request(store, user, rid)
    cand = next((c for c in doc["candidates"] if c["company_id"] == cid), None)
    if cand is None:
        raise EngineError(404, "not_found", f"후보 {cid} 없음")
    ins_doc = store.get("insight", user.workspace_id,
                        _derived_key(doc, rid, cid))
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
        store.put("email_draft", user.workspace_id, _derived_key(doc, rid, cid),
                  res.model_dump(mode="json"))
        # 초안 생성은 서버가 아는 사실 — 결과 원장에 자동 기록 (B4)
        _record_outcome(store, user.workspace_id, rid, cid, cand, drafted=True)
        return res.model_dump(mode="json")

    return _submit(background, _run, user)
