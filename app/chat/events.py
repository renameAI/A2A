"""실시간 전파 — 커밋된 변경을 구독자에게 알린다.

이벤트는 '무엇이 바뀌었는가'의 알림일 뿐 본문을 싣지 않는다({type, seq}).
구독자는 받은 seq와 자기가 가진 마지막 seq를 비교해 공백이 있으면 커서 조회
(after=)로 채운다. 그래서 전파가 유실·중복·순서 뒤바뀜을 겪어도 화면은 결국
DB와 같아진다 — 전파 계층은 '빨리 알리는' 책임만 지고 정합성은 DB가 진다.
본문을 싣지 않는 또 하나의 이유: 전파 경로에서 권한 판정을 다시 할 필요가
없다. 본문은 조회 시점에 RLS를 통과해야만 나온다.

발행 백엔드(CHAT_FANOUT):
  memory    프로세스 안 — 테스트용
  redis     Redis pub/sub — 여러 프로세스·인스턴스 간 팬아웃(로컬·자체 게이트웨이)
  supabase  Supabase Realtime broadcast(HTTP) — 운영(Vercel 서버리스) 경로.
            브라우저가 Realtime에 직접 구독한다. 이 저장소에서 실연결 검증 안 됨.
  none      끔
발행은 게시 요청의 지연에 더해지면 안 된다 — redis는 ~0.2ms라 동기로, supabase는
HTTP 왕복이라 유한 큐 + 배경 스레드로 묶어 보낸다. 큐가 넘치면 버리고 센다
(버려도 클라이언트 공백 보충이 메운다 — 유실은 지연이지 데이터 손실이 아니다).
"""
import asyncio
import json
import logging
import os
import queue
import threading
import time
from collections import defaultdict
from typing import Callable

log = logging.getLogger("chat.events")

stats = defaultdict(int)          # published, dropped, publish_errors, delivered


def topic(tenant_id: str, channel_id: int) -> str:
    return f"t:{tenant_id}:c:{channel_id}"


# ── 발행자 ──────────────────────────────────────────────────────────

class _Memory:
    def __init__(self):
        self.subs: "dict[str, list[Callable[[dict], None]]]" = defaultdict(list)
        self.lock = threading.Lock()

    def publish(self, tp: str, payload: dict) -> None:
        with self.lock:
            hs = list(self.subs.get(tp, ()))
        for h in hs:
            try:
                h(payload)
            except Exception:  # noqa: BLE001
                stats["handler_errors"] += 1


class _Redis:
    def __init__(self, url: str):
        import redis
        self.r = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)

    def publish(self, tp: str, payload: dict) -> None:
        self.r.publish(tp, json.dumps(payload, default=str))


class _Supabase:
    """POST {url}/realtime/v1/api/broadcast — 묶고, 합치고, 속도를 지키고, 재시도한다.

    실측(로컬 Supabase, 무료 플랜과 같은 한도 200동시·초당 100이벤트): 묶어 보내기만
    했을 때 초당 100건 게시에서 429가 쏟아져 이벤트 1/3이 버려졌다. 그래서:
      - 합치기: WINDOW 동안 같은 토픽의 message 알림은 마지막 seq 하나로 접는다.
        알림은 본문이 없고 클라이언트가 after=로 메우므로 합쳐도 잃는 것이 없다.
      - 속도: 초당 MAX_EPS 이하로 보낸다(플랜 한도에 맞춰 설정).
      - 재시도: 429/5xx면 지수 백오프(Retry-After 존중)로 같은 묶음을 다시 보낸다.
        큐가 넘칠 때만 버리고 센다.
    """

    WINDOW = float(os.environ.get("CHAT_RT_WINDOW_SEC", "0.1"))
    MAX_EPS = float(os.environ.get("CHAT_RT_MAX_EPS", "80"))

    def __init__(self, url: str, key: str, cap: int = 50000):
        self.endpoint = url.rstrip("/") + "/realtime/v1/api/broadcast"
        self.key = key
        self.q: "queue.Queue[tuple[str, dict]]" = queue.Queue(maxsize=cap)
        threading.Thread(target=self._run, name="chat-broadcast", daemon=True).start()

    def publish(self, tp: str, payload: dict) -> None:
        try:
            self.q.put_nowait((tp, payload))
        except queue.Full:
            stats["dropped"] += 1

    @staticmethod
    def _coalesce(items: "list[tuple[str, dict]]") -> "list[tuple[str, dict]]":
        """단위 시험용 — 같은 토픽의 message 알림을 마지막 seq 하나로."""
        p = _Pending()
        for tp, pl in items:
            p.add(tp, pl)
        return p.take(len(items))

    def _run(self):
        """대기열은 '게시 수'가 아니라 '토픽 수'로 묶인다 — 토픽마다 마지막 알림 하나만
        기다린다. 실측: 고정 0.1초 창은 채널 20개에 흩어진 초당 100건을 거의 못 합쳐
        (1395→754) 보낼 수 있는 속도(80/초)를 넘었고, 대기열이 끝없이 길어져 '알게 되는
        시간' p95가 6.1초가 됐다. 이제 과부하에서 지연은 대략 활성 토픽 수 ÷ MAX_EPS로
        수렴한다(초과분은 합쳐질 뿐 쌓이지 않는다)."""
        import httpx
        client = httpx.Client(timeout=5)
        pending = _Pending()
        budget, last = self.MAX_EPS, time.monotonic()
        backoff_until = 0.0
        delay = 0.2
        while True:
            try:
                tp, pl = self.q.get(timeout=0.05 if pending else 1.0)
                pending.add(tp, pl)
                while True:
                    tp, pl = self.q.get_nowait()
                    pending.add(tp, pl)
            except queue.Empty:
                pass
            now = time.monotonic()
            budget = min(self.MAX_EPS, budget + (now - last) * self.MAX_EPS)
            last = now
            if not pending or now < backoff_until or budget < 1:
                continue
            chunk = pending.take(min(int(budget), 100))
            body = {"messages": [{"topic": t_, "event": p_.get("type", "change"),
                                  "payload": p_, "private": True} for t_, p_ in chunk]}
            try:
                r = client.post(self.endpoint, json=body,
                                headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"})
                code = r.status_code
            except Exception:  # noqa: BLE001
                code, r = 599, None
            if code < 300:
                budget -= len(chunk)
                stats["sent"] += len(chunk)
                delay = 0.2
            elif code in (429, 500, 502, 503, 504, 599):
                # 되돌려 놓는다 — 그 사이 더 새 알림이 왔으면 그쪽이 이긴다
                for t_, p_ in chunk:
                    pending.add(t_, p_, front=True)
                stats["retries"] += 1
                ra = r.headers.get("retry-after") if r is not None else None
                backoff_until = time.monotonic() + (float(ra) if ra and ra.isdigit() else delay)
                delay = min(delay * 2, 5.0)
            else:
                stats["publish_errors"] += len(chunk)


class _Pending:
    """토픽별 최신 알림. message는 seq 큰 쪽이 이기고, 그 밖의 종류(edit·delete·
    reaction·membership)는 합치지 않고 순서대로 보낸다. 오래 기다린 토픽부터 나간다."""

    def __init__(self):
        from collections import OrderedDict, deque
        self.msgs: "OrderedDict[str, dict]" = OrderedDict()
        self.others: "deque[tuple[str, dict]]" = deque()

    def __len__(self):
        return len(self.msgs) + len(self.others)

    def add(self, tp: str, p: dict, front: bool = False) -> None:
        stats["coalesced_in"] += 0 if front else 1
        if p.get("type") != "message":
            (self.others.appendleft if front else self.others.append)((tp, p))
            return
        cur = self.msgs.get(tp)
        if cur is None:
            self.msgs[tp] = {**p, "merged": p.get("merged", 1)}
            if front:
                self.msgs.move_to_end(tp, last=False)
        elif p["seq"] > cur["seq"]:
            self.msgs[tp] = {**p, "merged": cur.get("merged", 1) + p.get("merged", 1)}
        else:
            cur["merged"] = cur.get("merged", 1) + p.get("merged", 1)

    def take(self, n: int) -> "list[tuple[str, dict]]":
        out = []
        while self.others and len(out) < n:
            out.append(self.others.popleft())
        while self.msgs and len(out) < n:
            out.append(self.msgs.popitem(last=False))
        stats["coalesced_out"] += len(out)
        return out


_pub = None
_pub_lock = threading.Lock()


def _publisher():
    global _pub
    with _pub_lock:
        if _pub is None:
            kind = os.environ.get("CHAT_FANOUT", "memory").lower()
            if kind == "redis":
                _pub = _Redis(os.environ.get("CHAT_REDIS_URL", "redis://127.0.0.1:6379/0"))
            elif kind == "supabase":
                _pub = _Supabase(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
            elif kind == "none":
                _pub = None
                return None
            else:
                _pub = _Memory()
        return _pub


def reset() -> None:
    global _pub
    with _pub_lock:
        _pub = None


def publish(t, channel_id: int, event: dict) -> None:
    payload = {**event, "tenant": t.slug, "channel_id": channel_id, "ts": time.time()}
    p = _publisher()
    if p is None:
        return
    try:
        p.publish(topic(t.id, channel_id), payload)
        stats["published"] += 1
    except Exception as e:  # noqa: BLE001 — 전파 실패가 이미 커밋된 게시를 실패로 만들지 않는다
        stats["publish_errors"] += 1
        log.warning("publish failed: %s", e)


def subscribe(tenant_id: str, channel_id: int, handler: Callable[[dict], None]):
    """memory 백엔드 전용 동기 구독(테스트)."""
    p = _publisher()
    if not isinstance(p, _Memory):
        raise RuntimeError("동기 subscribe는 memory 백엔드에서만")
    tp = topic(tenant_id, channel_id)
    with p.lock:
        p.subs[tp].append(handler)

    def off():
        with p.lock:
            if handler in p.subs.get(tp, []):
                p.subs[tp].remove(handler)
    return off


# ── 구독 허브 (프로세스당 하나, SSE용) ─────────────────────────────────

class Hub:
    """프로세스당 Redis 연결 하나로 필요한 토픽만 구독하고, 로컬 SSE 연결들의
    asyncio 큐로 나눠 준다. 토픽은 참조 계수로 관리해 마지막 구독자가 떠나면
    Redis 구독도 끊는다 — 접속자 수가 Redis 연결 수가 되지 않는다."""

    def __init__(self):
        self.local: "dict[str, set[asyncio.Queue]]" = defaultdict(set)
        self.pubsub = None
        self.task: "asyncio.Task | None" = None
        self.lock = asyncio.Lock()
        self.mem_off: dict[str, Callable] = {}

    async def _ensure(self):
        if os.environ.get("CHAT_FANOUT", "memory").lower() != "redis" or self.pubsub:
            return
        import redis.asyncio as aioredis
        r = aioredis.Redis.from_url(os.environ.get("CHAT_REDIS_URL", "redis://127.0.0.1:6379/0"))
        self.pubsub = r.pubsub(ignore_subscribe_messages=True)
        await self.pubsub.subscribe("chat:__keepalive__")
        self.task = asyncio.create_task(self._pump())

    async def _pump(self):
        # listen()은 구독이 잠깐이라도 비면(마지막 SSE가 떠난 순간) 끝나 버린다 — 그 뒤 새 구독이 와도
        # 아무도 읽지 않아 실시간이 조용히 멈췄다. get_message로 계속 돈다.
        while True:
            try:
                msg = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            except Exception as e:  # noqa: BLE001 — 끊김은 다음 주기에 다시
                log.warning("pubsub read failed: %s", e)
                await asyncio.sleep(0.5)
                continue
            if not msg or msg.get("type") != "message":
                continue
            tp = msg["channel"].decode()
            data = json.loads(msg["data"])
            self._deliver(tp, data)

    def _deliver(self, tp: str, data: dict):
        for q in list(self.local.get(tp, ())):
            try:
                q.put_nowait(data)
                stats["delivered"] += 1
            except asyncio.QueueFull:
                stats["slow_consumer_drops"] += 1     # 느린 소비자는 공백 보충으로 따라온다

    async def join(self, tp: str, q: asyncio.Queue):
        async with self.lock:
            await self._ensure()
            first = not self.local[tp]
            self.local[tp].add(q)
            if first:
                if self.pubsub is not None:
                    await self.pubsub.subscribe(tp)
                elif isinstance(_publisher(), _Memory):
                    loop = asyncio.get_running_loop()
                    self.mem_off[tp] = subscribe_raw(
                        tp, lambda d, tp=tp: loop.call_soon_threadsafe(self._deliver, tp, d))

    async def leave(self, tp: str, q: asyncio.Queue):
        async with self.lock:
            self.local[tp].discard(q)
            if not self.local[tp]:
                del self.local[tp]
                if self.pubsub is not None:
                    await self.pubsub.unsubscribe(tp)
                elif tp in self.mem_off:
                    self.mem_off.pop(tp)()

    def topics(self) -> int:
        return len(self.local)


def subscribe_raw(tp: str, handler):
    p = _publisher()
    with p.lock:
        p.subs[tp].append(handler)

    def off():
        with p.lock:
            if handler in p.subs.get(tp, []):
                p.subs[tp].remove(handler)
    return off


_hub: "Hub | None" = None


def hub() -> Hub:
    global _hub
    if _hub is None:
        _hub = Hub()
    return _hub


# ── 진단: 이벤트 루프 지연 탐침 (CHAT_LOOP_PROBE=1) ─────────────────────
probe = {"sse": 0, "rechecks_inflight": 0}


async def loop_probe():
    """100ms 타이머가 실제로 얼마나 늦게 깨는지 5초마다 기록한다. 루프가 막히면
    이 값이 커진다 — 요청이 앱에 닿기 전에 기다리는 시간을 직접 본다."""
    import os as _os
    loop = asyncio.get_running_loop()
    worst, t_win = 0.0, loop.time()
    while True:
        t0 = loop.time()
        await asyncio.sleep(0.1)
        worst = max(worst, loop.time() - t0 - 0.1)
        if loop.time() - t_win >= 5:
            log.warning(json.dumps({"probe": _os.getpid(), "lag_ms": round(worst * 1000, 1),
                                    "sse": probe["sse"], "topics": hub().topics(),
                                    "rechecks": probe["rechecks_inflight"]}))
            worst, t_win = 0.0, loop.time()


def install_slow_callback_probe(threshold_ms: float = 20.0) -> None:
    """이벤트 루프를 threshold 이상 붙잡은 콜백을 그 정체와 함께 기록한다
    (CHAT_LOOP_PROBE=2). asyncio 디버그 모드는 코루틴 추적 비용이 커서 측정
    대상을 바꿔 버리므로, Handle._run에 타이머만 얹는다."""
    import asyncio.events as ae
    import os as _os
    import traceback
    orig = ae.Handle._run
    lim = threshold_ms / 1000

    def timed(self):
        t0 = time.perf_counter()
        orig(self)
        dt = time.perf_counter() - t0
        if dt > lim:
            cb = getattr(self, "_callback", None)
            name = getattr(cb, "__qualname__", repr(cb))[:160]
            coro = getattr(getattr(cb, "__self__", None), "get_coro", None)
            if coro:
                c = coro()
                name = f"task:{getattr(c, '__qualname__', c)}"
            log.warning(json.dumps({"slow_cb": round(dt * 1000, 1), "pid": _os.getpid(),
                                    "cb": name}))
    ae.Handle._run = timed
