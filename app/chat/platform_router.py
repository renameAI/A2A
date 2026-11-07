"""플랫폼 어드민 HTTP API — /platform/v1/...  (권한: platform_admins)

주기 작업은 서버리스에 상주 프로세스가 없으므로 크론이 /platform/v1/jobs/tick을
부른다(Vercel Cron 등). 이 경로는 사용자 인증이 아니라 공유 비밀(CHAT_CRON_SECRET)
헤더로 보호한다 — 비어 있으면 거부(fail closed).
"""
import hmac
import json
import os
import time

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..errors import EngineError
from ..saas.auth import SaasUser, current_user
from . import archive, metrics, platform as plat, provision
from .db import registry

router = APIRouter(prefix="/platform/v1", tags=["platform"])


@router.get("/me")
def me(user: SaasUser = Depends(current_user)):
    return plat.require(user.uid)


@router.get("/overview")
def overview(user: SaasUser = Depends(current_user)):
    return plat.overview(user.uid)


@router.get("/tenants")
def tenants(user: SaasUser = Depends(current_user)):
    return {"tenants": plat.list_tenants(user.uid)}


class TenantIn(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=120)
    owner_uid: str
    owner_email: str
    plan: str = "standard"
    cell: "str | None" = None


@router.post("/tenants")
def create_tenant(body: TenantIn, user: SaasUser = Depends(current_user)):
    return plat.create_tenant(user.uid, body.slug, body.name, body.owner_uid, body.owner_email,
                              body.plan, body.cell)


class TenantPatch(BaseModel):
    status: "str | None" = None
    retention_hot_months: "int | None" = Field(default=None, ge=1, le=120)
    legal_hold: "bool | None" = None
    archive_purge_months: "int | None" = Field(default=None, ge=12)
    clear_archive_purge: bool = False          # 영구 삭제 설정을 지워 무기한 보존으로
    reason: str = ""


@router.patch("/tenants/{slug}")
def patch_tenant(slug: str, body: TenantPatch, user: SaasUser = Depends(current_user)):
    plat.set_tenant(user.uid, slug, status=body.status,
                    retention_hot_months=body.retention_hot_months, legal_hold=body.legal_hold,
                    archive_purge_months=body.archive_purge_months, reason=body.reason,
                    clear_archive_purge=body.clear_archive_purge)
    return {"ok": True}


class MoveIn(BaseModel):
    to_cell: str


@router.post("/tenants/{slug}/move", status_code=202)
def move_tenant(slug: str, body: MoveIn, user: SaasUser = Depends(current_user)):
    """셀 이동(전용 셀 승격 포함) — superadmin. 대기열에 넣고 바로 돌려준다(202).
    진행은 GET /moves/{id}로 본다. 상주 서버면 이 프로세스의 배경 스레드가 곧바로
    처리하고, 서버리스(CHAT_MOVE_INLINE=0)면 작업자(app.chat.worker)가 가져간다."""
    import threading
    from . import move
    plat.require(user.uid, "superadmin")
    mid = move.enqueue(slug, body.to_cell, user.uid)
    if os.environ.get("CHAT_MOVE_INLINE", "1") != "0":
        threading.Thread(target=move.run_pending, args=(f"api:{os.getpid()}",),
                         name=f"move-{mid}", daemon=True).start()
    return {"move_id": mid, "state": "queued"}


@router.get("/moves/{move_id}")
def get_move(move_id: int, user: SaasUser = Depends(current_user)):
    plat.require(user.uid)
    with registry().control_tx() as c:
        m = c.execute("""select m.*, t.slug from tenant_moves m join tenants t on t.id = m.tenant_id
                          where m.id = %s""", (move_id,)).fetchone()
    if m is None:
        raise EngineError(404, "not_found", "이동 없음")
    return m


@router.post("/moves/{move_id}/cancel")
def cancel_move(move_id: int, user: SaasUser = Depends(current_user)):
    from . import move
    plat.require(user.uid, "superadmin")
    return move.cancel(move_id, user.uid)


@router.post("/moves/{move_id}/finalize")
def finalize_move(move_id: int, user: SaasUser = Depends(current_user)):
    from . import move
    plat.require(user.uid, "superadmin")
    return move.finalize(move_id, user.uid)


@router.get("/moves")
def moves(user: SaasUser = Depends(current_user)):
    plat.require(user.uid)
    with registry().control_tx() as c:
        return {"moves": c.execute("""select m.*, t.slug from tenant_moves m
                                        join tenants t on t.id = m.tenant_id
                                       order by m.id desc limit 100""").fetchall()}


@router.get("/cells")
def cells(user: SaasUser = Depends(current_user)):
    return {"cells": plat.list_cells(user.uid)}


class CellPatch(BaseModel):
    status: str


@router.patch("/cells/{cell_id}")
def patch_cell(cell_id: str, body: CellPatch, user: SaasUser = Depends(current_user)):
    plat.set_cell(user.uid, cell_id, body.status)
    return {"ok": True}


@router.get("/metrics")
def metrics_all(minutes: int = 60, user: SaasUser = Depends(current_user)):
    return plat.tenant_metrics(user.uid, None, minutes)


@router.get("/tenants/{slug}/metrics")
def metrics_tenant(slug: str, minutes: int = 60, user: SaasUser = Depends(current_user)):
    return plat.tenant_metrics(user.uid, slug, minutes)


@router.get("/alerts")
def alerts(open_only: bool = True, user: SaasUser = Depends(current_user)):
    return {"alerts": plat.list_alerts(user.uid, open_only)}


@router.get("/audit")
def audit(limit: int = 200, slug: "str | None" = None, user: SaasUser = Depends(current_user)):
    return {"rows": plat.platform_audit(user.uid, limit, slug)}


# 운영 채널
@router.get("/ops/channels")
def ops_channels(user: SaasUser = Depends(current_user)):
    return {"channels": plat.ops_channels(user.uid)}


@router.get("/ops/channels/{name}/messages")
def ops_messages(name: str, after: int = 0, user: SaasUser = Depends(current_user)):
    return {"messages": plat.ops_messages(user.uid, name, after)}


class OpsIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


@router.post("/ops/channels/{name}/messages")
def ops_post(name: str, body: OpsIn, user: SaasUser = Depends(current_user)):
    return plat.ops_post(user.uid, name, body.body)


@router.post("/announcements")
def announce(body: OpsIn, user: SaasUser = Depends(current_user)):
    return plat.announce(user.uid, body.body)


@router.get("/announcements")
def announcements(user: SaasUser = Depends(current_user)):
    plat.require(user.uid)
    with registry().control_tx() as c:
        rows = c.execute("""select a.id, a.author, a.body, a.created_at,
                                   count(d.*) as targets,
                                   count(d.delivered_at) as delivered,
                                   count(d.error) filter (where d.delivered_at is null) as failed
                              from announcements a
                              left join announcement_delivery d on d.announcement_id = a.id
                             group by a.id order by a.id desc limit 50""").fetchall()
    return {"announcements": rows}


@router.post("/announcements/{aid}/retry")
def retry(aid: int, user: SaasUser = Depends(current_user)):
    return plat.retry_announcement(user.uid, aid)


# 본문 열람 — 사유를 받는다(필수는 아니지만 기록된다)
@router.get("/tenants/{slug}/channels")
def admin_channels(slug: str, reason: str = "", user: SaasUser = Depends(current_user)):
    return {"channels": plat.admin_channels(user.uid, slug, reason)}


@router.get("/tenants/{slug}/channels/{cid}/messages")
def admin_messages(slug: str, cid: int, before: "int | None" = None, limit: int = 100,
                   reason: str = "", user: SaasUser = Depends(current_user)):
    return plat.admin_messages(user.uid, slug, cid, before=before, limit=limit, reason=reason)


@router.get("/tenants/{slug}/channels/{cid}/export")
def export(slug: str, cid: int, reason: str = "", user: SaasUser = Depends(current_user)):
    plat.require(user.uid, "operator")
    t = registry().tenant(slug)
    plat._access(user.uid, t, "content.export", cid, reason, {})

    def gen():
        for m in archive.read_archived(t, cid, before=None, limit=10**9):
            yield json.dumps(m, default=str, ensure_ascii=False) + "\n"
        from .db import tenant_admin_tx
        with tenant_admin_tx(t) as conn:
            with conn.cursor(name="admin_export") as cur:
                cur.execute("select * from messages where channel_id = %s order by seq", (cid,))
                for m in cur:
                    yield json.dumps(m, default=str, ensure_ascii=False) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ── 주기 작업 ───────────────────────────────────────────────────────

def _cron(x_cron_secret: str = Header(default="")) -> None:
    secret = os.environ.get("CHAT_CRON_SECRET", "")
    if not secret or not hmac.compare_digest(secret, x_cron_secret):
        raise EngineError(403, "forbidden", "크론 비밀이 없거나 틀립니다")


@router.post("/jobs/tick", dependencies=[Depends(_cron)])
def tick(daily: bool = False):
    """분 단위: 메트릭 반영 + 알림 평가. daily=true: 파티션 유지·아카이빙·영구 삭제·정리."""
    t0 = time.time()
    from . import move
    out = {"metrics_rows": metrics.flush(), "alerts": plat.evaluate_alerts(),
           # 작업자를 잃은 이동을 되돌린다 — 상주 작업자가 없어도 펜스에 갇히지 않게
           "moves_recovered": move.recover_stale(),
           # 스키마 이전 — 작업자가 없는 배포도 크론으로 올라가게
           "schema_migrated": provision.migrate_all()}
    if daily:
        out["maintenance"] = provision.run_maintenance()
        out["archive"] = archive.archive_all()
        out["purge"] = archive.purge_expired()
        out["reconcile"] = provision.reconcile()
    out["sec"] = round(time.time() - t0, 2)
    return out
