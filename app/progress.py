"""진행 로그 — 엔진의 사고 과정을 실시간으로 보여준다.

contextvar 기반이라 엔진 코드 어디서든 progress.log()를 호출하면
현재 실행 중인 job의 로그로 수렴한다. job이 없으면 no-op (엔진 순수성 유지).
"""
import contextvars
import time

_current: contextvars.ContextVar = contextvars.ContextVar("run_log", default=None)


class RunLog:
    def __init__(self):
        self.entries: list[dict] = []
        self._t0 = time.time()

    def add(self, stage: str, message: str) -> None:
        self.entries.append({
            "t": round(time.time() - self._t0, 1),   # 경과 초
            "stage": stage,
            "message": message,
        })


def bind() -> RunLog:
    """job 실행 시작 시 호출 — 이후 같은 컨텍스트의 log()가 여기로 모인다."""
    run = RunLog()
    _current.set(run)
    return run


def log(stage: str, message: str) -> None:
    run = _current.get()
    if run is not None:
        run.add(stage, message)
