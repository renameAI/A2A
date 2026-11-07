"""메시징 서비스 — 판정(authz) → 실행(셀 트랜잭션) → 전파(events).

규칙:
- 사용자 동작은 tenant_tx(앱 역할, RLS 적용)에서 실행한다. 관리 동작(멤버·역할·
  정책 변경)만 판정을 통과한 뒤 tenant_admin_tx(owner)로 실행한다.
- 실시간 전파는 **커밋 뒤에** 한다. 커밋 전에 알리면 구독자가 아직 보이지 않는
  메시지를 조회해 빈손으로 돌아가고, 롤백되면 존재하지 않는 메시지를 알린 셈이 된다.
- 순서는 seq가 정한다. 시계(created_at)는 표시용이다.
"""
import re
import time
import uuid

import psycopg
from psycopg.types.json import Jsonb

from ..errors import EngineError
from . import authz, events
from .db import Tenant, registry, tenant_admin_tx, tenant_tx

MAX_BODY = 8000
PAGE_MAX = 200
_MENTION = re.compile(r"@([A-Za-z0-9._%+\-]+(?:@[A-Za-z0-9.\-]+)?)")


def _principal(conn, uid: str) -> "tuple[authz.Principal, list[dict]]":
    m = conn.execute("""select m.user_id, m.role, m.attrs, m.status, r.permissions
                          from members m join roles r on r.name = m.role
                         where m.user_id = %s""", (uid,)).fetchone()
    if m is None:
        raise EngineError(403, "forbidden", "이 테넌트의 멤버가 아닙니다")
    pols = conn.execute("select id, effect, actions, condition, description "
                        "from policies").fetchall()
    return (authz.Principal(uid=uid, role=m["role"], permissions=set(m["permissions"]),
                            attrs=m["attrs"] or {}, active=m["status"] == "active"),
            [dict(p) for p in pols])


def _require(conn, uid: str, action: str, channel: "dict | None" = None,
             channel_role: "str | None" = None) -> authz.Principal:
    p, pols = _principal(conn, uid)
    d = authz.check(p, action, channel=channel, channel_role=channel_role, policies=pols)
    if not d.allowed:
        raise EngineError(403, "forbidden", d.reason)
    return p


def _channel(conn, channel_id: int, uid: str) -> "tuple[dict, str | None]":
    """RLS를 통과한 채널만 보인다 — 없으면 '없음'(존재 여부도 새지 않게 404)."""
    ch = conn.execute("""select id, name, topic, visibility, kind, attrs, archived_at,
                                last_seq, last_top_seq, created_by
                           from channels where id = %s""", (channel_id,)).fetchone()
    if ch is None:
        raise EngineError(404, "not_found", "채널 없음")
    cm = conn.execute("select role from channel_members where channel_id = %s and user_id = %s",
                      (channel_id, uid)).fetchone()
    return dict(ch), (cm["role"] if cm else None)


def _authz_channel(ch: dict) -> dict:
    return {"id": ch["id"], "visibility": ch["visibility"], "kind": ch["kind"],
            "attrs": ch.get("attrs") or {}}


# ── 채널 ───────────────────────────────────────────────────────────

def list_channels(t: Tenant, uid: str) -> list[dict]:
    with tenant_tx(t, uid) as conn:
        _require(conn, uid, "channel.read")
        rows = conn.execute("""
            select c.id, c.name, c.topic, c.visibility, c.kind, c.archived_at,
                   c.last_seq, c.last_top_seq, c.last_message_at,
                   cm.user_id is not null as joined, coalesce(cm.last_read_seq, 0) as last_read_seq,
                   cm.role as channel_role, coalesce(cm.muted, false) as muted,
                   (select count(*) from mentions mn
                     where mn.user_id = %(u)s and mn.channel_id = c.id
                       and mn.seq > coalesce(cm.last_read_seq, 0)) as mentions
              from channels c
              left join channel_members cm on cm.channel_id = c.id and cm.user_id = %(u)s
             where c.archived_at is null
             order by c.kind = 'announcements' desc, c.name""", {"u": uid}).fetchall()
    return [{**r, "unread": r["last_top_seq"] > r["last_read_seq"]} for r in rows]


def visible_channel_ids(t: Tenant, uid: str, ids: "list[int] | None" = None) -> set[int]:
    """구독 권한 확인용 가벼운 질의 — RLS를 통과한 채널 id만. list_channels는
    멘션 집계 하위 질의가 채널마다 돌아 무겁다(실측: 1,000개 스트림이 동시에
    재확인하자 게시 p50이 3.8ms → 19초로 무너졌다)."""
    with tenant_tx(t, uid) as conn:
        p, pols = _principal(conn, uid)
        if not p.active:
            return set()
        if ids:
            rows = conn.execute("select id from channels where archived_at is null "
                                "and id = any(%s)", (list(ids),)).fetchall()
        else:
            rows = conn.execute("select id from channels where archived_at is null").fetchall()
    return {r["id"] for r in rows}


def create_channel(t: Tenant, uid: str, name: str, *, visibility: str = "public",
                   topic: str = "", attrs: "dict | None" = None) -> dict:
    name = (name or "").strip().lower()
    if not re.match(r"^[a-z0-9가-힣][a-z0-9가-힣_\-]{0,79}$", name):
        raise EngineError(400, "invalid_input", "채널 이름은 소문자·숫자·한글·-·_ 80자 이내")
    if visibility not in ("public", "private"):
        raise EngineError(400, "invalid_input", "visibility는 public|private")
    action = "channel.create_private" if visibility == "private" else "channel.create"
    with tenant_tx(t, uid) as conn:
        _require(conn, uid, action,
                 channel={"visibility": visibility, "attrs": attrs or {}})
    with tenant_admin_tx(t) as conn:
        try:
            ch = conn.execute("""insert into channels (name, topic, visibility, attrs, created_by)
                                 values (%s, %s, %s, %s, %s)
                                 returning id, name, topic, visibility, kind, last_seq""",
                              (name, topic, visibility, Jsonb(attrs or {}), uid)).fetchone()
        except psycopg.errors.UniqueViolation as e:
            raise EngineError(409, "conflict", f"이미 있는 채널: {name}") from e
        conn.execute("""insert into channel_members (channel_id, user_id, role)
                        values (%s, %s, 'channel_admin')""", (ch["id"], uid))
        conn.execute("insert into audit_log (actor, action, target) values (%s, %s, %s)",
                     (uid, "channel.create", f"{ch['id']}:{name}:{visibility}"))
    return dict(ch)


def set_channel_member(t: Tenant, uid: str, channel_id: int, target: str, on: bool,
                       role: str = "member") -> None:
    with tenant_tx(t, uid) as conn:
        ch, my_role = _channel(conn, channel_id, uid)
        self_join = target == uid and on and ch["visibility"] == "public"
        self_leave = target == uid and not on
        if not (self_join or self_leave):
            _require(conn, uid, "channel.manage_members", _authz_channel(ch), my_role)
    with tenant_admin_tx(t) as conn:
        if on:
            ok = conn.execute("select 1 from members where user_id = %s and status = 'active'",
                              (target,)).fetchone()
            if not ok:
                raise EngineError(404, "not_found", "그런 멤버가 없습니다")
            conn.execute("""insert into channel_members (channel_id, user_id, role)
                            values (%s, %s, %s)
                            on conflict (channel_id, user_id) do update set role = excluded.role""",
                         (channel_id, target, role))
        else:
            conn.execute("delete from channel_members where channel_id = %s and user_id = %s",
                         (channel_id, target))
        conn.execute("insert into audit_log (actor, action, target) values (%s, %s, %s)",
                     (uid, "channel.member_add" if on else "channel.member_remove",
                      f"{channel_id}:{target}"))
    events.publish(t, channel_id, {"type": "membership", "user": target, "on": on})


def archive_channel(t: Tenant, uid: str, channel_id: int) -> None:
    with tenant_tx(t, uid) as conn:
        ch, my_role = _channel(conn, channel_id, uid)
        if ch["kind"] != "channel":
            raise EngineError(409, "invalid_state", "기본 채널은 보관할 수 없습니다")
        _require(conn, uid, "channel.archive", _authz_channel(ch), my_role)
    with tenant_admin_tx(t) as conn:
        conn.execute("update channels set archived_at = now() where id = %s", (channel_id,))
        conn.execute("insert into audit_log (actor, action, target) values (%s, 'channel.archive', %s)",
                     (uid, str(channel_id)))


# ── 메시지 ─────────────────────────────────────────────────────────

def _resolve_mentions(conn, body: str) -> list[str]:
    toks = {t.lower() for t in _MENTION.findall(body)}
    if not toks:
        return []
    rows = conn.execute("select user_id, email from members where status = 'active'").fetchall()
    out = []
    for r in rows:
        email = r["email"].lower()
        if r["user_id"].lower() in toks or email in toks or email.split("@")[0] in toks:
            out.append(r["user_id"])
    return out


def post_message(t: Tenant, uid: str, channel_id: int, body: str, *,
                 parent_seq: "int | None" = None, client_msg_id: "str | None" = None,
                 kind: str = "text") -> dict:
    body = (body or "").strip()
    if not body:
        raise EngineError(400, "invalid_input", "빈 메시지")
    if len(body) > MAX_BODY:
        raise EngineError(400, "invalid_input", f"메시지는 {MAX_BODY}자 이내")
    if kind not in ("text", "activity"):
        raise EngineError(400, "invalid_input", "kind는 text|activity")
    if client_msg_id is not None and not re.match(r"^[A-Za-z0-9_\-]{8,64}$", client_msg_id):
        raise EngineError(400, "invalid_input", "client_msg_id 형식 오류")
    with tenant_tx(t, uid) as conn:
        ch, my_role = _channel(conn, channel_id, uid)
        if ch["archived_at"] is not None:
            raise EngineError(409, "invalid_state", "보관된 채널입니다")
        if ch["kind"] == "announcements":
            _require(conn, uid, "channel.manage_members", _authz_channel(ch), my_role)
        _require(conn, uid, "message.post", _authz_channel(ch), my_role)
        mentions = _resolve_mentions(conn, body)
        try:
            r = conn.execute("select * from post_message(%s, %s, %s, %s, %s, %s, %s)",
                             (channel_id, uid, body, kind, parent_seq, client_msg_id,
                              mentions)).fetchone()
        except psycopg.errors.NoDataFound as e:
            raise EngineError(404, "not_found", str(e).split("\n")[0]) from e
        except psycopg.errors.InsufficientPrivilege as e:
            raise EngineError(403, "forbidden", "이 채널에 쓸 수 없습니다") from e
    msg = {"channel_id": channel_id, "seq": r["seq"], "parent_seq": parent_seq,
           "author": uid, "body": body, "kind": kind, "created_at": r["created_at"],
           "mentions": mentions, "duplicate": r["duplicate"], "client_msg_id": client_msg_id,
           "_acked": time.time()}
    if not r["duplicate"]:
        events.publish(t, channel_id, {"type": "message", "seq": r["seq"],
                                       "parent_seq": parent_seq})
    return msg


_COLS = """channel_id, seq, parent_seq, author, case when deleted_at is null then body else '' end as body,
           kind, created_at, edited_at, deleted_at is not null as deleted, reply_count, last_reply_at,
           client_msg_id"""


def list_messages(t: Tenant, uid: str, channel_id: int, *, before: "int | None" = None,
                  after: "int | None" = None, limit: int = 50,
                  thread: "int | None" = None) -> dict:
    """커서 페이지네이션. before=과거로(무한 스크롤), after=미래로(공백 보충).
    thread가 있으면 그 루트의 답글만, 없으면 본문만."""
    limit = max(1, min(limit, PAGE_MAX))
    with tenant_tx(t, uid) as conn:
        ch, my_role = _channel(conn, channel_id, uid)
        _require(conn, uid, "channel.read", _authz_channel(ch), my_role)
        where = ["channel_id = %(c)s",
                 "parent_seq = %(th)s" if thread is not None else "parent_seq is null"]
        params = {"c": channel_id, "th": thread, "lim": limit + 1}
        if after is not None:
            where.append("seq > %(a)s"); params["a"] = after; order = "asc"
        else:
            if before is not None:
                where.append("seq < %(b)s"); params["b"] = before
            order = "desc"
        rows = conn.execute(f"select {_COLS} from messages where {' and '.join(where)} "
                            f"order by seq {order} limit %(lim)s", params).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        if order == "desc":
            rows.reverse()
        seqs = [r["seq"] for r in rows]
        reacts = conn.execute("""select seq, emoji, count(*) as n,
                                        bool_or(user_id = %s) as mine
                                   from reactions where channel_id = %s and seq = any(%s)
                                  group by seq, emoji order by seq, emoji""",
                              (uid, channel_id, seqs)).fetchall() if seqs else []
        root = None
        if thread is not None:
            root = conn.execute(f"select {_COLS} from messages where channel_id = %s "
                                f"and seq = %s and parent_seq is null",
                                (channel_id, thread)).fetchone()
            if root is None:
                raise EngineError(404, "not_found", "스레드 루트 없음")
    # 핫 데이터가 바닥나면 아카이브로 이어 읽는다(과거 방향일 때만). 권한은 위에서
    # 이미 확인했다 — 아카이브 읽기는 owner 경로지만 판정을 우회하지 않는다.
    archived: list[dict] = []
    if after is None and not more:
        floor = rows[0]["seq"] if rows else before
        want = limit - len(rows)
        if want > 0:
            from . import archive
            got = archive.read_archived(t, channel_id, before=floor, limit=want + 1,
                                        thread=thread)
            more = len(got) > want
            archived = got[-want:]
    by_seq: dict[int, list] = {}
    for rr in reacts:
        by_seq.setdefault(rr["seq"], []).append(
            {"emoji": rr["emoji"], "count": rr["n"], "mine": rr["mine"]})
    msgs = archived + [{**r, "reactions": by_seq.get(r["seq"], []), "archived": False}
                       for r in rows]
    if root is not None:
        # 루트에도 본문 메시지와 같은 모양(리액션 포함)을 준다 — 빠지면 화면이 터졌다(실측)
        root = {**root, "archived": False,
                "reactions": _reactions_for(t, uid, channel_id, [root["seq"]]).get(root["seq"], [])}
    return {"channel": {"id": ch["id"], "name": ch["name"], "last_seq": ch["last_seq"]},
            "messages": msgs, "has_more": more, "root": root}


def _reactions_for(t: Tenant, uid: str, channel_id: int, seqs: list[int]) -> dict[int, list]:
    if not seqs:
        return {}
    with tenant_tx(t, uid) as conn:
        rows = conn.execute("""select seq, emoji, count(*) as n, bool_or(user_id = %s) as mine
                                 from reactions where channel_id = %s and seq = any(%s)
                                group by seq, emoji order by seq, emoji""",
                            (uid, channel_id, seqs)).fetchall()
    out: dict[int, list] = {}
    for r in rows:
        out.setdefault(r["seq"], []).append({"emoji": r["emoji"], "count": r["n"], "mine": r["mine"]})
    return out


def edit_message(t: Tenant, uid: str, channel_id: int, seq: int, body: str) -> None:
    body = (body or "").strip()
    if not body or len(body) > MAX_BODY:
        raise EngineError(400, "invalid_input", "본문 길이 오류")
    with tenant_tx(t, uid) as conn:
        ch, my_role = _channel(conn, channel_id, uid)
        _require(conn, uid, "message.edit_own", _authz_channel(ch), my_role)
        ok = conn.execute("select edit_message(%s, %s, %s, %s) as ok",
                          (channel_id, seq, uid, body)).fetchone()["ok"]
    if not ok:
        raise EngineError(404, "not_found", "수정할 수 있는 내 메시지가 아닙니다")
    events.publish(t, channel_id, {"type": "edit", "seq": seq})


def delete_message(t: Tenant, uid: str, channel_id: int, seq: int) -> None:
    with tenant_tx(t, uid) as conn:
        ch, my_role = _channel(conn, channel_id, uid)
        author = conn.execute("select author from messages where channel_id = %s and seq = %s",
                              (channel_id, seq)).fetchone()
        if author is None:
            raise EngineError(404, "not_found", "메시지 없음")
        mine = author["author"] == uid
        _require(conn, uid, "message.delete_own" if mine else "message.delete_any",
                 _authz_channel(ch), my_role)
        ok = conn.execute("select delete_message(%s, %s, %s, %s) as ok",
                          (channel_id, seq, uid, not mine)).fetchone()["ok"]
    if not ok:
        raise EngineError(404, "not_found", "이미 삭제됐거나 없는 메시지")
    events.publish(t, channel_id, {"type": "delete", "seq": seq})


def react(t: Tenant, uid: str, channel_id: int, seq: int, emoji: str, on: bool) -> None:
    with tenant_tx(t, uid) as conn:
        ch, my_role = _channel(conn, channel_id, uid)
        _require(conn, uid, "reaction.add", _authz_channel(ch), my_role)
        conn.execute("select react(%s, %s, %s, %s, %s)", (channel_id, seq, uid, emoji, on))
    events.publish(t, channel_id, {"type": "reaction", "seq": seq})


def mark_read(t: Tenant, uid: str, channel_id: int, seq: int) -> int:
    with tenant_tx(t, uid) as conn:
        _channel(conn, channel_id, uid)
        v = conn.execute("select mark_read(%s, %s, %s) as v", (channel_id, uid, seq)).fetchone()["v"]
    return v or 0


def search(t: Tenant, uid: str, q: str, limit: int = 30) -> list[dict]:
    q = (q or "").strip()
    if len(q) < 2:
        raise EngineError(400, "invalid_input", "검색어는 2자 이상")
    with tenant_tx(t, uid) as conn:
        _require(conn, uid, "channel.read")
        # RLS가 볼 수 없는 채널의 메시지를 결과에서 이미 걸러 낸다
        rows = conn.execute(f"""select {_COLS} from messages
                                 where deleted_at is null
                                   and to_tsvector('simple', body) @@ plainto_tsquery('simple', %s)
                                 order by created_at desc limit %s""",
                            (q, max(1, min(limit, 100)))).fetchall()
    return rows


# ── 멤버·역할·정책 관리 ─────────────────────────────────────────────

def add_member(t: Tenant, actor: str, uid: str, email: str, role: str = "member",
               attrs: "dict | None" = None) -> None:
    with tenant_tx(t, actor) as conn:
        _require(conn, actor, "member.invite")
        if role == "owner":
            p, _ = _principal(conn, actor)
            if p.role != "owner":
                raise EngineError(403, "forbidden", "소유자만 소유자를 지정할 수 있습니다")
        if not conn.execute("select 1 from roles where name = %s", (role,)).fetchone():
            raise EngineError(400, "invalid_input", f"없는 역할: {role}")
    from .provision import _add_member_admin
    _add_member_admin(t, uid, email, role, attrs or {})
    with tenant_admin_tx(t) as conn:
        conn.execute("insert into audit_log (actor, action, target, detail) values (%s, %s, %s, %s)",
                     (actor, "member.invite", uid, Jsonb({"role": role})))


def update_member(t: Tenant, actor: str, uid: str, *, role: "str | None" = None,
                  attrs: "dict | None" = None, active: "bool | None" = None) -> None:
    with tenant_tx(t, actor) as conn:
        _require(conn, actor, "member.update" if active is not False else "member.remove")
        target = conn.execute("select role from members where user_id = %s", (uid,)).fetchone()
        if target is None:
            raise EngineError(404, "not_found", "멤버 없음")
        me, _ = _principal(conn, actor)
        if (target["role"] == "owner" or role == "owner") and me.role != "owner":
            raise EngineError(403, "forbidden", "소유자 변경은 소유자만")
    with tenant_admin_tx(t) as conn:
        if role is not None:
            conn.execute("update members set role = %s where user_id = %s", (role, uid))
        if attrs is not None:
            conn.execute("update members set attrs = %s where user_id = %s", (Jsonb(attrs), uid))
        if active is not None:
            conn.execute("update members set status = %s where user_id = %s",
                         ("active" if active else "deactivated", uid))
            if not active:
                conn.execute("delete from channel_members where user_id = %s", (uid,))
        # 역할 강등이든 비활성화든 소유자가 0명이 되면 테넌트를 아무도 관리할 수
        # 없다. 같은 트랜잭션 안에서 검사하고 어기면 예외로 전체를 되돌린다.
        n = conn.execute("select count(*) as n from members where role = 'owner' "
                         "and status = 'active'").fetchone()["n"]
        if n == 0:
            raise EngineError(409, "invalid_state", "마지막 소유자를 강등·비활성화할 수 없습니다")
        conn.execute("insert into audit_log (actor, action, target, detail) values (%s, %s, %s, %s)",
                     (actor, "member.update", uid,
                      Jsonb({"role": role, "attrs": attrs, "active": active})))
    if active is False:
        with registry().control_tx() as c:
            c.execute("delete from tenant_users where tenant_id = %s and user_id = %s", (t.id, uid))


def upsert_role(t: Tenant, actor: str, name: str, permissions: list[str]) -> None:
    unknown = [p for p in permissions if p != "*" and p not in authz.PERMISSIONS
               and not (p.endswith(".*") and any(k.startswith(p[:-1]) for k in authz.PERMISSIONS))]
    if unknown:
        raise EngineError(400, "invalid_input", f"모르는 권한: {unknown}")
    with tenant_tx(t, actor) as conn:
        _require(conn, actor, "role.manage")
        if "*" in permissions:
            me, _ = _principal(conn, actor)
            if me.role != "owner":
                raise EngineError(403, "forbidden", "전권(*) 역할은 소유자만 만들 수 있습니다")
    with tenant_admin_tx(t) as conn:
        if name == "owner":
            raise EngineError(409, "invalid_state", "owner 역할은 바꿀 수 없습니다")
        conn.execute("""insert into roles (name, permissions) values (%s, %s)
                        on conflict (name) do update set permissions = excluded.permissions""",
                     (name, permissions))
        conn.execute("insert into audit_log (actor, action, target, detail) values (%s, %s, %s, %s)",
                     (actor, "role.upsert", name, Jsonb({"permissions": permissions})))


def add_policy(t: Tenant, actor: str, effect: str, actions: list[str], condition: dict,
               description: str = "") -> int:
    if effect not in ("allow", "deny") or not actions:
        raise EngineError(400, "invalid_input", "effect는 allow|deny, actions는 1개 이상")
    try:
        authz.validate_condition(condition)
    except (ValueError, TypeError, IndexError) as e:
        raise EngineError(400, "invalid_input", f"조건 형식 오류: {e}") from e
    with tenant_tx(t, actor) as conn:
        _require(conn, actor, "policy.manage")
    with tenant_admin_tx(t) as conn:
        pid = conn.execute("""insert into policies (effect, actions, condition, description, created_by)
                              values (%s, %s, %s, %s, %s) returning id""",
                           (effect, actions, Jsonb(condition), description, actor)).fetchone()["id"]
        conn.execute("insert into audit_log (actor, action, target, detail) values (%s, %s, %s, %s)",
                     (actor, "policy.add", str(pid),
                      Jsonb({"effect": effect, "actions": actions, "condition": condition})))
    return pid


def remove_policy(t: Tenant, actor: str, pid: int) -> None:
    with tenant_tx(t, actor) as conn:
        _require(conn, actor, "policy.manage")
    with tenant_admin_tx(t) as conn:
        conn.execute("delete from policies where id = %s", (pid,))
        conn.execute("insert into audit_log (actor, action, target) values (%s, 'policy.remove', %s)",
                     (actor, str(pid)))


def read_logs(t: Tenant, actor: str, which: str, limit: int = 100) -> list[dict]:
    if which not in ("audit_log", "access_log"):
        raise EngineError(400, "invalid_input", "audit_log|access_log")
    with tenant_tx(t, actor) as conn:
        _require(conn, actor, "audit.read")
        return conn.execute(f"select * from {which} order by id desc limit %s",
                            (max(1, min(limit, 500)),)).fetchall()


def new_client_id() -> str:
    return uuid.uuid4().hex


# ── 조회 (UI용) ────────────────────────────────────────────────────

def me(t: Tenant, uid: str) -> dict:
    """내 역할·권한 — 화면이 관리 기능을 보일지 정한다(판정은 여전히 서버가 한다)."""
    with tenant_tx(t, uid) as conn:
        p, pols = _principal(conn, uid)
        m = conn.execute("select email, display_name, avatar_hue from members where user_id = %s",
                         (uid,)).fetchone()
    can = {a: authz.check(p, a, policies=pols).allowed
           for a in ("channel.create", "channel.create_private", "member.invite",
                     "member.update", "role.manage", "policy.manage", "audit.read",
                     "export.run", "lead.access")}
    return {"tenant": {"slug": t.slug, "name": t.name, "plan": t.plan},
            "user_id": uid, "email": m["email"], "display_name": m["display_name"],
            "avatar_hue": m["avatar_hue"],
            "role": p.role, "attrs": p.attrs, "can": can}


_UNSET = object()


def update_me(t: Tenant, uid: str, *, display_name=_UNSET, avatar_hue=_UNSET) -> dict:
    """내 프로필(표시 이름·아바타 색) — 본인만 자기 것을 바꾼다(역할·권한은 그대로). 감사 로그에 남긴다.
    넘기지 않은 항목은 그대로 둔다. avatar_hue=None은 기본 색으로 되돌리기."""
    changes: dict = {}
    if display_name is not _UNSET:
        name = " ".join(str(display_name or "").split())
        if not 1 <= len(name) <= 40:
            raise EngineError(400, "invalid_input", "표시 이름은 1~40자")
        changes["display_name"] = name
    if avatar_hue is not _UNSET:
        if avatar_hue is not None and (isinstance(avatar_hue, bool) or not isinstance(avatar_hue, int)
                                       or not 0 <= avatar_hue <= 359):
            raise EngineError(400, "invalid_input", "아바타 색은 0~359")
        changes["avatar_hue"] = avatar_hue
    if not changes:
        raise EngineError(400, "invalid_input", "바꿀 항목이 없습니다")
    with tenant_tx(t, uid) as conn:
        p, _ = _principal(conn, uid)                # 멤버가 아니면 여기서 막힌다
    if not p.active:                                # 비활성화된 멤버는 _principal이 막지 않는다
        raise EngineError(403, "forbidden", "비활성화된 멤버입니다")
    with tenant_admin_tx(t) as conn:
        for col, val in changes.items():          # 열 이름은 위에서 정한 두 가지뿐
            conn.execute(f"update members set {col} = %s where user_id = %s", (val, uid))
        conn.execute("insert into audit_log (actor, action, target, detail) values (%s, %s, %s, %s)",
                     (uid, "profile.update", uid, Jsonb(changes)))
    return me(t, uid)


def list_members(t: Tenant, uid: str) -> list[dict]:
    """활성 멤버 — RLS가 게스트에게는 자기 자신만 보여 준다."""
    with tenant_tx(t, uid) as conn:
        _require(conn, uid, "channel.read")
        return conn.execute("""select user_id, email, display_name, avatar_hue, role, status, joined_at
                                 from members where status = 'active'
                                order by display_name""").fetchall()


def list_roles(t: Tenant, uid: str) -> dict:
    with tenant_tx(t, uid) as conn:
        _require(conn, uid, "channel.read")
        roles = conn.execute("select name, permissions, builtin, description from roles "
                             "order by builtin desc, name").fetchall()
        pols = conn.execute("select id, effect, actions, condition, description, created_by, "
                            "created_at from policies order by id").fetchall()
    return {"roles": roles, "policies": pols, "catalog": authz.PERMISSIONS}
