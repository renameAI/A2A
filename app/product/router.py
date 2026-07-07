"""제품 레이어 API — 프론트엔드가 쓰는 stateful 오케스트레이션.

엔진(stateless)을 호출만 한다. v0는 같은 프로세스에서 함수 호출로 오케스트레이션하며,
분리 배포 시 이 계층만 HTTP 클라이언트로 바꾸면 된다 (엔진 계약 불변).
구매자 사전정보는 대회 데모 규약대로 시뮬레이션 가상 부여한다 (7-A.6, [시뮬] 표시).
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, BackgroundTasks, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import progress
from ..config import get_settings
from ..engine import pool as pool_module
from ..jobs import store as job_store
from ..engine.compose import compose
from ..engine.consultant import consult
from ..engine.judge import judge
from ..engine.negotiate import negotiate
from ..engine.represent import represent
from ..engine.retrieve import retrieve
from ..engine.vision import get_vision_extractor
from ..errors import EngineError
from ..ingest.fetchers import fetch_pdf_bytes
from ..ingest.pdf_render import render_pdf_pages
from ..schemas import (Asset, AssetType, BBox, CommentThread, ComposeMode,
                       ComposeRequest, DialogueTurn, Intent, JudgeRequest,
                       JudgeResult, Lens, NegotiateRequest, Objective,
                       PoolChoice, PrivateState, PrivateStateItem, Profile,
                       RepresentRequest, RetrieveDirection, RetrieveRequest,
                       SourceTag, ThreadComment, ThreadReplyRequest, Vantage,
                       ValueProp, VisualEvidence, Willingness)
from .store import store

router = APIRouter(prefix="/product", tags=["product"])

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"


def _pages_dir() -> Path:
    override = os.environ.get("A2A_PAGES_DIR")
    return Path(override) if override else \
        Path(__file__).resolve().parent.parent.parent / "pages"

# 근거 시각화 대상 필드 — Profile 핵심 필드 + 회사의 상(portrait) 7항목
_BBOX_TARGET_FIELDS = [
    "problem_solved", "solution", "target_customer",
    "sell_value_props", "purchase_value_props",
    "portrait.identity", "portrait.business_model", "portrait.edge",
    "portrait.stage_narrative", "portrait.assets", "portrait.gaps",
    "portrait.risk_signals",
]


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


# ── 근거 시각화 (bbox) — IR덱 원문 위 근거 위치 + 댓글 강제 (선택 기능) ──
# GEMINI_API_KEY가 없으면 아무 일도 하지 않는다 — 텍스트 추출과 완전히 독립.

def _run_visual_grounding(company_id: str, assets: list[Asset]
                          ) -> tuple[list[VisualEvidence], list[CommentThread]]:
    settings = get_settings()
    vision = get_vision_extractor(settings)
    if vision is None:
        return [], []

    evidence: list[VisualEvidence] = []
    threads: list[CommentThread] = []
    with progress.node("vision.grounding", "근거 위치 탐지 (bbox)"):
        for i, asset in enumerate(assets):
            if asset.type != AssetType.ir_deck or not asset.url:
                continue
            try:
                data = fetch_pdf_bytes(asset.url, settings)
                pages = render_pdf_pages(data)
            except Exception as e:
                progress.log("비전", f"⚠ a{i}:ir_deck 렌더링 실패 — 건너뜀 ({e})")
                continue
            progress.log("비전", f"a{i}:ir_deck — {len(pages)}페이지 렌더링 완료")
            asset_dir = _pages_dir() / company_id
            asset_dir.mkdir(parents=True, exist_ok=True)
            for page_no, png in enumerate(pages, start=1):
                (asset_dir / f"a{i}_p{page_no}.png").write_bytes(png)
                for item in vision.locate(png, _BBOX_TARGET_FIELDS, page_no):
                    box = item.get("box_2d") or [0, 0, 0, 0]
                    ev = VisualEvidence(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        field=item.get("field", ""), asset_index=i, page=page_no,
                        box=BBox(ymin=box[0], xmin=box[1], ymax=box[2], xmax=box[3]),
                        quote=item.get("quote", ""),
                        confidence=item.get("confidence"),
                        unclear=bool(item.get("unclear")),
                        unclear_reason=item.get("unclear_reason"))
                    evidence.append(ev)
                    if ev.unclear:
                        threads.append(CommentThread(
                            thread_id=f"th-{uuid.uuid4().hex[:8]}",
                            evidence_id=ev.evidence_id,
                            comments=[ThreadComment(
                                author="ai",
                                text=f"'{ev.field}' 근거가 확실하지 않습니다 — "
                                     f"{ev.unclear_reason or '표현이 모호합니다'}. "
                                     f"이 부분이 맞는지 확인해 주세요.",
                                ts=datetime.now(timezone.utc).isoformat())]))
        progress.log("비전", f"완료 — 근거 {len(evidence)}건, "
                            f"확인 필요 {len(threads)}건")
    return evidence, threads


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
        visual_evidence, threads = _run_visual_grounding(rec.company_id, req.assets)
        store.set_visual_evidence(rec.company_id, visual_evidence, threads)
        return {"company_id": rec.company_id,
                "profile": rep.profile.model_dump(mode="json"),
                "ontology_anchors": [a.model_dump() for a in rep.ontology_anchors],
                "open_questions": rep.open_questions,
                "evidence": rep.evidence, "engine_mode": rep.engine_mode,
                "visual_evidence_count": len(visual_evidence),
                "open_thread_count": sum(1 for t in threads if t.status == "open")}
    return _submit(background, _run)


@router.get("/companies")
def companies():
    return [{"company_id": r.company_id, "name": r.profile.basic.name,
             "country": r.profile.basic.country, "engine_mode": r.engine_mode}
            for r in store.list()]


# ── 근거 시각화 (bbox) 조회·페이지 이미지·댓글 답변 ─────────────────

@router.get("/companies/{company_id}/evidence")
def get_evidence(company_id: str):
    rec = _require_company(company_id)
    return {"evidence": [e.model_dump(mode="json") for e in rec.visual_evidence],
            "threads": [t.model_dump(mode="json") for t in rec.threads.values()],
            "open_thread_count": sum(1 for t in rec.threads.values()
                                     if t.status == "open")}


@router.get("/pages/{company_id}/{filename}")
def get_page_image(company_id: str, filename: str):
    path = _pages_dir() / company_id / filename
    if ".." in filename or not path.is_file():
        raise EngineError(404, "not_found", "페이지 이미지 없음")
    return FileResponse(path, media_type="image/png")


@router.post("/companies/{company_id}/threads/{thread_id}/reply")
def reply_thread(company_id: str, thread_id: str, req: ThreadReplyRequest):
    _require_company(company_id)
    thread = store.reply_thread(company_id, thread_id, req.text,
                                datetime.now(timezone.utc).isoformat())
    if thread is None:
        raise EngineError(404, "not_found", f"스레드 {thread_id} 없음")
    return thread.model_dump(mode="json")


# ── 컨설턴트 인터뷰 (CON-01~02) — 진단 대화로 아웃리치 가설 수립 ────

class ConsultTurnIn(BaseModel):
    question: str
    answer: str


class ConsultRequest(BaseModel):
    company_id: str
    history: list[ConsultTurnIn] = []   # 상태는 제품/클라이언트가 보유 (SYS-01)


@router.post("/consult", status_code=202)
def consult_turn(req: ConsultRequest, background: BackgroundTasks):
    rec = _require_company(req.company_id)

    def _run() -> dict:
        return consult(rec.profile,
                       [t.model_dump() for t in req.history])
    return _submit(background, _run)


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
    open_threads = [t for t in rec.threads.values() if t.status == "open"]
    if open_threads:
        # 강제 응답 — 근거가 불명확하다고 표시된 항목에 사람이 답하기 전엔 매칭 진행 불가
        raise EngineError(409, "unclear_evidence_unresolved",
                          f"근거가 불명확한 항목 {len(open_threads)}건에 답변이 필요합니다 — "
                          "먼저 확인해 주세요.",
                          {"open_threads": [t.model_dump(mode="json") for t in open_threads]})

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
