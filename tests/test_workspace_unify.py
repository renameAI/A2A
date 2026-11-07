"""멤버십 통합 — 메시징 테넌트가 리드 데이터의 워크스페이스가 된다(app/saas/workspace.py).

실제 Postgres(컨트롤·셀) + 로컬 SaaS 저장소 + API로 확인한다:
  - 테넌트가 없으면 예전 그대로(ws-{uid})
  - 테넌트 멤버끼리 리드 데이터를 공유하고, 비멤버는 못 본다
  - 예전 데이터가 남아 있으면 사라진 것처럼 보이지 않게 예전 워크스페이스에 머문다
  - 이전: 권한·복사·덮어쓰기 금지·예전 팀원 편입·재실행 거부·감사
  - X-Tenant는 내가 속한 테넌트일 때만
그리고 자료 URL 검사(로컬 경로·남의 파일·내부 주소).
"""
import os

import pytest

PG_A = os.environ.get("CHAT_TEST_PG_A")
PG_B = os.environ.get("CHAT_TEST_PG_B")
pytestmark = pytest.mark.skipif(not (PG_A and PG_B), reason="CHAT_TEST_PG_A/B 필요")

USERS = "owner1,mate1,solo,outsider,boss2"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from tests.test_chat_platform import _reset
    from app.chat import db, provision, service as svc
    _reset(PG_A, ["w_control", "w_cell_a"])
    _reset(PG_B, ["w_cell_b"])
    mp = pytest.MonkeyPatch()
    import app.config as config_mod
    mp.setattr(config_mod, "_load_dotenv", lambda: None)
    d = tmp_path_factory.mktemp("ws")
    for k, v in {"CHAT_CONTROL_DSN": f"{PG_A}/w_control", "W_CELL_A": f"{PG_A}/w_cell_a",
                 "W_CELL_B": f"{PG_B}/w_cell_b", "SAAS_AUTH": "dev", "SAAS_STORE": "local",
                 "SAAS_DB_PATH": str(d / "saas.db"), "SAAS_ALLOWED_USERS": USERS,
                 "CHAT_FANOUT": "memory"}.items():
        mp.setenv(k, v)
    from app.saas import store as store_mod, team, workspace
    store_mod._store = None
    team._ws_cache.clear(); workspace._cache.clear()
    db.reset_registry()
    provision.init_control()
    provision.register_cell("a", "W_CELL_A"); provision.register_cell("b", "W_CELL_B")
    t1 = provision.create_tenant("wsone", "WS One", owner_uid="boss2", owner_email="boss2@dev.local")
    t2 = provision.create_tenant("wstwo", "WS Two", owner_uid="boss2", owner_email="boss2@dev.local")
    from fastapi.testclient import TestClient
    from app.main import app
    yield {"c": TestClient(app), "t1": t1, "t2": t2, "svc": svc}
    mp.undo()
    db.reset_registry()


def H(uid, tenant=None):
    h = {"X-Dev-User": uid}
    if tenant:
        h["X-Tenant"] = tenant
    return h


def _lead(ws, rid, title):
    from app.saas.store import get_saas_store
    get_saas_store().put("lead_request", ws, rid,
                         {"request_id": rid, "title": title, "status": "draft",
                          "profile_version_id": "pv", "intent": {}, "candidates": []})


def _titles(c, uid, tenant=None):
    r = c.get("/saas/lead-requests", headers=H(uid, tenant))
    assert r.status_code == 200, r.text
    return sorted(x["title"] for x in r.json()["requests"])


def _forget():
    from app.saas import team, workspace
    team._ws_cache.clear(); workspace._cache.clear()


def test_no_tenant_keeps_legacy_workspace(env):
    w = env["c"].get("/saas/workspace", headers=H("solo")).json()
    assert w["workspace_id"] == "ws-solo" and w["mode"] == "legacy" and w["tenants"] == []


def test_tenant_members_share_lead_data(env):
    c, t1, svc = env["c"], env["t1"], env["svc"]
    svc.add_member(t1, "boss2", "mate1", "mate1@dev.local")
    _forget()
    w = c.get("/saas/workspace", headers=H("mate1")).json()
    assert w["workspace_id"] == f"t-{t1.id}" and w["mode"] == "tenant"
    _lead(f"t-{t1.id}", "lr-shared", "공유 리드")
    assert _titles(c, "mate1") == ["공유 리드"]
    assert _titles(c, "boss2", "wsone") == ["공유 리드"]
    assert _titles(c, "outsider") == []                     # 비멤버는 못 본다


def test_tenant_header_only_for_member_tenants(env):
    c, t1, t2 = env["c"], env["t1"], env["t2"]
    _lead(f"t-{t2.id}", "lr-two", "두 번째 테넌트 리드")
    assert _titles(c, "boss2", "wstwo") == ["두 번째 테넌트 리드"]
    # mate1은 wstwo 멤버가 아니다 — 헤더로 가리켜도 자기 테넌트(wsone)로 간다
    assert _titles(c, "mate1", "wstwo") == ["공유 리드"]


def test_legacy_data_is_never_hidden_and_migrates(env):
    from app.saas.store import get_saas_store
    from app.saas.team import TEAM
    c, t1, svc = env["c"], env["t1"], env["svc"]
    # owner1은 예전에 혼자(ws-owner1) 리드 2건을 만들었고, 예전 팀에 solo가 있었다
    _lead("ws-owner1", "lr-old1", "예전 리드 1")
    _lead("ws-owner1", "lr-old2", "예전 리드 2")
    st = get_saas_store()
    st.put("member", "ws-owner1", "owner1", {"uid": "owner1", "email": "owner1@dev.local", "role": "owner"})
    st.put("member", "ws-owner1", "solo", {"uid": "solo", "email": "solo@dev.local", "role": "member"})
    st.put("member_of", TEAM, "solo", {"ws": "ws-owner1"})
    # 같은 id의 문서가 대상에 이미 있으면 덮어쓰지 않는다
    _lead(f"t-{t1.id}", "lr-old2", "대상에서 이미 고친 리드")
    svc.add_member(t1, "boss2", "owner1", "owner1@dev.local")
    _forget()
    # 테넌트에 들어갔지만 예전 데이터가 남아 있어 예전 워크스페이스에 머문다
    w = c.get("/saas/workspace", headers=H("owner1")).json()
    assert w["workspace_id"] == "ws-owner1" and w["migration"]["pending"] is True
    assert w["migration"]["counts"]["lead_request"] == 2
    assert w["migration"]["can_migrate"] is False         # 아직 일반 멤버 — 초대 권한 없음
    assert _titles(c, "owner1") == ["예전 리드 1", "예전 리드 2"]
    # 예전 팀원(solo)은 소유자가 아니라 옮길 수 없다
    r = c.post("/saas/workspace/migrate", json={"tenant_slug": "wsone"}, headers=H("solo"))
    assert r.status_code == 403
    # owner1은 wsone의 일반 멤버 — 멤버를 들일 권한이 없다
    r = c.post("/saas/workspace/migrate", json={"tenant_slug": "wsone"}, headers=H("owner1"))
    assert r.status_code == 403
    svc.update_member(t1, "boss2", "owner1", role="admin")
    _forget()
    assert c.get("/saas/workspace", headers=H("owner1")).json()["migration"]["can_migrate"] is True
    r = c.post("/saas/workspace/migrate", json={"tenant_slug": "wsone"}, headers=H("owner1"))
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["copied"]["lead_request"] == 1 and rep["skipped_existing"]["lead_request"] == 1
    assert "solo" in rep["members_added"] and "member" not in rep["copied"]
    _forget()
    # 이제 owner1·예전 팀원 solo·기존 멤버 mate1이 같은 리드를 본다
    want = ["공유 리드", "대상에서 이미 고친 리드", "예전 리드 1"]
    assert _titles(c, "owner1") == want
    assert _titles(c, "solo") == want
    assert _titles(c, "mate1") == want
    # 예전 워크스페이스는 지우지 않고 '이전됨' 표시
    root = st.get("workspace", "ws-owner1", "root")
    assert root["migrated_to"] == f"t-{t1.id}"
    assert st.get("lead_request", "ws-owner1", "lr-old1") is not None
    r = c.post("/saas/workspace/migrate", json={"tenant_slug": "wsone"}, headers=H("owner1"))
    assert r.status_code == 409
    # 감사 — 플랫폼과 테넌트 양쪽
    from app.chat.db import registry
    with registry().control_tx() as cc:
        assert cc.execute("select count(*) as n from platform_audit where action = 'workspace.migrate'"
                          ).fetchone()["n"] == 1


@pytest.mark.parametrize("asset,ok", [
    ({"type": "ir_deck", "content": "", "url": "/etc/passwd"}, False),
    ({"type": "ir_deck", "content": "", "url": "supabase://ws-someone/x.pdf"}, False),
    ({"type": "ir_deck", "content": "", "url": "supabase://ws-outsider/abc.pdf"}, True),
    ({"type": "website", "content": "", "url": "http://127.0.0.1:8423/saas/me"}, False),
    ({"type": "website", "content": "", "url": "http://169.254.169.254/latest/meta-data"}, False),
    ({"type": "article", "content": "", "url": "http://10.0.0.5/x"}, False),
    ({"type": "website", "content": "", "url": "file:///srv/.env"}, False),
    ({"type": "website", "content": "", "url": "https://1.1.1.1/"}, True),
    ({"type": "instagram", "content": "", "url": "@rename_ai"}, True),
    ({"type": "text", "content": "회사 소개", "url": None}, True),
])
def test_asset_urls_are_checked(env, asset, ok):
    r = env["c"].post("/saas/onboarding-sessions", json={"assets": [asset]}, headers=H("outsider"))
    assert (r.status_code == 201) is ok, r.text


def test_guest_never_gets_lead_data(env):
    """게스트(외부 협력사)는 채팅만 — 테넌트 리드 파이프라인은 못 본다."""
    c, t1, svc = env["c"], env["t1"], env["svc"]
    svc.add_member(t1, "boss2", "outsider", "outsider@dev.local", role="guest")
    _forget()
    w = c.get("/saas/workspace", headers=H("outsider", "wsone")).json()
    assert w["workspace_id"] == "ws-outsider" and w["mode"] == "legacy" and w["tenants"] == []
    assert "공유 리드" not in _titles(c, "outsider", "wsone")
    # 역할을 member로 올리면 그때부터 대상이 된다 — outsider는 위 자료 시험에서 만든 온보딩이
    # 예전 공간에 있어 우선 옮기기 안내 상태다
    svc.update_member(t1, "boss2", "outsider", role="member")
    _forget()
    w = c.get("/saas/workspace", headers=H("outsider", "wsone")).json()
    assert w["tenant"]["slug"] == "wsone" and w["migration"]["pending"] is True


def test_schema_v2_grants_lead_access_to_existing_tenants(env):
    """v1로 만들어진 테넌트도 migrate_all이 v2(lead.access)로 올린다 — 재실행은 무변화."""
    from app.chat import provision
    from app.chat.db import registry, tenant_admin_tx
    t1 = env["t1"]
    with tenant_admin_tx(t1) as conn:     # v1 상태로 되돌린다
        conn.execute("update roles set permissions = array_remove(permissions, 'lead.access')")
    with registry().control_tx() as cc:
        cc.execute("update tenants set schema_version = 1 where id = %s", (t1.id,))
    out = provision.migrate_all()
    assert {"tenant": "wsone", "version": provision.LATEST, "ok": True} in out
    with tenant_admin_tx(t1) as conn:
        perms = {r["name"]: r["permissions"] for r in conn.execute(
            "select name, permissions from roles where builtin").fetchall()}
    assert "lead.access" in perms["member"] and "lead.access" in perms["admin"]
    assert "lead.access" not in perms["guest"]
    assert provision.migrate_all() == []
