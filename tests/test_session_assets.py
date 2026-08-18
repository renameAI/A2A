"""세션에 자료를 추가한다 — 새 세션을 만들지 않는다.

배경: 자산 추가 API가 없어 클라이언트가 파일이 더 오면 새 세션을 만들었고,
앞서 붙여넣은 소개 텍스트가 버려졌다. 정정 사고와 같은 계열(멀티턴 약함).
채팅 입력의 의미도 여기서 확정한다 — 프로필 전엔 자료, 후엔 정정.
"""
from tests.test_saas_layer import client, H  # noqa: F401


def _new(client, content="첫 소개"):
    return client.post("/saas/onboarding-sessions", headers=H, json={
        "assets": [{"type": "text", "content": content}]}).json()["session_id"]


def _doc(sid):
    from app.saas.store import get_saas_store
    return get_saas_store().get("onboarding", "ws-boram", sid)


def test_assets_are_appended_not_replaced(client):
    sid = _new(client)
    r = client.post(f"/saas/onboarding-sessions/{sid}/assets", headers=H, json={
        "assets": [{"type": "ir_deck", "content": "", "url": "supabase://ws-boram/a.pdf"}]})
    assert r.status_code == 200
    a = _doc(sid)["assets"]
    assert [x["type"] for x in a] == ["text", "ir_deck"]
    assert a[0]["content"] == "첫 소개"          # 앞의 자료가 살아 있다


def test_adding_material_after_a_profile_forces_rebuild(client):
    """새 자료가 어느 필드를 바꿀지 모른다 — 정정이 아니라 재생성이 맞다."""
    sid = _new(client)
    d = _doc(sid); d["profile"] = {"basic": {"name": "A"}}; d["corrections"] = ["x"]
    from app.saas.store import get_saas_store
    get_saas_store().put("onboarding", "ws-boram", sid, d)
    client.post(f"/saas/onboarding-sessions/{sid}/assets", headers=H, json={
        "assets": [{"type": "text", "content": "추가"}]})
    d = _doc(sid)
    assert d["profile"] is None and d["corrections"] == []


def test_cannot_add_to_an_approved_session(client):
    sid = _new(client)
    from app.saas.store import get_saas_store
    d = _doc(sid); d["status"] = "completed"; get_saas_store().put("onboarding", "ws-boram", sid, d)
    r = client.post(f"/saas/onboarding-sessions/{sid}/assets", headers=H, json={
        "assets": [{"type": "text", "content": "늦은 자료"}]})
    assert r.status_code == 409


def test_chat_before_profile_without_questions_is_material(client):
    """소개를 두 번에 나눠 붙여넣는 사용자 — 두 번째는 정정이 아니라 자료다."""
    sid = _new(client, "첫 문단")
    client.post(f"/saas/onboarding-sessions/{sid}/messages", headers=H,
                json={"answer": "둘째 문단"})
    d = _doc(sid)
    assert [a["content"] for a in d["assets"]] == ["첫 문단", "둘째 문단"]
    assert d.get("corrections", []) == [] and d["dialogue"] == []


def test_chat_while_clarifying_answers_the_pending_question(client):
    sid = _new(client)
    from app.saas.store import get_saas_store
    d = _doc(sid); d["current_questions"] = ["Q1", "Q2"]
    get_saas_store().put("onboarding", "ws-boram", sid, d)
    client.post(f"/saas/onboarding-sessions/{sid}/messages", headers=H,
                json={"answer": "A1"})
    d = _doc(sid)
    assert d["dialogue"] == [{"q": "Q1", "a": "A1"}]
    assert d["current_questions"] == ["Q2"]
    assert len(d["assets"]) == 1                    # 자료로 새지 않는다
