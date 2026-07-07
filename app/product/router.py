"""제품 레이어 API — 프론트엔드가 쓰는 stateful 오케스트레이션.

엔진(stateless)을 호출만 한다. v0는 같은 프로세스에서 함수 호출로 오케스트레이션하며,
분리 배포 시 이 계층만 HTTP 클라이언트로 바꾸면 된다 (엔진 계약 불변).
구매자 사전정보는 대회 데모 규약대로 시뮬레이션 가상 부여한다 (7-A.6, [시뮬] 표시).
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, BackgroundTasks, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import progress
from ..a2a import job_state_to_a2a
from ..config import get_settings
from ..engine import pool as pool_module
from ..jobs import store as job_store
from ..engine.compose import compose
from ..engine.consultant import consult
from ..engine.judge import judge
from ..engine.negotiate import negotiate
from ..engine.represent import represent
from ..engine.retrieve import retrieve
from ..engine import vision as vision_module
from ..engine.vision import get_vision_extractor
from ..errors import EngineError
from ..ingest.fetchers import fetch_pdf_bytes
from ..ingest.pdf_render import render_pdf_pages
from ..schemas import (Asset, AssetType, BBox, CommentThread, ComposeMode,
                       ComposeRequest, DialogueTurn, Intent, JudgeRequest,
                       JudgeResult, Lens, NegotiateRequest, Objective,
                       PoolChoice, PrivateState, PrivateStateItem, Profile,
                       QuestionPin, RepresentRequest, RetrieveDirection,
                       RetrieveRequest, SourceTag, ThreadComment,
                       ThreadReplyRequest, Vantage, ValueProp, Willingness)
from .store import store

router = APIRouter(prefix="/product", tags=["product"])

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"


def _pages_dir() -> Path:
    override = os.environ.get("A2A_PAGES_DIR")
    return Path(override) if override else \
        Path(__file__).resolve().parent.parent.parent / "pages"


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
    # A2A Task lifecycle 상태 매핑은 a2a 모듈에 단일 정의 (product·전송계층 공유).
    # 사람 입력 대기(최소 프로필 미달·미응답 질문 핀) = A2A input-required.
    return {"job_id": job.job_id, "status": job.status.value,
            "a2a_state": job_state_to_a2a(job.status.value, job.result, job.error),
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


# ── 질문 위치 탐지 (bbox) — 엑사원 질문을 IR덱 페이지에 핀 꽂기 (선택 기능) ──
# 역할 분리: 엑사원(추론)이 질문을 만들고, VLM은 그 질문을 페이지 어디에 붙일지
# 위치만 찾는다. GEMINI_API_KEY 없으면 아무 일도 안 함 — 텍스트 추출과 완전 독립.
# 위치를 못 찾은 질문은 여기서 빠지고, 기존 텍스트 보강질문(clarify) 흐름이 담당한다.

_PINS_PER_QUESTION = 2   # 질문당 상위 K개 핀 (결합 점수 s = r·g 내림차순)


def _run_question_pinning(company_id: str, assets: list[Asset],
                          questions: list[str]
                          ) -> tuple[list[QuestionPin], list[CommentThread]]:
    settings = get_settings()
    vision = get_vision_extractor(settings)
    if vision is None or not questions:
        return [], []

    # 후보 수집 → 계약 검증(인덱스·페이지·기하·그라운딩·관련도) → 중복 제거 →
    # 질문당 상위 K 선별. VLM 출력은 전부 '후보'일 뿐, 검증기를 통과해야 핀이 된다.
    # 전송은 배치 단위 — 페이지 수·바이트 이중 상한(make_batches), 토큰은 실측 누적.
    candidates: list[QuestionPin] = []
    rejected = {"index": 0, "page": 0, "geometry": 0, "grounding": 0, "relevance": 0}
    with progress.node("vision.pinning", "질문 위치 탐지 (bbox)"):
        progress.log("비전", f"엑사원 질문 {len(questions)}건을 IR덱 페이지에서 찾는 중")
        for i, asset in enumerate(assets):
            if asset.type != AssetType.ir_deck or not asset.url:
                continue
            try:
                data = fetch_pdf_bytes(asset.url, settings)
                pages = render_pdf_pages(data, jpeg_quality=settings.vision_jpeg_quality)
            except Exception as e:
                progress.log("비전", f"⚠ a{i}:ir_deck 렌더링 실패 — 건너뜀 ({e})")
                continue
            asset_dir = _pages_dir() / company_id
            asset_dir.mkdir(parents=True, exist_ok=True)
            page_text = {}
            api_pages = []
            for p in pages:
                (asset_dir / f"a{i}_p{p.page_no}.png").write_bytes(p.png)
                page_text[p.page_no] = p.text
                api_pages.append((p.page_no, p.api_image, p.api_mime))
            batches = vision_module.make_batches(
                api_pages, settings.vision_pages_per_call, settings.vision_batch_bytes)
            progress.log("비전", f"a{i}:ir_deck — {len(pages)}페이지 → 배치 {len(batches)}건 "
                                f"(호출당 ≤{settings.vision_pages_per_call}장·"
                                f"≤{settings.vision_batch_bytes // 1024}KB)")
            for batch in batches:
                batch_page_nos = {n for n, _, _ in batch}
                for item in vision.locate_batch(batch, questions):
                    qi = item.get("question_index")
                    if not isinstance(qi, int) or not (0 <= qi < len(questions)):
                        rejected["index"] += 1
                        continue
                    page_no = item.get("page")
                    if page_no not in batch_page_nos:   # 배치 밖 페이지 주장 = 폐기
                        rejected["page"] += 1
                        continue
                    box = item.get("box_2d")
                    if not vision_module.validate_box(box):
                        rejected["geometry"] += 1
                        continue
                    quote = item.get("quote", "")
                    g = vision_module.grounding_score(quote, page_text[page_no])
                    if g is not None and g < vision_module.GROUND_THRESHOLD:
                        rejected["grounding"] += 1
                        continue
                    r = float(item.get("relevance") or 0.0)
                    if r < vision_module.REL_THRESHOLD:
                        rejected["relevance"] += 1
                        continue
                    candidates.append(QuestionPin(
                        evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
                        question=questions[qi], asset_index=i, page=page_no,
                        box=BBox(ymin=box[0], xmin=box[1], ymax=box[2], xmax=box[3]),
                        quote=quote, relevance=r, grounding=g))

        # 중복 제거: 같은 (질문, 자산, 페이지)는 결합 점수 최대 1개만
        best: dict[tuple, QuestionPin] = {}
        for c in candidates:
            key = (c.question, c.asset_index, c.page)
            if key not in best or vision_module.pin_score(c.relevance, c.grounding) \
                    > vision_module.pin_score(best[key].relevance, best[key].grounding):
                best[key] = c
        # 질문당 상위 K개 (여러 페이지에 흩어진 핀 중 점수 높은 것만)
        by_question: dict[str, list[QuestionPin]] = {}
        for c in best.values():
            by_question.setdefault(c.question, []).append(c)
        pins: list[QuestionPin] = []
        for q, group in by_question.items():
            group.sort(key=lambda c: vision_module.pin_score(c.relevance, c.grounding),
                       reverse=True)
            pins.extend(group[:_PINS_PER_QUESTION])
        pins.sort(key=lambda p: (p.asset_index, p.page))

        threads = [CommentThread(
            thread_id=f"th-{uuid.uuid4().hex[:8]}",
            evidence_id=p.evidence_id,
            comments=[ThreadComment(author="ai", text=p.question,
                                    ts=datetime.now(timezone.utc).isoformat())])
            for p in pins]

        # 정직한 집계 — 폐기·비용을 숨기지 않는다 (측정과 자랑을 구분)
        n_rej = sum(rejected.values())
        n_dedup = len(candidates) - len(best)
        n_capped = len(best) - len(pins)
        unlocated = [q for q in questions if q not in by_question]
        progress.log("비전",
                     f"완료 — 후보 {len(candidates) + n_rej}건 중 핀 {len(pins)}건 채택 · "
                     f"폐기 {n_rej}건(인덱스 {rejected['index']}/페이지 {rejected['page']}"
                     f"/기하 {rejected['geometry']}/인용대조 {rejected['grounding']}"
                     f"/저관련 {rejected['relevance']}) · 중복제거 {n_dedup}건 · "
                     f"질문당 상위{_PINS_PER_QUESTION} 초과 {n_capped}건")
        progress.log("비전", f"비용 — API 호출 {vision.calls}회 · "
                            f"토큰 {vision.tokens_used:,}/{vision.token_budget:,} 사용"
                            + (" ⚠ 예산 소진으로 일부 배치 생략" if vision.budget_exhausted else ""))
        if unlocated:
            progress.log("비전", f"위치 못 찾은 질문 {len(unlocated)}건 → 텍스트 보강질문으로 처리")
    return pins, threads


# ── 온보딩: 자료 → 프로필 (엔진 represent 호출) ─────────────────────

class OnboardRequest(BaseModel):
    assets: list[Asset] = Field(min_length=1)
    dialogue: list[DialogueTurn] = []
    private_state: list[PrivateStateItem] = []   # 판매자 사전정보 (선택)
    company_id: Optional[str] = None             # 있으면 갱신 (REP-09), 없으면 신규


@router.post("/onboard", status_code=202)
def onboard(req: OnboardRequest, background: BackgroundTasks):
    def _run() -> dict:
        # 소통 루프 되먹임 — 같은 회사 재분석이면, 지금까지 질문 핀에 단 답변을
        # dialogue에 얹어 엑사원에게 전달한다. 답변이 프로필 개선으로 이어져야
        # 핀이 진짜로 해소된다 (엑사원 질문 → 핀 → 답변 → 재분석 → 프로필 개선).
        dialogue = list(req.dialogue)
        if req.company_id:
            dialogue += store.answered_dialogue(req.company_id)
        # 최소 프로필 미달이면 EngineError(409) → job.error로 수렴, 프론트가 보강 질문 표시
        rep = represent(RepresentRequest(assets=req.assets, dialogue=dialogue))
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
        pins, threads = _run_question_pinning(
            rec.company_id, req.assets, rep.open_questions)
        store.set_question_pins(rec.company_id, pins, threads)
        return {"company_id": rec.company_id,
                "profile": rep.profile.model_dump(mode="json"),
                "ontology_anchors": [a.model_dump() for a in rep.ontology_anchors],
                "open_questions": rep.open_questions,
                "evidence": rep.evidence, "engine_mode": rep.engine_mode,
                "question_pin_count": len(pins),
                "open_thread_count": sum(1 for t in threads if t.status == "open")}
    return _submit(background, _run)


@router.get("/companies")
def companies():
    return [{"company_id": r.company_id, "name": r.profile.basic.name,
             "country": r.profile.basic.country, "engine_mode": r.engine_mode}
            for r in store.list()]


# ── SQLite 인스펙터 (Phase 6) — 실제 저장 형태를 read-only로 노출 ────
# 운영 상태가 로컬 어디에 어떤 raw 블롭으로 저장되는지 눈으로 확인하기 위함.

@router.get("/db/inspect")
def db_inspect():
    import sqlite3
    from .store import _db_path
    path = _db_path()
    info = {"db_path": str(path), "exists": path.exists(),
            "size_bytes": path.stat().st_size if path.exists() else 0,
            "journal_mode": None, "schema": [], "row_count": 0, "companies": []}
    if not path.exists():
        return info
    conn = sqlite3.connect(path, timeout=10)
    try:
        info["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        info["schema"] = [
            {"name": name, "sql": sql} for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchall()]
        rows = conn.execute(
            "SELECT company_id, data FROM companies").fetchall()
        info["row_count"] = len(rows)
        for company_id, blob in rows:
            d = json.loads(blob)
            info["companies"].append({
                "company_id": company_id,
                "name": d.get("profile", {}).get("basic", {}).get("name"),
                "engine_mode": d.get("engine_mode"),
                "pins": len(d.get("question_pins", [])),
                "threads": len(d.get("threads", [])),
                "open_threads": sum(1 for t in d.get("threads", [])
                                    if t.get("status") == "open"),
                "answered": len(d.get("answered_questions", [])),
                "bytes": len(blob),
                # 실제 저장된 raw JSON 블롭 그대로 (예쁘게 들여쓰기만)
                "raw": json.dumps(d, ensure_ascii=False, indent=2)})
    finally:
        conn.close()
    return info


# ── 근거 시각화 (bbox) 조회·페이지 이미지·댓글 답변 ─────────────────

@router.get("/companies/{company_id}/evidence")
def get_evidence(company_id: str):
    rec = _require_company(company_id)
    return {"pins": [p.model_dump(mode="json") for p in rec.question_pins],
            "threads": [t.model_dump(mode="json") for t in rec.threads.values()],
            "open_thread_count": sum(1 for t in rec.threads.values()
                                     if t.status == "open"),
            # 소통 루프 상태 — 재분석에 실릴 대기 답변 수
            "answered_count": len(rec.answered_questions)}


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
    # 카운트는 mutation 이후 fresh 조회로 — 영속 store는 매 get이 새 스냅샷이라
    # reply 이전 레코드를 그대로 쓰면 낡은 값이 나온다.
    rec = store.get(company_id)
    return {"thread": thread.model_dump(mode="json"),
            "open_thread_count": sum(1 for t in rec.threads.values()
                                     if t.status == "open"),
            "answered_count": len(rec.answered_questions)}


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
