"""SupabaseSaasStore 계약 테스트 — 스텁 PostgREST로 HTTP 배선을 검증한다.

실 Supabase 없이 확인 가능한 것만 확인한다: 요청 메서드·경로·필터 인코딩,
upsert 헤더, 캡 초과 응답의 402 변환. DB 함수 reserve_cost의 원자성은 실제
Postgres에서 별도 검증했다(docs/DEPLOY.md — 동시 30건 중 10건 통과, 잔액 10.0).
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.errors import EngineError
from app.saas.store import SupabaseSaasStore

STATE: dict = {}
CALLS: list = []
FAIL_MODE = {"mode": ""}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, payload=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        CALLS.append(("GET", u.path, u.query))
        f = dict(p.split("=", 1) for p in u.query.split("&") if "=" in p)
        kind = unquote(f.get("kind", "")).removeprefix("eq.")
        ws = unquote(f.get("workspace_id", "")).removeprefix("eq.")
        did = unquote(f.get("doc_id", "")).removeprefix("eq.")
        rows = [{"body": v} for (k, w, d), v in STATE.items()
                if k == kind and w == ws and (not did or d == did)]
        self._send(200, rows)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        CALLS.append(("POST", u.path, self.headers.get("Prefer", "")))
        if u.path.endswith("/rpc/reserve_cost"):
            if FAIL_MODE["mode"] == "req":
                return self._send(400, {"message":
                                        'cost_cap_request:5.0:4.9 raised'})
            if FAIL_MODE["mode"] == "month":
                return self._send(400, {"message": "cost_cap_month:100:99"})
            STATE[("cost_request", body["p_ws"], body["p_request_id"])] = \
                {"usd": body["p_add"]}
            return self._send(200, None)
        STATE[(body["kind"], body["workspace_id"], body["doc_id"])] = body["body"]
        self._send(201, None)


@pytest.fixture()
def store(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    STATE.clear(); CALLS.clear(); FAIL_MODE["mode"] = ""
    monkeypatch.setenv("SUPABASE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    yield SupabaseSaasStore()
    srv.shutdown()


def test_put_get_roundtrip(store):
    store.put("lead_request", "ws-1", "lr-1", {"title": "일본 유통사", "n": 3})
    assert store.get("lead_request", "ws-1", "lr-1") == {"title": "일본 유통사",
                                                         "n": 3}


def test_upsert_header_is_merge_duplicates(store):
    """같은 키 재저장이 409가 아니라 갱신이어야 한다 — Prefer 헤더가 그 계약."""
    store.put("k", "ws-1", "d", {"v": 1})
    prefer = [c[2] for c in CALLS if c[0] == "POST"][-1]
    assert "resolution=merge-duplicates" in prefer
    store.put("k", "ws-1", "d", {"v": 2})
    assert store.get("k", "ws-1", "d") == {"v": 2}


def test_doc_id_with_url_is_encoded(store):
    """doc_id에 URL이 들어간다(company_ontology는 f"{rid}::{url}") —
    콜론·슬래시·물음표가 PostgREST 필터를 깨지 않아야 한다."""
    did = "lr-1::https://a.co/b?x=1,2"
    store.put("company_ontology", "ws-1", did, {"ok": True})
    assert store.get("company_ontology", "ws-1", did) == {"ok": True}
    q = [c[2] for c in CALLS if c[0] == "GET"][-1]
    assert "%3A%3A" in q and "?x=1" not in q      # 인코딩되어 쿼리를 안 깬다


def test_list_returns_bodies(store):
    store.put("outcome", "ws-1", "a", {"i": 1})
    store.put("outcome", "ws-1", "b", {"i": 2})
    store.put("outcome", "ws-2", "c", {"i": 3})
    got = sorted(x["i"] for x in store.list("outcome", "ws-1"))
    assert got == [1, 2]


def test_request_cap_maps_to_402(store):
    FAIL_MODE["mode"] = "req"
    with pytest.raises(EngineError) as e:
        store.reserve_cost("ws-1", "r", 1.0, 5.0, 100.0)
    assert e.value.http_status == 402 and e.value.code == "cost_cap"


def test_month_cap_maps_to_402(store):
    FAIL_MODE["mode"] = "month"
    with pytest.raises(EngineError) as e:
        store.reserve_cost("ws-1", "r", 1.0, 5.0, 100.0)
    assert e.value.http_status == 402


def test_infinite_cap_is_json_safe(store):
    """전역 캡 검사는 req_cap=inf로 부른다 — JSON에 inf를 실으면 요청이 깨진다."""
    store.reserve_cost("ws-1", "r", 0.5, float("inf"), 100.0)
    assert STATE[("cost_request", "ws-1", "r")] == {"usd": 0.5}


def test_missing_env_fails_loud(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    with pytest.raises(EngineError):
        SupabaseSaasStore()


class TestReserveCostMigrationSafety:
    """마이그레이션 SQL 자체를 정적으로 검사한다.

    실제 Postgres 없이는 revoke/grant나 음수 거부를 실행 검증할 수 없다 —
    이건 라이브 DB가 있어야 도는 테스트다. 그래도 이 정적 검사를 게이트에
    두는 이유: 마이그레이션 파일이 수정·재작성될 때 안전장치(execute 권한
    회수, 음수 거부)가 조용히 빠지는 것을 잡는다. 감사 확정 high — anon 키로
    __global__ 비용 원장을 무력화할 수 있었다.
    """

    def _sql(self) -> str:
        import pathlib
        d = pathlib.Path(__file__).resolve().parent.parent / "supabase" / "migrations"
        return "\n".join(p.read_text(encoding="utf-8") for p in sorted(d.glob("*.sql")))

    def test_execute_revoked_from_public_and_anon(self):
        sql = self._sql().lower()
        assert "revoke execute on function public.reserve_cost" in sql
        assert "from public, anon, authenticated" in sql

    def test_execute_granted_to_service_role_only(self):
        sql = self._sql().lower()
        assert "grant execute on function public.reserve_cost" in sql
        assert "to service_role" in sql

    def test_negative_amount_rejected_in_function_body(self):
        sql = self._sql().lower()
        assert "p_add < 0" in sql
