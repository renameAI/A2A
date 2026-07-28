"""진행 로그 + 파이프라인 노드 이벤트 — 엔진의 사고 과정을 DAG로 보여준다.

두 층위:
- log(stage, message): 자유 텍스트 진행 로그 (기존)
- node(node_id, label): 파이프라인 노드 수명주기 (node_start/node_end + 상태)
  컨텍스트 안에서 찍힌 log는 해당 노드에 태깅되어, UI에서 노드 클릭 시
  그 구간의 로그만 필터해 볼 수 있다.

contextvar 기반 — job이 없으면 전부 no-op (엔진 순수성 유지).
"""
import contextvars
import threading
import time
from contextlib import contextmanager

_current: contextvars.ContextVar = contextvars.ContextVar("run_log", default=None)


class RunLog:
    def __init__(self):
        self.entries: list[dict] = []
        self._t0 = time.time()
        self._t_end: float | None = None
        self._node_stack: list[str] = []
        self._lock = threading.Lock()
        self.llm_calls = 0            # LLM(K-EXAONE 등) 호출 횟수 — loop 예산 계측
        # 생성 중인 LLM 출력의 실시간 꼬리 — 폴링(jobs/{id})이 그대로 노출한다.
        # entries에 줄로 쌓지 않고 제자리 교체하는 이유: 토큰 단위로 append하면
        # 로그가 수천 줄로 폭주하고, 폴링 페이로드도 매번 전체 로그를 재전송한다.
        self.live: dict | None = None

    @property
    def elapsed(self) -> float:
        """서버 기준 경과 초 — 실행 중엔 흐르고, freeze() 후엔 실제 처리 시간으로 고정.
        고정하지 않으면 완료된 job을 폴링할 때마다 '처리 시간'이 계속 자라는 거짓 지표가 된다."""
        return round((self._t_end or time.time()) - self._t0, 1)

    def freeze(self) -> None:
        """작업 종료 시점 고정 — 이후 elapsed는 처리 시간 지표로 쓸 수 있다."""
        self._t_end = time.time()

    def add(self, stage: str, message: str, *, type: str = "log",
            node: str | None = None, status: str | None = None) -> None:
        with self._lock:
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

    def current_node(self) -> str | None:
        with self._lock:
            return self._node_stack[-1] if self._node_stack else None

    def tick_llm(self) -> None:
        """LLM 1회 호출 계측 — llm._chat이 호출 직전에 부른다."""
        with self._lock:
            self.llm_calls += 1

    def stage_timings(self) -> dict[str, float]:
        """node_start/node_end 쌍에서 노드별 소요(초)를 집계 — 재진입 노드는 합산.

        depth 1(최상위) 노드만 집계해 단계 총시간(represent/retrieve/judge/…)을
        본다. 중첩 노드(llm.reason 등)는 상위에 이미 포함되므로 뺀다."""
        opens: dict[str, float] = {}
        totals: dict[str, float] = {}
        for e in self.entries:
            if e.get("depth") != 1:
                continue
            node = e.get("node")
            if not node:
                continue
            if e.get("type") == "node_start":
                opens[node] = e["t"]
            elif e.get("type") == "node_end" and node in opens:
                totals[node] = round(totals.get(node, 0.0) + (e["t"] - opens.pop(node)), 1)
        return totals


def bind() -> RunLog:
    """job 실행 시작 시 호출 — 이후 같은 컨텍스트의 log()/node()가 여기로 모인다."""
    run = RunLog()
    _current.set(run)
    return run


def log(stage: str, message: str) -> None:
    run = _current.get()
    if run is not None:
        run.add(stage, message)


def tick_llm() -> None:
    """LLM 호출 계측 — job 컨텍스트 없으면 no-op."""
    run = _current.get()
    if run is not None:
        run.tick_llm()


def live_update(stage: str, text: str, *, thinking: bool = False) -> None:
    """생성 중 텍스트의 실시간 꼬리 교체 — 데모 채팅 UI가 폴링으로 읽는다.

    전체 텍스트가 아니라 꼬리 4,000자만 싣는다: 폴링(1.2s)마다 페이로드로
    나가므로 추론이 길어져도 응답 크기가 상수로 유지된다. chars로 전체 길이는
    정직하게 노출한다(꼬리만 보이는 게 아니라 얼마나 생성됐는지)."""
    run = _current.get()
    if run is not None:
        with run._lock:
            run.live = {"stage": stage, "text": text[-4000:],
                        "chars": len(text), "thinking": thinking,
                        "t": round(time.time() - run._t0, 1)}


def live_clear() -> None:
    run = _current.get()
    if run is not None:
        with run._lock:
            run.live = None


def current() -> RunLog | None:
    return _current.get()


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
