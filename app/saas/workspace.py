"""워크스페이스 해석과 이전 — 메시징 테넌트가 리드 데이터의 워크스페이스도 된다.

멤버십 통합(2026-10-21): 예전엔 두 가지 멤버십이 따로 있었다 — 메시징 테넌트 멤버
(channels·messages)와 리드 데이터 공유 팀(app/saas/team.py, ws-{uid} 공유). 이제
테넌트에 속한 사용자의 리드 데이터 워크스페이스는 `t-<tenant_id>`다. 같은 테넌트의
멤버는 같은 Lead Request·후보·초안·발송 기록을 본다.

해석 순서:
  1. 메시징 플랫폼이 꺼져 있거나(CHAT_CONTROL_DSN 없음) 속한 테넌트가 없으면 → 예전 그대로
     (ws-{uid} 또는 예전 팀 워크스페이스).
  2. 예전 워크스페이스에 **옮기지 않은 데이터가 남아 있으면** → 예전 워크스페이스에 머문다.
     테넌트로 바로 넘기면 사용자의 리드 데이터가 하루아침에 '사라진' 것처럼 보인다.
     화면이 '옮기기'를 안내하고, 소유자가 이전을 실행하면 그때 테넌트로 넘어간다.
  3. 그 밖에는 X-Tenant 헤더가 가리키는 테넌트(내가 속한 곳일 때만), 없으면 이름순 첫 테넌트.
  '속한 곳'은 역할에 lead.access가 있는 테넌트다 — 게스트는 채팅만, 리드 데이터는 못 본다.

이전은 복사다(삽입-부재 시에만) — 원본은 지우지 않고 '이전됨' 표시만 한다. 되돌릴 일이
생기면 표시만 지우면 된다.
"""
import os
import time
from dataclasses import dataclass

from ..errors import EngineError

_TTL = 20.0
_cache: "dict[str, tuple[object, float]]" = {}

# 이전하지 않는 문서 — 예전 팀 멤버십·예전 문서 저장소 채팅(메시징 플랫폼이 대체)
SKIP_KINDS = {"member", "invite", "presence", "chat_msg", "channel_meta", "thread_meta",
              "chat_read", "workspace"}
# '옮기지 않은 데이터가 있다'의 기준 — 사용자가 만든 리드 작업물
DATA_KINDS = ("lead_request", "onboarding", "profile_version")


def _chat_on() -> bool:
    return bool(os.environ.get("CHAT_CONTROL_DSN"))


def _cached(key: str, fn):
    hit = _cache.get(key)
    now = time.time()
    if hit and hit[1] > now:
        return hit[0]
    v = fn()
    if len(_cache) > 5000:
        _cache.clear()
    _cache[key] = (v, now + _TTL)
    return v


def forget(*keys: str) -> None:
    for k in keys:
        _cache.pop(k, None)


def tenants_of(uid: str) -> list[dict]:
    if not _chat_on():
        return []

    def load():
        from ..chat.db import registry
        try:
            with registry().control_tx() as c:
                return [dict(r) for r in c.execute(
                    """select t.id::text as id, t.slug, t.name from tenant_users tu
                         join tenants t on t.id = tu.tenant_id
                        where tu.user_id = %s and t.status not in ('deleted')
                        order by t.name""", (uid,)).fetchall()]
        except EngineError:
            return []
    return _cached(f"tenants:{uid}", load)


def lead_tenants(uid: str) -> list[dict]:
    """리드 데이터를 함께 볼 수 있는 테넌트 — 역할에 lead.access가 있는 곳만(게스트 제외).

    테넌트 멤버십(컨트롤 DB)만 보면 게스트도 회사 리드 파이프라인을 보게 된다. 권한 판정은
    채팅과 같은 ABAC(역할·deny 정책·속성)로 한다 — 관리자가 deny 정책으로 특정 사람을 뺄 수 있다."""
    def load():
        from ..chat import service as chat_svc
        from ..chat.db import registry
        out = []
        for t in tenants_of(uid):
            try:
                if chat_svc.me(registry().tenant(t["slug"]), uid)["can"].get("lead.access"):
                    out.append(t)
            except EngineError as e:
                # 셀 이동 펜스 중(503)이면 권한을 모른다 — 빼면 리드 데이터가 잠깐 예전 공간으로
                # 바뀌어 보이고, 넣으면 게스트에게 열릴 수 있다. 요청 자체를 재시도로 돌린다
                # (펜스는 수십 ms — §12). 예외는 캐시되지 않는다
                if e.http_status == 503:
                    raise
                continue                    # 비활성 멤버 등 — 볼 권한 없음
        return out
    return _cached(f"lead:{uid}", load)


def legacy_pending(ws: str) -> bool:
    """예전 워크스페이스에 아직 옮기지 않은 리드 데이터가 있는가."""
    def load():
        from .store import get_saas_store
        st = get_saas_store()
        root = st.get("workspace", ws, "root") or {}
        if root.get("migrated_to"):
            return False
        return any(st.list(k, ws, limit=1) for k in DATA_KINDS)
    return _cached(f"pending:{ws}", load)


@dataclass
class Resolved:
    workspace_id: str
    tenant: "dict | None"          # 리드 데이터가 테넌트에 있을 때
    legacy_ws: str
    pending: bool                  # 예전 데이터가 남아 테넌트로 못 넘어간 상태


def resolve(uid: str, tenant_hint: str = "") -> Resolved:
    from .team import resolve_workspace
    legacy = resolve_workspace(uid)
    ts = lead_tenants(uid)
    if not ts:
        return Resolved(legacy, None, legacy, False)
    chosen = next((t for t in ts if t["slug"] == tenant_hint), ts[0])
    if legacy_pending(legacy):
        return Resolved(legacy, chosen, legacy, True)
    return Resolved(f"t-{chosen['id']}", chosen, legacy, False)


def migrate(uid: str, email: str, tenant_slug: str) -> dict:
    """예전 워크스페이스의 리드 데이터를 테넌트 워크스페이스로 옮긴다.

    권한: 예전 워크스페이스의 소유자이면서, 대상 테넌트에서 멤버 초대 권한(member.invite)이
    있어야 한다 — 예전 팀원을 테넌트 멤버로 들이는 일이 함께 일어나기 때문이다."""
    from ..chat import service as chat_svc
    from ..chat.db import registry
    from ..chat.provision import _add_member_admin
    from .store import get_saas_store
    from .team import resolve_workspace, dissolve
    from psycopg.types.json import Jsonb

    if not _chat_on():
        raise EngineError(409, "invalid_state", "메시징 플랫폼이 꺼져 있어 이전할 곳이 없습니다")
    legacy = resolve_workspace(uid)
    if legacy != f"ws-{uid}":
        raise EngineError(403, "forbidden", "예전 워크스페이스의 소유자만 옮길 수 있어요 — "
                                            "소유자에게 요청하세요")
    st = get_saas_store()
    root = st.get("workspace", legacy, "root") or {}
    if root.get("migrated_to"):
        raise EngineError(409, "invalid_state", f"이미 옮겼습니다 ({root['migrated_to']})")
    t = registry().tenant(tenant_slug)
    if not chat_svc.me(t, uid)["can"].get("member.invite"):
        raise EngineError(403, "forbidden", "대상 워크스페이스에서 멤버를 들일 권한이 필요합니다")
    target = f"t-{t.id}"

    # 1) 예전 팀원을 테넌트 멤버로 (이미 멤버면 역할은 그대로 둔다)
    existing = {m["user_id"] for m in chat_svc.list_members(t, uid)}
    added = []
    for m in st.list("member", legacy):
        mu = m.get("uid")
        if mu and mu not in existing:
            _add_member_admin(t, mu, m.get("email") or f"{mu}@unknown", "member", {})
            added.append(mu)

    # 2) 문서 복사 — 대상에 이미 같은 문서가 있으면 건드리지 않는다(나중 수정을 덮지 않게)
    copied: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for kind, doc_id, body in list(st.dump_workspace(legacy)):
        if kind in SKIP_KINDS:
            continue
        if st.get(kind, target, doc_id) is not None:
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        st.put(kind, target, doc_id, body)
        copied[kind] = copied.get(kind, 0) + 1

    # 3) 예전 워크스페이스는 지우지 않고 '이전됨' 표시 — 예전 팀 색인은 거둔다
    root.update({"workspace_id": legacy, "migrated_to": target, "migrated_at": time.time(),
                 "migrated_by": uid, "tenant_slug": t.slug})
    st.put("workspace", legacy, "root", root)
    dissolve(legacy)
    forget(f"pending:{legacy}", f"tenants:{uid}", f"lead:{uid}",
           *(k for a in added for k in (f"tenants:{a}", f"lead:{a}")))
    from .team import _forget as forget_team
    forget_team(uid)

    report = {"from": legacy, "to": target, "tenant": t.slug, "copied": copied,
              "skipped_existing": skipped, "members_added": added}
    with registry().control_tx() as c:
        c.execute("""insert into platform_audit (actor, action, tenant_id, target, detail)
                     values (%s, 'workspace.migrate', %s, %s, %s)""",
                  (uid, t.id, legacy, Jsonb(report)))
    from ..chat.db import tenant_admin_tx
    with tenant_admin_tx(t) as conn:
        conn.execute("insert into audit_log (actor, action, target, detail) values (%s, %s, %s, %s)",
                     (uid, "workspace.migrate", legacy, Jsonb(report)))
    return report


def status(uid: str, tenant_hint: str = "") -> dict:
    from .store import get_saas_store
    r = resolve(uid, tenant_hint)
    counts = {}
    can = False
    if r.pending:
        st = get_saas_store()
        counts = {k: len(st.list(k, r.legacy_ws, limit=500)) for k in DATA_KINDS}
        # migrate()와 같은 조건 — 버튼을 보여 놓고 403을 주지 않게
        if r.legacy_ws == f"ws-{uid}" and r.tenant:
            from ..chat import service as chat_svc
            from ..chat.db import registry
            can = bool(chat_svc.me(registry().tenant(r.tenant["slug"]), uid)["can"].get("member.invite"))
    return {"workspace_id": r.workspace_id,
            "mode": "tenant" if r.workspace_id.startswith("t-") else "legacy",
            "tenant": r.tenant, "tenants": lead_tenants(uid),
            "migration": {"pending": r.pending, "legacy_ws": r.legacy_ws,
                          "can_migrate": can,
                          "counts": counts}}
