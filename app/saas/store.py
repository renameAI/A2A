"""SaaS 보존 계층 (이슈 #6-B) — 엔진은 계산기, 보존은 여기 (스펙 Architecture).

백엔드는 SAAS_STORE로 고정한다:
- firestore: google-cloud-firestore. 컬렉션은 기획서 §10 엔티티와 1:1
  (workspaces/{ws}/requests/{rid}/messages 등). 라이브러리·자격증명 없으면
  기동 시 즉시 실패 (조용한 대체 없음).
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

    def list(self, kind: str, ws: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT body FROM docs WHERE kind=? AND ws=? ORDER BY updated DESC",
                (kind, ws)).fetchall()
        return [json.loads(r[0]) for r in rows]

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

    def list(self, kind: str, ws: str) -> list[dict]:
        col = self._db.collection("saas").document(ws).collection(kind)
        return [d.to_dict() for d in
                col.order_by("_updated", direction=self._fs.Query.DESCENDING).stream()]

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


_store = None


def get_saas_store():
    global _store
    if _store is not None:
        return _store
    backend = os.environ.get("SAAS_STORE", "local").lower()
    if backend == "firestore":
        _store = FirestoreSaasStore()
    elif backend == "local":
        _store = LocalSaasStore()
    else:
        raise EngineError(500, "config_error",
                          f"SAAS_STORE={backend} — firestore|local만 지원")
    return _store
