"""비동기 job 스토어 (API_계약서 §0.2, SYS-02).

v0: 인메모리 + BackgroundTasks. Phase 5: Redis/PostgreSQL 영속화 + webhook 통지.
client_request_id 멱등 처리 (API §0 공통 규약).
"""
import uuid
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


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._idempotency: dict[str, str] = {}   # client_request_id → job_id

    def create(self, client_request_id: Optional[str] = None) -> tuple[Job, bool]:
        """job 생성. 동일 client_request_id 재시도면 기존 job 반환 (멱등)."""
        if client_request_id and client_request_id in self._idempotency:
            return self._jobs[self._idempotency[client_request_id]], True
        job = Job(uuid.uuid4().hex[:12])
        self._jobs[job.job_id] = job
        if client_request_id:
            self._idempotency[client_request_id] = job.job_id
        return job, False

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def run(self, job: Job, fn: Callable[[], dict]) -> None:
        """BackgroundTasks에서 실행. EngineError는 job.error로 수렴 (예: 423 deal_breaker).
        실행 컨텍스트에 진행 로그를 바인딩해 엔진 내부 progress.log()를 수집한다."""
        job.status = JobStatus.running
        job.log = progress.bind()
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


store = JobStore()
