"""테넌트별 메트릭 — 요청 수·오류·지연 히스토그램을 분 단위로 모아 컨트롤 플레인에 합산.

요청마다 DB에 쓰지 않는다. 프로세스 메모리 버킷에 더하고, 배경 스레드가
FLUSH_SEC마다 (tenant, minute, route) 단위로 upsert한다. 여러 인스턴스가 같은
행에 더해도 카운트·히스토그램 버킷은 원자 가산이라 합이 맞다(분위수는
버킷에서 계산 — 인스턴스별 p95를 평균내는 오류를 피한다).

경로는 '그룹'으로 접는다(post/list/stream/admin/other) — 경로 변수를 그대로
키로 쓰면 행 수가 채널 수만큼 폭증한다.
"""
import logging
import os
import re
import threading
import time
from collections import defaultdict

log = logging.getLogger("chat.metrics")
BOUNDS = [5, 10, 25, 50, 100, 250, 500, 1000, 2500]     # ms, 마지막 칸은 그 이상
FLUSH_SEC = float(os.environ.get("CHAT_METRICS_FLUSH_SEC", "10"))

_lock = threading.Lock()
_PID = str(os.getpid()).encode()
_DIAG = os.environ.get("CHAT_DIAG_HEADERS") == "1"
_buf: "dict[tuple[str, int, str], list]" = {}
_slug_to_id: dict[str, str] = {}
_started = False

_MSG = re.compile(r"^/chat/v1/t/[^/]+/channels/\d+/messages(/\d+)?$")
_STREAM = re.compile(r"^/chat/v1/t/[^/]+/stream$")
_ADMIN = re.compile(r"^/chat/v1/t/[^/]+/(members|roles|policies|logs)")
_SLUG = re.compile(r"^/chat/v1/t/([a-z][a-z0-9_]{1,40})/")


def route_group(method: str, path: str) -> "str | None":
    if not path.startswith("/chat/v1/t/"):
        return None
    if _MSG.match(path):
        return {"POST": "post", "GET": "list"}.get(method, "edit")
    if _STREAM.match(path):
        return "stream"
    if _ADMIN.match(path):
        return "admin"
    return "other"


def _bucket(ms: float) -> int:
    for i, b in enumerate(BOUNDS):
        if ms <= b:
            return i
    return len(BOUNDS)


def record(slug: str, group: str, ms: float, error: bool) -> None:
    minute = int(time.time() // 60 * 60)
    key = (slug, minute, group)
    with _lock:
        row = _buf.get(key)
        if row is None:
            row = _buf[key] = [0, 0, [0] * (len(BOUNDS) + 1)]
        row[0] += 1
        row[1] += int(error)
        row[2][_bucket(ms)] += 1
    _ensure_flusher()


def _ensure_flusher():
    global _started
    if _started or os.environ.get("CHAT_METRICS", "on") == "off":
        return
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="chat-metrics", daemon=True).start()


def _loop():
    while True:
        time.sleep(FLUSH_SEC)
        try:
            flush()
        except Exception as e:  # noqa: BLE001 — 메트릭 실패가 서비스를 멈추지 않는다
            log.warning("metrics flush failed: %s", e)


def flush() -> int:
    with _lock:
        rows, _buf_copy = list(_buf.items()), None
        _buf.clear()
    if not rows:
        return 0
    from datetime import datetime, timezone
    from .db import registry
    out = 0
    with registry().control_tx() as c:
        for (slug, minute, group), (n, err, hist) in rows:
            tid = _slug_to_id.get(slug)
            if tid is None:
                r = c.execute("select id::text from tenants where slug = %s", (slug,)).fetchone()
                if r is None:
                    continue
                tid = _slug_to_id[slug] = r["id"]
            c.execute("""insert into metrics_minute (tenant_id, minute, route, requests, errors, lat_hist)
                         values (%s, %s, %s, %s, %s, %s)
                         on conflict (tenant_id, minute, route) do update set
                           requests = metrics_minute.requests + excluded.requests,
                           errors = metrics_minute.errors + excluded.errors,
                           lat_hist = (select array_agg(a + b order by i)
                                         from unnest(metrics_minute.lat_hist, excluded.lat_hist)
                                              with ordinality as u(a, b, i))""",
                      (tid, datetime.fromtimestamp(minute, timezone.utc), group, n, err, hist))
            out += 1
    return out


def quantile(hist: list[int], q: float) -> "float | None":
    """히스토그램 → 분위수(버킷 상한). 마지막 칸은 '>2500'으로 표기할 수 있게 inf."""
    total = sum(hist)
    if not total:
        return None
    target = q * total
    run = 0
    for i, n in enumerate(hist):
        run += n
        if run >= target:
            return float(BOUNDS[i]) if i < len(BOUNDS) else float("inf")
    return float("inf")


class Middleware:
    """ASGI 미들웨어 — 처리 시간을 재고 X-Server-Ms 헤더를 붙인다. SSE는 연결
    수명이 길어 지연 히스토그램을 오염시키므로 '연결 수'로만 센다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope.get("path", ""), scope.get("method", "")
        group = route_group(method, path)
        if group is None:
            return await self.app(scope, receive, send)
        m = _SLUG.match(path)
        slug = m.group(1) if m else None
        t0 = time.perf_counter()
        recv_wall = time.time()           # 진단: 앱이 요청을 받은 벽시계 시각
        status = {"code": 500}

        async def _send(msg):
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
                ms = (time.perf_counter() - t0) * 1000
                msg.setdefault("headers", [])
                extra = [(b"x-server-ms", f"{ms:.1f}".encode())]
                if _DIAG:
                    # 진단 전용 — 프로세스 id·수신 시각은 내부 정보라 기본으로 내보내지 않는다
                    extra += [(b"x-worker", _PID), (b"x-server-recv", f"{recv_wall:.6f}".encode())]
                msg["headers"] = list(msg["headers"]) + extra
                if slug and group != "stream":
                    record(slug, group, ms, status["code"] >= 500)
                elif slug:
                    record(slug, "stream", 0.0, status["code"] >= 400)
            await send(msg)

        await self.app(scope, receive, _send)
