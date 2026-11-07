"""아카이빙 — 핫 보존기간(기본 12개월)이 지난 월 파티션을 객체 저장소로 옮긴다.

절차 (월 하나, 테넌트 하나):
  1. 내보내기: 파티션 행을 (channel_id, seq) 순으로 JSONL.gz로 쓴다
  2. 저장: 객체 키 archives/<tenant>/<YYYY-MM>.jsonl.gz
  3. 검증: 저장소에서 **다시 읽어** sha256과 행 수가 같은지 확인한다
  4. 색인: 테넌트 archive_index에 (월, 키, sha, 행 수, 채널별 seq 범위)를 기록
  5. 분리: 그제서야 파티션을 detach + drop 한다
검증 전에 지우는 경로는 없다 — 3이 실패하면 파티션은 그대로 남고 다음 실행이
같은 달을 처음부터 다시 한다(재실행 안전). 4와 5는 같은 트랜잭션이다.

조회: 핫 데이터가 바닥나면 list_messages가 read_archived로 이어 읽는다(느린 경로,
채널 seq 범위 색인으로 필요한 달만 연다). 아카이브 본문 검색은 지원하지 않는다
(문서화된 한계 — 내보내기 후 오프라인 검색).

저장소(CHAT_ARCHIVE_STORE): local(디렉터리, 기본) | supabase(Storage REST, 미검증)
"""
import gzip
import hashlib
import io
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from ..errors import EngineError
from .db import Tenant, registry, tenant_admin_tx


class LocalStore:
    def __init__(self, root: str):
        self.root = Path(root)

    def put(self, key: str, data: bytes) -> None:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)                       # 원자 교체 — 반쯤 쓴 파일이 보이지 않게

    def get(self, key: str) -> bytes:
        p = self.root / key
        if not p.exists():
            raise EngineError(404, "not_found", f"아카이브 객체 없음: {key}")
        return p.read_bytes()

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)


class SupabaseStore:
    """Supabase Storage — 이 저장소에서 실연결 검증 안 됨(키 필요)."""

    def __init__(self, url: str, key: str, bucket: str):
        import httpx
        self.base = url.rstrip("/") + "/storage/v1/object"
        self.h = {"apikey": key, "Authorization": f"Bearer {key}"}
        self.bucket = bucket
        self.c = httpx.Client(timeout=60)

    def put(self, key, data):
        r = self.c.post(f"{self.base}/{self.bucket}/{key}", content=data,
                        headers={**self.h, "x-upsert": "true",
                                 "content-type": "application/gzip"})
        r.raise_for_status()

    def get(self, key):
        r = self.c.get(f"{self.base}/{self.bucket}/{key}", headers=self.h)
        if r.status_code == 404:
            raise EngineError(404, "not_found", f"아카이브 객체 없음: {key}")
        r.raise_for_status()
        return r.content

    def delete(self, key):
        # httpx의 delete()는 본문을 받지 않는다 — 실연결 시험 전에 찾은 결함(purge가 터졌을 것)
        r = self.c.request("DELETE", f"{self.base}/{self.bucket}", headers=self.h,
                           json={"prefixes": [key]})
        r.raise_for_status()

    def ensure_bucket(self) -> None:
        """비공개 버킷이 없으면 만든다(이미 있으면 그대로)."""
        r = self.c.post(self.base.replace("/object", "/bucket"), headers=self.h,
                        json={"id": self.bucket, "name": self.bucket, "public": False})
        if r.status_code not in (200, 201) and "already exists" not in r.text.lower() \
                and r.status_code != 409:
            r.raise_for_status()


def store():
    kind = os.environ.get("CHAT_ARCHIVE_STORE", "local")
    if kind == "supabase":
        return SupabaseStore(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"],
                             os.environ.get("CHAT_ARCHIVE_BUCKET", "chat-archive"))
    return LocalStore(os.environ.get("CHAT_ARCHIVE_DIR", "chat_archive"))


def _months_before(conn, cutoff: date) -> list[date]:
    rows = conn.execute("""
        select c.relname from pg_inherits i
          join pg_class c on c.oid = i.inhrelid
          join pg_class p on p.oid = i.inhparent
          join pg_namespace n on n.oid = p.relnamespace
         where p.relname = 'messages' and n.nspname = current_schema()
           and c.relname ~ '^messages_[0-9]{6}$'""").fetchall()
    out = []
    for r in rows:
        y, m = int(r["relname"][9:13]), int(r["relname"][13:15])
        d = date(y, m, 1)
        if d < cutoff:
            out.append(d)
    return sorted(out)


def _cutoff(hot_months: int, today: "date | None" = None) -> date:
    today = today or datetime.now(timezone.utc).date()
    y, m = today.year, today.month - hot_months
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def archive_month(t: Tenant, month: date) -> dict:
    st = store()
    part = f"messages_{month:%Y%m}"
    with tenant_admin_tx(t) as conn:
        if conn.execute("select to_regclass(%s) as r", (part,)).fetchone()["r"] is None:
            return {"month": str(month), "status": "no_partition"}
        rows = conn.execute(f"select * from {part} order by channel_id, seq").fetchall()
    if not rows:
        with tenant_admin_tx(t) as conn:
            conn.execute("select detach_month(%s)", (month,))
        return {"month": str(month), "status": "empty_detached"}
    buf = io.BytesIO()
    ranges: dict[str, list[int]] = {}
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:   # mtime=0 → 같은 내용 = 같은 sha
        for r in rows:
            gz.write((json.dumps(r, default=str, ensure_ascii=False) + "\n").encode())
            k = str(r["channel_id"])
            lo, hi = ranges.get(k, [r["seq"], r["seq"]])
            ranges[k] = [min(lo, r["seq"]), max(hi, r["seq"])]
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    # 키에 내용 해시를 넣는다 — 같은 달의 두 번째 보관이 첫 번째를 덮지 못한다.
    # 같은 내용을 다시 보관하면 같은 키가 되어 멱등이다.
    key = f"archives/{t.slug}/{month:%Y-%m}/{sha[:16]}.jsonl.gz"
    st.put(key, data)
    back = st.get(key)                                   # 검증 — 다시 읽어 비교
    if hashlib.sha256(back).hexdigest() != sha:
        raise EngineError(500, "archive_verify_failed", f"{key} sha 불일치 — 파티션 유지")
    n_back = sum(1 for _ in gzip.decompress(back).splitlines())
    if n_back != len(rows):
        raise EngineError(500, "archive_verify_failed", f"{key} 행 수 불일치 — 파티션 유지")
    with tenant_admin_tx(t) as conn:
        # 내보낸 뒤 그 달에 행이 더 들어왔으면(시계가 크게 틀린 쓰기 등) 중단한다
        now_n = conn.execute(f"select count(*) as n from {part}").fetchone()["n"]
        if now_n != len(rows):
            raise EngineError(409, "archive_raced", f"{part}가 내보내는 중 변했습니다 — 재시도")
        conn.execute("""insert into archive_index (month, object_key, sha256, rows, channels)
                        values (%s, %s, %s, %s, %s)
                        on conflict (object_key) do nothing""",
                     (month, key, sha, len(rows), Jsonb(ranges)))
        conn.execute("select detach_month(%s)", (month,))
        conn.execute("insert into audit_log (actor, action, target, detail) "
                     "values ('system', 'archive.month', %s, %s)",
                     (str(month), Jsonb({"rows": len(rows), "sha256": sha, "key": key})))
    return {"month": str(month), "status": "archived", "rows": len(rows), "sha256": sha,
            "bytes": len(data)}


def archive_tenant(t: Tenant, today: "date | None" = None) -> list[dict]:
    with registry().control_tx() as c:
        hot = c.execute("select retention_hot_months from tenants where id = %s",
                        (t.id,)).fetchone()["retention_hot_months"]
    cutoff = _cutoff(hot, today)
    with tenant_admin_tx(t) as conn:
        months = _months_before(conn, cutoff)
    return [archive_month(t, m) for m in months]


def archive_all(today: "date | None" = None) -> list[dict]:
    """주기 작업 — 테넌트 하나의 실패가 나머지를 막지 않는다."""
    with registry().control_tx() as c:
        # 이동 중인 테넌트는 건너뛴다 — 복사 중에 원본 파티션이 사라지면 어긋난다
        rows = c.execute("""select slug from tenants t where status = 'active'
                              and not exists (select 1 from tenant_moves m where m.tenant_id = t.id
                                                and m.state in ('queued','copying','fencing'))
                            order by slug""").fetchall()
    out = []
    for r in rows:
        t = registry().tenant(r["slug"])
        try:
            out.append({"tenant": t.slug, "months": archive_tenant(t, today)})
        except Exception as e:  # noqa: BLE001
            out.append({"tenant": t.slug, "error": f"{type(e).__name__}: {e}"})
    return out


def read_archived(t: Tenant, channel_id: int, *, before: "int | None", limit: int,
                  thread: "int | None" = None) -> list[dict]:
    """핫 데이터보다 오래된 메시지 — 채널 seq 범위가 겹치는 달만 새 것부터 연다.
    권한은 호출자(list_messages)가 이미 확인했다(채널 가시성·channel.read)."""
    with tenant_admin_tx(t) as conn:
        idx = conn.execute("select month, object_key, channels from archive_index "
                           "order by month desc, id desc").fetchall()
    st = store()
    out: list[dict] = []
    for row in idx:
        rng = (row["channels"] or {}).get(str(channel_id))
        if not rng or (before is not None and rng[0] >= before):
            continue
        msgs = []
        for line in gzip.decompress(st.get(row["object_key"])).splitlines():
            m = json.loads(line)
            if m["channel_id"] != channel_id:
                continue
            if thread == -1:                     # 내보내기: 답글 전부
                if m["parent_seq"] is None:
                    continue
            elif (thread is None) != (m["parent_seq"] is None) or \
                    (thread is not None and m["parent_seq"] != thread):
                continue
            if before is not None and m["seq"] >= before:
                continue
            if m.get("deleted_at"):
                m["body"] = ""
            msgs.append({k: m[k] for k in ("channel_id", "seq", "parent_seq", "author", "body",
                                           "kind", "created_at", "edited_at", "reply_count",
                                           "last_reply_at")}
                        | {"deleted": bool(m.get("deleted_at")), "archived": True,
                           "reactions": []})
        msgs.sort(key=lambda m: m["seq"], reverse=True)
        out.extend(msgs[: limit - len(out)])
        if len(out) >= limit:
            break
    out.sort(key=lambda m: m["seq"])
    return out


def export_channel(t: Tenant, uid: str, channel_id: int):
    """채널 전체(아카이브 + 핫)를 NDJSON으로 흘려보낸다. export.run 권한 필요."""
    from . import service as svc
    from .db import tenant_tx
    with tenant_tx(t, uid) as conn:
        ch, my_role = svc._channel(conn, channel_id, uid)
        svc._require(conn, uid, "export.run", svc._authz_channel(ch), my_role)
    with tenant_admin_tx(t) as conn:
        conn.execute("insert into audit_log (actor, action, target) values (%s, 'export.channel', %s)",
                     (uid, str(channel_id)))

    def gen():
        for m in read_archived(t, channel_id, before=None, limit=10**9):
            yield json.dumps(m, default=str, ensure_ascii=False) + "\n"
        for m in read_archived(t, channel_id, before=None, limit=10**9, thread=-1):
            yield json.dumps(m, default=str, ensure_ascii=False) + "\n"
        # 핫 데이터는 서버 측 커서로 — 큰 채널도 메모리에 다 올리지 않는다
        with tenant_admin_tx(t) as conn:
            with conn.cursor(name="export_cur") as cur:
                cur.itersize = 2000
                cur.execute("select * from messages where channel_id = %s order by seq",
                            (channel_id,))
                for m in cur:
                    yield json.dumps(m, default=str, ensure_ascii=False) + "\n"
    return gen()


def purge_expired(today: "date | None" = None) -> list[dict]:
    """아카이브 영구 삭제 — archive_purge_months가 설정된 테넌트만, 법적 보존 중이면 건너뛴다."""
    out = []
    with registry().control_tx() as c:
        rows = c.execute("""select slug, archive_purge_months, legal_hold from tenants
                             where status = 'active' and archive_purge_months is not null""").fetchall()
    for r in rows:
        if r["legal_hold"]:
            out.append({"tenant": r["slug"], "skipped": "legal_hold"})
            continue
        t = registry().tenant(r["slug"])
        cutoff = _cutoff(r["archive_purge_months"], today)
        st = store()
        with tenant_admin_tx(t) as conn:
            old = conn.execute("select month, object_key from archive_index where month < %s",
                               (cutoff,)).fetchall()
            for o in old:
                st.delete(o["object_key"])
                conn.execute("delete from archive_index where object_key = %s", (o["object_key"],))
                conn.execute("insert into audit_log (actor, action, target) "
                             "values ('system', 'archive.purge', %s)", (str(o["month"]),))
        out.append({"tenant": t.slug, "purged": sorted({str(o["month"]) for o in old})})
    return out
