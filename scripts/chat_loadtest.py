"""메시징 플랫폼 부하 시험 — 숫자로 말한다.

  setup: 테넌트 N개(기본 100)를 셀들에 프로비저닝하고, 테넌트마다 사용자 U명
         (기본 10)과 채널 2개를 만든다. 허용 목록 파일을 쓴다.
  run:   사용자 전원이 SSE로 구독한 상태에서 초당 R건(기본 200)을 D초(기본 60)
         게시하고 측정한다:
           - 게시 지연 p50/p95/p99, 상태 코드 분포
           - 전파 지연(서버 발행 시각 → 클라이언트 수신) p50/p95/p99
           - 전파 완결성: 각 구독자가 받아야 할 이벤트 중 못 받은 수
           - 정합성: 채널마다 DB의 seq가 1..last_seq로 빈틈없는지, 성공한 게시 수와 같은지

  python scripts/chat_loadtest.py setup --tenants 100 --users 10
  python scripts/chat_loadtest.py run --base http://127.0.0.1:8500 --rate 200 --duration 60
"""
import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STATE = Path(os.environ.get("CHAT_LT_STATE", "/tmp/chat_loadtest_state.json"))


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p / 100))], 1)


def setup(a):
    from app.chat import provision
    from app.chat.db import tenant_admin_tx
    provision.init_control()
    for spec in a.cells.split(","):
        cid, env = spec.split("=")
        provision.register_cell(cid, env, max_tenants=a.max_per_cell)
    t0 = time.time()
    tenants, users = [], []
    per_cell = defaultdict(int)
    for i in range(a.tenants):
        slug = f"lt{i:03d}"
        owner = f"{slug}_u0"
        t = provision.create_tenant(slug, f"Load {i}", owner_uid=owner,
                                    owner_email=f"{owner}@lt.test")
        per_cell[t.cell_id] += 1
        us = [owner]
        for k in range(1, a.users):
            uid = f"{slug}_u{k}"
            provision._add_member_admin(t, uid, f"{uid}@lt.test", "member", {})
            us.append(uid)
        with tenant_admin_tx(t) as conn:
            ch = conn.execute("insert into channels (name, created_by) values ('ops', %s) "
                              "returning id", (owner,)).fetchone()["id"]
            conn.execute("insert into channel_members (channel_id, user_id) "
                         "select %s, user_id from members", (ch,))
            ids = [r["id"] for r in conn.execute(
                "select id from channels where kind = 'channel' order by id").fetchall()]
        tenants.append({"slug": slug, "id": t.id, "channels": ids, "users": us})
        users += us
    took = time.time() - t0
    STATE.write_text(json.dumps({"tenants": tenants}))
    Path(a.allow_file).write_text(",".join(users))
    print(json.dumps({"tenants": len(tenants), "users": len(users),
                      "provision_sec": round(took, 1),
                      "per_tenant_ms": round(took / len(tenants) * 1000),
                      "per_cell": per_cell}, ensure_ascii=False))


async def run(a):
    import httpx
    st = json.loads(STATE.read_text())
    tenants = st["tenants"]
    limits = httpx.Limits(max_connections=5000, max_keepalive_connections=5000)
    sse_client = httpx.AsyncClient(base_url=a.base, timeout=None, limits=limits)
    post_client = httpx.AsyncClient(base_url=a.base, timeout=30, limits=limits)

    # 구독자별 수신 기록: (tenant, channel) -> set(seq)
    received = defaultdict(lambda: defaultdict(set))
    deliver_ms = []
    ready = asyncio.Event()
    n_ready = 0
    subscribers = [(t, u) for t in tenants for u in t["users"]][: a.subscribers]

    async def subscribe(t, uid):
        nonlocal n_ready
        key = (t["slug"], uid)
        while True:
            try:
                async with sse_client.stream("GET", f"/chat/v1/t/{t['slug']}/stream",
                                             headers={"X-Dev-User": uid}) as r:
                    ev = None
                    async for line in r.aiter_lines():
                        if line.startswith("event: "):
                            ev = line[7:]
                        elif line.startswith("data: "):
                            d = json.loads(line[6:])
                            if ev == "ready":
                                n_ready += 1
                                if n_ready >= len(subscribers):
                                    ready.set()
                            elif ev == "message":
                                received[key][d["channel_id"]].add(d["seq"])
                                deliver_ms.append((time.time() - d["ts"]) * 1000)
                            elif ev == "reconnect":
                                break
            except (httpx.HTTPError, asyncio.CancelledError) as e:
                if isinstance(e, asyncio.CancelledError):
                    return
                await asyncio.sleep(0.5)

    print(f"opening {len(subscribers)} SSE streams…", flush=True)
    subs = [asyncio.create_task(subscribe(t, u)) for t, u in subscribers]
    t_open = time.time()
    try:
        if subscribers:
            await asyncio.wait_for(ready.wait(), timeout=120)
    except asyncio.TimeoutError:
        print(f"only {n_ready}/{len(subscribers)} streams ready", flush=True)
    open_sec = time.time() - t_open

    # 기준선 — 이전 실행·수동 게시가 남긴 메시지를 이번 측정에서 뺀다
    from app.chat.db import registry, tenant_admin_tx
    base = {}
    for t in tenants:
        with tenant_admin_tx(registry().tenant(t["slug"])) as conn:
            for ch in t["channels"]:
                base[(t["slug"], ch)] = conn.execute(
                    "select count(*) as n from messages where channel_id = %s", (ch,)).fetchone()["n"]

    post_ms, codes = [], defaultdict(int)
    posted = defaultdict(set)          # (slug, channel) -> seq (성공·비중복)
    tasks = []

    async def one(t, uid, ch, i):
        t0 = time.perf_counter()
        w0 = time.time()
        try:
            marks = {}

            async def trace(name, info):  # httpcore 연결 단위 사건 — 새 TCP 연결의 connect 시간
                if name == "connection.connect_tcp.started":
                    marks["c0"] = time.perf_counter()
                elif name == "connection.connect_tcp.complete":
                    connects.append((time.perf_counter() - marks["c0"]) * 1000)
                elif name == "http11.send_request_headers.started":
                    marks["sent"] = time.time()      # 요청이 실제로 소켓에 쓰이기 시작한 시각
            r = await post_client.post(f"/chat/v1/t/{t['slug']}/channels/{ch}/messages",
                                       extensions={"trace": trace},
                                       headers={"X-Dev-User": uid},
                                       json={"body": f"load {i} @{t['users'][0]}",
                                             "client_msg_id": f"lt{i:010d}x{random.randrange(1<<30):x}"})
            codes[r.status_code] += 1
            post_ms.append((time.perf_counter() - t0) * 1000)
            if "x-server-recv" in r.headers:
                # 구간 분해: 보냄→서버 수신 / 서버 처리 / 서버 완료→받음 (같은 기계의 벽시계)
                recv = float(r.headers["x-server-recv"])
                srv = float(r.headers["x-server-ms"]) / 1000
                seg_pre.append((recv - w0) * 1000)
                (pre_new if "c0" in marks else pre_reuse).append((recv - w0) * 1000)
                if "sent" in marks:
                    in_client.append((marks["sent"] - w0) * 1000)     # 클라이언트 안에서 기다림
                    on_wire.append((recv - marks["sent"]) * 1000)     # 소켓 → 서버 앱 도달
                seg_post.append((time.time() - (recv + srv)) * 1000)
            if "x-server-ms" in r.headers:
                server_ms.append(float(r.headers["x-server-ms"]))
                by_worker[r.headers.get("x-worker", "?")].append(
                    (post_ms[-1], float(r.headers["x-server-ms"])))
            timeline[int((t0 - start) // 5)].append(post_ms[-1])
            if r.status_code == 200 and not r.json().get("duplicate"):
                posted[(t["slug"], ch)].add(r.json()["seq"])
        except httpx.HTTPError as e:
            codes[type(e).__name__] += 1

    print(f"posting {a.rate}/s for {a.duration}s…", flush=True)
    seg_pre, seg_post = [], []
    connects = []                      # 새 연결마다 TCP connect 소요(ms)
    pre_new, pre_reuse = [], []        # '보냄→서버 수신'을 새 연결/재사용 연결로 나눠
    in_client, on_wire = [], []        # 그 구간을 다시 '클라이언트 안' / '전송 후 서버 앱까지'로
    client_lag = []

    async def lag_probe():                # 부하 생성기 자신의 이벤트 루프 지연
        while True:
            t_ = time.perf_counter()
            await asyncio.sleep(0.01)
            client_lag.append((time.perf_counter() - t_ - 0.01) * 1000)
    lag_task = asyncio.create_task(lag_probe())
    by_worker = defaultdict(list)      # 워커별 (클라이언트 지연, 서버 처리) — 꼬리가 어느 워커에 몰리나
    server_ms = []                     # 서버 처리 시간 — 클라이언트 지연과 비교해 병목 위치를 가른다
    timeline = defaultdict(list)       # 5초 구간별 게시 지연 — 꼬리가 주기적인지 본다
    start = time.perf_counter()
    total = int(a.rate * a.duration)
    if total == 0:                      # 구독 전용 프로세스 — 게시는 다른 프로세스가 한다
        await asyncio.sleep(a.duration)
    for i in range(total):
        target = start + i / a.rate
        delay = target - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        t = random.choice(tenants)
        uid = random.choice(t["users"])
        ch = random.choice(t["channels"])
        tasks.append(asyncio.create_task(one(t, uid, ch, i)))
    await asyncio.gather(*tasks)
    achieved = total / max(1e-9, time.perf_counter() - start)
    await asyncio.sleep(a.settle)
    for s in subs:
        s.cancel()
    await asyncio.gather(*subs, return_exceptions=True)

    # 전파 완결성 — 구독자는 자기 테넌트의 모든 채널을 구독했다
    expected = missing = 0
    for t, uid in subscribers:
        for ch in t["channels"]:
            want = posted[(t["slug"], ch)]
            got = received[(t["slug"], uid)][ch]
            expected += len(want)
            missing += len(want - got)

    # 정합성 — DB에서 직접 센다
    gaps = mismatch = 0
    for t in tenants:
        tn = registry().tenant(t["slug"])
        with tenant_admin_tx(tn) as conn:
            for ch in t["channels"]:
                r = conn.execute("select count(*) as n, coalesce(max(seq),0) as mx, "
                                 "(select last_seq from channels where id = %s) as last "
                                 "from messages where channel_id = %s", (ch, ch)).fetchone()
                if r["n"] != r["mx"] or r["mx"] != r["last"]:
                    gaps += 1
                if r["n"] - base[(t["slug"], ch)] != len(posted[(t["slug"], ch)]):
                    mismatch += 1
    await sse_client.aclose()
    await post_client.aclose()
    lag_task.cancel()
    worst_lag = max(client_lag) if client_lag else 0
    if worst_lag > 100:
        # 실측 교훈: 꼬리 지연의 대부분이 부하 생성기 자신이었다(호스트 CPU가 모자랄 때
        # 단일 프로세스 게시기 안에서 요청이 쌓였다). 이 경고가 뜨면 측정을 믿지 말고
        # 게시기를 여러 프로세스로 나누거나(POSTERS) 다른 기계에서 돌린다.
        print(f"⚠ 부하 생성기 이벤트 루프가 최대 {worst_lag:.0f}ms 밀렸습니다 — "
              f"지연 꼬리가 측정 도구 쪽일 수 있습니다 (in_client 구간 확인)", file=sys.stderr)
    print(json.dumps({
        "subscribers": len(subscribers), "streams_ready": n_ready,
        "stream_open_sec": round(open_sec, 1),
        "target_rate": a.rate, "achieved_rate": round(achieved, 1), "posts": total,
        "status": dict(codes),
        "post_ms": {"p50": pct(post_ms, 50), "p95": pct(post_ms, 95), "p99": pct(post_ms, 99),
                    "max": round(max(post_ms), 1) if post_ms else None},
        "server_ms": {"p50": pct(server_ms, 50), "p95": pct(server_ms, 95),
                      "p99": pct(server_ms, 99), "max": round(max(server_ms), 1) if server_ms else None},
        "deliveries": len(deliver_ms),
        "deliver_ms": {"p50": pct(deliver_ms, 50), "p95": pct(deliver_ms, 95),
                       "p99": pct(deliver_ms, 99)},
        "delivery_expected": expected, "delivery_missing": missing,
        "channels_with_seq_gaps": gaps, "channels_count_mismatch": mismatch,
        "p95_by_5s": [pct(timeline[k], 95) for k in sorted(timeline)],
        "segments_ms": {"send_to_server_recv": {"p50": pct(seg_pre, 50), "p95": pct(seg_pre, 95),
                                                "p99": pct(seg_pre, 99)},
                        "server_done_to_client": {"p50": pct(seg_post, 50), "p95": pct(seg_post, 95),
                                                  "p99": pct(seg_post, 99)}},
        "pre_split_ms": {"in_client": {"p50": pct(in_client, 50), "p99": pct(in_client, 99)},
                         "socket_to_app": {"p50": pct(on_wire, 50), "p99": pct(on_wire, 99)}},
        "new_connections": len(connects),
        "pre_by_conn": {"new": {"n": len(pre_new), "p50": pct(pre_new, 50), "p99": pct(pre_new, 99),
                                "max": round(max(pre_new), 1) if pre_new else None},
                        "reuse": {"n": len(pre_reuse), "p50": pct(pre_reuse, 50), "p99": pct(pre_reuse, 99),
                                  "max": round(max(pre_reuse), 1) if pre_reuse else None}},
        "connect_ms": {"p50": pct(connects, 50), "p99": pct(connects, 99),
                       "max": round(max(connects), 1) if connects else None,
                       "over_900ms": sum(1 for c in connects if c > 900),
                       "buckets_s": {b: sum(1 for c in connects if lo <= c < hi) for b, lo, hi in
                                     (("~1", 900, 2000), ("~3", 2000, 5000), ("~7", 5000, 11000),
                                      ("~15+", 11000, 1e9))}},
        "client_loop_lag_ms": {"p99": pct(client_lag, 99), "max": round(max(client_lag), 1) if client_lag else None},
        "by_worker": {w: {"n": len(v), "client_p99": pct([a for a, _ in v], 99),
                          "server_p99": pct([b for _, b in v], 99),
                          "over_100ms": sum(1 for a, _ in v if a > 100)}
                      for w, v in sorted(by_worker.items())},
    }, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--tenants", type=int, default=100)
    s.add_argument("--users", type=int, default=10)
    s.add_argument("--cells", default="a=CELL_A_DSN,b=CELL_B_DSN")
    s.add_argument("--max-per-cell", type=int, default=60)
    s.add_argument("--allow-file", default="/tmp/chat_lt_allow.txt")
    r = sub.add_parser("run")
    r.add_argument("--base", default="http://127.0.0.1:8500")
    r.add_argument("--rate", type=float, default=200)
    r.add_argument("--duration", type=float, default=60)
    r.add_argument("--subscribers", type=int, default=100000)
    r.add_argument("--settle", type=float, default=5)
    a = ap.parse_args()
    if a.cmd == "setup":
        setup(a)
    else:
        asyncio.run(run(a))


if __name__ == "__main__":
    main()
