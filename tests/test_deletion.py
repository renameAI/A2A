"""T13·T14 — 삭제 경로와 무한 성장 제어 (감사 확정 medium).

배경: "내 자료 지워주세요"에 응할 수 있는 코드 경로가 앱 전체에 0개였다.
저장 계층 계약에 delete()조차 없었다. 그리고 recommend()가 원장 전체를
무제한 list()로 끌어와, 많이 쓴 사용자일수록 검색 시작이 느려졌다.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

H = {"X-Dev-User": "boram"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "_load_dotenv", lambda: None)
    monkeypatch.setenv("SAAS_AUTH", "dev")
    monkeypatch.setenv("SAAS_STORE", "local")
    monkeypatch.setenv("SAAS_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("A2A_DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("SAAS_ALLOWED_USERS", "boram")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "up"))
    import app.saas.store as store_mod
    store_mod._store = None
    import app.main as main_mod
    return TestClient(main_mod.app)


def _seed(store, ws="ws-boram", rid="lr-x"):
    store.put("lead_request", ws, rid, {"request_id": rid, "title": "t"})
    store.put("company_ontology", ws, f"{rid}::web-1", {"name": "A"})
    store.put("company_ontology", ws, f"{rid}::web-2", {"name": "B"})
    store.put("insight", ws, f"{rid}::g1::web-1", {"x": 1})
    store.put("email_draft", ws, f"{rid}::g1::web-1", {"x": 1})
    store.put("outcome", ws, f"{rid}::web-1", {"saved": True})
    store.put("keyword_run", ws, f"{rid}-w1::seg", {"segment": "seg"})
    # 다른 요청의 문서 — 지워지면 안 된다
    store.put("lead_request", ws, "lr-other", {"request_id": "lr-other"})
    store.put("company_ontology", ws, "lr-other::web-1", {"name": "keep"})


class TestStoreDeleteContract:
    def test_delete_and_prefix_and_workspace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAAS_DB_PATH", str(tmp_path / "s.db"))
        from app.saas.store import LocalSaasStore
        st = LocalSaasStore(str(tmp_path / "s.db"))
        _seed(st)
        assert st.delete("lead_request", "ws-boram", "lr-x") is True
        assert st.delete("lead_request", "ws-boram", "lr-x") is False
        assert st.delete_prefix("company_ontology", "ws-boram", "lr-x::") == 2
        assert st.get("company_ontology", "ws-boram", "lr-other::web-1") is not None
        assert st.delete_workspace("ws-boram") > 0
        assert st.list("lead_request", "ws-boram") == []

    def test_list_limit(self, tmp_path):
        from app.saas.store import LocalSaasStore
        st = LocalSaasStore(str(tmp_path / "s.db"))
        for i in range(10):
            st.put("keyword_run", "ws-1", f"r{i}", {"i": i})
        assert len(st.list("keyword_run", "ws-1")) == 10
        assert len(st.list("keyword_run", "ws-1", limit=3)) == 3


class TestDeleteEndpoints:
    def test_delete_request_cascades(self, client):
        from app.saas.store import get_saas_store
        store = get_saas_store()
        _seed(store)
        r = client.delete("/saas/lead-requests/lr-x", headers=H)
        assert r.status_code == 200
        for kind, doc_id in [("lead_request", "lr-x"),
                             ("company_ontology", "lr-x::web-1"),
                             ("insight", "lr-x::g1::web-1"),
                             ("email_draft", "lr-x::g1::web-1"),
                             ("outcome", "lr-x::web-1"),
                             ("keyword_run", "lr-x-w1::seg")]:
            assert store.get(kind, "ws-boram", doc_id) is None, f"{kind} 잔존"
        # 다른 요청은 남는다
        assert store.get("lead_request", "ws-boram", "lr-other") is not None
        assert store.get("company_ontology", "ws-boram", "lr-other::web-1") is not None

    def test_delete_missing_request_is_404(self, client):
        assert client.delete("/saas/lead-requests/lr-none",
                             headers=H).status_code == 404

    def test_delete_me_requires_exact_confirmation(self, client):
        from app.saas.store import get_saas_store
        _seed(get_saas_store())
        bad = client.post("/saas/me/delete", headers=H, json={"confirm": "네"})
        assert bad.status_code == 400
        assert get_saas_store().get("lead_request", "ws-boram", "lr-x") is not None

    def test_delete_me_wipes_workspace_and_files(self, client, tmp_path):
        from app.saas.store import get_saas_store
        store = get_saas_store()
        _seed(store)
        up = client.post("/saas/upload", headers=H,
                         files={"file": ("a.pdf", b"%PDF-1.4\n" + b"x" * 50,
                                         "application/pdf")})
        assert up.status_code == 200
        # 확인 문구는 사용자의 email — dev 모드는 {user}@dev.local
        r = client.post("/saas/me/delete", headers=H,
                        json={"confirm": "boram@dev.local"})
        assert r.status_code == 200 and r.json()["files"] == 1
        assert store.list("lead_request", "ws-boram") == []
        assert not (Path(tmp_path / "up") / "ws-boram").exists()


class TestLedgerScanBounded:
    def test_recommend_scans_bounded_window(self, monkeypatch):
        """원장이 커져도 조회량이 상한을 넘지 않는다."""
        monkeypatch.setenv("KEYWORD_LEDGER_SCAN", "5")
        import importlib

        from app.engine import keywords as kwmod
        importlib.reload(kwmod)
        seen = {}

        class St:
            def list(self, kind, ws, limit=None):
                seen[kind] = limit
                return []
        kwmod.recommend(St(), "ws-1", ["질의"])
        assert seen["keyword_run"] == 5
        importlib.reload(kwmod)      # 다른 테스트에 새지 않게 되돌린다


class TestSnippetLogOptIn:
    def test_disabled_by_default(self, tmp_path, monkeypatch):
        """Cloud Run에서는 어차피 증발하는 쓰기 — 기본은 끔."""
        monkeypatch.delenv("SNIPPET_LOG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        import app.saas.router as r
        assert r._SkipSnippetLog is not None
        # 경로가 없으면 파일을 만들지 않는다는 계약을 소스로 확인
        import inspect
        src = inspect.getsource(r._discover)
        assert 'os.environ.get("SNIPPET_LOG_PATH", "")' in src
        assert "raise _SkipSnippetLog" in src
