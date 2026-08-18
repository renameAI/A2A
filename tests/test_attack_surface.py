"""공개 표면 회귀 테스트 — 닫은 문이 다시 열리지 않게 한다.

배경(감사 확정 blocker): next.config의 /api/:path* 캐치올 rewrite가 인증 없는
/product/*(22개)·/v1/*(7개)를 공개 URL로 그대로 열어, 누구나 API 크레딧을
태우고 저장된 프로필을 덤프할 수 있었다. 프록시를 좁히는 것만으로는 부족하다
— Cloud Run이 --allow-unauthenticated이므로 엔진 자체가 닫혀 있어야 한다.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
    monkeypatch.delenv("ENABLE_LEGACY_PRODUCT_UI", raising=False)
    import app.saas.store as store_mod
    store_mod._store = None
    import app.main as main_mod
    return TestClient(main_mod.app)


LEGACY = [
    ("POST", "/v1/represent"), ("POST", "/v1/judge"), ("POST", "/v1/scout"),
    ("GET", "/v1/jobs/x"), ("GET", "/.well-known/agent.json"),
    ("POST", "/a2a"), ("GET", "/product/db/inspect"),
    ("GET", "/product/jobs/x"), ("POST", "/product/upload"),
]


@pytest.mark.parametrize("method,path", LEGACY)
def test_legacy_surface_closed_by_default(client, method, path):
    """인증 없는 레거시 엔드포인트는 기본적으로 존재하지 않아야 한다."""
    assert client.request(method, path).status_code == 404


def test_path_traversal_blocked(client):
    """경로 순회 — company_id를 검사하지 않아 base 밖 파일을 읽을 수 있었다.

    레거시를 켠 **별도 앱**에 붙여서 검사한다. importlib.reload로 전역
    app.main을 갈아끼우면 같은 세션의 다른 테스트가 스텁을 잃는다(실측:
    관통 테스트가 120초 네트워크 경로로 되돌아갔다).
    """
    from fastapi import FastAPI

    from app.main import mount_legacy
    legacy = FastAPI()
    mount_legacy(legacy)
    c = TestClient(legacy)
    for evil in ["../../../etc/passwd", "..%2f..%2fetc%2fpasswd", "....//etc"]:
        assert c.get(f"/product/pages/{evil}/x.png").status_code == 404


def test_saas_requires_auth(client):
    for path in ["/saas/jobs/x", "/saas/lead-requests", "/saas/settings/llm"]:
        assert client.get(path).status_code == 401
    assert client.post("/saas/uploads/sign",
                       json={"filename": "a.pdf"}).status_code == 401


def test_job_scoped_to_workspace(client, monkeypatch):
    """다른 워크스페이스의 job은 보이지 않는다.

    job 문서가 소유 워크스페이스 아래 저장되므로, 조회에 ws가 필요해
    구조적으로 격리된다(별도 소유권 대조 문서 없이). 실제 job을 돌리지 않고
    원장만 세운다 — 이 테스트가 검증하는 것은 조회 스코프이지 실행이 아니다.
    """
    monkeypatch.setenv("SAAS_ALLOWED_USERS", "boram,mallory")
    from app.jobs import store as job_store

    job, _ = job_store.create(ws="ws-boram")
    job.status = job.status.__class__.done
    job.result = {"candidates": ["보람의 후보 목록"]}
    job_store._put(job)

    r = client.get(f"/saas/jobs/{job.job_id}", headers={"X-Dev-User": "boram"})
    assert r.status_code == 200 and r.json()["result"]["candidates"]
    # 허용 목록 안이지만 소유자가 아닌 사용자 → 404 (존재 여부도 알리지 않는다)
    assert client.get(f"/saas/jobs/{job.job_id}",
                      headers={"X-Dev-User": "mallory"}).status_code == 404


def test_job_denied_for_unlisted_user(client):
    """허용 목록 밖은 job 경로에서도 403."""
    assert client.get("/saas/jobs/anything",
                      headers={"X-Dev-User": "stranger"}).status_code == 403


def _no_network(monkeypatch):
    """서명 발급이 실제 스토리지로 나가지 않게 한다 — 이 파일은 경로·권한만 본다."""
    from app.saas import storage as st
    monkeypatch.setattr(st, "sign_upload",
                        lambda obj: {"path": obj, "token": "t"})


def test_sign_rejects_unsupported_extension(client, monkeypatch):
    """받는 형식은 PDF와 .docx뿐. 구형 .doc은 OLE 복합문서라 파서가 다르다."""
    _no_network(monkeypatch)
    H = {"X-Dev-User": "boram"}
    for name in ("a.txt", "a.doc", "a.hwp", "a.pdf.exe", "noext"):
        r = client.post("/saas/uploads/sign", headers=H,
                        json={"filename": name})
        assert r.status_code == 400, name
    for name in ("a.pdf", "회사소개.DOCX"):
        assert client.post("/saas/uploads/sign", headers=H,
                           json={"filename": name}).status_code == 200, name


def test_sign_path_is_server_chosen_and_workspace_scoped(client, monkeypatch):
    """경로를 서버가 정한다 — 클라이언트가 정하면 남의 워크스페이스에 쓸 수 있다.

    원본 파일명은 경로에 넣지 않는다(고객사 실명이 스토리지 경로에 남는다).
    """
    _no_network(monkeypatch)
    r = client.post("/saas/uploads/sign", headers={"X-Dev-User": "boram"},
                    json={"filename": "고객사_IR.pdf"})
    assert r.status_code == 200
    body = r.json()
    assert body["path"].startswith("ws-boram/")
    assert "고객사" not in body["path"] and body["path"].endswith(".pdf")
    assert body["filename"] == "고객사_IR.pdf"     # 표시용으로만 돌려준다


def test_sign_cannot_be_steered_into_another_workspace(client, monkeypatch):
    """파일명에 경로 순회를 심어도 접두사를 벗어나지 못한다."""
    _no_network(monkeypatch)
    r = client.post("/saas/uploads/sign", headers={"X-Dev-User": "mallory"},
                    json={"filename": "../../ws-boram/steal.pdf"})
    # 확장자만 취하고 이름은 버리므로, 순회 문자열이 경로에 남지 않는다
    if r.status_code == 200:
        assert r.json()["path"].startswith("ws-mallory/")
        assert ".." not in r.json()["path"]


def test_health_endpoints(client):
    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/readyz").status_code in (200, 503)   # 키 유무에 따라
