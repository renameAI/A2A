"""진행 로그 + 파이프라인 노드 이벤트 — 엔진의 사고 과정을 DAG로 보여준다.

두 층위:
- log(stage, message): 자유 텍스트 진행 로그 (기존)
- node(node_id, label): 파이프라인 노드 수명주기 (node_start/node_end + 상태)
  컨텍스트 안에서 찍힌 log는 해당 노드에 태깅되어, UI에서 노드 클릭 시
  그 구간의 로그만 필터해 볼 수 있다.

contextvar 기반 — job이 없으면 전부 no-op (엔진 순수성 유지).
"""
import contextvars
import time
from contextlib import contextmanager

_current: contextvars.ContextVar = contextvars.ContextVar("run_log", default=None)


class RunLog:
    def __init__(self):
        self.entries: list[dict] = []
        self._t0 = time.time()
        self._node_stack: list[str] = []

    @property
    def elapsed(self) -> float:
        """서버 기준 총 경과 초 — UI가 실행 중 노드의 소요 시간을 계산하는 기준."""
        return round(time.time() - self._t0, 1)

    def add(self, stage: str, message: str, *, type: str = "log",
            node: str | None = None, status: str | None = None) -> None:
        entry = {
            "t": round(time.time() - self._t0, 1),
            "type": type,
            "stage": stage,
            "message": message,
        }
        current_node = node or (self._node_stack[-1] if self._node_stack else None)
        if current_node:
            entry["node"] = current_node
        if type in ("node_start", "node_end"):
            entry["depth"] = max(len(self._node_stack), 1)   # 중첩 시각화용
        if status:
            entry["status"] = status
        self.entries.append(entry)


def bind() -> RunLog:
    """job 실행 시작 시 호출 — 이후 같은 컨텍스트의 log()/node()가 여기로 모인다."""
    run = RunLog()
    _current.set(run)
    return run


def log(stage: str, message: str) -> None:
    run = _current.get()
    if run is not None:
        run.add(stage, message)


@contextmanager
def node(node_id: str, label: str = ""):
    """파이프라인 노드 경계 — 시작/종료(성공·실패)를 구조화 이벤트로 기록.

    예외는 삼키지 않는다 — 노드를 error로 마킹하고 그대로 전파 (실패 지점이
    DAG에서 빨간 노드로 보인다).
    """
    run = _current.get()
    if run is None:
        yield
        return
    run._node_stack.append(node_id)
    run.add(label or node_id, "▶ 시작", type="node_start", node=node_id)
    try:
        yield
    except Exception as e:                       # noqa: BLE001 — 마킹 후 재전파
        run.add(label or node_id, f"✗ 실패: {e}", type="node_end",
                node=node_id, status="error")
        run._node_stack.pop()
        raise
    run.add(label or node_id, "✓ 완료", type="node_end",
            node=node_id, status="ok")
    run._node_stack.pop()
