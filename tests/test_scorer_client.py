"""학습 스코어러 배관 테스트 — HTTP mock, 재랭킹, 정직 폴백. 완전 오프라인.

계약: τ 게이트는 휴리스틱이 유지하고, 학습 점수는 게이트 통과 후보의 '순서'만
바꾼다. 서버 부재 시 조용한 대체 없이 휴리스틱 순서 그대로 (learned=None).
"""
import httpx
import pytest

import app.engine.retrieve as R
import app.engine.scorer_client as SC
from app.engine.pool import CandidateRecord
from app.schemas import (BasicInfo, Intent, PoolKind, Profile, ProvField,
                         Provenance, RetrieveDirection, RetrieveRequest,
                         ValueProp)


def _prof(name="다이브인그룹"):
    def f(v):
        return ProvField(value=v, provenance=Provenance.stated, confidence=None)
    return Profile(basic=BasicInfo(name=name, country="한국", industry="hospitality"),
                   description="노후 호텔 재생", problem_solved=f("노후 호텔 객실의 매출 정체"),
                   solution=f("저자본 예술 전환"), target_customer=f("중소 호텔 오너"),
                   sell_value_props=[ValueProp.revenue_growth])


def _cand(cid, pain):
    p = _prof(name=f"기업{cid}")
    return CandidateRecord(company_id=cid, pool=PoolKind.external, profile=p,
                           pain_points=pain, tags=["노후 객실"])


def _req():
    return RetrieveRequest(requester_profile=_prof(),
                           intent=Intent(value_props=[ValueProp.revenue_growth],
                                         target_region="한국"),
                           direction=RetrieveDirection.sell_outreach)


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._p


class TestScoreBatch:
    def test_off_without_url(self, monkeypatch):
        monkeypatch.delenv("A2A_SCORER_URL", raising=False)
        assert SC.score_batch([("a", "b")]) is None

    def test_success(self, monkeypatch):
        monkeypatch.setenv("A2A_SCORER_URL", "http://localhost:8500")
        monkeypatch.setattr(SC.httpx, "post", lambda *a, **k: _Resp(
            {"scores": [{"score": 7.2}, {"score": 2.1}]}))
        assert SC.score_batch([("a", "b"), ("c", "d")]) == [7.2, 2.1]

    def test_connection_error_falls_back(self, monkeypatch):
        monkeypatch.setenv("A2A_SCORER_URL", "http://localhost:1")   # 연결 불가
        def boom(*a, **k):
            raise httpx.ConnectError("refused")
        monkeypatch.setattr(SC.httpx, "post", boom)
        assert SC.score_batch([("a", "b")]) is None

    def test_count_mismatch_falls_back(self, monkeypatch):
        """부분 응답은 순서 정합성이 깨지므로 전체 폴백 — 부분 채택 없음."""
        monkeypatch.setenv("A2A_SCORER_URL", "http://localhost:8500")
        monkeypatch.setattr(SC.httpx, "post", lambda *a, **k: _Resp(
            {"scores": [{"score": 7.2}]}))
        assert SC.score_batch([("a", "b"), ("c", "d")]) is None


class TestRetrieveRerank:
    PAIN = "노후 호텔 객실 매출 정체로 저자본 해법이 필요"

    def test_learned_order_overrides_heuristic_ties(self, monkeypatch):
        """동점(휴리스틱) 후보의 순서를 학습 점수가 결정 + 필드 채움."""
        pool = [_cand("co-aaa", self.PAIN), _cand("co-bbb", self.PAIN)]
        monkeypatch.setattr(R, "get_pool", lambda: pool)
        # co-bbb에 더 높은 학습 점수 — 휴리스틱 tie-break(id순)면 co-aaa가 앞이었다
        monkeypatch.setattr(SC, "score_batch", lambda pairs: [3.0, 8.5])
        res = R.retrieve(_req())
        assert [c.company_id for c in res.candidates][:2] == ["co-bbb", "co-aaa"]
        assert res.candidates[0].learned_relatedness == 8.5
        assert res.candidates[0].retrieval_score > 0    # 휴리스틱 점수도 보존

    def test_fallback_keeps_heuristic_order(self, monkeypatch):
        """서버 부재(None) — 순서·필드 모두 기존 동작 그대로 (회귀 0)."""
        pool = [_cand("co-aaa", self.PAIN), _cand("co-bbb", self.PAIN)]
        monkeypatch.setattr(R, "get_pool", lambda: pool)
        monkeypatch.setattr(SC, "score_batch", lambda pairs: None)
        res = R.retrieve(_req())
        assert [c.company_id for c in res.candidates][:2] == ["co-aaa", "co-bbb"]
        assert all(c.learned_relatedness is None for c in res.candidates)

    def test_gate_stays_heuristic(self, monkeypatch):
        """학습 점수가 높아도 τ 미달 후보는 못 들어온다 — 게이트 불변."""
        pool = [_cand("co-aaa", self.PAIN),
                _cand("co-zzz", "무관한 반도체 장비 수출")]   # τ 미달 예상
        monkeypatch.setattr(R, "get_pool", lambda: pool)
        monkeypatch.setattr(SC, "score_batch",
                            lambda pairs: [1.0] * len(pairs))
        res = R.retrieve(_req())
        ids = [c.company_id for c in res.candidates]
        assert "co-zzz" not in ids                     # 학습 점수로 게이트 못 뚫음


def test_profile_facts_matches_training_format():
    t = SC.profile_facts("한화", "화학", "한국", "방산·화학 대기업")
    assert t.startswith("한화 — 산업 섹터: 화학, 국가: 한국.")
    assert "방산" in t
