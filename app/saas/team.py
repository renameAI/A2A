"""팀 워크스페이스 + 채널 채팅 — 슬랙형 협업 계층.

워크스페이스 공유는 **저장소 키를 바꾸지 않고** 푼다. 모든 문서가 이미
(kind, workspace_id, doc_id)로 저장되므로, 여러 사용자가 같은 workspace_id로
해석되기만 하면 요청·후보·초안·원장이 그대로 공유된다. 그래서 이 모듈이 하는
일은 세 가지뿐이다:

1. uid → workspace_id 해석 (멤버십 색인). 색인이 없으면 예전과 똑같이
   ws-{uid} — 혼자 쓰는 사용자의 동작은 한 글자도 바뀌지 않는다.
2. 초대·수락·내보내기·나가기. 초대는 **자동 수락하지 않는다** — 자기 자료가
   있는 사용자가 로그인했더니 남의 워크스페이스가 떠 있으면 그건 사고다.
3. 채널 메시지·스레드·읽음 표시. 채널 = #general + Lead Request 하나당 하나.

문서 배치:
- ("member", ws, uid)                 팀 명부
- ("member_of", TEAM, uid)            uid → ws 색인 (전역)
- ("invite", ws, email)               보낸 초대
- ("invite_for", TEAM, email)         email → 초대한 ws 색인 (전역)
- ("chat_msg", ws, "{ch}::m::{ts}")   채널 본문 메시지
- ("chat_msg", ws, "{ch}::t::{thread}::{ts}")  스레드 답글
- ("thread_meta", ws, "{ch}::{thread}")        답글 수·마지막 시각
- ("channel_meta", ws, ch)            채널 마지막 메시지 시각
- ("chat_read", ws, "{uid}::{ch}")    내가 읽은 시각 + 안 읽은 멘션 수
- ("presence", ws, uid)               마지막 접속 시각

메시지 키에 시각을 박는 이유: list_prefix가 doc_id 순서로 읽으므로, 수정
(삭제 표시)이 순서를 흔들지 않는다.
"""
import re
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..errors import EngineError
from .auth import SaasUser, current_user
from .store import get_saas_store

TEAM = "__team__"          # 전역 색인 전용 가상 워크스페이스
GENERAL = "general"

router = APIRouter(prefix="/saas", tags=["saas-team"])


def _now() -> float:
    return time.time()


def _norm_email(e: str) -> str:
    return (e or "").strip().lower()


# ── 워크스페이스 해석 ───────────────────────────────────────────────
# 매 요청 저장소 왕복을 피하는 짧은 캐시. TTL이 짧은 이유: 내보낸 팀원의
# 접근이 다른 프로세스에서 늦게 끊기면 안 된다. 이 프로세스에서 바뀐 것은
# _forget()으로 즉시 무효화한다.
_WS_TTL = 20.0
_ws_cache: "dict[str, tuple[str, float]]" = {}


def resolve_workspace(uid: str) -> str:
    hit = _ws_cache.get(uid)
    now = _now()
    if hit and hit[1] > now:
        return hit[0]
    idx = get_saas_store().get("member_of", TEAM, uid)
    ws = (idx or {}).get("ws") or f"ws-{uid}"
    if len(_ws_cache) > 2000:
        _ws_cache.clear()
    _ws_cache[uid] = (ws, now + _WS_TTL)
    return ws


def _forget(uid: str) -> None:
    _ws_cache.pop(uid, None)


def is_owner(user: SaasUser) -> bool:
    return user.workspace_id == f"ws-{user.uid}"


def _ensure_owner_row(store, user: SaasUser) -> None:
    if is_owner(user) and store.get("member", user.workspace_id, user.uid) is None:
        store.put("member", user.workspace_id, user.uid,
                  {"uid": user.uid, "email": user.email, "role": "owner",
                   "joined": _now()})


def dissolve(ws: str) -> None:
    """워크스페이스 파기 전에 팀원들의 색인을 거둔다 — 남겨 두면 그들은
    사라진 워크스페이스를 가리키는 색인을 들고 빈 화면을 보게 된다."""
    store = get_saas_store()
    for m in store.list("member", ws):
        uid = m.get("uid", "")
        if uid and store.get("member_of", TEAM, uid) == {"ws": ws}:
            store.delete("member_of", TEAM, uid)
            _forget(uid)
    for inv in store.list("invite", ws):
        email = inv.get("email", "")
        if (store.get("invite_for", TEAM, email) or {}).get("ws") == ws:
            store.delete("invite_for", TEAM, email)


# ── 접속 표시 ───────────────────────────────────────────────────────
_PRESENCE_EVERY = 20.0
_presence_seen: "dict[tuple[str, str], float]" = {}


def touch_presence(user: SaasUser) -> None:
    key = (user.workspace_id, user.uid)
    now = _now()
    if _presence_seen.get(key, 0) + _PRESENCE_EVERY > now:
        return          # 폴링마다 쓰지 않는다 — 쓰기 폭주 방지
    _presence_seen[key] = now
    get_saas_store().put("presence", user.workspace_id, user.uid, {"at": now})


# ── 팀 ──────────────────────────────────────────────────────────────

@router.get("/team")
def get_team(user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    ws = user.workspace_id
    _ensure_owner_row(store, user)
    touch_presence(user)
    seen = {p_uid: p.get("at") for p_uid, p in
            ((m.get("uid"), store.get("presence", ws, m.get("uid", "")) or {})
             for m in store.list("member", ws))}
    members = sorted(
        ({**m, "last_seen": seen.get(m.get("uid"))}
         for m in store.list("member", ws)),
        key=lambda m: (m.get("role") != "owner", m.get("joined") or 0))
    pending = store.get("invite_for", TEAM, _norm_email(user.email))
    if pending and pending.get("ws") == ws:
        pending = None      # 이미 그 워크스페이스에 있다
    return {"workspace_id": ws,
            "me": {"uid": user.uid, "email": user.email,
                   "role": "owner" if is_owner(user) else "member"},
            "members": members,
            "invites": store.list("invite", ws) if is_owner(user) else [],
            "pending_invite": pending}


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/team/invites")
def invite(body: InviteIn, user: SaasUser = Depends(current_user)):
    if not is_owner(user):
        raise EngineError(403, "forbidden", "초대는 워크스페이스 소유자만 할 수 있어요")
    email = _norm_email(body.email)
    if not _EMAIL.match(email):
        raise EngineError(400, "invalid_input", "이메일 형식이 아닙니다")
    if email == _norm_email(user.email):
        raise EngineError(400, "invalid_input", "자기 자신은 초대할 수 없어요")
    store = get_saas_store()
    ws = user.workspace_id
    if any(_norm_email(m.get("email", "")) == email
           for m in store.list("member", ws)):
        raise EngineError(409, "invalid_state", "이미 팀원이에요")
    other = store.get("invite_for", TEAM, email)
    if other and other.get("ws") != ws:
        raise EngineError(409, "invalid_state",
                          "다른 워크스페이스의 초대를 기다리는 사용자예요")
    doc = {"email": email, "invited_by": user.email or user.uid, "at": _now()}
    store.put("invite", ws, email, doc)
    store.put("invite_for", TEAM, email, {"ws": ws, "invited_by": doc["invited_by"]})
    # 초대해도 접근 허용 목록(SAAS_ALLOWED_USERS)은 그대로 적용된다 — 초대가
    # 인증 관문을 여는 뒷문이 되면 안 된다. 화면이 그 사실을 먼저 말하게 한다.
    from ..config import get_settings
    allowed = email in get_settings().saas_allowed_users
    return {**doc, "allowed": allowed}


@router.delete("/team/invites/{email}")
def cancel_invite(email: str, user: SaasUser = Depends(current_user)):
    if not is_owner(user):
        raise EngineError(403, "forbidden", "초대 취소는 소유자만 할 수 있어요")
    store = get_saas_store()
    email = _norm_email(email)
    ok = store.delete("invite", user.workspace_id, email)
    if (store.get("invite_for", TEAM, email) or {}).get("ws") == user.workspace_id:
        store.delete("invite_for", TEAM, email)
    return {"cancelled": ok}


@router.post("/team/accept")
def accept(user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    email = _norm_email(user.email)
    inv = store.get("invite_for", TEAM, email)
    if not inv:
        raise EngineError(404, "not_found", "받은 초대가 없어요")
    target = inv["ws"]
    own = f"ws-{user.uid}"
    if user.workspace_id == own and any(
            m.get("uid") != user.uid for m in store.list("member", own)):
        raise EngineError(409, "invalid_state",
                          "내 워크스페이스에 팀원이 있어 옮길 수 없어요 — "
                          "먼저 팀원을 내보내세요")
    store.put("member", target, user.uid,
              {"uid": user.uid, "email": user.email, "role": "member",
               "joined": _now()})
    store.put("member_of", TEAM, user.uid, {"ws": target})
    store.delete("invite", target, email)
    store.delete("invite_for", TEAM, email)
    _forget(user.uid)
    return {"workspace_id": target}


@router.post("/team/decline")
def decline(user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    email = _norm_email(user.email)
    inv = store.get("invite_for", TEAM, email)
    if inv:
        store.delete("invite", inv["ws"], email)
        store.delete("invite_for", TEAM, email)
    return {"declined": bool(inv)}


@router.delete("/team/members/{uid}")
def remove_member(uid: str, user: SaasUser = Depends(current_user)):
    if not is_owner(user):
        raise EngineError(403, "forbidden", "팀원 내보내기는 소유자만 할 수 있어요")
    if uid == user.uid:
        raise EngineError(400, "invalid_input", "소유자는 자신을 내보낼 수 없어요")
    store = get_saas_store()
    if not store.delete("member", user.workspace_id, uid):
        raise EngineError(404, "not_found", "그런 팀원이 없어요")
    if (store.get("member_of", TEAM, uid) or {}).get("ws") == user.workspace_id:
        store.delete("member_of", TEAM, uid)
    _forget(uid)
    return {"removed": uid}


@router.post("/team/leave")
def leave(user: SaasUser = Depends(current_user)):
    if is_owner(user):
        raise EngineError(409, "invalid_state", "소유자는 자기 워크스페이스를 떠날 수 없어요")
    store = get_saas_store()
    store.delete("member", user.workspace_id, user.uid)
    store.delete("member_of", TEAM, user.uid)
    _forget(user.uid)
    return {"workspace_id": f"ws-{user.uid}"}


# ── 채널 채팅 ───────────────────────────────────────────────────────

_THREAD = re.compile(r"^[A-Za-z0-9_.:\-]{1,120}$")
_MENTION = re.compile(r"@([^\s@,]+(?:@[^\s@,]+)?)")


def _check_channel(store, user: SaasUser, ch: str) -> None:
    if ch == GENERAL:
        return
    if store.get("lead_request", user.workspace_id, ch) is None:
        raise EngineError(404, "not_found", f"채널 {ch} 없음")


def _ts_key(ts: float) -> str:
    return f"{int(ts * 1000):013d}-{uuid.uuid4().hex[:6]}"


def _mentioned(store, ws: str, text: str, author: str) -> list[str]:
    """@메일 또는 @메일앞부분을 팀원 uid로. 모르는 이름은 조용히 무시한다 —
    본문은 그대로 남으므로 정보가 사라지지 않는다."""
    tokens = {t.lower().rstrip(".,!?)") for t in _MENTION.findall(text)}
    if not tokens:
        return []
    out = []
    for m in store.list("member", ws):
        email = _norm_email(m.get("email", ""))
        if m.get("uid") != author and (email in tokens
                                       or email.split("@")[0] in tokens):
            out.append(m["uid"])
    return out


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    thread: "str | None" = None
    kind: Literal["text", "activity"] = "text"


@router.post("/chat/{ch}/messages")
def post_message(ch: str, body: MessageIn,
                 user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    ws = user.workspace_id
    _check_channel(store, user, ch)
    text = body.text.strip()
    if not text:
        raise EngineError(400, "invalid_input", "빈 메시지는 보낼 수 없어요")
    if body.thread is not None and not _THREAD.match(body.thread):
        raise EngineError(400, "invalid_input", "스레드 키 형식이 아닙니다")
    ts = _now()
    key = (f"{ch}::t::{body.thread}::{_ts_key(ts)}" if body.thread
           else f"{ch}::m::{_ts_key(ts)}")
    mentions = _mentioned(store, ws, text, user.uid)
    msg = {"id": key, "channel": ch, "thread": body.thread, "text": text,
           "kind": body.kind, "ts": ts, "mentions": mentions,
           "author": {"uid": user.uid, "email": user.email}}
    store.put("chat_msg", ws, key, msg)
    if body.thread:
        tk = f"{ch}::{body.thread}"
        meta = store.get("thread_meta", ws, tk) or {"count": 0}
        store.put("thread_meta", ws, tk,
                  {"thread": body.thread, "count": meta["count"] + 1,
                   "last_ts": ts, "last_by": user.email})
    else:
        store.put("channel_meta", ws, ch,
                  {"channel": ch, "last_ts": ts, "last_by": user.uid})
    for uid in mentions:
        rk = f"{uid}::{ch}"
        r = store.get("chat_read", ws, rk) or {"ts": 0, "mentions": 0}
        r["mentions"] = r.get("mentions", 0) + 1
        store.put("chat_read", ws, rk, r)
    touch_presence(user)
    return msg


@router.get("/chat/{ch}/messages")
def list_messages(ch: str, since: float = 0, thread: "str | None" = None,
                  limit: int = 200, user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    ws = user.workspace_id
    _check_channel(store, user, ch)
    if thread is not None and not _THREAD.match(thread):
        raise EngineError(400, "invalid_input", "스레드 키 형식이 아닙니다")
    prefix = f"{ch}::t::{thread}::" if thread else f"{ch}::m::"
    rows = store.list_prefix("chat_msg", ws, prefix,
                             limit=max(1, min(limit, 500)))
    msgs = sorted((m for m in rows if m.get("ts", 0) > since),
                  key=lambda m: m["id"])
    out = {"messages": msgs, "now": _now()}
    if not thread:
        out["threads"] = {t["thread"]: {"count": t["count"],
                                        "last_ts": t["last_ts"]}
                          for t in store.list_prefix("thread_meta", ws, f"{ch}::")}
    touch_presence(user)
    return out


@router.delete("/chat/{ch}/messages")
def delete_message(ch: str, id: str, user: SaasUser = Depends(current_user)):
    """자기 메시지만 지운다. 흔적은 남긴다 — 답글이 달린 메시지가 통째로
    사라지면 스레드가 뿌리 없이 떠다닌다."""
    store = get_saas_store()
    ws = user.workspace_id
    if not id.startswith(f"{ch}::"):
        raise EngineError(400, "invalid_input", "채널과 메시지가 맞지 않아요")
    m = store.get("chat_msg", ws, id)
    if m is None:
        raise EngineError(404, "not_found", "메시지 없음")
    if (m.get("author") or {}).get("uid") != user.uid:
        raise EngineError(403, "forbidden", "자기 메시지만 지울 수 있어요")
    m.update({"text": "", "deleted": True, "mentions": []})
    store.put("chat_msg", ws, id, m)
    return m


class ReadIn(BaseModel):
    ts: float


@router.post("/chat/{ch}/read")
def mark_read(ch: str, body: ReadIn, user: SaasUser = Depends(current_user)):
    store = get_saas_store()
    _check_channel(store, user, ch)
    rk = f"{user.uid}::{ch}"
    prev = store.get("chat_read", user.workspace_id, rk) or {"ts": 0}
    store.put("chat_read", user.workspace_id, rk,
              {"ts": max(prev.get("ts", 0), body.ts), "mentions": 0})
    return {"ok": True}


@router.get("/chat/channels")
def channel_states(user: SaasUser = Depends(current_user)):
    """사이드바용 — 채널마다 마지막 메시지 시각, 내가 읽은 시각, 안 읽은 멘션.
    안 읽음 여부는 화면이 last_ts > read_ts로 판정한다(내가 쓴 마지막
    메시지는 안 읽음이 아니다 — last_by로 거른다)."""
    store = get_saas_store()
    ws = user.workspace_id
    out: dict = {}
    for meta in store.list("channel_meta", ws):
        ch = meta.get("channel")
        if not ch:
            continue
        r = store.get("chat_read", ws, f"{user.uid}::{ch}") or {}
        out[ch] = {"last_ts": meta.get("last_ts", 0),
                   "mine": meta.get("last_by") == user.uid,
                   "read_ts": r.get("ts", 0),
                   "mentions": r.get("mentions", 0)}
    touch_presence(user)
    return {"channels": out, "now": _now()}
