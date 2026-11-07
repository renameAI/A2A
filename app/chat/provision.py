"""프로비저닝·마이그레이션 — 셀 등록, 테넌트 생성·배치, 스키마 버전 관리.

테넌트 생성은 두 DB(컨트롤 + 셀)에 걸친 작업이라 분산 트랜잭션이 없다. 그래서
상태 기계로 푼다: 컨트롤에 'provisioning'으로 먼저 기록 → 셀에 스키마 생성 →
성공하면 'active'. 셀 작업이 실패하면 셀 쪽을 되돌리고(스키마 drop) 컨트롤
행을 지운다. 프로세스가 중간에 죽으면 'provisioning'으로 남은 행을
reconcile()이 찾아 같은 절차로 마저 끝내거나 정리한다 — 재실행이 안전하다
(모든 셀 DDL이 if not exists / 존재 확인 뒤 실행).
"""
import re
from pathlib import Path

from psycopg import sql

from ..errors import EngineError
from .db import SLUG, Tenant, registry

SQL_DIR = Path(__file__).parent / "sql"
# (버전, 파일). 새 버전은 끝에 추가만 한다 — 이미 배포된 파일은 고치지 않는다.
MIGRATIONS: list[tuple[int, str]] = [(1, "tenant_v1.sql"), (2, "tenant_v2.sql"), (3, "tenant_v3.sql"), (4, "tenant_v4.sql")]
LATEST = MIGRATIONS[-1][0]

_IDENT = re.compile(r"^t_[a-z][a-z0-9_]{1,40}$")


def init_control() -> None:
    with registry().control_tx() as c:
        c.execute((SQL_DIR / "control.sql").read_text())


def register_cell(cell_id: str, dsn_env: str, *, region: str = "ap-southeast-1",
                  dedicated: bool = False, max_tenants: int = 50) -> None:
    if not re.match(r"^[a-z][a-z0-9-]{0,30}$", cell_id):
        raise EngineError(400, "invalid_input", "셀 id 형식이 아닙니다")
    with registry().control_tx() as c:
        c.execute("""insert into cells (id, dsn_env, region, dedicated, max_tenants)
                     values (%s, %s, %s, %s, %s)
                     on conflict (id) do update set dsn_env = excluded.dsn_env,
                       region = excluded.region, dedicated = excluded.dedicated,
                       max_tenants = excluded.max_tenants""",
                  (cell_id, dsn_env, region, dedicated, max_tenants))
    # 셀에 앱 로그인 역할을 준비한다(없으면). 비밀번호는 운영에서 셀 DSN_APP이 정한다.
    with registry().cell_pool(cell_id, admin=True).connection() as conn:
        with conn.transaction():
            conn.execute("""do $$ begin
                if not exists (select 1 from pg_roles where rolname = 'chat_service') then
                  create role chat_service login noinherit;
                end if; end $$""")


def _place(c, plan: str, cell: "str | None") -> str:
    """배치 — 지정이 없으면 열린 공용 셀 중 테넌트가 가장 적은 곳.
    전용 셀(dedicated)은 명시적으로 지정될 때만 쓴다."""
    if cell:
        row = c.execute("select id, status from cells where id = %s", (cell,)).fetchone()
        if row is None:
            raise EngineError(404, "not_found", f"셀 {cell} 없음")
        if row["status"] not in ("open", "full"):
            raise EngineError(409, "invalid_state", f"셀 {cell} 상태가 {row['status']}")
        return cell
    # 배치는 직렬화한다 — 두 생성이 동시에 같은 '가장 한가한 셀'을 보고 둘 다
    # 들어가면 max_tenants를 넘는다. 행 잠금은 집계 질의와 같이 못 쓰므로
    # 트랜잭션 범위 advisory lock으로 배치 구간만 잠근다.
    c.execute("select pg_advisory_xact_lock(hashtext('chat.tenant_placement'))")
    row = c.execute("""
        select c.id from cells c
          left join tenants t on t.cell_id = c.id and t.status <> 'deleted'
         where c.status = 'open' and not c.dedicated
         group by c.id, c.max_tenants
        having count(t.id) < c.max_tenants
         order by count(t.id), c.id
         limit 1""").fetchone()
    if row is None:
        raise EngineError(503, "no_capacity", "배치할 수 있는 셀이 없습니다 — 셀을 추가하세요")
    return row["id"]


def _render(path: str, t: Tenant) -> str:
    for ident in (t.schema, t.owner_role, t.app_role):
        if not _IDENT.match(ident.removesuffix("_owner").removesuffix("_app")):
            raise EngineError(400, "invalid_input", f"식별자 형식 오류: {ident}")
    text = (SQL_DIR / path).read_text()
    return (text.replace("__SCHEMA__", t.schema)
                .replace("__OWNER__", t.owner_role)
                .replace("__APP__", t.app_role))


def _cell_setup_roles(conn, t: Tenant) -> None:
    conn.execute(sql.SQL("""do $$ begin
        if not exists (select 1 from pg_roles where rolname = {o}) then
          create role {oi} nologin;
        end if;
        if not exists (select 1 from pg_roles where rolname = {a}) then
          create role {ai} nologin;
        end if; end $$""").format(
        o=sql.Literal(t.owner_role), oi=sql.Identifier(t.owner_role),
        a=sql.Literal(t.app_role), ai=sql.Identifier(t.app_role)))
    # 마이그레이터(현재 사용자)가 owner로 SET ROLE 할 수 있어야 한다
    conn.execute(sql.SQL("grant {} to current_user").format(sql.Identifier(t.owner_role)))
    conn.execute(sql.SQL("grant {} to current_user").format(sql.Identifier(t.app_role)))
    conn.execute(sql.SQL("grant {} to chat_service").format(sql.Identifier(t.app_role)))


def _apply(t: Tenant, from_version: int) -> int:
    pool = registry().cell_pool(t.cell_id, admin=True)
    v = from_version
    for ver, path in MIGRATIONS:
        if ver <= from_version:
            continue
        with pool.connection() as conn:
            with conn.transaction():
                if ver == 1:
                    _cell_setup_roles(conn, t)
                conn.execute(_render(path, t))
        v = ver
    return v


def create_tenant(slug: str, name: str, *, plan: str = "standard",
                  cell: "str | None" = None, owner_uid: str, owner_email: str) -> Tenant:
    if not SLUG.match(slug):
        raise EngineError(400, "invalid_input", "slug는 영소문자로 시작하는 2~41자")
    schema = f"t_{slug}"
    with registry().control_tx() as c:
        if c.execute("select 1 from tenants where slug = %s", (slug,)).fetchone():
            raise EngineError(409, "conflict", f"이미 있는 테넌트: {slug}")
        cell_id = _place(c, plan, cell)
        row = c.execute("""insert into tenants (slug, name, cell_id, schema_name, plan)
                           values (%s, %s, %s, %s, %s) returning id::text""",
                        (slug, name, cell_id, schema, plan)).fetchone()
    t = Tenant(row["id"], slug, name, cell_id, schema, "provisioning", plan)
    try:
        version = _apply(t, 0)
        t = Tenant(t.id, slug, name, cell_id, schema, "active", plan)
        _add_member_admin(t, owner_uid, owner_email, "owner", {})
    except Exception:
        _drop_cell_schema(t)
        with registry().control_tx() as c:
            c.execute("delete from tenants where id = %s", (t.id,))
        raise
    with registry().control_tx() as c:
        c.execute("update tenants set status = 'active', schema_version = %s where id = %s",
                  (version, t.id))
        c.execute("""insert into platform_audit (actor, action, tenant_id, target, detail)
                     values ('system', 'tenant.create', %s, %s, jsonb_build_object('cell', %s::text))""",
                  (t.id, slug, cell_id))
    registry().forget(slug)
    return t


def _drop_cell_schema(t: Tenant) -> None:
    with registry().cell_pool(t.cell_id, admin=True).connection() as conn:
        with conn.transaction():
            conn.execute(sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(t.schema)))


def _add_member_admin(t: Tenant, uid: str, email: str, role: str, attrs: dict) -> None:
    from psycopg.types.json import Jsonb
    from .db import tenant_admin_tx
    with tenant_admin_tx(t) as conn:
        conn.execute("""insert into members (user_id, email, display_name, role, attrs)
                        values (%s, %s, %s, %s, %s)
                        on conflict (user_id) do update set role = excluded.role,
                          attrs = excluded.attrs, status = 'active'""",
                     (uid, email, email.split("@")[0], role, Jsonb(attrs)))
        # 공개 기본 채널 자동 가입 — 게스트는 제외한다. 채널 멤버십이 곧 열람
        # 권한이라, 게스트를 자동 가입시키면 '초대받은 채널만'이 무너진다
        # (실측: 게스트에게 #general이 보였다).
        if role != "guest":
            conn.execute("""insert into channel_members (channel_id, user_id)
                            select id, %s from channels where kind in ('channel','announcements')
                              and visibility = 'public' and archived_at is null
                            on conflict do nothing""", (uid,))
    with registry().control_tx() as c:
        c.execute("""insert into tenant_users (tenant_id, user_id, email) values (%s, %s, %s)
                     on conflict (tenant_id, user_id) do update set email = excluded.email""",
                  (t.id, uid, email))


def migrate_all() -> list[dict]:
    """모든 활성 테넌트를 최신 스키마로. 테넌트 하나의 실패가 나머지를 막지 않는다."""
    out = []
    with registry().control_tx() as c:
        rows = c.execute("""select id::text, slug, name, cell_id, schema_name, status, plan,
                                   schema_version from tenants
                             where status = 'active' and schema_version < %s
                               -- 이동 중인 테넌트는 건너뛴다: 복사 스냅샷 뒤의 스키마 변경은 대상에
                               -- 반영되지 않는다. 이동이 끝나면 다음 실행이 올린다
                               and not exists (select 1 from tenant_moves m where m.tenant_id = tenants.id
                                                 and m.state in ('queued','copying','fencing'))
                             order by cell_id, slug""", (LATEST,)).fetchall()
    for r in rows:
        t = Tenant(r["id"], r["slug"], r["name"], r["cell_id"], r["schema_name"],
                   r["status"], r["plan"])
        try:
            v = _apply(t, r["schema_version"])
            with registry().control_tx() as c:
                c.execute("update tenants set schema_version = %s where id = %s", (v, t.id))
            out.append({"tenant": t.slug, "version": v, "ok": True})
        except Exception as e:  # noqa: BLE001 — 실패를 모아 보고한다
            out.append({"tenant": t.slug, "ok": False, "error": f"{type(e).__name__}: {e}"})
    return out


def reconcile() -> list[dict]:
    """중간에 죽은 프로비저닝을 정리한다(재실행 안전)."""
    out = []
    with registry().control_tx() as c:
        rows = c.execute("""select id::text, slug, name, cell_id, schema_name, plan
                              from tenants where status = 'provisioning'
                               and created_at < now() - interval '2 minutes'""").fetchall()
    for r in rows:
        t = Tenant(r["id"], r["slug"], r["name"], r["cell_id"], r["schema_name"],
                   "provisioning", r["plan"])
        _drop_cell_schema(t)
        with registry().control_tx() as c:
            c.execute("delete from tenants where id = %s", (t.id,))
        out.append({"tenant": t.slug, "action": "rolled_back"})
    return out


def run_maintenance() -> list[dict]:
    """모든 테넌트의 파티션 선생성·멱등 원장 정리 — 주기 작업이 부른다."""
    from .db import tenant_admin_tx
    out = []
    with registry().control_tx() as c:
        rows = c.execute("""select id::text, slug, name, cell_id, schema_name, status, plan
                              from tenants t where status = 'active'
                               and not exists (select 1 from tenant_moves m where m.tenant_id = t.id
                                                 and m.state in ('queued','copying','fencing'))""").fetchall()
    for r in rows:
        t = Tenant(r["id"], r["slug"], r["name"], r["cell_id"], r["schema_name"],
                   r["status"], r["plan"])
        try:
            with tenant_admin_tx(t) as conn:
                res = conn.execute("select maintenance() as r").fetchone()["r"]
            out.append({"tenant": t.slug, **res})
        except Exception as e:  # noqa: BLE001
            out.append({"tenant": t.slug, "error": f"{type(e).__name__}: {e}"})
    return out
