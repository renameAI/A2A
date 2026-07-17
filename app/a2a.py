"""A2A 프로토콜 전송 계층 — JSON-RPC 2.0 + SSE 스트리밍.

Google Agent2Agent 규약을 우리 엔진 위에 정식 채택한다. 단일 엔드포인트 POST /a2a가
JSON-RPC 봉투를 받아 다음 메서드를 처리한다:
  message/send      메시지 전송 → Task 반환 (비동기, 폴링으로 상태 동기화)
  message/stream    메시지 전송 → SSE로 Task 상태·산출물 실시간 스트림
  tasks/get         Task 조회 (폴링)
  tasks/cancel      Task 취소 요청 (협조적 — 아래 한계 참고)

우리 엔진 스킬(represent/retrieve/judge/compose/negotiate)을 A2A Task로 감싼다.
클라이언트는 message의 DataPart에 {"skill": "...", "input": {...}}를 실어 보내고,
서버는 그 스킬을 job으로 실행한다. job의 progress 로그는 SSE status-update 이벤트로,
최종 결과는 artifact-update 이벤트로 흘러나간다.

취소 한계(정직하게): 엔진 스킬은 계산 중간에 중단 지점이 없다. tasks/cancel은
협조적 취소 — 이미 종료된 Task는 -32002(TaskNotCancelable), 실행 중 Task는 취소로
마킹하고 완료돼도 결과를 폐기한다(상태 계약 유지). 계산 자체는 job 경계까지 계속된다.
"""
import json
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import progress
from .errors import EngineError
from .jobs import Job, store as job_store
from .schemas import (ComposeRequest, JobStatus, JudgeRequest, NegotiateRequest,
                      RepresentRequest, RetrieveRequest)

router = APIRouter(tags=["a2a"])

# JSON-RPC 표준 에러코드 + A2A 확장코드
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002

_TERMINAL = {JobStatus.done, JobStatus.error}


# ── 엔진 스킬 레지스트리 — 스킬 id → (요청 스키마, 엔진 함수) ──────────
# 지연 import: 엔진 모듈이 이 모듈을 역참조하지 않도록 함수 안에서 부른다.

def _skill_registry() -> dict[str, tuple]:
    from .engine.compose import compose
    from .engine.judge import judge
    from .engine.negotiate import negotiate
    from .engine.represent import represent
    from .engine.retrieve import retrieve
    return {
        "represent": (RepresentRequest, represent),
        "retrieve": (RetrieveRequest, retrieve),
        "judge": (JudgeRequest, judge),
        "compose": (ComposeRequest, compose),
        "negotiate": (NegotiateRequest, negotiate),
    }


# ── Task 메타 영속화 (Phase 6) ────────────────────────────────────
# job은 jobs 테이블에 영속화되지만 A2A 고유 메타(skill·contextId·history·취소
# 마킹)는 여기 있다 — 이것도 영속화해야 재시작 후 tasks/get이 완전한 Task를 준다.
# 캐시(메모리) + write-through(SQLite), 같은 DB 파일·같은 패턴(A2A_DB_PATH).

_task_meta: dict[str, dict] = {}     # job_id → {"skill", "contextId", "history"} (캐시)
_canceled: set[str] = set()          # 협조적 취소 마킹된 job_id (캐시)


def _meta_connect():
    import sqlite3
    from .jobs import _db_path
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS a2a_task_meta "
                 "(job_id TEXT PRIMARY KEY, data TEXT NOT NULL, "
                 " canceled INTEGER NOT NULL DEFAULT 0)")
    return conn


def _meta_put(job_id: str, meta: dict, canceled: bool = False) -> None:
    import sqlite3
    try:
        with _meta_connect() as conn:
            conn.execute(
                "INSERT INTO a2a_task_meta(job_id, data, canceled) VALUES(?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET data=excluded.data, "
                "canceled=excluded.canceled",
                (job_id, json.dumps(meta, ensure_ascii=False), int(canceled)))
    except sqlite3.Error:
        pass   # 영속화는 보조 — 메모리 캐시로 계속 동작


def _meta_get(job_id: str) -> tuple[dict, bool]:
    """메모리 캐시 우선 → 없으면 DB(재시작 후). (meta, canceled) 반환."""
    if job_id in _task_meta:
        return _task_meta[job_id], job_id in _canceled
    import sqlite3
    try:
        with _meta_connect() as conn:
            row = conn.execute(
                "SELECT data, canceled FROM a2a_task_meta WHERE job_id=?",
                (job_id,)).fetchone()
    except sqlite3.Error:
        return {}, False
    if row is None:
        return {}, False
    meta = json.loads(row[0])
    _task_meta[job_id] = meta                 # 캐시 채움
    canceled = bool(row[1])
    if canceled:
        _canceled.add(job_id)
    return meta, canceled


def job_state_to_a2a(status: str, result, error) -> str:
    """job 상태 → A2A TaskState. 사람 입력을 기다리는 두 경우(최소 프로필 미달,
    미응답 질문 핀)는 A2A input-required와 정확히 같은 개념이다."""
    if status == "queued":
        return "submitted"
    if status == "running":
        return "working"
    if status == "canceled":
        return "canceled"
    if status == "error":
        code = (error or {}).get("code")
        if code in ("profile_below_minimum", "unclear_evidence_unresolved"):
            return "input-required"
        return "failed"
    if status == "done" and isinstance(result, dict) \
            and result.get("open_thread_count", 0) > 0:
        return "input-required"
    return "completed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_message(text: str, context_id: str) -> dict:
    return {"role": "agent", "parts": [{"kind": "text", "text": text}],
            "messageId": f"msg-{context_id}-{len(text)}", "kind": "message"}


def _effective_status(job: Job) -> str:
    _, canceled = _meta_get(job.job_id)      # 재시작 후에도 취소 마킹을 읽는다
    if canceled:
        return "canceled"
    return job.status.value


def _artifact(skill: str, result: dict) -> dict:
    return {"artifactId": f"art-{skill}", "name": f"{skill}-result",
            "parts": [{"kind": "data", "data": result}]}


def task_from_job(job: Job) -> dict:
    meta, _ = _meta_get(job.job_id)           # 캐시 → DB (재시작 생존)
    status = _effective_status(job)
    state = job_state_to_a2a(status, job.result, job.error)
    task = {
        "id": job.job_id,
        "contextId": meta.get("contextId", job.job_id),
        "kind": "task",
        "status": {"state": state, "timestamp": _now()},
        "artifacts": [], "history": meta.get("history", []),
        "metadata": {"skill": meta.get("skill")},
    }
    if state not in ("canceled",) and job.result is not None:
        task["artifacts"] = [_artifact(meta.get("skill", "result"), job.result)]
    if job.error is not None and status != "canceled":
        task["status"]["message"] = _agent_message(
            job.error.get("message", ""), task["contextId"])
    return task


# ── JSON-RPC 봉투 ─────────────────────────────────────────────────

def _ok(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code: int, message: str, data=None) -> dict:
    e = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": e}


class RpcError(Exception):
    def __init__(self, code: int, message: str, data=None):
        self.code, self.message, self.data = code, message, data


# ── 메시지 → 스킬 실행 ────────────────────────────────────────────

def _extract_skill_call(params: dict) -> tuple[str, dict, dict]:
    """A2A message에서 (skill, input, message) 추출.
    DataPart {"kind":"data","data":{"skill":..,"input":{..}}}를 찾는다."""
    if not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "params는 객체여야 합니다")
    message = params.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("parts"), list):
        raise RpcError(INVALID_PARAMS, "message.parts(배열)가 필요합니다")
    for part in message["parts"]:
        if isinstance(part, dict) and part.get("kind") == "data" \
                and isinstance(part.get("data"), dict) and "skill" in part["data"]:
            data = part["data"]
            return data["skill"], data.get("input", {}), message
    raise RpcError(INVALID_PARAMS,
                   'skill을 지정한 DataPart가 없습니다 — parts에 '
                   '{"kind":"data","data":{"skill":"judge","input":{...}}} 형식 필요')


def _skill_fn(skill: str, raw_input: dict) -> Callable[[], dict]:
    registry = _skill_registry()
    if skill not in registry:
        raise RpcError(INVALID_PARAMS,
                       f"알 수 없는 skill '{skill}' — 가능: {sorted(registry)}")
    model_cls, engine_fn = registry[skill]
    try:
        req = model_cls(**raw_input)
    except Exception as e:                       # pydantic ValidationError 등
        raise RpcError(INVALID_PARAMS, f"'{skill}' 입력 검증 실패", str(e))
    return lambda: engine_fn(req).model_dump(mode="json")


_META_CAP = 500   # 메모리 캐시 무한 성장 방어 (A2A-5) — 초과 시 종료 Task부터 회수


def _evict_terminal_meta() -> None:
    """메모리 캐시만 비운다 — DB(a2a_task_meta)는 유지되므로 축출된 Task도
    tasks/get으로 복원된다(캐시 축출 ≠ Task 소멸)."""
    if len(_task_meta) <= _META_CAP:
        return
    for jid in list(_task_meta):
        job = job_store.get(jid)
        if job is not None and job.status in _TERMINAL:
            _task_meta.pop(jid, None)
            _canceled.discard(jid)
        if len(_task_meta) <= _META_CAP:
            break


def _start_job(skill: str, raw_input: dict, message: dict) -> Job:
    """스킬을 job으로 만들어 background 스레드에서 실행 (Task 비동기 모델).

    멱등성 (적대적 검토 확정 A2A-2): 스킬 input의 client_request_id를 REST 경로와
    동일하게 job 멱등 키로 사용한다 — 재시도가 중복 Task·중복 엔진 실행을 만들지 않는다."""
    fn = _skill_fn(skill, raw_input)   # 검증 실패는 여기서 RpcError로 즉시 던진다
    crid = raw_input.get("client_request_id") if isinstance(raw_input, dict) else None
    job, existed = job_store.create(crid)
    if existed:
        return job                      # 기존 Task 반환 — 새 스레드 없음
    _evict_terminal_meta()
    meta = {
        "skill": skill,
        "contextId": message.get("contextId") or job.job_id,
        "history": [message],
    }
    _task_meta[job.job_id] = meta
    _meta_put(job.job_id, meta)              # 영속화 — 재시작 후 tasks/get 완전 복원
    threading.Thread(target=job_store.run, args=(job, fn), daemon=True).start()
    return job


# ── SSE 스트림 (message/stream) ───────────────────────────────────

def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _status_event(job: Job, state: str, *, final: bool,
                  text: Optional[str] = None) -> dict:
    meta, _ = _meta_get(job.job_id)
    ctx = meta.get("contextId", job.job_id)
    status = {"state": state, "timestamp": _now()}
    if text:
        status["message"] = _agent_message(text, ctx)
    return {"taskId": job.job_id, "contextId": ctx, "kind": "status-update",
            "status": status, "final": final}


def _artifact_event(job: Job) -> dict:
    meta, _ = _meta_get(job.job_id)
    return {"taskId": job.job_id, "contextId": meta.get("contextId", job.job_id),
            "kind": "artifact-update",
            "artifact": _artifact(meta.get("skill", "result"), job.result or {}),
            "append": False, "lastChunk": True}


def _stream_task(req_id, skill: str, raw_input: dict, message: dict):
    """SSE 제너레이터 — 초기 Task → 진행 status-update들 → 산출물 → 최종 status-update.
    각 SSE data는 완전한 JSON-RPC 응답({jsonrpc, id, result:<event>})이다."""
    job = _start_job(skill, raw_input, message)
    yield _sse(_ok(req_id, task_from_job(job)))          # 최초 Task 스냅샷

    sent = 0
    while True:
        entries = job.log.entries                        # run()이 bind로 교체 → 최신 참조
        while sent < len(entries):
            e = entries[sent]
            sent += 1
            yield _sse(_ok(req_id, _status_event(
                job, "working", final=False,
                text=f"[{e.get('stage', '')}] {e.get('message', '')}")))
        if job.status in _TERMINAL or job.job_id in _canceled:
            break
        time.sleep(0.3)

    state = job_state_to_a2a(_effective_status(job), job.result, job.error)
    if job.result is not None and job.job_id not in _canceled:
        yield _sse(_ok(req_id, _artifact_event(job)))    # 산출물
    yield _sse(_ok(req_id, _status_event(                # 최종 상태 (final=true)
        job, state, final=True,
        text=(job.error or {}).get("message") if job.error else None)))


# ── 메서드 디스패치 ────────────────────────────────────────────────

def _task_id_from(params, req_id) -> str:
    """params 타입 방어 (적대적 검토 확정 A2A-1) — 예전엔 배열 params가
    list.get AttributeError → -32603 내부오류 + 파이썬 내부 문자열 누출."""
    if params is not None and not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "params는 객체여야 합니다")
    task_id = (params or {}).get("id")
    if not isinstance(task_id, str) or not task_id:
        raise RpcError(INVALID_PARAMS, "params.id(문자열)가 필요합니다")
    return task_id


def _handle_tasks_get(params, req_id):
    task_id = _task_id_from(params, req_id)
    job = job_store.get(task_id)
    if job is None:
        raise RpcError(TASK_NOT_FOUND, f"Task {task_id} 없음")
    return _ok(req_id, task_from_job(job))


def _handle_tasks_cancel(params, req_id):
    task_id = _task_id_from(params, req_id)
    job = job_store.get(task_id)
    if job is None:
        raise RpcError(TASK_NOT_FOUND, f"Task {task_id} 없음")
    # 이미 취소 마킹된 Task의 재취소는 멱등 (A2A-3) — 표시상태 canceled와
    # raw status(done)가 모순된 -32002를 내지 않는다.
    meta, canceled = _meta_get(job.job_id)
    if canceled:
        return _ok(req_id, task_from_job(job))
    if job.status in _TERMINAL:
        raise RpcError(TASK_NOT_CANCELABLE,
                       "이미 종료된 Task는 취소할 수 없습니다")
    _canceled.add(job.job_id)   # 협조적 — 완료돼도 결과 폐기(task_from_job이 canceled로 표기)
    _meta_put(job.job_id, meta, canceled=True)   # 취소 마킹도 재시작 생존
    return _ok(req_id, task_from_job(job))


def _dispatch(method: str, params: dict, req_id):
    if method == "message/send":
        skill, raw_input, message = _extract_skill_call(params)
        job = _start_job(skill, raw_input, message)
        return _ok(req_id, task_from_job(job))
    if method == "tasks/get":
        return _handle_tasks_get(params, req_id)
    if method == "tasks/cancel":
        return _handle_tasks_cancel(params, req_id)
    raise RpcError(METHOD_NOT_FOUND, f"메서드 '{method}' 없음")


@router.post("/a2a")
async def a2a_endpoint(request: Request):
    # -32700 파싱 에러 — 봉투를 읽기 전이라 id는 null
    try:
        body = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(_err(None, PARSE_ERROR, "JSON 파싱 실패"))

    # -32600 잘못된 요청 (배치·비객체·버전 불일치)
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" \
            or not isinstance(body.get("method"), str):
        return JSONResponse(_err(body.get("id") if isinstance(body, dict) else None,
                                 INVALID_REQUEST, "유효한 JSON-RPC 2.0 요청이 아닙니다"))

    req_id = body.get("id")
    method = body["method"]
    params = body.get("params") or {}

    # 스트리밍 메서드는 SSE, 나머지는 단일 JSON 응답
    if method == "message/stream":
        try:
            skill, raw_input, message = _extract_skill_call(params)
            # 검증 실패를 스트림 시작 전에 잡아 JSON-RPC 에러로 반환
            _skill_fn(skill, raw_input)
        except RpcError as e:
            return JSONResponse(_err(req_id, e.code, e.message, e.data))
        return StreamingResponse(
            _stream_task(req_id, skill, raw_input, message),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    try:
        return JSONResponse(_dispatch(method, params, req_id))
    except RpcError as e:
        return JSONResponse(_err(req_id, e.code, e.message, e.data))
    except EngineError as e:
        return JSONResponse(_err(req_id, INTERNAL_ERROR, e.message, e.code))
    except Exception as e:                       # noqa: BLE001
        return JSONResponse(_err(req_id, INTERNAL_ERROR, str(e)))
