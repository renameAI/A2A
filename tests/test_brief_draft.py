"""승인 직후 Lead Request 폼 초안 (propose_brief).

배경: 폼이 호텔 데모 값("일본"/"독립 호텔"/"객실 리노베이션과 운영 개선")으로
하드코딩돼 있어, 탄소 MRV 회사가 온보딩을 마치면 일본 호텔을 찾자고 제안했다.
채워진 칸은 사용자가 '엔진이 내 프로필을 읽고 판단한 값'으로 읽으므로, 근거
없는 값은 빈칸보다 나쁘다 — 그래서 실패는 **빈 초안**으로 떨어져야 하고,
승인 자체를 막으면 안 된다.
"""
import pytest

from app.engine import retrieve as R
from app.schemas import BasicInfo, Profile, Provenance, ProvField


def _profile():
    return Profile(
        basic=BasicInfo(name="뉴톤", country="한국", industry="탄소 제거 MRV"),
        description="센서와 디지털 트윈으로 탄소 제거 MRV를 디지털화한다.",
        problem_solved=ProvField(value="개발사가 감축량을 증빙하기 어렵다",
                                 provenance=Provenance.stated, confidence=0.9),
        solution=ProvField(value="MRV 디지털화",
                           provenance=Provenance.stated, confidence=0.9),
        target_customer=ProvField(value="베트남·인도네시아 탄소 프로젝트 개발사",
                                  provenance=Provenance.stated, confidence=0.9))


class _Canned:
    def __init__(self, payload): self.payload = payload
    def extract_json(self, *a, **k): return self.payload


def _use(monkeypatch, payload_or_exc):
    def _get(_settings):
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return _Canned(payload_or_exc)
    monkeypatch.setattr(R, "get_extractor", _get, raising=False)
    import app.engine.llm as llm
    monkeypatch.setattr(llm, "get_extractor", _get)


def test_draft_is_passed_through_and_stripped(monkeypatch):
    _use(monkeypatch, {"region": " 베트남 ", "target_type": " 개발사 ",
                       "notes": " MRV 제안 ", "purpose": "poc", "why": " 실증 중 "})
    d = R.propose_brief(_profile())
    assert d == {"region": "베트남", "target_type": "개발사",
                 "notes": "MRV 제안", "purpose": "poc", "why": "실증 중"}


def test_unknown_purpose_falls_back_to_revenue(monkeypatch):
    """purpose는 화면의 토글을 움직인다 — 모르는 값이 오면 기본 쪽으로."""
    _use(monkeypatch, {"region": "", "target_type": "x", "notes": "y",
                       "purpose": "무엇이든", "why": ""})
    assert R.propose_brief(_profile())["purpose"] == "revenue"


def test_missing_fields_become_empty_not_none(monkeypatch):
    """화면이 그대로 input value로 쓴다 — None이 들어가면 제어 컴포넌트가 깨진다."""
    _use(monkeypatch, {})
    d = R.propose_brief(_profile())
    assert all(isinstance(v, str) for v in d.values())
    assert d["region"] == "" and d["purpose"] == "revenue"


def test_failure_returns_empty_draft_and_never_raises(monkeypatch):
    """초안 실패가 승인을 막으면 안 된다 — 빈 폼은 지금까지의 동작과 같다."""
    _use(monkeypatch, RuntimeError("LLM down"))
    d = R.propose_brief(_profile())
    assert d["region"] == "" and d["target_type"] == "" and d["notes"] == ""
    assert d["purpose"] == "revenue"


def test_prompt_carries_no_industry_vocabulary():
    """프롬프트에 업종 어휘가 박히면 그 업종으로 초안이 쏠린다(호텔 사고의 재발).

    공용 HARD_RULES는 제외하고 이 프롬프트 고유 부분만 본다 — HARD_RULES에는
    '일본어 가나'처럼 언어 규칙으로서의 지역어가 정당하게 들어 있다.
    """
    from app.engine.prompts import HARD_RULES
    own = R.BRIEF_SYSTEM.replace(HARD_RULES, "")
    for word in ("호텔", "객실", "음료", "리노베이션", "일본", "패션", "탄소"):
        assert word not in own, f"업종 어휘 '{word}'가 초안 프롬프트에 있다"
    assert "지역·업종의 예시 어휘를 미리 갖고 있지 마라" in own
