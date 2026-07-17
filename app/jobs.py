"""비동기 job 스토어 (API_계약서 §0.2, SYS-02).

Phase 6 운영화: 인메모리 → SQLite write-through (재시작 생존). ProductStore와 같은
패턴·같은 DB 파일(A2A_DB_PATH) — 무인프라(stdlib sqlite3), 커넥션은 호출마다 새로
열어 백그라운드 job 스레드가 커넥션을 공유하지 않는다.

왜 영속화가 필요한가: A2A 전송층이 Task를 핵심 추상으로 세웠는데(tasks/get·
tasks/cancel) 인메모리면 재시작 후 완료된 Task도 404가 된다 — 프로토콜 구멍이다.
이제 완료 Task는 재시작을 생존하고, client_request_id 멱등도 재시작을 넘어 유지된다.

좀비 수확(정직성): 재시작 시 running이던 job은 그 스레드가 죽었으므로 되살아나지
않는다. 시작 시 error로 수확해 '영원한 running'(A2A SSE 무한 루프의 원인)을 막는다.

로그 쓰기 정책: 실행 중 진행 로그는 인메모리(폴링이 읽음), 상태 전이(queued→running
→done/error) 시점에만 DB에 기록한다 — 로그 한 줄마다 쓰면 쓰기 폭주가 된다.
"""
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Callable, Optional

from . import progress
from .errors import EngineError
from .schemas import JobStatus


class Job:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.status = JobStatus.queued
        self.result: Optional[dict] = None
        self.error: Optional[dict] = None
        self.log = progress.RunLog()   # 실행 과정 로그 (폴링으로 실시간 노출)


def _db_path() -> Path:
    override = os.environ.get("A2A_DB_PATH")
    return Path(override) if override else \
        Path(__file__).resolve().parent.parent / "data" / "a2a.db"


class _RestoredLog:
    """DB에서 되살린 job의 로그 — RunLog 인터페이스 중 읽기 경로만 채운다
    (되살린 job은 이미 종료돼 더 쓸 일이 없다)."""

    def __init__(self, entries: list[dict], elapsed: float):
        self.entries = entries
        self.elapsed = elapsed

    def add(self, *a, **k) -> None:      # 종료된 job에는 no-op
        pass


class JobStore:
    """SQLite write-through. 살아있는 job은 메모리(실시간 로그), 조회는 메모리 우선·
    없으면 DB에서 복원 — 재시작 후에도 완료 Task를 돌려준다."""

    def __init__(self, reap: bool = True):
        self._jobs: dict[str, Job] = {}
        if reap:
            self._reap_zombies()

    def _connect(self) -> sqlite3.Connection:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS jobs "
                     "(job_id TEXT PRIMARY KEY, client_request_id TEXT, "
                     " data TEXT NOT NULL)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_crid "
                     "ON jobs(client_request_id) WHERE client_request_id IS NOT NULL")
        return conn

    def _reap_zombies(self) -> None:
        """재시작 시 running 고착 수확 — 그 스레드는 이미 죽었다."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT job_id, data FROM jobs").fetchall()
                for job_id, blob in rows:
                    d = json.loads(blob)
                    if d.get("status") in (JobStatus.running.value,
                                           JobStatus.queued.value):
                        d["status"] = JobStatus.error.value
                        d["error"] = {"code": "internal",
                                      "message": "서버 재시작으로 중단된 작업",
                                      "details": None}
                        conn.execute("UPDATE jobs SET data=? WHERE job_id=?",
                                     (json.dumps(d, ensure_ascii=False), job_id))
        except sqlite3.Error:
            pass   # 영속화는 보조 — DB 문제로 서버 기동을 막지 않는다

    def _put(self, job: Job, client_request_id: Optional[str] = None) -> None:
        data = json.dumps({
            "job_id": job.job_id,
            "status": job.status.value,
            "result": job.result,
            "error": job.error,
            "logs": job.log.entries,
            "elapsed": job.log.elapsed,
        }, ensure_ascii=False, default=str)
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO jobs(job_id, client_request_id, data) VALUES(?,?,?) "
                    "ON CONFLICT(job_id) DO UPDATE SET data=excluded.data",
                    (job.job_id, client_request_id, data))
        except sqlite3.Error:
            pass   # 쓰기 실패가 실행을 막지 않는다 (메모리 job은 계속 유효)

    def _load(self, job_id: str) -> Optional[Job]:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT data FROM jobs WHERE job_id=?",
                                   (job_id,)).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        d = json.loads(row[0])
        job = Job(d["job_id"])
        job.status = JobStatus(d["status"])
        job.result = d.get("result")
        job.error = d.get("error")
        job.log = _RestoredLog(d.get("logs", []), d.get("elapsed", 0.0))
        return job

    def create(self, client_request_id: Optional[str] = None) -> tuple[Job, bool]:
        """job 생성. 동일 client_request_id 재시도면 기존 job 반환 (멱등 — 재시작 생존)."""
        if client_request_id:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT job_id FROM jobs WHERE client_request_id=?",
                        (client_request_id,)).fetchone()
            except sqlite3.Error:
                row = None
            if row:
                existing = self._jobs.get(row[0]) or self._load(row[0])
                if existing is not None:
                    return existing, True
        job = Job(uuid.uuid4().hex[:12])
        self._jobs[job.job_id] = job
        self._put(job, client_request_id)
        return job, False

    def get(self, job_id: str) -> Optional[Job]:
        """메모리 우선(실행 중 실시간 로그) → 없으면 DB 복원(재시작 후 완료 Task)."""
        return self._jobs.get(job_id) or self._load(job_id)

    def run(self, job: Job, fn: Callable[[], dict]) -> None:
        """BackgroundTasks에서 실행. EngineError는 job.error로 수렴 (예: 423 deal_breaker).
        실행 컨텍스트에 진행 로그를 바인딩해 엔진 내부 progress.log()를 수집한다.
        상태 전이 시점에 DB write-through."""
        job.status = JobStatus.running
        job.log = progress.bind()
        self._put(job)
        try:
            job.result = fn()
            job.log.add("완료", "작업이 정상 완료되었습니다.")
            job.status = JobStatus.done
        except EngineError as e:
            job.log.add("오류", f"{e.code}: {e.message}")
            job.error = e.payload()["error"]
            job.status = JobStatus.error
        except Exception as e:                       # noqa: BLE001
            job.log.add("오류", f"internal: {e}")
            job.error = {"code": "internal", "message": str(e), "details": None}
            job.status = JobStatus.error
        finally:
            # BaseException(SystemExit 등)이 위 핸들러를 건너뛰어도 running으로
            # 고착시키지 않는다 — running 고착은 A2A SSE 스트림 무한 루프가 된다.
            if job.status == JobStatus.running:
                job.error = {"code": "internal",
                             "message": "작업 스레드 비정상 종료", "details": None}
                job.status = JobStatus.error
            self._put(job)   # 종료 상태·최종 로그 영속화


store = JobStore()
