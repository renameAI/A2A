"""T4 — 라우터 상태 가드 회귀 테스트 (감사 확정).

계약상 막혀 있어야 할 순서 위반이 500이나 잘못된 결과로 새어나가던 네 지점.
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
    import app.saas.store as store_mod
    store_mod._store = None
    import app.main as main_mod
    return TestClient(main_mod.app)


def _request_without_brief(client):
    """프로필 버전만 만들고 search-brief는 건너뛴 요청."""
    from app.saas.store import get_saas_store
    store = get_saas_store()
    store.put("profile_version", "ws-boram", "pv-1", {
        "version_id": "pv-1",
        "profile": {
            "basic": {"name": "귤메달", "country": "한국", "industry": "food"},
            "description": "건강음료",
            "problem_solved": {"value": "무가당 부족", "provenance": "stated"},
            "solution": {"value": "감귤 음료", "provenance": "stated"},
            "target_customer": {"value": "유통사", "provenance": "stated"}}})
    return client.post("/saas/lead-requests", headers=H, json={
        "title": "t", "profile_version_id": "pv-1",
        "intent": {"value_props": ["revenue_growth"], "target_region": "일본"},
    }).json()["request_id"]


def test_segments_without_brief_is_409_not_500(client):
    """search-brief 없이 /segments를 부르면 None 역참조로 500이 났다."""
    rid = _request_without_brief(client)
    r = client.post(f"/saas/lead-requests/{rid}/segments", headers=H)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_state"


def test_refine_before_search_is_409(client):
    rid = _request_without_brief(client)
    r = client.post(f"/saas/lead-requests/{rid}/refine", headers=H,
                    json={"answers": [], "liked": [], "disliked": [],
                          "done": False})
    assert r.status_code == 409


def test_refine_allowed_after_empty_wave(client):
    """웨이브1이 0곳으로 끝나도 재시도 버튼이 살아 있어야 한다 —
    pool 진리값으로 가드하면 여기서 '1차 검색이 먼저'라는 자기모순이 났다."""
    from app.saas.store import get_saas_store
    rid = _request_without_brief(client)
    store = get_saas_store()
    doc = store.get("lead_request", "ws-boram", rid)
    doc.update({"search_brief": {"synthesized_counterpart": "상대상",
                                 "query_hypotheses": ["q"]},
                "pool": [], "searched": True, "wave": 1,
                "feedback": {"liked": [], "disliked": [], "answers": []},
                "asked": []})
    store.put("lead_request", "ws-boram", rid, doc)
    r = client.post(f"/saas/lead-requests/{rid}/refine", headers=H,
                    json={"answers": [], "liked": [], "disliked": [],
                          "done": True})
    assert r.status_code == 202       # 409가 아니다 — 확정 경로가 열려 있다


def test_derived_key_includes_generation():
    """/search 재실행 시 파생 문서가 세대로 분리된다 — 같은 cid에 다른
    회사의 인사이트가 남아 메일 초안이 엉뚱한 근거로 나가던 경로."""
    from app.saas.router import _derived_key
    k1 = _derived_key({"generation": 1}, "lr-x", "web-lr-x-01")
    k2 = _derived_key({"generation": 2}, "lr-x", "web-lr-x-01")
    assert k1 != k2 and "lr-x" in k1 and "web-lr-x-01" in k1


def test_rank_pool_has_no_k_ceiling():
    """풀이 50을 넘어도 피드백 보정이 전 후보에 닿아야 한다."""
    import inspect

    from app.saas import router as r
    src = inspect.getsource(r._rank_pool)
    assert "min(max(k, len(records)), 50)" not in src
    assert "max(k, len(records))" in src


def test_usage_reports_caps_and_marks_estimate(client):
    """이번 달 사용량이 캡과 함께 나오고, 추정치임을 표시한다."""
    r = client.get("/saas/usage", headers=H)
    assert r.status_code == 200
    j = r.json()
    assert j["estimated"] is True
    assert j["workspace_cap_usd"] > 0 and j["global_cap_usd"] > 0
    assert "month" in j


def test_cost_cap_env_names_match_docs():
    """환경변수 이름이 코드와 문서에서 어긋나면 운영자가 설정한 캡이
    조용히 무시된다(감사 확정 — .env.example은 COST_CAP_REQUEST,
    코드는 COST_CAP_REQUEST_USD였다)."""
    import inspect
    import pathlib

    from app.saas import cost
    src = inspect.getsource(cost)
    example = (pathlib.Path(__file__).resolve().parent.parent
               / ".env.example").read_text(encoding="utf-8")
    for name in ("COST_CAP_REQUEST_USD", "COST_CAP_MONTH_USD",
                 "COST_CAP_GLOBAL_MONTH_USD"):
        assert name in src, f"{name}이 코드에 없다"
        assert name in example, f"{name}이 .env.example에 없다"


def test_request_list_is_summary_not_full_documents(client):
    """목록은 요약만 — pool·candidates 전문을 실어 보내면 요청이 쌓일수록
    사이드바 한 번에 수 MB가 나간다."""
    from app.saas.store import get_saas_store
    get_saas_store().put("lead_request", "ws-boram", "lr-big", {
        "request_id": "lr-big", "title": "큰 요청", "status": "candidates_ready",
        "intent": {"target_region": "일본"},
        "pool": [{"company_id": f"c{i}", "junk": "x" * 500} for i in range(50)],
        "candidates": [{"company_id": f"c{i}"} for i in range(30)]})
    j = client.get("/saas/lead-requests", headers=H).json()
    row = next(r for r in j["requests"] if r["request_id"] == "lr-big")
    assert row["candidate_count"] == 30
    assert "pool" not in row and "candidates" not in row
