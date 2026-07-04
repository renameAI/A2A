"""제품 레이어 API — 프론트엔드가 쓰는 stateful 오케스트레이션.

엔진(stateless)을 호출만 한다. v0는 같은 프로세스에서 함수 호출로 오케스트레이션하며,
분리 배포 시 이 계층만 HTTP 클라이언트로 바꾸면 된다 (엔진 계약 불변).
구매자 사전정보는 대회 데모 규약대로 시뮬레이션 가상 부여한다 (7-A.6, [시뮬] 표시).
"""
import uuid
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, BackgroundTasks, UploadFile
from pydantic import BaseModel, Field

from ..engine import pool as pool_module
from ..jobs import store as job_store
from ..engine.compose import compose
from ..engine.judge import judge
from ..engine.negotiate import negotiate
from ..engine.represent import represent
from ..engine.retrieve import retrieve
from ..errors import EngineError
from ..schemas import (Asset, ComposeMode, ComposeRequest, DialogueTurn, Intent,
                       JudgeRequest, JudgeResult, Lens, NegotiateRequest,
                       Objective, PoolChoice, PrivateState, PrivateStateItem,
                       Profile, RepresentRequest, RetrieveDirection,
                       RetrieveRequest, SourceTag, Vantage, ValueProp,
                       Willingness)
from .store import store

router = APIRouter(prefix="/product", tags=["product"])

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"


# ── 비동기 job 공통 (LLM 작업은 수 분 소요 — UI가 로그를 폴링) ──────

def _submit(background: BackgroundTasks, fn: Callable[[], dict]) -> dict:
    job, _ = job_store.create()
    background.add_task(job_store.run, job, fn)
    return {"job_id": job.job_id}


@router.get("/jobs/{job_id}")
def product_job(job_id: str):
    job = job_store.get(job_id)
    if job is None:
        raise EngineError(404, "not_found", f"job {job_id} 없음")
    return {"job_id": job.job_id, "status": job.status.value,
            "result": job.result, "error": job.error,
            "logs": job.log.entries, "elapsed": job.log.elapsed}


# ── 업로드 (IR덱 PDF) ────────────────────────────────────────────────

@router.post("/upload")
async def upload(file: UploadFile):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise EngineError(400, "invalid_input", "PDF 파일만 업로드할 수 있습니다.")
    _UPLOAD_DIR.mkdir(exist_ok=True)
    path = _UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    path.write_bytes(await file.read())
    return {"path": str(path), "filename": file.filename}


# ── 온보딩: 자료 → 프로필 (엔진 represent 호출) ─────────────────────

class OnboardRequest(BaseModel):
    assets: list[Asset] = Field(min_length=1)
    dialogue: list[DialogueTurn] = []
    private_state: list[PrivateStateItem] = []   # 판매자 사전정보 (선택)
    company_id: Optional[str] = None             # 있으면 갱신 (REP-09), 없으면 신규


@router.post("/onboard", status_code=202)
def onboard(req: OnboardRequest, background: BackgroundTasks):
    def _run() -> dict:
        # 최소 프로필 미달이면 EngineError(409) → job.error로 수렴, 프론트가 보강 질문 표시
        rep = represent(RepresentRequest(assets=req.assets, dialogue=req.dialogue))
        rec = req.company_id and store.update_company(
            req.company_id, profile=rep.profile,
            private_state=PrivateState(items=req.private_state),
            open_questions=rep.open_questions, evidence=rep.evidence,
            engine_mode=rep.engine_mode)
        rec = rec or store.save_company(
            profile=rep.profile,
            private_state=PrivateState(items=req.private_state),
            open_questions=rep.open_questions,
            evidence=rep.evidence,
            engine_mode=rep.engine_mode)
        return {"company_id": rec.company_id,
                "profile": rep.profile.model_dump(mode="json"),
                "ontology_anchors": [a.model_dump() for a in rep.ontology_anchors],
                "open_questions": rep.open_questions,
                "evidence": rep.evidence, "engine_mode": rep.engine_mode}
    return _submit(background, _run)


@router.get("/companies")
def companies():
    return [{"company_id": r.company_id, "name": r.profile.basic.name,
             "country": r.profile.basic.country, "engine_mode": r.engine_mode}
            for r in store.list()]


# ── 후보 발굴 (엔진 retrieve 호출) ──────────────────────────────────

class MatchRequest(BaseModel):
    company_id: str
    intent: Intent
    pool: PoolChoice = PoolChoice.external
    k: int = Field(default=5, ge=1, le=20)


def _require_company(company_id: str):
    rec = store.get(company_id)
    if rec is None:
        raise EngineError(404, "not_found", f"회사 {company_id} 없음 — 온보딩 먼저")
    return rec


@router.post("/match", status_code=202)
def match(req: MatchRequest, background: BackgroundTasks):
    rec = _require_company(req.company_id)

    def _run() -> dict:
        result = retrieve(RetrieveRequest(
            requester_profile=rec.profile, intent=req.intent,
            direction=RetrieveDirection.sell_outreach, pool=req.pool, k=req.k))
        enriched = []
        for cand in result.candidates:
            record = pool_module.find(cand.company_id)
            enriched.append({
                **cand.model_dump(mode="json"),
                "name": record.profile.basic.name if record else cand.company_id,
                "country": record.profile.basic.country if record else "",
                "summary": record.profile.description if record else "",
            })
        return {"candidates": enriched,
                "synthesized_counterpart": result.synthesized_counterpart}
    return _submit(background, _run)


# ── 판단 (엔진 judge 호출 — 보내는 쪽 · 탐색 예산) ──────────────────

class JudgeCallRequest(BaseModel):
    company_id: str
    candidate_id: str
    intent: Intent


@router.post("/judge", status_code=202)
def judge_candidate(req: JudgeCallRequest, background: BackgroundTasks):
    rec = _require_company(req.company_id)
    cand = pool_module.find(req.candidate_id)
    if cand is None:
        raise EngineError(404, "not_found", f"후보 {req.candidate_id} 없음")

    def _run() -> dict:
        result = judge(JudgeRequest(
            vantage=Vantage.seller, objective=Objective.exploration_budget,
            self_profile=rec.profile, self_private_state=rec.private_state,
            counterpart_profile=cand.profile, intent=req.intent))
        return {"candidate_id": req.candidate_id,
                "judge_result": result.model_dump(mode="json")}
    return _submit(background, _run)


# ── 초안 생성 (엔진 compose 호출 — 발송은 항상 사람) ────────────────

class ComposeCallRequest(BaseModel):
    company_id: str
    candidate_id: str
    judge_result: JudgeResult
    mode: ComposeMode = ComposeMode.outreach
    variants: int = Field(default=2, ge=1, le=3)
    tone: Optional[str] = None


@router.post("/compose", status_code=202)
def compose_draft(req: ComposeCallRequest, background: BackgroundTasks):
    rec = _require_company(req.company_id)
    cand = pool_module.find(req.candidate_id)
    if cand is None:
        raise EngineError(404, "not_found", f"후보 {req.candidate_id} 없음")

    def _run() -> dict:
        return compose(ComposeRequest(
            mode=req.mode, judge_result=req.judge_result,
            self_profile=rec.profile, counterpart_profile=cand.profile,
            lens=Lens.sell, variants=req.variants, tone=req.tone,
        )).model_dump(mode="json")
    return _submit(background, _run)


# ── A2A 협상 시뮬레이션 (7-A) ───────────────────────────────────────

class NegotiateCallRequest(BaseModel):
    company_id: str
    candidate_id: str
    intent: Intent
    max_rounds: int = Field(default=3, ge=1, le=5)


def _simulated_buyer(cand, intent: Intent) -> tuple[Profile, PrivateState]:
    """외부 풀 후보의 구매자 사전정보 시뮬레이션 가상 부여 (7-A.6 결정, [시뮬] 표시)."""
    buyer = cand.profile.model_copy(deep=True)
    if not buyer.purchase_value_props:
        buyer.purchase_value_props = list(intent.value_props)
    if buyer.willingness_purchase is None:
        buyer.willingness_purchase = Willingness.medium
    private = PrivateState(items=[
        PrivateStateItem(key="의사결정 기준", value="리스크 최소화 + ROI",
                         source=SourceTag.simulated),
        PrivateStateItem(key="검증 요구", value="실측 데이터·레퍼런스 확인 후 수용",
                         source=SourceTag.simulated),
    ])
    return buyer, private


@router.post("/negotiate", status_code=202)
def negotiate_sim(req: NegotiateCallRequest, background: BackgroundTasks):
    rec = _require_company(req.company_id)
    cand = pool_module.find(req.candidate_id)
    if cand is None:
        raise EngineError(404, "not_found", f"후보 {req.candidate_id} 없음")

    def _run() -> dict:
        buyer_profile, buyer_private = _simulated_buyer(cand, req.intent)
        result = negotiate(NegotiateRequest(
            seller_profile=rec.profile, seller_private_state=rec.private_state,
            buyer_profile=buyer_profile, buyer_private_state=buyer_private,
            intent=req.intent, max_rounds=req.max_rounds))
        return {"buyer_simulated": True,   # 정직 프레이밍: 실데이터 아님을 명시
                "negotiation": result.model_dump(mode="json")}
    return _submit(background, _run)
