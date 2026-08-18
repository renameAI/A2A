"""프로필 정정 — 다시 만들지 않고 고친다.

실측(프로덕션 저장 세션)으로 확인된 사고:
- 정정문 '뉴톤이야 기업명이'(9글자)가 새 온보딩 세션의 **유일한 자료**가 되어,
  회사 소개 15,559자로 만든 프로필이 버려지고 회사를 처음부터 다시 파악했다.
  그 결과 '뉴톤이야'가 회사명이 되고 기본 질문으로 되돌아갔다.
- 두 번째 정정('암석 풍화가 너무 강조되어 있어요')도 같은 방식으로 18글자짜리
  세션을 하나 더 만들었다.

세 겹으로 막는다: 정정은 세션에 붙고(라우터), 없는 질문을 지어내지 않으며,
프로필을 재생성하지 않고 편집한다.
"""
import pytest

from tests.test_saas_layer import client  # noqa: F401
from app.engine.represent import revise_profile
from app.schemas import BasicInfo, Profile, Provenance, ProvField


def _profile():
    return Profile(
        basic=BasicInfo(name="뉴턴", country="한국", industry="탄소 제거 MRV"),
        description="센서와 디지털 트윈으로 탄소 제거 MRV를 디지털화한다.",
        problem_solved=ProvField(value="개발사가 감축량을 증빙하기 어렵다",
                                 provenance=Provenance.inferred, confidence=0.7),
        solution=ProvField(value="MRV를 디지털화한다",
                           provenance=Provenance.inferred, confidence=0.7),
        target_customer=ProvField(value="베트남 개발사",
                                  provenance=Provenance.inferred, confidence=0.7))


class _Canned:
    def __init__(self, payload): self.payload = payload
    def extract_json(self, *a, **k):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _use(monkeypatch, payload):
    # revise_profile은 함수 안에서 `from .llm import get_extractor`를 한다 —
    # represent 모듈 속성을 패치해도 안 먹고 실제 API로 나간다(실측: 스위트가
    # 5분 타임아웃). 원본 모듈을 패치해야 한다.
    import app.engine.llm as llm
    monkeypatch.setattr(llm, "get_extractor", lambda s: _Canned(payload))


def _full(**over):
    base = {"name": "뉴턴", "country": "한국", "industry": "탄소 제거 MRV",
            "description": "센서와 디지털 트윈으로 탄소 제거 MRV를 디지털화한다.",
            "problem_solved": "개발사가 감축량을 증빙하기 어렵다",
            "solution": "MRV를 디지털화한다",
            "target_customer": "베트남 개발사",
            "changed": [], "unclear": ""}
    base.update(over)
    return base


def test_only_the_pointed_field_moves(monkeypatch):
    """지적하지 않은 필드가 바뀌면 사용자는 '바보가 됐다'고 느낀다."""
    _use(monkeypatch, _full(name="뉴톤", changed=["name"]))
    before = _profile()
    out, changed, unclear = revise_profile(before, ["뉴톤이야 기업명이"])
    assert out.basic.name == "뉴톤" and changed == ["name"] and not unclear
    assert out.description == before.description
    assert out.problem_solved.value == before.problem_solved.value
    assert out.solution.value == before.solution.value
    assert out.target_customer.value == before.target_customer.value


def test_corrected_field_becomes_stated(monkeypatch):
    """사용자가 직접 말한 값이므로 추론이 아니라 stated다."""
    _use(monkeypatch, _full(target_customer="한국 개발사",
                            changed=["target_customer"]))
    out, _, _ = revise_profile(_profile(), ["타깃은 한국이에요"])
    assert out.target_customer.value == "한국 개발사"
    assert out.target_customer.provenance == Provenance.stated
    assert out.target_customer.confidence >= 0.9


def test_ambiguous_note_changes_nothing_and_asks(monkeypatch):
    """짐작해서 고치는 것보다 되묻는 편이 낫다."""
    _use(monkeypatch, _full(unclear="어느 필드를 고칠지 알 수 없습니다"))
    before = _profile()
    out, changed, unclear = revise_profile(before, ["뭔가 이상해요"])
    assert changed == [] and unclear
    assert out.model_dump() == before.model_dump()


def test_failure_keeps_the_profile(monkeypatch):
    """정정을 못 반영하는 것이 프로필을 잃는 것보다 낫다."""
    _use(monkeypatch, RuntimeError("LLM down"))
    before = _profile()
    out, changed, unclear = revise_profile(before, ["이름 고쳐요"])
    assert out.model_dump() == before.model_dump()
    assert changed == [] and unclear == ""


def test_no_corrections_is_a_no_op(monkeypatch):
    """정정이 없으면 LLM을 부르지 않는다 — 부르면 비용만 든다."""
    called = []
    import app.engine.llm as llm
    monkeypatch.setattr(llm, "get_extractor",
                        lambda s: called.append(1) or _Canned(_full()))
    before = _profile()
    out, changed, _ = revise_profile(before, [])
    assert called == [] and changed == []
    assert out.model_dump() == before.model_dump()


def test_prompt_says_corrections_are_not_source_material():
    """지적을 자료로 읽으면 지시문이 필드 값이 된다 — 사고의 핵심이었다."""
    from app.engine.represent import REVISE_SYSTEM
    assert "지적은 자료가 아니다" in REVISE_SYSTEM
    assert "지시문 자체를 필드 값으로 넣지 마라" in REVISE_SYSTEM


class TestSessionRouting:
    """정정이 세션에 붙는가 — 자료를 대체하지 않는가."""

    def test_correction_needs_an_existing_profile(self, client):
        """프로필이 없으면 정정할 대상도 없다. 이때 자료로 삼으면 사고가 난다."""
        H = {"X-Dev-User": "boram"}
        sid = client.post("/saas/onboarding-sessions", headers=H, json={
            "assets": [{"type": "text", "content": "회사 소개"}]}).json()["session_id"]
        r = client.post(f"/saas/onboarding-sessions/{sid}/corrections",
                        headers=H, json={"note": "뉴톤이야 기업명이"})
        assert r.status_code == 409

    def test_correction_is_stored_as_correction_not_material(self, client):
        """정정은 assets에도 dialogue에도 들어가지 않는다."""
        from app.saas.store import get_saas_store
        H = {"X-Dev-User": "boram"}
        sid = client.post("/saas/onboarding-sessions", headers=H, json={
            "assets": [{"type": "text", "content": "긴 회사 소개 자료"}]}).json()["session_id"]
        store = get_saas_store()
        doc = store.get("onboarding", "ws-boram", sid)
        doc["profile"] = {"basic": {"name": "뉴턴", "country": "한국",
                                    "industry": "x"},
                          "description": "d"}
        store.put("onboarding", "ws-boram", sid, doc)

        r = client.post(f"/saas/onboarding-sessions/{sid}/corrections",
                        headers=H, json={"note": "뉴톤이야 기업명이"})
        assert r.status_code == 200
        doc = store.get("onboarding", "ws-boram", sid)
        assert doc["corrections"] == ["뉴톤이야 기업명이"]
        # 자료는 그대로 — 정정문이 회사 자료가 되면 안 된다(실제 사고)
        assert [a["content"] for a in doc["assets"]] == ["긴 회사 소개 자료"]
        assert doc["dialogue"] == []

    def test_empty_note_is_rejected(self, client):
        from app.saas.store import get_saas_store
        H = {"X-Dev-User": "boram"}
        sid = client.post("/saas/onboarding-sessions", headers=H, json={
            "assets": [{"type": "text", "content": "자료"}]}).json()["session_id"]
        store = get_saas_store()
        doc = store.get("onboarding", "ws-boram", sid)
        doc["profile"] = {"basic": {"name": "A", "country": "한국",
                                    "industry": "x"}, "description": "d"}
        store.put("onboarding", "ws-boram", sid, doc)
        assert client.post(f"/saas/onboarding-sessions/{sid}/corrections",
                           headers=H, json={"note": "   "}).status_code == 400

    def test_message_without_pending_question_is_not_invented_into_one(
            self, client):
        """물은 적 없는 질문을 지어내면 답의 의미가 바뀐다(사고의 2단계)."""
        from app.saas.store import get_saas_store
        H = {"X-Dev-User": "boram"}
        sid = client.post("/saas/onboarding-sessions", headers=H, json={
            "assets": [{"type": "text", "content": "자료"}]}).json()["session_id"]
        r = client.post(f"/saas/onboarding-sessions/{sid}/messages",
                        headers=H, json={"answer": "뉴톤이야 기업명이"})
        assert r.status_code == 200
        doc = get_saas_store().get("onboarding", "ws-boram", sid)
        assert doc["dialogue"] == []                       # 가짜 Q&A 없음
        assert doc["corrections"] == ["뉴톤이야 기업명이"]   # 정정으로 남는다

    def test_message_after_profile_is_a_correction_even_if_questions_linger(
            self, client):
        """성공한 /run이 남긴 보강 질문은 화면에 안 보인다 — 그 답으로 기록하면
        안 된다(실측: '뉴톤이야 기업명이'가 '푸는 문제' 답이 됐다)."""
        from app.saas.store import get_saas_store
        H = {"X-Dev-User": "boram"}
        sid = client.post("/saas/onboarding-sessions", headers=H, json={
            "assets": [{"type": "text", "content": "자료"}]}).json()["session_id"]
        store = get_saas_store()
        doc = store.get("onboarding", "ws-boram", sid)
        doc["profile"] = {"basic": {"name": "뉴턴", "country": "한국",
                                    "industry": "x"}, "description": "d"}
        doc["current_questions"] = ["귀사가 해결하는 문제는 무엇인가요?"]
        store.put("onboarding", "ws-boram", sid, doc)
        client.post(f"/saas/onboarding-sessions/{sid}/messages",
                    headers=H, json={"answer": "뉴톤이야 기업명이"})
        doc = store.get("onboarding", "ws-boram", sid)
        assert doc["dialogue"] == []
        assert doc["corrections"] == ["뉴톤이야 기업명이"]
        assert doc["current_questions"] == ["귀사가 해결하는 문제는 무엇인가요?"]
