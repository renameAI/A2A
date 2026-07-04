"""A2A 매칭엔진 — stateless 4엔드포인트 API (SYS-01, API_계약서 v1.0).

엔진은 상태를 보유하지 않는다. 대화·인박스·설정은 제품(클라이언트)이 보유하고
매 요청에 필요한 입력을 전달한다. judge·negotiate는 비동기 (SYS-02).
"""
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine.compose import compose
from .engine.judge import judge
from .engine.negotiate import negotiate
from .engine.represent import represent
from .engine.retrieve import retrieve
from .errors import EngineError
from .jobs import store
from .schemas import (ComposeRequest, ComposeResponse, JobOut, JudgeRequest,
                      NegotiateRequest, RepresentRequest, RepresentResponse,
                      RetrieveRequest, RetrieveResponse)

app = FastAPI(title="A2A B2B 매칭엔진", version="0.1.0")


@app.exception_handler(EngineError)
async def engine_error_handler(_: Request, exc: EngineError):
    return JSONResponse(status_code=exc.http_status, content=exc.payload())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    # 스키마 위반·필수 누락 → 400 invalid_input (API §0.1)
    return JSONResponse(status_code=400, content={
        "error": {"code": "invalid_input", "message": "스키마 위반 또는 필수 항목 누락",
                  "details": {"errors": exc.errors()}}})


@app.post("/v1/represent", response_model=RepresentResponse)
def post_represent(req: RepresentRequest):
    return represent(req)


@app.post("/v1/retrieve", response_model=RetrieveResponse)
def post_retrieve(req: RetrieveRequest):
    return retrieve(req)


@app.post("/v1/judge", status_code=202)
def post_judge(req: JudgeRequest, background: BackgroundTasks):
    job, existed = store.create(req.client_request_id)
    if not existed:
        background.add_task(store.run, job,
                            lambda: judge(req).model_dump(mode="json"))
    return {"job_id": job.job_id}


@app.post("/v1/negotiate", status_code=202)
def post_negotiate(req: NegotiateRequest, background: BackgroundTasks):
    job, existed = store.create(req.client_request_id)
    if not existed:
        background.add_task(store.run, job,
                            lambda: negotiate(req).model_dump(mode="json"))
    return {"job_id": job.job_id}


@app.get("/v1/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise EngineError(404, "not_found", f"job {job_id} 없음")
    return JobOut(job_id=job.job_id, status=job.status,
                  result=job.result, error=job.error, logs=job.log.entries,
                  elapsed=job.log.elapsed)


@app.post("/v1/compose", response_model=ComposeResponse)
def post_compose(req: ComposeRequest):
    return compose(req)


# ── 제품 레이어 (stateful) + 프론트엔드 ─────────────────────────────
# v0는 한 프로세스에 함께 띄운다. 분리 배포 시 product만 떼어내면 된다.
from .product.router import router as product_router   # noqa: E402

app.include_router(product_router)
app.mount("/", StaticFiles(directory=Path(__file__).parent / "product" / "static",
                           html=True), name="ui")
