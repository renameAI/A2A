"""테넌트 셀 이동 — 공용 셀의 기업을 전용 셀(다른 Postgres 서버)로 옮긴다.

두 DB 사이에 분산 트랜잭션이 없으므로 단계로 푼다. 쓰기가 멈추는 구간은 B뿐이다.

  A. 복사 (서비스 유지)
     원본에서 REPEATABLE READ 스냅샷을 잡고 pg_current_snapshot()을 기록한 뒤,
     대상 셀에 같은 버전 스키마를 만들고 모든 테이블을 COPY BINARY로 흘린다.
  B. 펜스 (짧은 쓰기 중단)
     1) 컨트롤: status = migrating  (캐시가 새로운 인스턴스는 503 재시도)
     2) 원본: 앱 역할의 스키마 USAGE 회수 — 캐시가 낡은 인스턴스도 DB가 거부한다.
        라우팅 캐시(TTL)에 의존하지 않는 펜스다.
     3) 원본: 쓰기 테이블을 EXCLUSIVE로 잠가 **이미 진행 중이던** 쓰기 트랜잭션이
        끝나기를 기다린다(회수 전에 시작해 아직 커밋 전인 쓰기를 놓치지 않게).
     4) 델타: 스냅샷에 보이지 않는 행(xmin 기준 — 스냅샷 이후 삽입·수정된 행)만
        메시지에서 골라 옮기고, 작은 테이블은 통째로 다시 복사한다. 시계(created_at)로
        고르지 않는다 — 스냅샷 직전에 시작해 직후에 커밋한 트랜잭션의 행은 시각이
        스냅샷보다 이르지만 스냅샷엔 없다.
  C. 검증 — 테이블마다 행 수 + 행 텍스트 해시 합(순서 무관 체크섬)을 양쪽에서 비교
  D. 전환 — 컨트롤에서 cell_id를 바꾸고 active로. 원본 스키마는 롤백 대비로 남긴다
     (USAGE가 회수된 채). finalize()가 나중에 지운다.
  실패 — D 전이면 원본 USAGE를 되돌리고 대상 스키마를 지운다(원상 복구).

한계: 같은 Postgres 메이저 버전 사이에서만(COPY BINARY). xid 순환(wraparound)이
스냅샷과 펜스 사이에 일어나면 델타 판정이 틀리므로, 그 경우 감지해 메시지를 통째로
다시 복사한다.
"""
import time

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..errors import EngineError
from . import provision
from .db import Tenant, registry

# 의존 순서(복사 순서). 비우기는 역순.
TABLES = ["schema_meta", "roles", "members", "policies", "channels", "channel_members",
          "messages", "message_dedupe", "mentions", "reactions", "audit_log", "access_log",
          "archive_index"]
SERIALS = ["channels", "policies", "audit_log", "access_log", "archive_index"]
WRITE_TABLES = ["channels", "channel_members", "messages", "message_dedupe", "mentions",
                "reactions", "members"]


def _conn(cell_id: str) -> psycopg.Connection:
    """이동 작업 전용 직접 연결(마이그레이터 권한). 풀을 쓰지 않는다 — 긴 복사가
    서비스 풀의 연결을 붙잡지 않게."""
    dsn = registry()._cell_dsn(cell_id, app=False)
    c = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, prepare_threshold=None)
    c.execute("set timezone = 'UTC'")            # 체크섬의 행 텍스트를 양쪽에서 같게
    return c


def _identity(c: psycopg.Connection) -> tuple:
    r = c.execute("select system_identifier::text as sid, current_database() as db "
                  "from pg_control_system()").fetchone()
    return r["sid"], r["db"]


def _schema(c, t: Tenant):
    c.execute(sql.SQL("set search_path = {}").format(sql.Identifier(t.schema)))


def _copy(src, dst, query: sql.Composable, table: str, beat=None) -> int:
    """원본 질의 결과를 대상 테이블로 COPY BINARY 스트리밍. 옮긴 바이트를 돌려준다.
    beat는 큰 테이블 복사 중에도 하트비트·취소 확인을 하기 위한 콜백(1천 덩어리마다)."""
    n = 0
    with src.cursor().copy(sql.SQL("copy ({}) to stdout (format binary)").format(query)) as out, \
         dst.cursor().copy(sql.SQL("copy {} from stdin (format binary)").format(
             sql.Identifier(table))) as inp:
        for i, chunk in enumerate(out):
            inp.write(chunk)
            n += len(chunk)
            if beat and i % 1000 == 999:
                beat(table, n)
    return n


def _partitions(c) -> list:
    return [r["m"] for r in c.execute("""
        select to_date(substr(c.relname, 10), 'YYYYMM') as m
          from pg_inherits i join pg_class c on c.oid = i.inhrelid
          join pg_class p on p.oid = i.inhparent
          join pg_namespace n on n.oid = p.relnamespace
         where p.relname = 'messages' and n.nspname = current_schema()
           and c.relname ~ '^messages_[0-9]{6}$'""").fetchall()]


def _checksums(c) -> dict:
    out = {}
    for tb in TABLES:
        r = c.execute(sql.SQL(
            "select count(*) as n, coalesce(sum(('x' || substr(md5(x::text), 1, 15))"
            "::bit(60)::bigint), 0)::text as h from {} x").format(sql.Identifier(tb))).fetchone()
        out[tb] = (r["n"], r["h"])
    return out


def _parse_snapshot(s: str) -> "tuple[int, int, list[int]] | None":
    """pg_snapshot 'xmin:xmax:xip,...'(xid8) → 32비트 xid 공간. 구간이 순환 경계를
    넘으면 None(호출자가 전체 재복사로 대체)."""
    a, b, xs = s.split(":")
    lo, hi = int(a), int(b)
    if (lo >> 32) != (hi >> 32):
        return None
    return lo & 0xFFFFFFFF, hi & 0xFFFFFFFF, [int(x) & 0xFFFFFFFF for x in xs.split(",") if x]


def _set_serials(dst) -> None:
    for tb in SERIALS:
        dst.execute(sql.SQL(
            "select setval(pg_get_serial_sequence({t}, 'id'), "
            "greatest(coalesce((select max(id) from {i}), 0), 1), "
            "(select max(id) from {i}) is not null)").format(
            t=sql.Literal(tb), i=sql.Identifier(tb)))


class _Cancelled(Exception):
    pass


class _SimulatedCrash(BaseException):
    """시험 전용 — 작업자가 죽은 것처럼 예외 처리(되돌리기)를 건너뛴다."""


_CRASH_AT: "str | None" = None        # 시험 전용 — 이 단계에 이르면 작업자가 '죽는다'
STALE_SEC = 300                        # 하트비트가 이만큼 끊기면 작업자를 잃은 것으로 본다


def enqueue(slug: str, to_cell: str, actor: str) -> int:
    """이동을 대기열에 넣는다 — 실행은 작업자(배경 스레드 또는 크론 tick)가 한다.
    검증은 여기서 미리 한다: 대기열에 들어간 뒤 실패하면 사람이 늦게 안다."""
    reg = registry()
    reg.forget(slug)
    t = reg.tenant(slug)
    if t.cell_id == to_cell:
        raise EngineError(409, "invalid_state", "이미 그 셀에 있습니다")
    with reg.control_tx() as c:
        cell = c.execute("select status, dedicated, max_tenants, (select count(*) from tenants x "
                         "where x.cell_id = cells.id and x.status <> 'deleted') as n "
                         "from cells where id = %s", (to_cell,)).fetchone()
        if cell is None:
            raise EngineError(404, "not_found", f"셀 {to_cell} 없음")
        if cell["status"] not in ("open", "full") or cell["n"] >= cell["max_tenants"]:
            raise EngineError(409, "invalid_state", f"셀 {to_cell}에 자리가 없습니다 "
                                                    f"({cell['status']}, {cell['n']}/{cell['max_tenants']})")
        ver = c.execute("select schema_version from tenants where id = %s", (t.id,)).fetchone()
        if ver["schema_version"] != provision.LATEST:
            raise EngineError(409, "invalid_state", "먼저 최신 스키마로 마이그레이션하세요")
    src, dst = _conn(t.cell_id), _conn(to_cell)
    try:
        if _identity(src) == _identity(dst):
            raise EngineError(409, "invalid_state", "원본과 대상이 같은 물리 데이터베이스입니다")
    finally:
        src.close(); dst.close()
    with reg.control_tx() as c:
        try:
            mid = c.execute("""insert into tenant_moves (tenant_id, from_cell, to_cell, actor)
                               values (%s, %s, %s, %s) returning id""",
                            (t.id, t.cell_id, to_cell, actor)).fetchone()["id"]
        except psycopg.errors.UniqueViolation as e:
            raise EngineError(409, "conflict", "이미 진행 중인 이동이 있습니다") from e
        c.execute("""insert into platform_audit (actor, action, tenant_id, target, detail)
                     values (%s, 'tenant.move.enqueue', %s, %s, %s)""",
                  (actor, t.id, slug, Jsonb({"to": to_cell, "move_id": mid})))
    return mid


def claim_next(worker: str) -> "int | None":
    """대기 중인 이동 하나를 가져간다. SKIP LOCKED라 작업자가 여럿이어도 하나만 잡는다."""
    with registry().control_tx() as c:
        r = c.execute("""update tenant_moves set state = 'copying', worker = %s,
                                heartbeat_at = now(), phase = 'claimed'
                          where id = (select id from tenant_moves where state = 'queued'
                                       order by id limit 1 for update skip locked)
                          returning id""", (worker,)).fetchone()
    return r["id"] if r else None


def run_pending(worker: str, limit: int = 1) -> list[dict]:
    out = []
    for _ in range(limit):
        mid = claim_next(worker)
        if mid is None:
            break
        try:
            out.append(run_move(mid))
        except Exception as e:  # noqa: BLE001 — 실패는 tenant_moves에 이미 기록됐다
            out.append({"move_id": mid, "error": f"{type(e).__name__}: {e}"})
    return out


def _beat(mid: int, phase: str, report: "dict | None" = None) -> None:
    """하트비트 + 진행 기록 + 취소 확인(펜스 전까지만 취소된다)."""
    if _CRASH_AT == phase:
        raise _SimulatedCrash(phase)
    with registry().control_tx() as c:
        r = c.execute("""update tenant_moves set heartbeat_at = now(), phase = %s,
                                report = coalesce(%s, report)
                          where id = %s returning state, cancel_requested""",
                      (phase, Jsonb(report) if report is not None else None, mid)).fetchone()
    if r and r["cancel_requested"] and r["state"] == "copying":
        raise _Cancelled("취소 요청")


def move_tenant(slug: str, to_cell: str, actor: str) -> dict:
    """대기열에 넣고 바로 실행한다(동기) — 시험·CLI용. API는 enqueue만 한다."""
    mid = enqueue(slug, to_cell, actor)
    with registry().control_tx() as c:
        c.execute("update tenant_moves set state = 'copying', worker = 'inline', "
                  "heartbeat_at = now() where id = %s and state = 'queued'", (mid,))
    return run_move(mid)


def run_move(mid: int) -> dict:
    reg = registry()
    with reg.control_tx() as c:
        m = c.execute("""select m.*, t.slug from tenant_moves m join tenants t on t.id = m.tenant_id
                          where m.id = %s""", (mid,)).fetchone()
    if m is None or m["state"] != "copying":
        raise EngineError(409, "invalid_state", "실행할 수 있는 이동이 아닙니다")
    slug, to_cell, actor = m["slug"], m["to_cell"], m["actor"]
    reg.forget(slug)
    t = reg.tenant(slug)
    src, dst = _conn(t.cell_id), _conn(to_cell)
    target = Tenant(t.id, t.slug, t.name, to_cell, t.schema, "provisioning", t.plan)
    report: dict = {"from": t.cell_id, "to": to_cell, "bytes": 0}
    fenced = switched = False

    def beat(table, n):
        report["bytes_in_table"] = n
        _beat(mid, f"copy:{table}", report)

    try:
        # ── A. 스냅샷 + 대상 스키마 + 전체 복사 ──
        t0 = time.perf_counter()
        _beat(mid, "provision", report)
        provision._apply(target, 0)                       # 대상 셀에 역할·스키마 생성
        src.execute("begin isolation level repeatable read")
        _schema(src, t)
        snap = src.execute("select pg_current_snapshot()::text as s").fetchone()["s"]
        months = _partitions(src)
        with dst.transaction():
            _schema(dst, t)
            dst.execute(sql.SQL("set local role {}").format(sql.Identifier(t.owner_role)))
            for mo in months:
                dst.execute("select create_month(%s)", (mo,))
            dst.execute(sql.SQL("truncate {} cascade").format(
                sql.SQL(", ").join(sql.Identifier(x) for x in TABLES)))
            for tb in TABLES:
                _beat(mid, f"copy:{tb}", report)
                report["bytes"] += _copy(src, dst, sql.SQL("select * from {}").format(
                    sql.Identifier(tb)), tb, beat)
            _set_serials(dst)
        src.execute("commit")
        report["copy_sec"] = round(time.perf_counter() - t0, 3)
        report["copy_bytes"] = report["bytes"]
        _beat(mid, "copied", report)                      # 취소는 여기까지만 받는다

        # ── B. 펜스 ──
        tf = time.perf_counter()
        with reg.control_tx() as c:
            c.execute("update tenants set status = 'migrating' where id = %s", (t.id,))
            c.execute("update tenant_moves set state = 'fencing', fenced_at = now(), "
                      "heartbeat_at = now(), phase = 'fence' where id = %s", (mid,))
        reg.forget(slug)
        with src.transaction():
            _schema(src, t)
            src.execute(sql.SQL("revoke usage on schema {} from {}").format(
                sql.Identifier(t.schema), sql.Identifier(t.app_role)))
            fenced = True
            src.execute(sql.SQL("set local role {}").format(sql.Identifier(t.owner_role)))
            src.execute(sql.SQL("lock table {} in exclusive mode").format(
                sql.SQL(", ").join(sql.Identifier(x) for x in WRITE_TABLES)))
        _beat(mid, "delta", report)
        # 이제 원본에는 새 쓰기도, 진행 중인 쓰기도 없다
        _schema(src, t)
        parsed = _parse_snapshot(snap)
        with dst.transaction():
            _schema(dst, t)
            dst.execute(sql.SQL("set local role {}").format(sql.Identifier(t.owner_role)))
            small = [x for x in TABLES if x != "messages"]
            dst.execute(sql.SQL("truncate {} cascade").format(
                sql.SQL(", ").join(sql.Identifier(x) for x in small)))
            for tb in small:
                _copy(src, dst, sql.SQL("select * from {}").format(sql.Identifier(tb)), tb)
            if parsed is None:
                dst.execute("truncate messages")
                _copy(src, dst, sql.SQL("select * from messages"), "messages")
                report["delta"] = "full_recopy_wraparound"
            else:
                lo, hi, xip = parsed
                # 스냅샷에 보이지 않는 행 = 그 이후(또는 동시에) 삽입·수정된 행
                changed = sql.SQL(
                    "select * from messages where not ((xmin::text::bigint < {lo}) or "
                    "(xmin::text::bigint < {hi} and not (xmin::text::bigint = any({xip}::bigint[]))))"
                ).format(lo=sql.Literal(lo), hi=sql.Literal(hi), xip=sql.Literal(xip))
                dst.execute("create temp table _delta (like messages) on commit drop")
                _copy(src, dst, changed, "_delta")
                n = dst.execute("select count(*) as n from _delta").fetchone()["n"]
                dst.execute("delete from messages m using _delta d "
                            "where m.channel_id = d.channel_id and m.seq = d.seq")
                dst.execute("insert into messages select * from _delta")
                report["delta_messages"] = n
            _set_serials(dst)

        # ── C. 검증 ──
        _beat(mid, "verify", report)
        _schema(dst, t)
        dst.execute(sql.SQL("set role {}").format(sql.Identifier(t.owner_role)))
        a, b = _checksums(src), _checksums(dst)
        dst.execute("reset role")
        bad = {k: (a[k], b[k]) for k in TABLES if a[k] != b[k]}
        if bad:
            raise EngineError(500, "move_verify_failed", f"체크섬 불일치: {sorted(bad)}")
        report["rows"] = {k: v[0] for k, v in a.items()}

        # ── D. 전환 ──
        _beat(mid, "switch", report)
        report["write_pause_sec"] = round(time.perf_counter() - tf, 3)
        with reg.control_tx() as c:
            c.execute("update tenants set cell_id = %s, status = 'active' where id = %s",
                      (to_cell, t.id))
            c.execute("update tenant_moves set state = 'switched', switched_at = now(), "
                      "phase = 'switched', report = %s where id = %s", (Jsonb(report), mid))
            c.execute("""insert into platform_audit (actor, action, tenant_id, target, detail)
                         values (%s, 'tenant.move', %s, %s, %s)""",
                      (actor, t.id, slug, Jsonb(report)))
        switched = True
        reg.forget(slug)
        return {"move_id": mid, **report}
    except _Cancelled:
        _rollback(t, target, src, fenced)
        _finish(mid, "cancelled", error="취소됨")
        return {"move_id": mid, "cancelled": True}
    except Exception as e:
        if not switched:
            _rollback(t, target, src, fenced)
            _finish(mid, "rolled_back", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        src.close()
        dst.close()


def cancel(mid: int, actor: str) -> dict:
    """대기 중이면 바로 취소, 복사 중이면 요청만 남긴다(작업자가 다음 하트비트에서 되돌린다).
    펜스 이후에는 취소하지 않는다 — 그때는 끝까지 가거나 실패 시 자동으로 되돌린다."""
    with registry().control_tx() as c:
        m = c.execute("select state, tenant_id from tenant_moves where id = %s for update",
                      (mid,)).fetchone()
        if m is None:
            raise EngineError(404, "not_found", "이동 없음")
        if m["state"] == "queued":
            c.execute("update tenant_moves set state = 'cancelled', finished_at = now(), "
                      "error = '취소됨' where id = %s", (mid,))
            out = "cancelled"
        elif m["state"] == "copying":
            c.execute("update tenant_moves set cancel_requested = true where id = %s", (mid,))
            out = "cancel_requested"
        else:
            raise EngineError(409, "invalid_state",
                              f"{m['state']} 단계에서는 취소할 수 없습니다(쓰기 펜스 이후)")
        c.execute("""insert into platform_audit (actor, action, tenant_id, target)
                     values (%s, 'tenant.move.cancel', %s, %s)""", (actor, m["tenant_id"], str(mid)))
    return {"move_id": mid, "result": out}


def recover_stale(stale_sec: "float | None" = None) -> list[dict]:
    """작업자를 잃은 이동을 되돌린다 — 하트비트가 끊긴 copying/fencing.

    가장 위험한 경우는 펜스(USAGE 회수) 뒤에 작업자가 죽는 것이다: 원본은 쓰기가 막힌 채
    남고 테넌트는 migrating이라 아무도 못 쓴다. 전환 전이었다면 원본이 여전히 정본이므로
    USAGE를 돌려주고, 대상 스키마를 지우고, 테넌트를 active로 되돌린다(재시도는 다시 enqueue)."""
    stale = STALE_SEC if stale_sec is None else stale_sec
    with registry().control_tx() as c:
        rows = c.execute("""select m.id, m.state, m.from_cell, m.to_cell, t.slug
                              from tenant_moves m join tenants t on t.id = m.tenant_id
                             where m.state in ('copying','fencing')
                               and coalesce(m.heartbeat_at, m.started_at)
                                   < now() - make_interval(secs => %s)""", (stale,)).fetchall()
    out = []
    for r in rows:
        with registry().control_tx() as c:          # 동시에 둘이 복구하지 않게 다시 잠그고 확인
            ok = c.execute("""update tenant_moves set phase = 'recovering', heartbeat_at = now()
                               where id = %s and state in ('copying','fencing') returning id""",
                           (r["id"],)).fetchone()
        if not ok:
            continue
        registry().forget(r["slug"])
        t = registry().tenant(r["slug"])
        target = Tenant(t.id, t.slug, t.name, r["to_cell"], t.schema, "provisioning", t.plan)
        src = _conn(r["from_cell"])
        try:
            _rollback(t, target, src, fenced=True)          # GRANT는 멱등 — 펜스 전이어도 무해
        finally:
            src.close()
        _finish(r["id"], "rolled_back", error=f"작업자 응답 없음(하트비트 {stale}초 초과) — 되돌림")
        out.append({"move_id": r["id"], "was": r["state"], "action": "rolled_back"})
    return out


def _rollback(t: Tenant, target: Tenant, src, fenced: bool) -> None:
    try:
        src.execute("rollback")
    except Exception:  # noqa: BLE001
        pass
    if fenced:
        src.execute(sql.SQL("grant usage on schema {} to {}").format(
            sql.Identifier(t.schema), sql.Identifier(t.app_role)))
    try:
        provision._drop_cell_schema(target)
    except Exception:  # noqa: BLE001 — 대상 정리 실패는 기록만, 원본 복구가 우선
        pass
    with registry().control_tx() as c:
        c.execute("update tenants set status = 'active' where id = %s", (t.id,))
    registry().forget(t.slug)


def _finish(mid: int, state: str, error: "str | None" = None) -> None:
    with registry().control_tx() as c:
        c.execute("update tenant_moves set state = %s, finished_at = now(), error = %s "
                  "where id = %s", (state, error, mid))


def finalize(move_id: int, actor: str) -> dict:
    """전환 뒤 원본 스키마를 지운다 — 롤백 창이 끝났다고 사람이 판단한 뒤에."""
    with registry().control_tx() as c:
        m = c.execute("""select m.*, t.slug, t.schema_name from tenant_moves m
                           join tenants t on t.id = m.tenant_id where m.id = %s""",
                      (move_id,)).fetchone()
    if m is None or m["state"] != "switched":
        raise EngineError(409, "invalid_state", "전환 완료된 이동만 정리할 수 있습니다")
    old = Tenant(str(m["tenant_id"]), m["slug"], "", m["from_cell"], m["schema_name"], "", "")
    provision._drop_cell_schema(old)
    _finish(move_id, "retired")
    with registry().control_tx() as c:
        c.execute("""insert into platform_audit (actor, action, tenant_id, target)
                     values (%s, 'tenant.move.finalize', %s, %s)""",
                  (actor, m["tenant_id"], str(move_id)))
    return {"move_id": move_id, "dropped": f"{m['from_cell']}:{m['schema_name']}"}
