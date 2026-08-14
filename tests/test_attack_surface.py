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
    assert client.post("/saas/upload").status_code == 401


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


def test_upload_rejects_non_pdf_content(client):
    """확장자는 사용자가 붙이는 것이라 신뢰할 수 없다 — 매직바이트로 본다."""
    r = client.post("/saas/upload", headers={"X-Dev-User": "boram"},
                    files={"file": ("a.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400
    r = client.post("/saas/upload", headers={"X-Dev-User": "boram"},
                    files={"file": ("a.txt", b"%PDF-1.4\n", "application/pdf")})
    assert r.status_code == 400          # 확장자도 함께 본다


def test_upload_size_cap(client, monkeypatch):
    """상한 로직을 검증한다 — 40MB 페이로드를 실제로 만들면 테스트가 느려지고
    (실측 수십 초) 검증되는 것은 같다. 상수를 낮춰 경계만 본다."""
    from app.saas import router as r_mod
    monkeypatch.setattr(r_mod, "_UPLOAD_MAX", 4096)
    ok = b"%PDF-1.4\n" + b"x" * 1000
    assert client.post("/saas/upload", headers={"X-Dev-User": "boram"},
                       files={"file": ("ok.pdf", ok, "application/pdf")}
                       ).status_code == 200
    big = b"%PDF-1.4\n" + b"x" * 8192
    assert client.post("/saas/upload", headers={"X-Dev-User": "boram"},
                       files={"file": ("big.pdf", big, "application/pdf")}
                       ).status_code == 413


def test_upload_stored_under_workspace(client, tmp_path):
    """업로드는 워크스페이스 디렉터리에 담기고, 원본 파일명은 경로에 안 쓴다
    (고객사 실명이 경로에 남지 않게)."""
    r = client.post("/saas/upload", headers={"X-Dev-User": "boram"},
                    files={"file": ("고객사_IR.pdf", b"%PDF-1.4\n" + b"x" * 100,
                                    "application/pdf")})
    assert r.status_code == 200
    path = r.json()["path"]
    assert "ws-boram" in path and "고객사" not in path
    assert r.json()["filename"] == "고객사_IR.pdf"


def test_health_endpoints(client):
    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/readyz").status_code in (200, 503)   # 키 유무에 따라
