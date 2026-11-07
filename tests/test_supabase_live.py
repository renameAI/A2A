"""Supabase Realtime·Storage 실연결 검증 — 로컬 Supabase 스택(`supabase start`)에서.

목·스텁 없이 실제 Realtime 서버(Phoenix 웹소켓)와 Storage API에 붙는다. 환경:
  SUPA_API_URL, SUPA_ANON_KEY, SUPA_SERVICE_ROLE_KEY, SUPA_JWT_SECRET  (supabase status -o env)
  CHAT_TEST_PG_A / CHAT_TEST_PG_B                                      (셀 Postgres — 종단 시험용)
그리고 app/chat/sql/realtime_policy.sql 이 Supabase DB에 적용돼 있어야 한다.
"""
import asyncio
import json
import os
import time
import uuid

import pytest

from app.chat import realtime

API = os.environ.get("SUPA_API_URL")
ANON = os.environ.get("SUPA_ANON_KEY")
SERVICE = os.environ.get("SUPA_SERVICE_ROLE_KEY")
SECRET = os.environ.get("SUPA_JWT_SECRET")
pytestmark = pytest.mark.skipif(not (API and ANON and SERVICE and SECRET),
                                reason="로컬 Supabase 미기동 — supabase start 후 SUPA_* 설정")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", API or "")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", SERVICE or "")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET or "")


class Sub:
    """Realtime 웹소켓 구독자 — Phoenix 프로토콜을 직접 말한다(클라이언트 라이브러리 없이)."""

    def __init__(self):
        self.ws = None
        self.got: list[dict] = []
        self.ref = 0
        self.task = None

    async def connect(self, retries: int = 8):
        """429(연결 속도 제한)면 지터를 둔 지수 백오프로 다시 — 장애 후 일제 재접속이
        같은 벽에 부딪히므로 실제 클라이언트도 이렇게 해야 한다(실측)."""
        import random
        import websockets
        url = API.replace("http", "ws") + f"/realtime/v1/websocket?apikey={ANON}&vsn=1.0.0"
        kw = {}
        direct = os.environ.get("LIVE_RT_DIRECT")    # 로컬 Kong(512 연결 상한)을 우회
        if direct:
            h, prt = direct.split(":")
            url = f"ws://realtime-dev.local:{prt}/socket/websocket?apikey={ANON}&vsn=1.0.0"
            kw = {"host": h, "port": int(prt)}       # Host 헤더는 테넌트(realtime-dev)로
        delay = 0.2
        for i in range(retries):
            try:
                self.ws = await websockets.connect(url, max_size=2**22, **kw)
                return
            except websockets.exceptions.InvalidStatus as e:
                if e.response.status_code != 429 or i == retries - 1:
                    raise
                await asyncio.sleep(delay + random.random() * delay)
                delay = min(delay * 2, 5)

    async def join(self, topic: str, token: str, timeout=5.0) -> dict:
        self.ref += 1
        ref = str(self.ref)
        await self.ws.send(json.dumps({
            "topic": f"realtime:{topic}", "event": "phx_join", "ref": ref, "join_ref": ref,
            "payload": {"config": {"broadcast": {"self": False, "ack": False},
                                   "presence": {"key": ""}, "postgres_changes": [],
                                   "private": True},
                        "access_token": token}}))
        end = time.time() + timeout
        while time.time() < end:
            m = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=end - time.time()))
            if m.get("event") == "phx_reply" and m.get("ref") == ref:
                return m["payload"]
            if m.get("event") == "broadcast":
                self.got.append(m)
        raise TimeoutError("join 응답 없음")

    async def send_broadcast(self, topic: str, payload: dict):
        self.ref += 1
        await self.ws.send(json.dumps({"topic": f"realtime:{topic}", "event": "broadcast",
                                       "ref": str(self.ref), "join_ref": "1",
                                       "payload": {"type": "broadcast", "event": "message",
                                                   "payload": payload}}))

    def listen(self):
        async def run():
            try:
                async for raw in self.ws:
                    m = json.loads(raw)
                    if m.get("event") == "broadcast":
                        m["_rx"] = time.time()
                        self.got.append(m)
            except Exception:  # noqa: BLE001
                pass
        self.task = asyncio.create_task(run())

    async def close(self):
        if self.task:
            self.task.cancel()
        await self.ws.close()


async def join_refused(sub, topic, token) -> bool:
    try:
        return (await sub.join(topic, token))["status"] == "error"
    except Exception:  # noqa: BLE001 — 응답 없이 끊는 거부도 거부
        return True


def _publish(topic: str, payload: dict):
    import httpx
    r = httpx.post(f"{API}/realtime/v1/api/broadcast",
                   headers={"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}"},
                   json={"messages": [{"topic": topic, "event": "message",
                                       "payload": payload, "private": True}]})
    assert r.status_code == 202, r.text


def _topic():
    return f"t:{uuid.uuid4()}:c:{int(time.time()*1000) % 100000}"


def run(coro):
    return asyncio.run(coro)


# ── Realtime 인가 ───────────────────────────────────────────────────

def test_token_for_topic_receives_broadcast():
    async def go():
        tp = _topic()
        s = Sub(); await s.connect()
        tok = realtime.topic_token("u1", [tp])["token"]
        rep = await s.join(tp, tok)
        assert rep["status"] == "ok", rep
        s.listen()
        await asyncio.sleep(0.3)
        _publish(tp, {"type": "message", "seq": 7})
        for _ in range(50):
            if s.got:
                break
            await asyncio.sleep(0.05)
        await s.close()
        assert s.got and s.got[0]["payload"]["payload"]["seq"] == 7
    run(go())


@pytest.mark.parametrize("case", ["other_topic", "anon", "expired", "forged"])
def test_join_is_refused(case):
    async def go():
        tp, other = _topic(), _topic()
        tok = {
            "other_topic": lambda: realtime.topic_token("u1", [other])["token"],
            "anon": lambda: ANON,
            "expired": lambda: realtime.topic_token("u1", [tp], ttl=-60)["token"],
            "forged": lambda: realtime.sign({"sub": "u1", "role": "authenticated",
                                             "aud": "authenticated", "exp": int(time.time()) + 300,
                                             "chat_topics": [tp]}, "x" * 40),
        }[case]()
        s = Sub(); await s.connect()
        try:
            rep = await s.join(tp, tok)
        except Exception as e:  # noqa: BLE001 — 서버가 소켓을 끊는 거부도 거부다
            rep = {"status": "error", "response": {"reason": f"{type(e).__name__}"}}
        await s.close()
        assert rep["status"] == "error", rep
    run(go())


def test_refused_subscriber_receives_nothing():
    """거부된 구독자에게는 그 토픽 이벤트가 오지 않는다(정책이 실제 배달을 막는다)."""
    async def go():
        tp, other = _topic(), _topic()
        s = Sub(); await s.connect()
        try:
            rep = await s.join(tp, realtime.topic_token("u1", [other])["token"])
            assert rep["status"] == "error", rep
        except TimeoutError:
            pass                       # 응답 없이 끊는 거부도 거부다 — 아래에서 배달 여부를 본다
        s.listen()
        await asyncio.sleep(0.3)
        _publish(tp, {"type": "message", "seq": 1})
        await asyncio.sleep(1.0)
        await s.close()
        assert s.got == []
    run(go())


def test_clients_cannot_broadcast():
    """구독 권한이 있어도 클라이언트 송신(INSERT)은 정책이 없어 다른 구독자에게 닿지 않는다."""
    async def go():
        tp = _topic()
        tok = realtime.topic_token("u1", [tp])["token"]
        a, b = Sub(), Sub()
        await a.connect(); await b.connect()
        assert (await a.join(tp, tok))["status"] == "ok"
        assert (await b.join(tp, tok))["status"] == "ok"
        b.listen()
        await a.send_broadcast(tp, {"type": "message", "seq": 999, "forged": True})
        await asyncio.sleep(1.0)
        await a.close(); await b.close()
        assert b.got == []
    run(go())


# ── 종단: 우리 서비스 → Supabase Realtime → 구독자 ─────────────────────

PG_A = os.environ.get("CHAT_TEST_PG_A")
PG_B = os.environ.get("CHAT_TEST_PG_B")


@pytest.fixture(scope="module")
def plat():
    if not (PG_A and PG_B):
        pytest.skip("CHAT_TEST_PG_A/B 필요")
    from tests.test_chat_platform import _reset
    from app.chat import db, events, provision
    _reset(PG_A, ["s_control", "s_cell_a"])
    _reset(PG_B, ["s_cell_b"])
    os.environ.update({"CHAT_CONTROL_DSN": f"{PG_A}/s_control",
                       "S_CELL_A": f"{PG_A}/s_cell_a", "S_CELL_B": f"{PG_B}/s_cell_b",
                       "CHAT_FANOUT": "supabase", "SUPABASE_URL": API,
                       "SUPABASE_SERVICE_KEY": SERVICE})
    db.reset_registry(); events.reset()
    provision.init_control()
    provision.register_cell("a", "S_CELL_A"); provision.register_cell("b", "S_CELL_B")
    t = provision.create_tenant("rtco", "RT Co", owner_uid="own", owner_email="o@rt.co")
    from app.chat import service as svc
    svc.add_member(t, "own", "alice", "alice@rt.co")
    svc.add_member(t, "own", "bob", "bob@rt.co")
    yield t
    os.environ["CHAT_FANOUT"] = "memory"
    events.reset(); db.reset_registry()


def test_end_to_end_service_publish_and_gap_fill(plat):
    from app.chat import events, service as svc
    t = plat
    priv = svc.create_channel(t, "alice", "rt-secret", visibility="private")
    gen = next(c for c in svc.list_channels(t, "alice") if c["name"] == "general")

    async def go():
        tok_a = realtime.topic_token("alice", [events.topic(t.id, i)
                                               for i in svc.visible_channel_ids(t, "alice")])
        tok_b = realtime.topic_token("bob", [events.topic(t.id, i)
                                             for i in svc.visible_channel_ids(t, "bob")])
        assert events.topic(t.id, priv["id"]) in tok_a["topics"]
        assert events.topic(t.id, priv["id"]) not in tok_b["topics"]      # 판정이 토큰에 반영
        a, b = Sub(), Sub()
        await a.connect(); await b.connect()
        assert (await a.join(events.topic(t.id, gen["id"]), tok_a["token"]))["status"] == "ok"
        assert (await a.join(events.topic(t.id, priv["id"]), tok_a["token"]))["status"] == "ok"
        assert await join_refused(b, events.topic(t.id, priv["id"]), tok_b["token"])
        a.listen(); b.listen()
        await asyncio.sleep(0.3)
        posted = [svc.post_message(t, "alice", gen["id"], f"rt {i}")["seq"] for i in range(5)]
        svc.post_message(t, "alice", priv["id"], "기밀")
        for _ in range(100):
            if len(a.got) >= 6:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)
        await a.close(); await b.close()
        got = sorted(m["payload"]["payload"]["seq"] for m in a.got
                     if m["payload"]["payload"]["channel_id"] == gen["id"])
        # 알림은 합쳐질 수 있다(같은 창의 여러 게시 → 마지막 seq 하나). 기준은
        # "마지막 게시를 알았는가"와 "메우면 DB와 같은가"다.
        assert got and got[-1] == posted[-1]
        assert b.got == []                                    # 비공개 채널 이벤트가 bob에게 안 감
        page = svc.list_messages(t, "alice", gen["id"], after=posted[0] - 1)
        assert [m["seq"] for m in page["messages"]] == posted
    run(go())


def test_realtime_load_delivery(plat):
    """구독 200개(토픽 20개 × 10명) · 서비스 게시 초당 100건 × 15초 — 유실·지연 측정."""
    from app.chat import events, service as svc
    t = plat
    n_topics = int(os.environ.get("LIVE_TOPICS", "20"))
    per = int(os.environ.get("LIVE_SUBS_PER_TOPIC", "10"))
    rate, dur = int(os.environ.get("LIVE_RATE", "100")), int(os.environ.get("LIVE_DUR", "15"))
    tag = uuid.uuid4().hex[:6]
    chans = [svc.create_channel(t, "own", f"load-{tag}-{i}") for i in range(n_topics)]
    tok = realtime.topic_token("own", [events.topic(t.id, c["id"]) for c in chans])["token"]

    async def go():
        subs = []
        pace = float(os.environ.get("LIVE_CONNECT_PACE", "0"))
        for c in chans:
            for _ in range(per):
                if pace:
                    await asyncio.sleep(pace)
                s = Sub(); await s.connect()
                assert (await s.join(events.topic(t.id, c["id"]), tok))["status"] == "ok"
                s.listen(); s.cid = c["id"]
                subs.append(s)
        await asyncio.sleep(0.5)
        loop = asyncio.get_running_loop()
        sent: dict[int, list] = {c["id"]: [] for c in chans}   # (seq, 커밋 확인 시각)
        pending = []
        t0 = time.perf_counter()
        for i in range(rate * dur):
            target = t0 + i / rate
            d = target - time.perf_counter()
            if d > 0:
                await asyncio.sleep(d)
            c = chans[i % len(chans)]
            pending.append((c["id"], loop.run_in_executor(
                None, svc.post_message, t, "own", c["id"], f"L{i}")))
        for cid, fut in pending:
            r = await fut
            sent[cid].append((r["seq"], r["_acked"]))
        await asyncio.sleep(float(os.environ.get("LIVE_SETTLE", "5")))
        lat, missing, expected = [], 0, 0
        for s in subs:
            notes = sorted((m["_rx"], m["payload"]["payload"]["seq"]) for m in s.got
                           if m["payload"]["payload"]["channel_id"] == s.cid)
            for seq, at in sent[s.cid]:
                expected += 1
                # 이 게시를 '알게 된' 첫 알림: seq 이상을 알린 첫 수신
                hit = next((rx for rx, sq in notes if sq >= seq), None)
                if hit is None:
                    missing += 1
                else:
                    lat.append(max(0.0, (hit - at) * 1000))
            await s.close()
        lat.sort()
        rep = {"subs": len(subs), "posts": rate * dur, "expected": expected, "missing": missing,
               "learn_p50": round(lat[len(lat)//2], 1), "learn_p95": round(lat[int(len(lat)*.95)], 1),
               "learn_p99": round(lat[int(len(lat)*.99)], 1),
               "stats": {k: v for k, v in events.stats.items()
                         if k in ("sent", "retries", "dropped", "publish_errors",
                                  "coalesced_in", "coalesced_out")}}
        print("\nREALTIME-LOAD", json.dumps(rep))
        assert missing == 0, rep
    run(go())


# ── Storage ────────────────────────────────────────────────────────

def test_storage_archive_roundtrip_and_purge(plat, monkeypatch):
    from datetime import datetime, timezone
    from app.chat import archive, service as svc
    from app.chat.db import registry, tenant_admin_tx
    monkeypatch.setenv("CHAT_ARCHIVE_STORE", "supabase")
    monkeypatch.setenv("CHAT_ARCHIVE_BUCKET", f"chat-archive-test")
    st = archive.store()
    st.ensure_bucket()
    st.ensure_bucket()                                   # 두 번째도 오류 없이
    t = plat
    ch = svc.create_channel(t, "own", "st-old")
    old = datetime.now(timezone.utc).replace(day=5)
    y, m = old.year, old.month - 15
    while m <= 0:
        m += 12; y -= 1
    old = old.replace(year=y, month=m)
    with tenant_admin_tx(t) as conn:
        conn.execute("select create_month(%s)", (old.date(),))
        for i in range(1, 21):
            conn.execute("insert into messages (channel_id, seq, author, body, created_at) "
                         "values (%s, %s, 'own', %s, %s)", (ch["id"], i, f"s{i}", old))
        conn.execute("update channels set last_seq = 20, last_top_seq = 20 where id = %s", (ch["id"],))
    res = [r for r in archive.archive_tenant(t) if r.get("rows")]
    assert res and res[0]["rows"] == 20
    with tenant_admin_tx(t) as conn:
        key = conn.execute("select object_key from archive_index").fetchone()["object_key"]
    import httpx
    anon = httpx.get(f"{API}/storage/v1/object/chat-archive-test/{key}",
                     headers={"apikey": ANON, "Authorization": f"Bearer {ANON}"})
    assert anon.status_code in (400, 401, 403, 404)       # 비공개 버킷 — 익명은 못 읽는다
    page = svc.list_messages(t, "own", ch["id"], limit=50)
    assert [m["body"] for m in page["messages"]] == [f"s{i}" for i in range(1, 21)]
    assert all(m["archived"] for m in page["messages"])
    with registry().control_tx() as c:
        c.execute("update tenants set archive_purge_months = 12 where slug = 'rtco'")
    out = archive.purge_expired()
    assert out and out[0]["purged"]
    with pytest.raises(Exception):
        st.get(key)                                      # 객체가 실제로 지워졌다
