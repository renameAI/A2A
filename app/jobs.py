"""비동기 job 스토어 (API_계약서 §0.2, SYS-02).

보존 백엔드는 SaasStore다(local=SQLite / supabase=Postgres). 이전에는 이 모듈이
직접 SQLite를 열었는데, 그러면 **인스턴스 로컬 파일**에 원장이 갇힌다:

- Cloud Run: /tmp라 인스턴스 교체 시 완료 job도 404
- 서버리스(Vercel): 호출마다 인스턴스가 달라 `/search`가 만든 job을
  `/saas/jobs/{id}` 폴링이 아예 못 찾는다 — 기능이 성립하지 않는다

SaasStore로 옮기면 두 문제가 함께 사라진다. 배포 형태와 무관하게 job은
공유 저장소에 있고, 폴링이 어느 인스턴스에 붙어도 같은 것을 본다.

**워크스페이스 스코프**: job 문서는 소유 워크스페이스 아래 저장된다. 조회에
ws가 필요하므로 남의 job은 구조적으로 보이지 않는다(별도 소유권 대조 불필요).

진행 로그 정책: 실행 중에도 주기적으로(_FLUSH_EVERY초) 저장소에 흘려보낸다.
매 줄마다 쓰면 쓰기 폭주가 되고, 종료 시점에만 쓰면 서버리스에서 폴링이
"작업 중"만 보다가 끝난다 — 그 사이를 절충한다.

좀비 수확(정직성): running으로 오래 멈춘 job은 그 실행이 죽은 것이다.
조회 시점에 판정해 error로 돌린다 — '영원한 running'(A2A SSE 무한 루프의 원인).
"""
import logging
import os
import time
import uuid
from typing import Callable, Optional

from . import progress
from .errors import EngineError
from .schemas import JobStatus

_log = logging.getLogger("a2a.jobs")

_KIND = "job"
# 레거시 경로(/v1/*, /a2a, /product/*)는 사용자 컨텍스트가 없다. 기본 차단
# 상태이며(ENABLE_LEGACY_PRODUCT_UI), 켜더라도 이 예약 워크스페이스를 쓴다.
_LEGACY_WS = "__legacy__"
_FLUSH_EVERY = 3.0        # 실행 중 진행 로그 flush 간격(초)
# running인데 이 시간 넘게 갱신이 없으면 죽은 실행으로 본다. 실측 최장 job
# (GPT API 검색 129초)의 5배 — 정상 job을 오판하지 않는 여유.
_STALE_AFTER = 600.0


class Job:
    def __init__(self, job_id: str, ws: str = _LEGACY_WS):
        self.job_id = job_id
        self.ws = ws
        # 작업 서명 — 문서의 모든 쓰기가 보존해야 한다. 처음엔 _put의 인자로만
        # 흘렸더니 create 직후 첫 진행 플러시가 None으로 덮어써서, 0.6초 뒤
        # 중복 제출이 서명을 못 찾았다(실측: 4연타 4잡 — 흡수 0).
        self.client_request_id: "str | None" = None
        self.status = JobStatus.queued
        self.result: Optional[dict] = None
        self.error: Optional[dict] = None
        self.log = progress.RunLog()   # 실행 과정 로그 (폴링으로 실시간 노출)


class _RestoredLog:
    """저장소에서 되살린 job의 로그 — RunLog 인터페이스 중 읽기 경로만 채운다."""

    def __init__(self, entries: list[dict], elapsed: float):
        self.entries = entries
        self.elapsed = elapsed
        self.llm_calls = 0
        self.live = None

    def add(self, *a, **k) -> None:
        pass

    def freeze(self) -> None:
        pass

    def stage_timings(self) -> dict:
        return {}


def _store():
    from .saas.store import get_saas_store
    return get_saas_store()


class JobStore:
    """SaasStore write-through. 살아있는 job은 메모리에도 두어 같은 인스턴스의
    폴링이 실시간 로그를 보고, 다른 인스턴스는 저장소에서 읽는다."""

    def __init__(self, reap: bool = True):
        self._jobs: dict[str, Job] = {}

    # ── 직렬화 ──
    def _body(self, job: Job) -> dict:
        return {
            "job_id": job.job_id,
            "workspace_id": job.ws,
            "client_request_id": getattr(job, "client_request_id", None),
            "status": job.status.value,
            "result": job.result,
            "error": job.error,
            "logs": job.log.entries,
            "elapsed": job.log.elapsed,
            "updated": time.time(),
        }

    def _put(self, job: Job) -> None:
        try:
            _store().put(_KIND, job.ws, job.job_id, self._body(job))
        except Exception as e:                        # noqa: BLE001
            # 원장 쓰기 실패가 실행을 막지 않는다. 다만 조용히 넘기면 폴링이
            # 왜 멈췄는지 알 수 없으므로 로그는 남긴다.
            _log.warning("job %s 원장 쓰기 실패: %s", job.job_id, type(e).__name__)

    def _restore(self, d: dict) -> Job:
        job = Job(d["job_id"], d.get("workspace_id", _LEGACY_WS))
        job.status = JobStatus(d["status"])
        job.result = d.get("result")
        job.error = d.get("error")
        job.log = _RestoredLog(d.get("logs", []), d.get("elapsed", 0.0))
        job.client_request_id = d.get("client_request_id")
        return job

    # ── 공개 API ──
    def create(self, client_request_id: Optional[str] = None, *,
               ws: str = _LEGACY_WS) -> tuple[Job, bool]:
        """job 생성. 같은 서명의 **활성** job이 있으면 그것을 반환 (single-flight).

        실측(프로덕션 잡 156건/10일): 같은 브리프가 2초 안에 5번 제출돼 LLM을
        5번 결제했고, 같은 판독이 18초 간격으로 겹쳐 돌았다. 이 멱등 훅은
        원래부터 있었지만 아무도 안 넘겨서(156건 중 client_request_id 0건)
        한 번도 안 걸렸다 — _submit이 작업 서명을 넘기면서 처음 배선된다.

        '활성일 때만' 재사용하는 이유: 끝난 job까지 재사용하면 정당한 재실행
        (브리프 다시 만들기)이 옛 결과를 돌려받는다. 재사용은 중복 제출을
        흡수하는 창이지 결과 캐시가 아니다. 좀비(running인데 오래 정지)는
        get()과 같은 기준으로 건너뛴다 — 죽은 실행이 10분간 새 실행을
        막으면 안 된다.
        """
        if client_request_id:
            now = time.time()
            for d in _safe_list(ws):
                if (d.get("client_request_id") == client_request_id
                        and d.get("status") in ("queued", "running")
                        and now - float(d.get("updated") or 0) < _STALE_AFTER):
                    return (self._jobs.get(d["job_id"]) or self._restore(d)), True
        job = Job(uuid.uuid4().hex[:12], ws)
        job.client_request_id = client_request_id
        self._jobs[job.job_id] = job
        self._put(job)
        return job, False

    def get(self, job_id: str, ws: str = _LEGACY_WS) -> Optional[Job]:
        """메모리 우선(같은 인스턴스의 실시간 로그) → 저장소(다른 인스턴스·재시작).

        메모리 job이 더 최신이라도, 저장소 쪽이 done/error면 그쪽을 믿는다 —
        같은 job을 두 인스턴스가 동시에 들고 있을 일은 없지만, 메모리 job이
        죽은 실행의 잔재일 수 있다.
        """
        # 메모리 캐시도 ws로 거른다. 거르지 않으면 같은 인스턴스에 붙은 다른
        # 워크스페이스 사용자가 남의 job을 그대로 받는다 — 저장소 스코프만
        # 믿고 캐시를 그냥 반환하면 격리가 캐시 히트 여부에 좌우된다(실측:
        # mallory가 보람의 후보 목록을 200으로 받았다).
        live = self._jobs.get(job_id)
        if live is not None and live.ws != ws:
            live = None
        try:
            d = _store().get(_KIND, ws, job_id)
        except Exception:                             # noqa: BLE001
            return live
        if d is None:
            return live
        if live is not None and live.status == JobStatus.running:
            if d.get("status") in (JobStatus.done.value, JobStatus.error.value):
                return self._restore(d)
            return live
        job = self._restore(d)
        return self._reap_if_stale(job, d)

    def _reap_if_stale(self, job: Job, d: dict) -> Job:
        """running인데 오래 갱신이 없으면 그 실행은 죽은 것이다.

        재시작 시 일괄 수확 대신 조회 시점에 판정하는 이유: 서버리스에는
        '재시작'이라는 시점이 없다. 인스턴스는 조용히 사라진다.
        """
        if job.status != JobStatus.running:
            return job
        if time.time() - float(d.get("updated", 0)) < _STALE_AFTER:
            return job
        job.status = JobStatus.error
        job.error = {"code": "internal",
                     "message": "작업이 중단되었습니다 (실행 인스턴스 소멸)",
                     "details": None}
        self._put(job)
        _log.warning("job %s 좀비 수확", job.job_id, extra={"job_id": job.job_id})
        return job

    def run(self, job: Job, fn: Callable[[], dict]) -> None:
        """BackgroundTasks에서 실행. EngineError는 job.error로 수렴.
        실행 컨텍스트에 진행 로그를 바인딩해 엔진 내부 progress.log()를 수집하고,
        주기적으로 원장에 흘려보내 다른 인스턴스의 폴링도 진행을 본다."""
        job.status = JobStatus.running
        job.log = progress.bind()
        tokens = progress.bind_tokens()   # 이 실행의 실측 사용량
        self._put(job)
        last = time.time()

        def _flush() -> None:
            nonlocal last
            now = time.time()
            if now - last >= _FLUSH_EVERY:
                last = now
                self._put(job)

        job.log.on_add = _flush          # RunLog가 줄을 더할 때마다 호출
        try:
            job.result = fn()
            if tokens["calls"]:
                from .saas import cost as _cost
                spent = _cost.usd(tokens["in"], tokens["out"])
                job.log.add("완료", f"모델 호출 {tokens['calls']}회 · "
                                    f"토큰 {tokens['in']:,}/{tokens['out']:,} · "
                                    f"실측 ${spent:.4f}")
                if isinstance(job.result, dict):
                    job.result["spend"] = {
                        "usd": round(spent, 4), "calls": tokens["calls"],
                        "tokens_in": tokens["in"], "tokens_out": tokens["out"]}
            job.log.add("완료", "작업이 정상 완료되었습니다.")
            job.status = JobStatus.done
        except EngineError as e:
            job.log.add("오류", f"{e.code}: {e.message}")
            job.error = e.payload()["error"]
            job.status = JobStatus.error
            _log.warning("job %s 실패: %s: %s", job.job_id, e.code, e.message,
                         extra={"job_id": job.job_id, "code": e.code})
        except Exception as e:                       # noqa: BLE001
            job.log.add("오류", f"internal: {e}")
            job.error = {"code": "internal", "message": str(e), "details": None}
            job.status = JobStatus.error
            # 트레이스백을 여기서 버리면 프로덕션에서 원인을 알 길이 없다 —
            # 사용자에게 가는 payload는 그대로 두고(내부 구조 비노출), 서버
            # 로그에만 전체 스택을 남긴다.
            _log.exception("job %s 실패 (internal)", job.job_id,
                           extra={"job_id": job.job_id})
        finally:
            job.log.on_add = None
            # BaseException(SystemExit 등)이 위 핸들러를 건너뛰어도 running으로
            # 고착시키지 않는다.
            if job.status == JobStatus.running:
                job.error = {"code": "internal",
                             "message": "작업 스레드 비정상 종료", "details": None}
                job.status = JobStatus.error
            job.log.freeze()   # 처리 시간 고정 — 폴링 시점에 따라 자라지 않게
            self._put(job)     # 종료 상태·최종 로그 영속화


def _safe_list(ws: str) -> list[dict]:
    try:
        return _store().list(_KIND, ws, limit=200)
    except Exception:                                 # noqa: BLE001
        return []


store = JobStore()
