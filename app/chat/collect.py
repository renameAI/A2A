"""업무 이벤트 받기 — A2A 브라우저 수집 계층(browser/events.js)의 로그를 워크스페이스로 (browser/docs/HARNESS.md L1).

브라우저가 이미 마스킹·해시·중복 제거를 했다. 여기서는 믿지 않고 다시 확인한다:
- 올린 사람 = 이벤트의 user (남의 이벤트를 대신 올리지 못한다), 활성 멤버만
- mode에 맞지 않는 필드는 버린다(signals면 원문 해시·주소·입력값이 오면 지운다)
- 원문(full)은 서버가 sha256을 다시 계산해 이름과 맞을 때만 받는다
- 같은 event_id·해시는 한 번만(재전송 안전)
"""
import hashlib
import re
from datetime import datetime

from psycopg.types.json import Jsonb

from ..errors import EngineError
from . import authz
from .db import Tenant, tenant_admin_tx, tenant_tx
from .service import _principal

MODES = ("signals", "hashes", "full")
MAX_EVENTS = 500
MAX_BLOBS = 200
MAX_BLOB_CHARS = 200_000
_EVENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_KIND = re.compile(r"^[a-z][a-z0-9_.:-]{1,60}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")

# signals에서 남기는 것 — 행동의 모양만(원문·주소·입력 내용 없음)
_SIGNAL_KEYS = ("schema_version", "event_id", "session_id", "kind", "recorded_at", "actor_kind", "content_origin",
                "platform", "doc_type", "approval_status", "task_id", "task_bound", "label_kind", "observation", "evidence_candidate")
_HASH_KEYS = _SIGNAL_KEYS + ("content_hash", "content_bytes", "span", "first_event", "derived_from", "derived_from_unknown", "title")


def _host(url) -> "str | None":
    m = re.match(r"^[a-z][a-z0-9+.-]*://([^/?#]+)", str(url or ""), re.I)
    return m.group(1) if m else None


def _shape(ev: dict, mode: str) -> dict:
    """mode가 허락하는 필드만 남긴다."""
    keys = _SIGNAL_KEYS if mode == "signals" else _HASH_KEYS if mode == "hashes" else None
    out = dict(ev) if keys is None else {k: ev.get(k) for k in keys if k in ev}
    data = ev.get("data") or {}
    if mode == "signals":
        # 도구 이름·성공 여부·승인 결정·하네스 버전만
        out["data"] = {k: data[k] for k in ("tool", "ok", "decision", "simulated", "harness", "dropped") if k in data}
    elif mode == "hashes":
        out["data"] = {k: data[k] for k in ("tool", "ok", "decision", "simulated", "harness", "dropped", "how") if k in data}
        out["source_host"] = _host(ev.get("source_url"))
    return out


def ingest(t: Tenant, uid: str, mode: str, events: list, blobs: "dict | None" = None) -> dict:
    if mode not in MODES:
        raise EngineError(400, "invalid_input", "mode는 signals·hashes·full 중 하나")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise EngineError(400, "invalid_input", f"이벤트는 한 번에 {MAX_EVENTS}건까지")
    blobs = blobs or {}
    if blobs and mode != "full":
        raise EngineError(400, "invalid_input", "원문은 full 범위에서만 올립니다")
    if not isinstance(blobs, dict) or len(blobs) > MAX_BLOBS:
        raise EngineError(400, "invalid_input", f"원문은 한 번에 {MAX_BLOBS}개까지")
    with tenant_tx(t, uid) as conn:
        p, _ = _principal(conn, uid)
    if not p.active:
        raise EngineError(403, "forbidden", "비활성화된 멤버입니다")
    rows = []
    for ev in events:
        if not isinstance(ev, dict):
            raise EngineError(400, "invalid_input", "이벤트 모양이 아닙니다")
        eid, kind = str(ev.get("event_id") or ""), str(ev.get("kind") or "")
        if not _EVENT_ID.match(eid) or not _KIND.match(kind):
            raise EngineError(400, "invalid_input", f"event_id·kind 형식: {eid[:40]}")
        if ev.get("user") not in (None, uid):
            raise EngineError(403, "forbidden", "다른 사람의 이벤트는 올릴 수 없습니다")
        try:
            at = datetime.fromisoformat(str(ev.get("recorded_at")).replace("Z", "+00:00"))
        except ValueError:
            raise EngineError(400, "invalid_input", f"recorded_at 형식: {eid[:40]}") from None
        task = ev.get("task_id")
        rows.append((eid, uid, at, kind, str(task)[:100] if task else None, mode, Jsonb(_shape(ev, mode))))
    good = []
    for h, text in blobs.items():
        if not _HASH.match(str(h)) or not isinstance(text, str) or len(text) > MAX_BLOB_CHARS:
            raise EngineError(400, "invalid_input", "원문 이름·크기")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != h:
            raise EngineError(400, "invalid_input", f"원문 해시가 맞지 않습니다: {h[:12]}")
        good.append((h, text, uid))
    with tenant_admin_tx(t) as conn:
        n = 0
        for r in rows:
            n += conn.execute("""insert into work_events (event_id, user_id, recorded_at, kind, task_id, mode, body)
                                 values (%s, %s, %s, %s, %s, %s, %s) on conflict (event_id) do nothing""", r).rowcount
        b = 0
        for g in good:
            b += conn.execute("insert into work_blobs (hash, body, first_user) values (%s, %s, %s) "
                              "on conflict (hash) do nothing", g).rowcount
    return {"accepted": n, "duplicates": len(rows) - n, "blobs": b}


def summary(t: Tenant, uid: str) -> dict:
    """내 것(RLS) + audit.read면 워크스페이스 전체 수."""
    with tenant_tx(t, uid) as conn:
        p, pols = _principal(conn, uid)
        mine = conn.execute("""select count(*) as n, max(recorded_at) as last,
                                      count(distinct task_id) as tasks from work_events""").fetchone()
        kinds = conn.execute("select kind, count(*) as n from work_events group by kind order by n desc limit 30").fetchall()
    out = {"mine": {"events": mine["n"], "tasks": mine["tasks"], "last": mine["last"].isoformat() if mine["last"] else None},
           "kinds": {k["kind"]: k["n"] for k in kinds}}
    if authz.check(p, "audit.read", policies=pols).allowed:
        with tenant_admin_tx(t) as conn:
            a = conn.execute("""select count(*) as n, count(distinct user_id) as users from work_events""").fetchone()
            out["workspace"] = {"events": a["n"], "users": a["users"],
                                "blobs": conn.execute("select count(*) as n from work_blobs").fetchone()["n"]}
    return out
