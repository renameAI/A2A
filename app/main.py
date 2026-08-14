"""A2A 매칭엔진 — stateless 4엔드포인트 API (SYS-01, API_계약서 v1.0).

엔진은 상태를 보유하지 않는다. 대화·인박스·설정은 제품(클라이언트)이 보유하고
매 요청에 필요한 입력을 전달한다. judge·negotiate는 비동기 (SYS-02).
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, FastAPI, Request
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
                      RetrieveRequest, RetrieveResponse, ScoutRequest)

from .log import setup as _log_setup   # noqa: E402

_log_setup()          # stdout JSON 한 줄 — Cloud Logging이 파싱한다
_boot = logging.getLogger("a2a.boot")

@asynccontextmanager
async def _lifespan(_: FastAPI):
    """적용된 설정을 기동 시 한 번 남긴다. 이름이 어긋난 환경변수가 조용히
    무시되던 사고(COST_CAP_* vs COST_CAP_*_USD)를 로그로 잡을 수 있게.

    on_event는 FastAPI에서 폐기됐다 — lifespan을 쓴다."""
    from .config import get_settings
    from .saas import cost
    s = get_settings()
    _boot.info("엔진 기동", extra={
        "llm_provider": s.llm_provider,
        "saas_auth": os.environ.get("SAAS_AUTH", "dev"),
        "saas_store": os.environ.get("SAAS_STORE", "local"),
        "allowed_users": len(s.saas_allowed_users),
        "cap_request_usd": cost.req_cap(),
        "cap_month_usd": cost.month_cap(),
        "cap_global_month_usd": cost.global_month_cap(),
        "legacy_surface": LEGACY_ON,
    })
    yield


app = FastAPI(title="A2A B2B 매칭엔진", version="0.1.0",
              lifespan=_lifespan)

# 레거시 엔진 API(/v1/*)와 A2A 전송층은 인증이 없다. SaaS 제품에서는 쓰지
# 않으므로 기본 차단하고, A2A 에이전트 연동이 필요할 때만 명시적으로 켠다.
# 켜는 순간 공개 URL로 API 크레딧이 열린다는 뜻이므로 문서에 경고를 남긴다.
#
# 라우터 등록은 프로세스 기동 시 한 번 정해진다(FastAPI 구조상 불가피).
# 테스트는 이 상수를 리로드로 바꾸지 말고 create_app()으로 별도 앱을 만든다 —
# 리로드는 다른 테스트의 monkeypatch·모듈 캐시를 조용히 오염시킨다(실측:
# 관통 테스트가 스텁을 잃고 120초 네트워크 경로로 되돌아갔다).
def _legacy_on() -> bool:
    return os.environ.get("ENABLE_LEGACY_PRODUCT_UI", "").lower() in ("1", "true", "yes")


LEGACY_ON = _legacy_on()

v1 = APIRouter()


@app.exception_handler(EngineError)
async def engine_error_handler(_: Request, exc: EngineError):
    return JSONResponse(status_code=exc.http_status, content=exc.payload())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    # 스키마 위반·필수 누락 → 400 invalid_input (API §0.1)
    return JSONResponse(status_code=400, content={
        "error": {"code": "invalid_input", "message": "스키마 위반 또는 필수 항목 누락",
                  "details": {"errors": exc.errors()}}})


@v1.post("/v1/represent", response_model=RepresentResponse)
def post_represent(req: RepresentRequest):
    return represent(req)


@v1.post("/v1/retrieve", response_model=RetrieveResponse)
def post_retrieve(req: RetrieveRequest):
    return retrieve(req)


@v1.post("/v1/judge", status_code=202)
def post_judge(req: JudgeRequest, background: BackgroundTasks):
    job, existed = store.create(req.client_request_id)
    if not existed:
        background.add_task(store.run, job,
                            lambda: judge(req).model_dump(mode="json"))
    return {"job_id": job.job_id}


@v1.post("/v1/negotiate", status_code=202)
def post_negotiate(req: NegotiateRequest, background: BackgroundTasks):
    job, existed = store.create(req.client_request_id)
    if not existed:
        background.add_task(store.run, job,
                            lambda: negotiate(req).model_dump(mode="json"))
    return {"job_id": job.job_id}


@v1.get("/v1/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise EngineError(404, "not_found", f"job {job_id} 없음")
    return JobOut(job_id=job.job_id, status=job.status,
                  result=job.result, error=job.error, logs=job.log.entries,
                  elapsed=job.log.elapsed)


@v1.post("/v1/compose", response_model=ComposeResponse)
def post_compose(req: ComposeRequest):
    return compose(req)


@v1.post("/v1/scout", status_code=202)
def post_scout(req: "ScoutRequest", background: BackgroundTasks):
    """지식 분리 → explore/exploit 가설 → 웹 검색 숏리스트 (JDG-09·기획서 6.4).
    웹 검색 + LLM 가설이라 비동기 job."""
    from .engine.scout import scout
    job, existed = store.create(req.client_request_id)
    if not existed:
        background.add_task(store.run, job,
                            lambda: scout(req).model_dump(mode="json"))
    return {"job_id": job.job_id}


# ── A2A capability discovery — Agent Card (/.well-known/agent.json) ─
# Google A2A 프로토콜 규약: 에이전트는 자기 능력을 JSON 카드로 광고하고,
# 클라이언트 에이전트는 카드를 읽어 어떤 태스크를 맡길 수 있는지 발견한다.

@v1.get("/.well-known/agent.json")
def agent_card(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "name": "a2a-matching-engine",
        "description": "B2B 매칭엔진 — 기업 자료로 회사의 상(像)을 세우고, "
                       "보완성 기반 후보 발굴·판단·초안·협상 시뮬레이션까지 수행하는 에이전트",
        "url": base,
        "version": app.version,
        "provider": {"organization": "MYSC"},
        "preferredTransport": "JSONRPC",
        "additionalInterfaces": [
            {"transport": "JSONRPC", "url": f"{base}/a2a"},
        ],
        "capabilities": {
            "streaming": True,              # message/stream (SSE) 지원
            "pushNotifications": False,
            "stateTransitionHistory": True, # job.logs에 노드 이벤트 전체 보존
        },
        "defaultInputModes": ["application/json", "application/pdf", "text/plain"],
        "defaultOutputModes": ["application/json", "image/png"],
        "skills": [
            {"id": "represent", "name": "프로필 구축",
             "description": "기업 자료(IR덱·웹·기사) → 5층 다층 독해로 프로필+회사의 상 추출. "
                            "최소 프로필 미달 시 input-required(보강 질문)로 전환",
             "tags": ["extraction", "profile"]},
            {"id": "retrieve", "name": "후보 발굴",
             "description": "보완성 기반 상대 후보 검색 (유사도 아님)", "tags": ["matching"]},
            {"id": "judge", "name": "판단",
             "description": "양측 상 재구성 → 진행/보류 판단 (장기 실행 태스크)",
             "tags": ["reasoning"]},
            {"id": "compose", "name": "초안 생성",
             "description": "아웃리치 초안 — 발송 결정은 항상 사람(CMP-06)", "tags": ["draft"]},
            {"id": "negotiate", "name": "협상 시뮬레이션",
             "description": "두 렌즈 분기 협상 (장기 실행 태스크)", "tags": ["simulation"]},
            {"id": "scout", "name": "웹 파트너 스카우트",
             "description": "명백지/암묵지 분리 → exploit(정석)·explore(모험) 가설 → "
                            "웹 검색으로 풀 밖 후보 숏리스트 (JDG-09 탐색 예산의 충원 단계 적용)",
             "tags": ["hypothesis", "web-search", "explore-exploit"]},
            {"id": "question-pinning", "name": "질문 위치 탐지",
             "description": "추론 모델의 질문을 VLM이 원문 좌표(bbox)에 핀 — 사람이 답하기 "
                            "전까지 input-required로 매칭을 막는다 (강제 응답)",
             "tags": ["vision", "human-in-the-loop"]},
        ],
    }


# ── A2A 전송 계층 (JSON-RPC 2.0 + SSE) ──────────────────────────────
def mount_legacy(target: FastAPI) -> None:
    """레거시 표면(인증 없음)을 앱에 붙인다 — 명시 호출로만."""
    from .a2a import router as a2a_router              # noqa: E402
    from .product.router import router as product_router   # noqa: E402

    target.include_router(v1)
    target.include_router(a2a_router)
    target.include_router(product_router)
    target.mount("/", StaticFiles(
        directory=Path(__file__).parent / "product" / "static",
        html=True), name="ui")


if LEGACY_ON:
    mount_legacy(app)


# ── SaaS 계층 (이슈 #6) — 인증·비용캡이 있는 유일한 표면 ────────────
from .saas.router import router as saas_router   # noqa: E402

app.include_router(saas_router)


@app.get("/healthz")
def healthz():
    """생존 확인 — 의존성을 건드리지 않는다. StaticFiles 마운트보다 위에
    선언해야 캐치올에 먹히지 않는다."""
    return {"ok": True, "version": app.version}


@app.get("/readyz")
def readyz():
    """준비 확인 — 실제로 요청을 처리할 수 있는 상태인지 본다.

    /healthz와 나누는 이유: 프로세스는 살아 있는데 스토어가 죽었거나
    허용 목록이 비어(전원 거부) 아무도 못 쓰는 상태를 '정상'으로
    보고하면 안 된다. 실패는 503 + 원인 목록으로 말한다.
    """
    from .config import get_settings
    from .saas.store import get_saas_store
    problems = []
    try:
        get_saas_store().list("workspace", "__readyz__")
    except Exception as e:                       # noqa: BLE001
        problems.append(f"store: {type(e).__name__}")
    s = get_settings()
    if not s.saas_allowed_users:
        problems.append("SAAS_ALLOWED_USERS 비어 있음 — 전원 거부 상태")
    if s.llm_provider == "openai" and not s.openai_api_key:
        problems.append("OPENAI_API_KEY 없음")
    # Settings에는 tavily 필드가 없다 — 커넥터가 os.environ을 직접 읽는다.
    # getattr(s, "tavily_api_key", "")로 보면 **항상** 빈 문자열이라
    # readyz가 영원히 503이 된다(실측: 키를 넣고도 503). 같은 출처를 본다.
    if not os.environ.get("TAVILY_API_KEY", ""):
        problems.append("TAVILY_API_KEY 없음 — 검색 없이 후보를 만들 수 없습니다")
    if problems:
        return JSONResponse(status_code=503, content={"ok": False,
                                                      "problems": problems})
    return {"ok": True}


# 레거시 제품 레이어(인증 없는 22개 엔드포인트)는 위 mount_legacy가 담당한다.
