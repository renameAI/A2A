"""SaaS 보존 계층 (이슈 #6-B) — 엔진은 계산기, 보존은 여기 (스펙 Architecture).

백엔드는 SAAS_STORE로 고정한다:
- firestore: google-cloud-firestore. 컬렉션은 기획서 §10 엔티티와 1:1
  (workspaces/{ws}/requests/{rid}/messages 등). 라이브러리·자격증명 없으면
  기동 시 즉시 실패 (조용한 대체 없음).
- supabase: Postgres(PostgREST). SUPABASE_URL + SUPABASE_SERVICE_KEY 필요.
  스키마는 supabase/migrations/*.sql. 비용 예약은 DB 함수 reserve_cost()로
  원자 실행한다 — 앱에서 read-then-write하면 동시 요청이 캡을 함께 통과한다.
- local: stdlib sqlite3 단일 파일(SAAS_DB_PATH, 기본 saas.db) — 개발·테스트·
  데모용. JobStore와 같은 패턴이라 무인프라로 즉시 돈다.

문서 본문은 JSON 텍스트로 저장한다 — 프로필·인사이트류가 중첩 구조라
컬럼 정규화보다 기획서 §10.1(JSONB 활용)의 취지에 맞고, Firestore 문서와
1:1 대응이라 백엔드 전환 시 형태 변환이 없다.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from ..errors import EngineError


def _now() -> float:
    return time.time()


class LocalSaasStore:
    """sqlite 백엔드 — (kind, workspace_id, doc_id) → JSON 문서."""

    def __init__(self, db_path: "str | None" = None):
        self._path = Path(db_path or os.environ.get("SAAS_DB_PATH", "saas.db"))
        self._lock = threading.Lock()   # 비용 예약의 원자성 (단일 프로세스 전제)
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS docs (
                kind TEXT, ws TEXT, doc_id TEXT, body TEXT, updated REAL,
                PRIMARY KEY (kind, ws, doc_id))""")

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._path)

    # ── 문서 CRUD ──
    def put(self, kind: str, ws: str, doc_id: str, body: dict) -> str:
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?)",
                        (kind, ws, doc_id, json.dumps(body, ensure_ascii=False),
                         _now()))
        return doc_id

    def get(self, kind: str, ws: str, doc_id: str) -> "dict | None":
        with self._connect() as con:
            row = con.execute(
                "SELECT body FROM docs WHERE kind=? AND ws=? AND doc_id=?",
                (kind, ws, doc_id)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind: str, ws: str,
             limit: "int | None" = None) -> "list[dict]":
        sql = "SELECT body FROM docs WHERE kind=? AND ws=? ORDER BY updated DESC"
        args: tuple = (kind, ws)
        if limit is not None:
            sql += " LIMIT ?"
            args = (kind, ws, limit)
        with self._connect() as con:
            rows = con.execute(sql, args).fetchall()
        return [json.loads(r[0]) for r in rows]

    def delete(self, kind: str, ws: str, doc_id: str) -> bool:
        with self._connect() as con:
            cur = con.execute(
                "DELETE FROM docs WHERE kind=? AND ws=? AND doc_id=?",
                (kind, ws, doc_id))
        return cur.rowcount > 0

    def delete_prefix(self, kind: str, ws: str, prefix: str) -> int:
        """doc_id가 prefix로 시작하는 문서를 지운다 — 요청 연쇄 삭제용
        (company_ontology의 f"{rid}::{cid}" 같은 합성 키)."""
        with self._connect() as con:
            cur = con.execute(
                "DELETE FROM docs WHERE kind=? AND ws=? AND doc_id LIKE ?",
                (kind, ws, prefix + "%"))
        return cur.rowcount

    def delete_workspace(self, ws: str) -> int:
        """워크스페이스 전체 파기 — '내 자료 지워주세요'에 응할 수 있는 경로."""
        with self._connect() as con:
            cur = con.execute("DELETE FROM docs WHERE ws=?", (ws,))
        return cur.rowcount

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    # ── 비용 예약 (이슈 #6-E) — check-and-add를 원자로 ──
    def reserve_cost(self, ws: str, request_id: str, add_usd: float,
                     req_cap: float, month_cap: float) -> None:
        """하드캡 검사+가산. 초과면 EngineError(402, cost_cap) — job이 이 예외로
        중단되고 사용자 안내로 이어진다. LLM/검색 호출 '직전'에 부른다."""
        month_key = time.strftime("%Y-%m")
        with self._lock, self._connect() as con:
            req = self.get("cost_request", ws, request_id) or {"usd": 0.0}
            mon = self.get("cost_month", ws, month_key) or {"usd": 0.0}
            if req["usd"] + add_usd > req_cap:
                raise EngineError(402, "cost_cap",
                                  f"Request 비용 한도(${req_cap}) 도달 — 사용액 "
                                  f"${req['usd']:.2f}. 조건을 좁혀 새 Request로 시도하세요.")
            if mon["usd"] + add_usd > month_cap:
                raise EngineError(402, "cost_cap",
                                  f"월 비용 한도(${month_cap}) 도달 — 이번 달 신규 "
                                  f"검색이 차단됩니다. 기존 데이터 열람은 유지돼요.")
            req["usd"] += add_usd
            mon["usd"] += add_usd
            con.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?)",
                        ("cost_request", ws, request_id,
                         json.dumps(req), _now()))
            con.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?)",
                        ("cost_month", ws, month_key,
                         json.dumps(mon), _now()))


class FirestoreSaasStore:
    """Firestore 백엔드 — LocalSaasStore와 동일 계약. 문서 경로:
    saas/{ws}/{kind}/{doc_id}. reserve_cost는 Firestore 트랜잭션으로 원자 수행."""

    def __init__(self):
        try:
            from google.cloud import firestore
        except ImportError as e:
            raise EngineError(500, "config_error",
                              "SAAS_STORE=firestore인데 google-cloud-firestore가 "
                              "없습니다 — pip install google-cloud-firestore") from e
        self._db = firestore.Client()   # ADC 자격증명 — 없으면 여기서 즉시 실패
        self._fs = firestore

    def _doc(self, kind: str, ws: str, doc_id: str):
        return self._db.collection("saas").document(ws)\
                       .collection(kind).document(doc_id)

    def put(self, kind: str, ws: str, doc_id: str, body: dict) -> str:
        self._doc(kind, ws, doc_id).set({**body, "_updated": _now()})
        return doc_id

    def get(self, kind: str, ws: str, doc_id: str) -> "dict | None":
        snap = self._doc(kind, ws, doc_id).get()
        return snap.to_dict() if snap.exists else None

    def list(self, kind: str, ws: str,
             limit: "int | None" = None) -> "list[dict]":
        col = self._db.collection("saas").document(ws).collection(kind)
        q = col.order_by("_updated", direction=self._fs.Query.DESCENDING)
        if limit is not None:
            q = q.limit(limit)
        return [d.to_dict() for d in q.stream()]

    def delete(self, kind: str, ws: str, doc_id: str) -> bool:
        ref = self._doc(kind, ws, doc_id)
        if not ref.get().exists:
            return False
        ref.delete()
        return True

    def delete_prefix(self, kind: str, ws: str, prefix: str) -> int:
        col = self._db.collection("saas").document(ws).collection(kind)
        n = 0
        # Firestore엔 prefix delete가 없다 — 범위 질의로 접두어를 잡는다
        # (\uf8ff는 유니코드 사용자 영역 마지막 문자로, 접두어 상한 관용구다).
        for d in col.where("__name__", ">=", prefix)\
                    .where("__name__", "<", prefix + "\uf8ff").stream():
            d.reference.delete()
            n += 1
        return n

    def delete_workspace(self, ws: str) -> int:
        n = 0
        for col in self._db.collection("saas").document(ws).collections():
            for d in col.stream():
                d.reference.delete()
                n += 1
        return n

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def reserve_cost(self, ws: str, request_id: str, add_usd: float,
                     req_cap: float, month_cap: float) -> None:
        month_key = time.strftime("%Y-%m")
        req_ref = self._doc("cost_request", ws, request_id)
        mon_ref = self._doc("cost_month", ws, month_key)

        @self._fs.transactional
        def _tx(tx):
            req = (req_ref.get(transaction=tx).to_dict() or {"usd": 0.0})
            mon = (mon_ref.get(transaction=tx).to_dict() or {"usd": 0.0})
            if req.get("usd", 0.0) + add_usd > req_cap:
                raise EngineError(402, "cost_cap",
                                  f"Request 비용 한도(${req_cap}) 도달")
            if mon.get("usd", 0.0) + add_usd > month_cap:
                raise EngineError(402, "cost_cap", f"월 비용 한도(${month_cap}) 도달")
            tx.set(req_ref, {"usd": req.get("usd", 0.0) + add_usd, "_updated": _now()})
            tx.set(mon_ref, {"usd": mon.get("usd", 0.0) + add_usd, "_updated": _now()})

        _tx(self._db.transaction())


class SupabaseSaasStore:
    """Postgres(PostgREST) 백엔드 — Local/Firestore와 동일 계약.

    service_role 키로 접속하므로 RLS를 우회한다(엔진이 곧 신뢰 경계다).
    키가 없으면 기동 시 즉시 실패 — 조용한 대체 없음.
    """

    TABLE = "saas_docs"

    def __init__(self):
        url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            raise EngineError(500, "config_error",
                              "SAAS_STORE=supabase인데 SUPABASE_URL 또는 "
                              "SUPABASE_SERVICE_KEY가 없습니다")
        self._base = f"{url}/rest/v1"
        self._headers = {"apikey": key, "Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"}

    def _req(self, method: str, path: str, body=None, extra_headers=None):
        import urllib.error
        import urllib.request
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(
            self._base + path, data=data, method=method,
            headers={**self._headers, **(extra_headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            # DB 함수가 올린 캡 초과를 402로 되돌린다 — 사용자에게는 '한도'지
            # '서버 오류'가 아니다.
            if "cost_cap_request" in detail:
                raise EngineError(402, "cost_cap",
                                  "Request 비용 한도 도달 — 조건을 좁혀 새 "
                                  "Request로 시도하세요.") from e
            if "cost_cap_month" in detail:
                raise EngineError(402, "cost_cap",
                                  "월 비용 한도 도달 — 이번 달 신규 검색이 "
                                  "차단됩니다. 기존 데이터 열람은 유지돼요.") from e
            raise EngineError(502, "store_error",
                              f"Supabase {method} 실패({e.code}): {detail}") from e

    def put(self, kind: str, ws: str, doc_id: str, body: dict) -> str:
        self._req("POST", f"/{self.TABLE}",
                  {"kind": kind, "workspace_id": ws, "doc_id": doc_id,
                   "body": body, "updated_at": "now()"},
                  {"Prefer": "resolution=merge-duplicates,return=minimal"})
        return doc_id

    def get(self, kind: str, ws: str, doc_id: str) -> "dict | None":
        rows = self._req(
            "GET", f"/{self.TABLE}?select=body&kind=eq.{_q(kind)}"
                   f"&workspace_id=eq.{_q(ws)}&doc_id=eq.{_q(doc_id)}&limit=1")
        return rows[0]["body"] if rows else None

    def list(self, kind: str, ws: str,
             limit: "int | None" = None) -> "list[dict]":
        url = (f"/{self.TABLE}?select=body&kind=eq.{_q(kind)}"
               f"&workspace_id=eq.{_q(ws)}&order=updated_at.desc")
        if limit is not None:
            url += f"&limit={int(limit)}"
        rows = self._req("GET", url)
        return [r["body"] for r in (rows or [])]

    def delete(self, kind: str, ws: str, doc_id: str) -> bool:
        self._req("DELETE",
                  f"/{self.TABLE}?kind=eq.{_q(kind)}&workspace_id=eq.{_q(ws)}"
                  f"&doc_id=eq.{_q(doc_id)}")
        return True

    def delete_prefix(self, kind: str, ws: str, prefix: str) -> int:
        # PostgREST의 like 필터 — *가 SQL의 %에 해당한다
        self._req("DELETE",
                  f"/{self.TABLE}?kind=eq.{_q(kind)}&workspace_id=eq.{_q(ws)}"
                  f"&doc_id=like.{_q(prefix + '*')}")
        return -1      # PostgREST는 삭제 건수를 기본 반환하지 않는다

    def delete_workspace(self, ws: str) -> int:
        self._req("DELETE", f"/{self.TABLE}?workspace_id=eq.{_q(ws)}")
        return -1

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def reserve_cost(self, ws: str, request_id: str, add_usd: float,
                     req_cap: float, month_cap: float) -> None:
        # 검사와 가산을 DB 함수 한 트랜잭션에서 — 앱 레벨 read-then-write는
        # 동시 요청에 캡이 뚫린다(예산 사고의 전형).
        self._req("POST", "/rpc/reserve_cost", {
            "p_ws": ws, "p_request_id": request_id,
            "p_month_key": time.strftime("%Y-%m"), "p_add": add_usd,
            "p_req_cap": min(req_cap, 1e12),      # inf는 JSON에 못 담는다
            "p_month_cap": min(month_cap, 1e12)})


def _q(v: str) -> str:
    """PostgREST 필터 값 인코딩 — 콤마·괄호가 든 doc_id(URL 포함)를 안전하게."""
    from urllib.parse import quote
    return quote(str(v), safe="")


_store = None


def get_saas_store():
    global _store
    if _store is not None:
        return _store
    backend = os.environ.get("SAAS_STORE", "local").lower()
    if backend == "firestore":
        _store = FirestoreSaasStore()
    elif backend == "supabase":
        _store = SupabaseSaasStore()
    elif backend == "local":
        _store = LocalSaasStore()
    else:
        raise EngineError(500, "config_error",
                          f"SAAS_STORE={backend} — supabase|firestore|local만 지원")
    return _store
