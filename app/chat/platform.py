"""플랫폼 어드민 — 모든 테넌트·셀을 관장한다 (컨트롤 플레인).

권한: platform_admins 테이블.
  superadmin  모든 것 + 어드민 관리
  operator    테넌트 생성·정지·보존 설정, 본문 열람, 공지, 운영 채널
  viewer      목록·상태·메트릭·알림만 (본문 열람 불가)

본문 열람(정책: 전체 열람 가능):
  모든 열람은 (1) 컨트롤 플레인 platform_audit (2) 해당 테넌트 access_log 양쪽에
  남는다. 테넌트 관리자는 자기 audit.read 권한으로 access_log를 본다 — 어드민이
  언제 무엇을 봤는지 고객이 직접 확인할 수 있어야 '전체 열람'이 신뢰를 잃지 않는다.
  ※ 약관·개인정보 처리방침에 이 열람 가능성과 기록 방침을 고지해야 한다(법무 검토).
"""
import json
import time
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from ..errors import EngineError
from . import events, metrics, provision
from .db import Tenant, registry, tenant_admin_tx

ROLES = {"superadmin": 3, "operator": 2, "viewer": 1}

# 알림 규칙 — (종류, 심각도, 설명). 임계값은 중규모 목표에 맞췄다.
ERROR_RATE = 0.02            # 5분 오류율 2% 초과(요청 50건 이상일 때)
P95_MS = 500                 # 5분 게시 p95 500ms 초과
CELL_CONN_RATIO = 0.8        # 셀 연결 수가 max_connections의 80% 초과


def require(uid: str, need: str = "viewer") -> dict:
    with registry().control_tx() as c:
        a = c.execute("select user_id, email, role from platform_admins where user_id = %s",
                      (uid,)).fetchone()
    if a is None or ROLES[a["role"]] < ROLES[need]:
        raise EngineError(403, "forbidden", f"플랫폼 {need} 권한이 필요합니다")
    return a


def add_admin(actor: "str | None", uid: str, email: str, role: str) -> None:
    """actor=None은 부트스트랩(CLI)용 — 첫 superadmin을 만들 때만."""
    if role not in ROLES:
        raise EngineError(400, "invalid_input", "role은 superadmin|operator|viewer")
    if actor is not None:
        require(actor, "superadmin")
    with registry().control_tx() as c:
        c.execute("""insert into platform_admins (user_id, email, role) values (%s, %s, %s)
                     on conflict (user_id) do update set role = excluded.role, email = excluded.email""",
                  (uid, email, role))
        _audit(c, actor or "bootstrap", "admin.upsert", None, uid, detail={"role": role})


def _audit(c, actor, action, tenant_id, target, reason=None, detail=None):
    c.execute("""insert into platform_audit (actor, action, tenant_id, target, reason, detail)
                 values (%s, %s, %s, %s, %s, %s)""",
              (actor, action, tenant_id, target, reason, Jsonb(detail or {})))


# ── 테넌트·셀 ───────────────────────────────────────────────────────

def list_tenants(uid: str) -> list[dict]:
    require(uid)
    since = datetime.now(timezone.utc) - timedelta(minutes=15)
    with registry().control_tx() as c:
        rows = c.execute("""
            select t.id::text, t.slug, t.name, t.cell_id, t.plan, t.status, t.schema_version,
                   t.retention_hot_months, t.legal_hold, t.archive_purge_months, t.created_at,
                   (select count(*) from tenant_users tu where tu.tenant_id = t.id) as users,
                   coalesce(m.requests, 0) as req_15m, coalesce(m.errors, 0) as err_15m,
                   (select count(*) from alerts a where a.tenant_id = t.id and a.closed_at is null) as open_alerts
              from tenants t
              left join (select tenant_id, sum(requests)::bigint as requests, sum(errors)::bigint as errors
                           from metrics_minute where minute >= %s group by tenant_id) m
                     on m.tenant_id = t.id
             where t.status <> 'deleted'
             order by t.name""", (since,)).fetchall()
    return rows


def create_tenant(uid: str, slug: str, name: str, owner_uid: str, owner_email: str,
                  plan: str = "standard", cell: "str | None" = None) -> dict:
    require(uid, "operator")
    t = provision.create_tenant(slug, name, plan=plan, cell=cell,
                                owner_uid=owner_uid, owner_email=owner_email)
    with registry().control_tx() as c:
        _audit(c, uid, "tenant.create", t.id, slug, detail={"cell": t.cell_id, "plan": plan})
    return {"slug": t.slug, "cell_id": t.cell_id, "id": t.id}


def set_tenant(uid: str, slug: str, *, status: "str | None" = None,
               retention_hot_months: "int | None" = None, legal_hold: "bool | None" = None,
               archive_purge_months: "int | None" = None, reason: str = "",
               clear_archive_purge: bool = False) -> None:
    require(uid, "operator")
    if status is not None and status not in ("active", "suspended"):
        raise EngineError(400, "invalid_input", "status는 active|suspended")
    if status is not None:
        _need_reason(reason, "상태 변경(정지·재개)")
    sets, vals = [], []
    if clear_archive_purge:
        sets.append("archive_purge_months = null")
    for col, v in (("status", status), ("retention_hot_months", retention_hot_months),
                   ("legal_hold", legal_hold), ("archive_purge_months", archive_purge_months)):
        if v is not None:
            sets.append(f"{col} = %s")
            vals.append(v)
    if not sets:
        return
    with registry().control_tx() as c:
        row = c.execute(f"update tenants set {', '.join(sets)} where slug = %s "
                        f"and status <> 'deleted' returning id::text", (*vals, slug)).fetchone()
        if row is None:
            raise EngineError(404, "not_found", "테넌트 없음")
        _audit(c, uid, "tenant.update", row["id"], slug, reason,
               {"status": status, "retention_hot_months": retention_hot_months,
                "legal_hold": legal_hold, "archive_purge_months": archive_purge_months,
                "clear_archive_purge": clear_archive_purge})
    registry().forget(slug)


def list_cells(uid: str) -> list[dict]:
    """셀 상태 — 실제로 붙어서 잰다. 붙지 못하면 그 사실이 곧 상태다."""
    require(uid)
    with registry().control_tx() as c:
        cells = c.execute("""select c.*, (select count(*) from tenants t where t.cell_id = c.id
                                           and t.status <> 'deleted') as tenants
                               from cells c order by c.id""").fetchall()
    out = []
    for cell in cells:
        info = {**cell, "reachable": False}
        try:
            info.update(registry().cell_probe(cell["id"]), reachable=True)
        except Exception as e:  # noqa: BLE001
            info["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        info.pop("dsn_env", None)                  # 환경변수 이름도 화면에 내지 않는다
        out.append(info)
    return out


def set_cell(uid: str, cell_id: str, status: str) -> None:
    require(uid, "superadmin")
    if status not in ("open", "full", "draining", "down"):
        raise EngineError(400, "invalid_input", "status는 open|full|draining|down")
    with registry().control_tx() as c:
        if c.execute("update cells set status = %s where id = %s returning id",
                     (status, cell_id)).fetchone() is None:
            raise EngineError(404, "not_found", "셀 없음")
        _audit(c, uid, "cell.update", None, cell_id, detail={"status": status})


# ── 메트릭 ─────────────────────────────────────────────────────────

def _q(hist: list[int], q: float) -> "float | None":
    """JSON에 실을 분위수 — 맨 위 칸(>2500ms)은 inf라 JSON이 거부한다(실측: 2.5초 넘는
    요청이 하나만 있어도 메트릭 API가 500). 그 경우 null을 주고 over_max로 표시한다."""
    v = metrics.quantile(hist, q)
    return None if v == float("inf") else v


def tenant_metrics(uid: str, slug: "str | None", minutes: int = 60) -> dict:
    """분 단위 시계열 + 구간 요약. slug=None이면 플랫폼 전체."""
    require(uid)
    minutes = max(5, min(minutes, 24 * 60))
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    q = """select m.minute, m.route, sum(m.requests)::bigint as requests, sum(m.errors)::bigint as errors,
                  array_agg(m.lat_hist) as hists
             from metrics_minute m {join}
            where m.minute >= %s {filt}
            group by m.minute, m.route order by m.minute"""
    with registry().control_tx() as c:
        if slug:
            rows = c.execute(q.format(join="join tenants t on t.id = m.tenant_id",
                                      filt="and t.slug = %s"), (since, slug)).fetchall()
        else:
            rows = c.execute(q.format(join="", filt=""), (since,)).fetchall()
    series: dict[str, list] = {}
    total = {}
    for r in rows:
        hist = [sum(col) for col in zip(*r["hists"])]
        series.setdefault(r["route"], []).append({
            "minute": r["minute"], "requests": r["requests"], "errors": r["errors"],
            "p50": _q(hist, 0.5), "p95": _q(hist, 0.95), "p99": _q(hist, 0.99),
            "over_max": hist[-1]})
        t = total.setdefault(r["route"], {"requests": 0, "errors": 0, "hist": [0] * len(hist)})
        t["requests"] += r["requests"]
        t["errors"] += r["errors"]
        t["hist"] = [a + b for a, b in zip(t["hist"], hist)]
    summary = {k: {"requests": v["requests"], "errors": v["errors"],
                   "error_rate": round(v["errors"] / v["requests"], 4) if v["requests"] else 0,
                   "p50": _q(v["hist"], 0.5), "p95": _q(v["hist"], 0.95),
                   "p99": _q(v["hist"], 0.99), "over_max": v["hist"][-1]}
               for k, v in total.items()}
    return {"minutes": minutes, "summary": summary, "series": series}


def top_tenants(uid: str, minutes: int = 15, limit: int = 10) -> list[dict]:
    require(uid)
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    with registry().control_tx() as c:
        return c.execute("""select t.slug, t.name, t.cell_id, sum(m.requests)::bigint as requests,
                                   sum(m.errors)::bigint as errors
                              from metrics_minute m join tenants t on t.id = m.tenant_id
                             where m.minute >= %s group by t.slug, t.name, t.cell_id
                             order by requests desc limit %s""", (since, limit)).fetchall()


# ── 알림 ────────────────────────────────────────────────────────────

def _open(c, kind, severity, detail, tenant_id=None, cell_id=None) -> bool:
    r = c.execute("""insert into alerts (tenant_id, cell_id, kind, severity, detail)
                     values (%s, %s, %s, %s, %s) on conflict do nothing returning id""",
                  (tenant_id, cell_id, kind, severity, Jsonb(detail))).fetchone()
    return r is not None


def _close(c, kind, tenant_id=None, cell_id=None) -> bool:
    r = c.execute("""update alerts set closed_at = now()
                      where kind = %s and closed_at is null
                        and coalesce(tenant_id::text, '') = coalesce(%s::text, '')
                        and coalesce(cell_id, '') = coalesce(%s, '') returning id""",
                  (kind, tenant_id, cell_id)).fetchone()
    return r is not None


def evaluate_alerts() -> dict:
    """규칙 평가 — 주기 작업. 열린 알림은 한 번만 열리고(유일 색인), 조건이 풀리면
    닫힌다. 새로 열리거나 닫힌 것은 #incidents 운영 채널에 글로 남는다."""
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    opened, closed = [], []
    with registry().control_tx() as c:
        rows = c.execute("""select t.id::text as tid, t.slug, m.route, sum(m.requests)::bigint as req,
                                   sum(m.errors)::bigint as err, array_agg(m.lat_hist) as hists
                              from metrics_minute m join tenants t on t.id = m.tenant_id
                             where m.minute >= %s group by t.id, t.slug, m.route""",
                           (since,)).fetchall()
        seen_err, seen_lat = set(), set()
        for r in rows:
            hist = [sum(col) for col in zip(*r["hists"])]
            if r["req"] >= 50 and r["err"] / r["req"] > ERROR_RATE:
                seen_err.add(r["tid"])
                if _open(c, "error_rate", "warning",
                         {"slug": r["slug"], "route": r["route"], "rate": round(r["err"] / r["req"], 4)},
                         tenant_id=r["tid"]):
                    opened.append(f"{r['slug']}: 오류율 {r['err']}/{r['req']} ({r['route']})")
            if r["route"] == "post" and r["req"] >= 50:
                p95 = metrics.quantile(hist, 0.95)
                if p95 and p95 > P95_MS:
                    seen_lat.add(r["tid"])
                    if _open(c, "post_latency", "warning",
                             {"slug": r["slug"], "p95": None if p95 == float("inf") else p95,
                              "over_max": hist[-1]},
                             tenant_id=r["tid"]):
                        opened.append(f"{r['slug']}: 게시 p95 "
                                      f"{'>2500' if p95 == float('inf') else p95}ms")
        for a in c.execute("""select tenant_id::text as tid, kind from alerts
                               where closed_at is null and kind in ('error_rate','post_latency')""").fetchall():
            if (a["kind"] == "error_rate" and a["tid"] not in seen_err) or \
               (a["kind"] == "post_latency" and a["tid"] not in seen_lat):
                if _close(c, a["kind"], tenant_id=a["tid"]):
                    closed.append(f"{a['kind']} 해소 ({a['tid'][:8]})")
    for cell in list_cells_internal():
        with registry().control_tx() as c:
            if not cell.get("reachable"):
                if _open(c, "cell_unreachable", "critical", {"error": cell.get("error")},
                         cell_id=cell["id"]):
                    opened.append(f"셀 {cell['id']} 연결 불가 — {cell.get('error')}")
            else:
                if _close(c, "cell_unreachable", cell_id=cell["id"]):
                    closed.append(f"셀 {cell['id']} 복구")
                ratio = cell["conns"] / max(1, cell["max_conns"])
                if ratio > CELL_CONN_RATIO:
                    if _open(c, "cell_connections", "warning", {"ratio": round(ratio, 2)},
                             cell_id=cell["id"]):
                        opened.append(f"셀 {cell['id']} 연결 {cell['conns']}/{cell['max_conns']}")
                elif _close(c, "cell_connections", cell_id=cell["id"]):
                    closed.append(f"셀 {cell['id']} 연결 정상화")
                if cell["tenants"] >= cell["max_tenants"] and cell["status"] == "open":
                    _open(c, "cell_capacity", "info", {"tenants": cell["tenants"]}, cell_id=cell["id"])
    for line in opened:
        _ops_post_system("incidents", f"🔴 {line}")
    for line in closed:
        _ops_post_system("incidents", f"🟢 {line}")
    return {"opened": opened, "closed": closed}


def list_cells_internal() -> list[dict]:
    """알림 평가용 — 어드민 권한 확인 없이(주기 작업)."""
    with registry().control_tx() as c:
        cells = c.execute("""select c.*, (select count(*) from tenants t where t.cell_id = c.id
                                           and t.status <> 'deleted') as tenants
                               from cells c""").fetchall()
    out = []
    for cell in cells:
        info = dict(cell, reachable=False)
        try:
            info.update(registry().cell_probe(cell["id"]), reachable=True)
        except Exception as e:  # noqa: BLE001
            info["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        out.append(info)
    return out


def list_alerts(uid: str, open_only: bool = True) -> list[dict]:
    require(uid)
    with registry().control_tx() as c:
        return c.execute(f"""select a.*, t.slug from alerts a left join tenants t on t.id = a.tenant_id
                              {'where a.closed_at is null' if open_only else ''}
                              order by a.opened_at desc limit 200""").fetchall()


# ── 운영 채널 ───────────────────────────────────────────────────────

OPS_CHANNELS = ("ops", "incidents")


def _ops_channel_id(c, name: str) -> int:
    # 정해진 채널만 — 예전엔 조회 URL의 오타가 새 채널을 만들었다
    if name not in OPS_CHANNELS:
        raise EngineError(404, "not_found", f"운영 채널은 {', '.join(OPS_CHANNELS)}뿐입니다")
    row = c.execute("insert into ops_channels (name) values (%s) on conflict (name) do update "
                    "set name = excluded.name returning id", (name,)).fetchone()
    return row["id"]


def _ops_insert(c, name: str, author: str, body: str, kind: str) -> dict:
    cid = _ops_channel_id(c, name)
    seq = c.execute("update ops_channels set last_seq = last_seq + 1 where id = %s "
                    "returning last_seq", (cid,)).fetchone()["last_seq"]
    return c.execute("""insert into ops_messages (channel_id, seq, author, body, kind)
                        values (%s, %s, %s, %s, %s) returning *""",
                     (cid, seq, author, body, kind)).fetchone()


def _ops_post_system(name: str, body: str) -> None:
    with registry().control_tx() as c:
        _ops_insert(c, name, "system", body, "system")


def ops_channels(uid: str) -> list[dict]:
    require(uid, "operator")
    with registry().control_tx() as c:
        for n in OPS_CHANNELS:
            _ops_channel_id(c, n)
        return c.execute("select * from ops_channels order by name").fetchall()


def ops_post(uid: str, name: str, body: str) -> dict:
    a = require(uid, "operator")
    body = (body or "").strip()
    if not body or len(body) > 8000:
        raise EngineError(400, "invalid_input", "본문 길이 오류")
    with registry().control_tx() as c:
        return _ops_insert(c, name, a["email"], body, "text")


def ops_messages(uid: str, name: str, after: int = 0, limit: int = 100) -> list[dict]:
    require(uid, "operator")
    with registry().control_tx() as c:
        cid = _ops_channel_id(c, name)
        return c.execute("""select * from ops_messages where channel_id = %s and seq > %s
                            order by seq limit %s""", (cid, after, max(1, min(limit, 500)))).fetchall()


# ── 전체 공지 ───────────────────────────────────────────────────────

def announce(uid: str, body: str) -> dict:
    """모든 활성 테넌트의 #announcements에 게시. 셀이 여럿이라 분산 작업이다:
    테넌트별 전달 상태를 남기고, 재실행(retry_announcement)은 미전달 테넌트만 한다.
    같은 공지의 중복 게시는 테넌트 쪽 멱등 키(announce:<id>)가 막는다."""
    require(uid, "operator")
    body = (body or "").strip()
    if not body:
        raise EngineError(400, "invalid_input", "빈 공지")
    with registry().control_tx() as c:
        aid = c.execute("insert into announcements (author, body) values (%s, %s) returning id",
                        (uid, body)).fetchone()["id"]
        c.execute("""insert into announcement_delivery (announcement_id, tenant_id)
                     select %s, id from tenants where status = 'active'""", (aid,))
        _audit(c, uid, "announce", None, str(aid), detail={"chars": len(body)})
    return deliver_announcement(aid)


def retry_announcement(uid: str, aid: int) -> dict:
    require(uid, "operator")
    with registry().control_tx() as c:
        _audit(c, uid, "announce.retry", None, str(aid))
    return deliver_announcement(aid)


def deliver_announcement(aid: int) -> dict:
    with registry().control_tx() as c:
        a = c.execute("select body from announcements where id = %s", (aid,)).fetchone()
        todo = c.execute("""select t.slug from announcement_delivery d join tenants t on t.id = d.tenant_id
                             where d.announcement_id = %s and d.delivered_at is null
                               and t.status = 'active'""", (aid,)).fetchall()
    ok, failed = 0, []
    for r in todo:
        t = registry().tenant(r["slug"])
        try:
            with tenant_admin_tx(t) as conn:
                ch = conn.execute("select id from channels where kind = 'announcements' "
                                  "order by id limit 1").fetchone()
                seq = conn.execute("select post_system(%s, %s, %s) as s",
                                   (ch["id"], a["body"], f"announce:{aid}")).fetchone()["s"]
            with registry().control_tx() as c:
                c.execute("""update announcement_delivery set delivered_at = now(), error = null
                              where announcement_id = %s and tenant_id = %s""", (aid, t.id))
            if seq is not None:
                events.publish(t, ch["id"], {"type": "message", "seq": seq})
            ok += 1
        except Exception as e:  # noqa: BLE001 — 한 셀의 실패가 나머지 전달을 막지 않는다
            failed.append(r["slug"])
            with registry().control_tx() as c:
                c.execute("""update announcement_delivery set error = %s
                              where announcement_id = %s and tenant_id = %s""",
                          (f"{type(e).__name__}: {str(e)[:200]}", aid, t.id))
    return {"announcement_id": aid, "delivered": ok, "failed": failed}


# ── 본문 열람 (감사 필수) ───────────────────────────────────────────

def _need_reason(reason: str, what: str) -> str:
    r = (reason or "").strip()
    if len(r) < 2:
        raise EngineError(400, "invalid_input", f"{what}에는 사유가 필요합니다 — 감사 기록에 남습니다")
    return r


def _access(uid: str, t: Tenant, action: str, channel_id, reason: str, detail: dict) -> None:
    reason = _need_reason(reason, "본문 열람")
    with registry().control_tx() as c:
        _audit(c, uid, action, t.id, f"{t.slug}:{channel_id}", reason, detail)
    with tenant_admin_tx(t) as conn:
        conn.execute("""insert into access_log (actor, actor_kind, action, channel_id, reason, detail)
                        values (%s, 'platform_admin', %s, %s, %s, %s)""",
                     (uid, action, channel_id, reason, Jsonb(detail)))


def admin_channels(uid: str, slug: str, reason: str = "") -> list[dict]:
    require(uid, "operator")
    t = registry().tenant(slug)
    _access(uid, t, "content.list_channels", None, reason, {})
    with tenant_admin_tx(t) as conn:
        return conn.execute("""select id, name, visibility, kind, last_seq, last_message_at,
                                      archived_at, (select count(*) from channel_members cm
                                                     where cm.channel_id = channels.id) as members
                                 from channels order by name""").fetchall()


def admin_messages(uid: str, slug: str, channel_id: int, *, before: "int | None" = None,
                   limit: int = 100, reason: str = "") -> dict:
    require(uid, "operator")
    t = registry().tenant(slug)
    limit = max(1, min(limit, 200))
    _access(uid, t, "content.read", channel_id, reason,
            {"before": before, "limit": limit})
    with tenant_admin_tx(t) as conn:
        ch = conn.execute("select id, name, visibility from channels where id = %s",
                          (channel_id,)).fetchone()
        if ch is None:
            raise EngineError(404, "not_found", "채널 없음")
        params = [channel_id]
        cond = ""
        if before is not None:
            cond = "and seq < %s"
            params.append(before)
        params.append(limit)
        rows = conn.execute(f"""select seq, parent_seq, author, body, kind, created_at, edited_at,
                                       deleted_at is not null as deleted, reply_count
                                  from messages where channel_id = %s {cond}
                                 order by seq desc limit %s""", params).fetchall()
    rows.reverse()
    return {"channel": ch, "messages": rows}


def platform_audit(uid: str, limit: int = 200, slug: "str | None" = None) -> list[dict]:
    require(uid, "viewer")
    with registry().control_tx() as c:
        if slug:
            return c.execute("""select a.*, t.slug from platform_audit a join tenants t on t.id = a.tenant_id
                                 where t.slug = %s order by a.id desc limit %s""",
                             (slug, max(1, min(limit, 1000)))).fetchall()
        return c.execute("""select a.*, t.slug from platform_audit a
                              left join tenants t on t.id = a.tenant_id
                             order by a.id desc limit %s""", (max(1, min(limit, 1000)),)).fetchall()


def overview(uid: str) -> dict:
    """대시보드 첫 화면 — 한 번의 호출로."""
    require(uid)
    with registry().control_tx() as c:
        counts = c.execute("""select count(*) filter (where status = 'active') as active,
                                     count(*) filter (where status = 'suspended') as suspended,
                                     count(*) filter (where status not in ('active','suspended','deleted')) as other,
                                     (select count(*) from tenant_users) as users,
                                     (select count(*) from alerts where closed_at is null) as open_alerts
                                from tenants""").fetchone()
    m = tenant_metrics(uid, None, 15)
    return {"tenants": counts, "last_15m": m["summary"],
            "fanout": dict(events.stats), "top": top_tenants(uid, 15, 5)}
