"""메시징 HTTP API — /chat/v1/...

인증은 기존 SaaS 인증(current_user)을 그대로 쓴다: 누구인지(uid·email)만 받고,
어느 테넌트의 무엇을 할 수 있는지는 테넌트 스키마(members·roles·policies)가
정한다. 동기 엔드포인트는 스레드풀에서 DB를 기다린다(psycopg 동기 풀).
"""
import asyncio
import json
import os
import random
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..errors import EngineError
from ..saas.auth import SaasUser, current_user
from . import collect, events, ratelimit, service as svc
from .db import registry

router = APIRouter(prefix="/chat/v1", tags=["chat"])

SSE_MAX_SEC = float(os.environ.get("CHAT_SSE_MAX_SEC", "280"))   # Vercel 함수 300초 안
SSE_RECHECK_SEC = float(os.environ.get("CHAT_SSE_RECHECK_SEC", "60"))


def _t(slug: str):
    return registry().tenant(slug)


@router.get("/me/tenants")
def my_tenants(user: SaasUser = Depends(current_user)):
    with registry().control_tx() as c:
        rows = c.execute("""select t.slug, t.name, t.plan, t.status from tenant_users tu
                              join tenants t on t.id = tu.tenant_id
                             where tu.user_id = %s and t.status <> 'deleted'
                             order by t.name""", (user.uid,)).fetchall()
    return {"tenants": rows}


@router.get("/t/{slug}/me")
def me(slug: str, user: SaasUser = Depends(current_user)):
    return svc.me(_t(slug), user.uid)


class MePatch(BaseModel):
    display_name: "str | None" = Field(default=None, min_length=1, max_length=40)
    avatar_hue: "int | None" = Field(default=None, ge=0, le=359)


@router.patch("/t/{slug}/me")
def update_me(slug: str, body: MePatch, user: SaasUser = Depends(current_user)):
    # 보낸 항목만 바꾼다 — {"avatar_hue": null}은 기본 색으로 되돌리기
    sent = {k: getattr(body, k) for k in body.model_fields_set}
    if sent.get("display_name", "") is None:
        sent.pop("display_name")
    return svc.update_me(_t(slug), user.uid, **sent)


# ── 업무 이벤트(A2A 브라우저 수집 계층) — browser/docs/HARNESS.md L1 ──
class CollectIn(BaseModel):
    mode: str
    events: list = Field(default_factory=list, max_length=500)
    blobs: dict = Field(default_factory=dict)


@router.post("/t/{slug}/collect/events")
def collect_events(slug: str, body: CollectIn, user: SaasUser = Depends(current_user)):
    return collect.ingest(_t(slug), user.uid, body.mode, body.events, body.blobs)


@router.get("/t/{slug}/collect/summary")
def collect_summary(slug: str, user: SaasUser = Depends(current_user)):
    return collect.summary(_t(slug), user.uid)


@router.get("/t/{slug}/members")
def members(slug: str, user: SaasUser = Depends(current_user)):
    return {"members": svc.list_members(_t(slug), user.uid)}


@router.get("/t/{slug}/roles")
def roles(slug: str, user: SaasUser = Depends(current_user)):
    return svc.list_roles(_t(slug), user.uid)


@router.get("/t/{slug}/channels")
def channels(slug: str, user: SaasUser = Depends(current_user)):
    return {"channels": svc.list_channels(_t(slug), user.uid)}


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    visibility: str = "public"
    topic: str = Field(default="", max_length=250)
    attrs: dict = Field(default_factory=dict)


@router.post("/t/{slug}/channels")
def create_channel(slug: str, body: ChannelIn, user: SaasUser = Depends(current_user)):
    return svc.create_channel(_t(slug), user.uid, body.name, visibility=body.visibility,
                              topic=body.topic, attrs=body.attrs)


class MemberIn(BaseModel):
    user_id: str
    role: str = "member"


@router.post("/t/{slug}/channels/{cid}/members")
def add_channel_member(slug: str, cid: int, body: MemberIn,
                       user: SaasUser = Depends(current_user)):
    svc.set_channel_member(_t(slug), user.uid, cid, body.user_id, True, body.role)
    return {"ok": True}


@router.delete("/t/{slug}/channels/{cid}/members/{target}")
def remove_channel_member(slug: str, cid: int, target: str,
                          user: SaasUser = Depends(current_user)):
    svc.set_channel_member(_t(slug), user.uid, cid, target, False)
    return {"ok": True}


@router.post("/t/{slug}/channels/{cid}/archive")
def archive(slug: str, cid: int, user: SaasUser = Depends(current_user)):
    svc.archive_channel(_t(slug), user.uid, cid)
    return {"ok": True}


@router.get("/t/{slug}/channels/{cid}/messages")
def messages(slug: str, cid: int, before: "int | None" = None, after: "int | None" = None,
             thread: "int | None" = None, limit: int = Query(50, ge=1, le=200),
             user: SaasUser = Depends(current_user)):
    return svc.list_messages(_t(slug), user.uid, cid, before=before, after=after,
                             thread=thread, limit=limit)


class PostIn(BaseModel):
    body: str = Field(min_length=1, max_length=svc.MAX_BODY)
    parent_seq: "int | None" = None
    client_msg_id: "str | None" = None
    kind: str = "text"


@router.post("/t/{slug}/channels/{cid}/messages")
def post(slug: str, cid: int, body: PostIn, user: SaasUser = Depends(current_user)):
    t = _t(slug)
    ratelimit.limiter().check_post(t.id, user.uid)
    return svc.post_message(t, user.uid, cid, body.body, parent_seq=body.parent_seq,
                            client_msg_id=body.client_msg_id, kind=body.kind)


class EditIn(BaseModel):
    body: str = Field(min_length=1, max_length=svc.MAX_BODY)


@router.patch("/t/{slug}/channels/{cid}/messages/{seq}")
def edit(slug: str, cid: int, seq: int, body: EditIn, user: SaasUser = Depends(current_user)):
    svc.edit_message(_t(slug), user.uid, cid, seq, body.body)
    return {"ok": True}


@router.delete("/t/{slug}/channels/{cid}/messages/{seq}")
def delete(slug: str, cid: int, seq: int, user: SaasUser = Depends(current_user)):
    svc.delete_message(_t(slug), user.uid, cid, seq)
    return {"ok": True}


class ReactIn(BaseModel):
    emoji: str = Field(min_length=1, max_length=32)
    on: bool = True


@router.post("/t/{slug}/channels/{cid}/messages/{seq}/reactions")
def react(slug: str, cid: int, seq: int, body: ReactIn, user: SaasUser = Depends(current_user)):
    svc.react(_t(slug), user.uid, cid, seq, body.emoji, body.on)
    return {"ok": True}


class ReadIn(BaseModel):
    seq: int = Field(ge=0)


@router.post("/t/{slug}/channels/{cid}/read")
def read(slug: str, cid: int, body: ReadIn, user: SaasUser = Depends(current_user)):
    return {"last_read_seq": svc.mark_read(_t(slug), user.uid, cid, body.seq)}


@router.get("/t/{slug}/search")
def search(slug: str, q: str, user: SaasUser = Depends(current_user)):
    return {"results": svc.search(_t(slug), user.uid, q)}


# ── 관리 ────────────────────────────────────────────────────────────

class InviteIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=3, max_length=200)
    role: str = "member"
    attrs: dict = Field(default_factory=dict)


@router.post("/t/{slug}/members")
def invite(slug: str, body: InviteIn, user: SaasUser = Depends(current_user)):
    svc.add_member(_t(slug), user.uid, body.user_id, body.email, body.role, body.attrs)
    return {"ok": True}


class MemberPatch(BaseModel):
    role: "str | None" = None
    attrs: "dict | None" = None
    active: "bool | None" = None


@router.patch("/t/{slug}/members/{uid}")
def update_member(slug: str, uid: str, body: MemberPatch, user: SaasUser = Depends(current_user)):
    svc.update_member(_t(slug), user.uid, uid, role=body.role, attrs=body.attrs, active=body.active)
    return {"ok": True}


class RoleIn(BaseModel):
    permissions: list[str]


@router.put("/t/{slug}/roles/{name}")
def put_role(slug: str, name: str, body: RoleIn, user: SaasUser = Depends(current_user)):
    svc.upsert_role(_t(slug), user.uid, name, body.permissions)
    return {"ok": True}


class PolicyIn(BaseModel):
    effect: str
    actions: list[str]
    condition: dict = Field(default_factory=dict)
    description: str = ""


@router.post("/t/{slug}/policies")
def add_policy(slug: str, body: PolicyIn, user: SaasUser = Depends(current_user)):
    return {"id": svc.add_policy(_t(slug), user.uid, body.effect, body.actions,
                                 body.condition, body.description)}


@router.delete("/t/{slug}/policies/{pid}")
def remove_policy(slug: str, pid: int, user: SaasUser = Depends(current_user)):
    svc.remove_policy(_t(slug), user.uid, pid)
    return {"ok": True}


@router.get("/t/{slug}/logs/{which}")
def logs(slug: str, which: str, limit: int = 100, user: SaasUser = Depends(current_user)):
    return {"rows": svc.read_logs(_t(slug), user.uid, which, limit)}


@router.get("/t/{slug}/realtime-token")
def realtime_token(slug: str, user: SaasUser = Depends(current_user)):
    """Supabase Realtime 구독 토큰 — 볼 수 있는 채널의 토픽만 담는다(realtime.py)."""
    from . import realtime
    t = _t(slug)
    ids = svc.visible_channel_ids(t, user.uid)
    return realtime.topic_token(user.uid, [events.topic(t.id, i) for i in ids])


@router.get("/t/{slug}/channels/{cid}/export")
def export_channel(slug: str, cid: int, user: SaasUser = Depends(current_user)):
    from . import archive
    gen = archive.export_channel(_t(slug), user.uid, cid)
    return StreamingResponse(gen, media_type="application/x-ndjson",
                             headers={"Content-Disposition": f'attachment; filename="channel-{cid}.ndjson"'})


# ── 실시간 (SSE) ────────────────────────────────────────────────────

def _sse(event: str, data) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n".encode()


@router.get("/t/{slug}/stream")
async def stream(slug: str, request: Request, channels: str = "",
                 user: SaasUser = Depends(current_user)):
    """구독 — 이벤트는 {type, seq}뿐이다. 클라이언트는 공백을 after=로 메운다.

    연결 수명에 상한을 둔다(서버리스 함수 시간 제한). 끝나면 'reconnect'를 보내고
    닫는다 — 클라이언트는 마지막 seq부터 다시 붙는다(공백 보충이 끊김을 메운다).
    권한 재확인: SSE_RECHECK_SEC마다 볼 수 있는 채널을 다시 계산해, 쫓겨난
    채널의 이벤트는 더 보내지 않는다(본문은 애초에 안 보내지만 '언제 대화가
    있었는가'라는 메타데이터도 새지 않게).
    """
    t = _t(slug)
    want = {int(x) for x in channels.split(",") if x.strip().isdigit()}
    loop = asyncio.get_running_loop()

    if os.environ.get("CHAT_LOOP_PROBE") in ("1", "2") and not getattr(events, "_probe_on", False):
        events._probe_on = True
        asyncio.get_running_loop().create_task(events.loop_probe())
        if os.environ.get("CHAT_LOOP_PROBE") == "2":
            events.install_slow_callback_probe()

    async def visible() -> set[int]:
        # 멤버 자격을 잃으면(비활성·탈퇴) 예외 대신 빈 집합 — 스트림이 모든
        # 채널에 'revoked'를 보내고 조용히 비워진다.
        events.probe["rechecks_inflight"] += 1
        try:
            return await loop.run_in_executor(
                None, svc.visible_channel_ids, t, user.uid, sorted(want) or None)
        except EngineError as e:
            if e.code == "tenant_moving":
                return None              # 셀 이동 — 권한 박탈이 아니다. 다시 붙게 한다
            return set()
        finally:
            events.probe["rechecks_inflight"] -= 1

    allowed = await visible()
    if allowed is None:
        raise EngineError(503, "tenant_moving", "워크스페이스를 옮기는 중입니다 — 잠시 후 다시")
    if want and not allowed:
        raise EngineError(404, "not_found", "구독할 수 있는 채널이 없습니다")
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    hub = events.hub()

    async def gen():
        joined: set[int] = set()
        events.probe["sse"] += 1
        try:
            for cid in allowed:
                await hub.join(events.topic(t.id, cid), q)
                joined.add(cid)
            yield _sse("ready", {"channels": sorted(joined)})
            # 재확인 시각을 연결마다 흩뿌린다 — 같은 주기로 동시에 재확인하면
            # 수천 연결이 한 순간에 DB를 친다(실측된 thundering herd).
            started = time.monotonic()
            last_check = started - random.uniform(0, SSE_RECHECK_SEC)
            while True:
                if await request.is_disconnected():
                    return
                now = time.monotonic()
                if now - started > SSE_MAX_SEC:
                    yield _sse("reconnect", {"reason": "max_duration"})
                    return
                if now - last_check > SSE_RECHECK_SEC:
                    last_check = now
                    still = await visible()
                    if still is None:
                        yield _sse("reconnect", {"reason": "tenant_moving"})
                        return
                    for cid in joined - still:
                        await hub.leave(events.topic(t.id, cid), q)
                        yield _sse("revoked", {"channel_id": cid})
                    joined &= still
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if ev.get("channel_id") in joined:
                    yield _sse(ev.get("type", "change"), ev)
        finally:
            events.probe["sse"] -= 1
            for cid in joined:
                await hub.leave(events.topic(t.id, cid), q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             # no-transform: 압축 미들웨어·프록시가 스트림을 버퍼에 쥐지 않게.
                             # 실측: Next 개발 프록시가 gzip으로 감싸 ready 이벤트가 영영 안 왔다.
                             headers={"Cache-Control": "no-cache, no-transform",
                                      "X-Accel-Buffering": "no"})
