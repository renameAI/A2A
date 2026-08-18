"""회사명은 사용자가 아는 사실 — 추론값이 이길 수 없다.

배경: represent.py의 자기참조 금칙 등은 "회사명은 온보딩 모달 필수 항목이라
이미 안다"를 전제로 설계됐는데, 채팅 UI엔 그 입력이 없었다. 그래서 이름이
LLM 추출에만 의존했고 '뉴턴/뉴톤'이 실행마다 오갔다. 세션이 company_name을
받으면 코드가 basic.name을 확정한다(판정=모델, 결정=코드).
"""
from tests.test_saas_layer import client, H  # noqa: F401


def _wait(client, job):
    import time
    for _ in range(50):
        d = client.get(f"/saas/jobs/{job}", headers=H).json()
        if d["status"] in ("done", "error"):
            assert d["status"] == "done", d.get("error")
            return d
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _stub_represent(monkeypatch, extracted_name="뉴턴"):
    """represent가 추출한 이름을 고정한다 — 검증 대상은 그 위에 얹는 코드 결정이다."""
    import app.saas.router as R
    from app.schemas import (BasicInfo, Profile, Provenance, ProvField,
                             RepresentResponse)

    def fake(req, settings=None):
        prof = Profile(
            basic=BasicInfo(name=extracted_name, country="한국", industry="x"),
            description="d",
            problem_solved=ProvField(value="p", provenance=Provenance.stated, confidence=0.9),
            solution=ProvField(value="s", provenance=Provenance.stated, confidence=0.9),
            target_customer=ProvField(value="t", provenance=Provenance.stated, confidence=0.9))
        fake.last_dialogue = [(t.q, t.a) for t in req.dialogue]
        return RepresentResponse(profile=prof, embedding=[0.0], ontology_anchors=[],
                                 minimum_met=True, open_questions=[],
                                 engine_mode="llm", sources=[])
    fake.last_dialogue = []
    monkeypatch.setattr(R, "represent", fake)
    return fake


def _new(client, name=None):
    body = {"assets": [{"type": "text", "content": "회사 소개"}]}
    if name is not None:
        body["company_name"] = name
    return client.post("/saas/onboarding-sessions", headers=H, json=body).json()["session_id"]


def _run(client, sid):
    return _wait(client, client.post(f"/saas/onboarding-sessions/{sid}/run",
                                     headers=H).json()["job_id"])


def test_given_name_overrides_extracted_name(client, monkeypatch):
    fake = _stub_represent(monkeypatch, extracted_name="뉴턴")
    d = _run(client, _new(client, "뉴톤"))
    assert d["result"]["session"]["profile"]["basic"]["name"] == "뉴톤"
    # 모델에게도 이름이 전달된다 — 서술이 다른 표기로 회사를 부르지 않도록
    assert ("회사 이름은 무엇인가요?", "뉴톤") in fake.last_dialogue


def test_missing_name_keeps_extraction(client, monkeypatch):
    """이름을 안 주면 지금까지처럼 추출값 — 회귀 없음."""
    fake = _stub_represent(monkeypatch, extracted_name="뉴턴")
    d = _run(client, _new(client))
    assert d["result"]["session"]["profile"]["basic"]["name"] == "뉴턴"
    assert fake.last_dialogue == []


def test_blank_name_is_treated_as_missing(client):
    from app.saas.store import get_saas_store
    sid = _new(client, "   ")
    assert get_saas_store().get("onboarding", "ws-boram", sid)["company_name"] is None


def test_corrected_name_becomes_the_new_fact(client, monkeypatch):
    """정정으로 이름이 바뀌면 다음 /run이 옛 이름으로 되돌리면 안 된다."""
    from app.saas.store import get_saas_store
    _stub_represent(monkeypatch, extracted_name="뉴턴")
    sid = _new(client, "뉴턴")
    _run(client, sid)

    import app.engine.represent as rep
    def fake_revise(profile, notes):
        p = profile.model_copy(deep=True); p.basic.name = "뉴톤"
        return p, ["name"], ""
    monkeypatch.setattr(rep, "revise_profile", fake_revise)
    client.post(f"/saas/onboarding-sessions/{sid}/corrections", headers=H,
                json={"note": "뉴톤이야 기업명이"})
    _run(client, sid)
    doc = get_saas_store().get("onboarding", "ws-boram", sid)
    assert doc["company_name"] == "뉴톤"
    assert doc["profile"]["basic"]["name"] == "뉴톤"
    # 그 뒤 재생성이 일어나도 새 이름이 이긴다
    _run(client, sid)
    assert get_saas_store().get("onboarding", "ws-boram", sid)["profile"]["basic"]["name"] == "뉴톤"
