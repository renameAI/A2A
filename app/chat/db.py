"""셀 라우팅과 연결 — 테넌트 하나의 질의는 그 테넌트의 셀에서만 실행된다.

연결 규칙:
- 셀마다 풀 하나. DSN은 컨트롤 플레인의 cells.dsn_env가 가리키는 환경변수에서
  읽는다(비밀번호를 DB에 두지 않는다). 앱 경로는 `<dsn_env>_APP`이 있으면 그것을,
  없으면 같은 DSN을 쓴다 — 어느 쪽이든 트랜잭션마다 테넌트 앱 역할로 내려간다.
- 모든 질의는 트랜잭션 안에서 SET LOCAL로 역할·search_path·사용자를 건다.
  세션 단위 SET을 쓰지 않는 이유: 운영에선 트랜잭션 풀러(Supavisor) 뒤에 있어
  세션 상태가 다음 요청(남의 테넌트)으로 새어 나간다. 같은 이유로 prepared
  statement를 끈다(prepare_threshold=None).
- 라우팅 캐시(tenant → cell)는 TTL이 짧다. 서버리스 인스턴스가 여럿이라 한
  인스턴스의 무효화가 다른 인스턴스에 전파되지 않기 때문이다. 정지(suspend)는
  캐시와 무관하게 즉시 걸리도록 셀 쪽 schema_meta를 다시 확인하지 않고, 대신
  TTL을 상한으로 둔다 — 운영 문서에 '정지 반영 최대 ROUTE_TTL초'로 명시.
"""
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..errors import EngineError

ROUTE_TTL = float(os.environ.get("CHAT_ROUTE_TTL", "15"))
SLUG = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def control_dsn() -> str:
    dsn = os.environ.get("CHAT_CONTROL_DSN", "")
    if not dsn:
        raise EngineError(500, "config_error", "CHAT_CONTROL_DSN이 없습니다 — 메시징 "
                                               "플랫폼은 컨트롤 플레인 DB가 필요합니다")
    return dsn


def _pool(dsn: str, name: str, max_size: int) -> ConnectionPool:
    return ConnectionPool(
        dsn, min_size=0, max_size=max_size, name=name, open=True,
        kwargs={"autocommit": True, "prepare_threshold": None,
                "row_factory": dict_row},
        timeout=10)


@dataclass(frozen=True)
class Tenant:
    id: str
    slug: str
    name: str
    cell_id: str
    schema: str
    status: str
    plan: str

    @property
    def app_role(self) -> str:
        return f"{self.schema}_app"

    @property
    def owner_role(self) -> str:
        return f"{self.schema}_owner"


class Registry:
    """컨트롤 플레인 + 셀 풀. 프로세스당 하나."""

    def __init__(self):
        self._lock = threading.Lock()
        self._control: "ConnectionPool | None" = None
        self._cells: dict[str, ConnectionPool] = {}
        self._admin_cells: dict[str, ConnectionPool] = {}
        self._routes: dict[str, tuple[Tenant, float]] = {}

    # ── 컨트롤 플레인 ──
    def control(self) -> ConnectionPool:
        with self._lock:
            if self._control is None:
                self._control = _pool(control_dsn(), "control",
                                      int(os.environ.get("CHAT_CONTROL_POOL", "10")))
            return self._control

    @contextmanager
    def control_tx(self):
        with self.control().connection() as conn:
            with conn.transaction():
                yield conn

    # ── 셀 ──
    def _cell_dsn(self, cell_id: str, app: bool) -> str:
        with self.control_tx() as c:
            row = c.execute("select dsn_env, status from cells where id = %s",
                            (cell_id,)).fetchone()
        if row is None:
            raise EngineError(500, "config_error", f"셀 {cell_id} 미등록")
        env = row["dsn_env"]
        dsn = (os.environ.get(f"{env}_APP") if app else None) or os.environ.get(env, "")
        if not dsn:
            raise EngineError(500, "config_error", f"셀 {cell_id}의 DSN 환경변수 {env} 없음")
        return dsn

    def cell_pool(self, cell_id: str, admin: bool = False) -> ConnectionPool:
        book = self._admin_cells if admin else self._cells
        with self._lock:
            p = book.get(cell_id)
        if p is not None:
            return p
        dsn = self._cell_dsn(cell_id, app=not admin)
        size = int(os.environ.get("CHAT_CELL_POOL", "20")) if not admin else 4
        p = _pool(dsn, f"cell-{cell_id}{'-admin' if admin else ''}", size)
        with self._lock:
            # 동시에 둘이 만들었으면 먼저 등록된 것을 쓰고 내 것은 닫는다
            cur = book.setdefault(cell_id, p)
        if cur is not p:
            p.close()
        return cur

    def cell_probe(self, cell_id: str, timeout: float = 2.0) -> dict:
        """헬스 체크 — 풀을 쓰지 않고 1회 직접 연결한다. 죽은 셀에 풀을 쓰면 풀이
        배경에서 재연결을 계속 시도해 장애 셀에 부하와 로그 폭주를 더한다(실측)."""
        dsn = self._cell_dsn(cell_id, app=False)
        t0 = time.perf_counter()
        with psycopg.connect(dsn, connect_timeout=max(1, int(timeout)), autocommit=True,
                             row_factory=dict_row) as conn:
            r = conn.execute("""select pg_database_size(current_database()) as size,
                                       (select count(*) from pg_stat_activity) as conns,
                                       current_setting('max_connections')::int as max_conns,
                                       (select count(*) from pg_namespace
                                         where nspname ~ '^t_[a-z][a-z0-9_]*$'
                                           and exists (select 1 from pg_tables
                                                        where schemaname = nspname
                                                          and tablename = 'schema_meta')) as schemas""").fetchone()
        return {**r, "ping_ms": round((time.perf_counter() - t0) * 1000, 1)}

    # ── 라우팅 ──
    def tenant(self, slug: str) -> Tenant:
        if not SLUG.match(slug or ""):
            raise EngineError(404, "not_found", "테넌트 없음")
        hit = self._routes.get(slug)
        now = time.time()
        if hit and hit[1] > now:
            t = hit[0]
        else:
            with self.control_tx() as c:
                row = c.execute(
                    "select id::text, slug, name, cell_id, schema_name, status, plan "
                    "from tenants where slug = %s and status <> 'deleted'",
                    (slug,)).fetchone()
            if row is None:
                raise EngineError(404, "not_found", "테넌트 없음")
            t = Tenant(row["id"], row["slug"], row["name"], row["cell_id"],
                       row["schema_name"], row["status"], row["plan"])
            self._routes[slug] = (t, now + ROUTE_TTL)
        return t

    def forget(self, slug: str) -> None:
        self._routes.pop(slug, None)

    def close(self) -> None:
        with self._lock:
            pools = [self._control, *self._cells.values(), *self._admin_cells.values()]
            self._control = None
            self._cells.clear()
            self._admin_cells.clear()
            self._routes.clear()
        for p in pools:
            if p is not None:
                p.close()


_registry: "Registry | None" = None
_reg_lock = threading.Lock()


def registry() -> Registry:
    global _registry
    with _reg_lock:
        if _registry is None:
            _registry = Registry()
            import atexit
            atexit.register(reset_registry)   # 풀 워커 스레드를 정리하고 끝난다
        return _registry


def reset_registry() -> None:
    """테스트·설정 변경용 — 모든 풀을 닫고 다시 만든다."""
    global _registry
    with _reg_lock:
        if _registry is not None:
            _registry.close()
        _registry = None


@contextmanager
def tenant_tx(t: Tenant, uid: str):
    """사용자 경로 트랜잭션 — 테넌트 앱 역할(RLS 적용)로 실행된다."""
    if t.status == "migrating":
        raise EngineError(503, "tenant_moving", "워크스페이스를 옮기는 중입니다 — 잠시 후 다시")
    if t.status != "active":
        raise EngineError(423, "tenant_unavailable",
                          f"테넌트가 사용 가능한 상태가 아닙니다 ({t.status})")
    with registry().cell_pool(t.cell_id).connection() as conn:
        with conn.transaction():
            conn.execute(sql.SQL("set local role {}").format(sql.Identifier(t.app_role)))
            # 셀 이동 펜스 확인 — 이미 하던 질의에 얹어 왕복을 늘리지 않는다. 라우팅
            # 캐시가 낡아 옛 셀로 왔다면 여기서 USAGE가 없다(move.py가 회수했다).
            ok = conn.execute("select set_config('search_path', %s, true), "
                              "set_config('chat.uid', %s, true), "
                              "has_schema_privilege(%s, 'USAGE') as ok",
                              (t.schema, uid, t.schema)).fetchone()["ok"]
            if not ok:
                registry().forget(t.slug)
                raise EngineError(503, "tenant_moving",
                                  "워크스페이스가 다른 셀로 옮겨졌습니다 — 다시 시도하세요")
            yield conn


@contextmanager
def tenant_admin_tx(t: Tenant):
    """관리 경로 — 테넌트 owner 역할(RLS 우회). 권한 판정을 마친 뒤에만 쓴다.
    플랫폼 어드민 열람·멤버/역할 관리·아카이빙·마이그레이션이 여기로 온다."""
    with registry().cell_pool(t.cell_id, admin=True).connection() as conn:
        with conn.transaction():
            conn.execute(sql.SQL("set local role {}").format(sql.Identifier(t.owner_role)))
            conn.execute("select set_config('search_path', %s, true)", (t.schema,))
            yield conn


def is_unique_violation(e: Exception) -> bool:
    return isinstance(e, psycopg.errors.UniqueViolation)
