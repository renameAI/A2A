"""judge 자기일관성 투표(L2) + 하드 게이트(L3) 테스트 — 완전 오프라인.

_llm_judge를 스텁으로 대체해 결정 시퀀스를 제어하고, _vote_llm_judge의 다수결·일치율과
_apply_consistency_gate의 밴드 도출·저합의 캡·사람 라우팅을 검증한다.
"""
import types

import app.engine.judge as J
from app.schemas import (BasicInfo, CategoryJudgment, ConfidenceBand,
                         DecisionType, Dimension, Intent, JudgeRequest,
                         JudgeResult, MatchSummary, Objective, Profile,
                         ProvField, Provenance, ValueProp, Vantage, VerdictType)


def _prof(name):
    def f(v):
        return ProvField(value=v, provenance=Provenance.stated)
    return Profile(basic=BasicInfo(name=name, country="한국", industry="hospitality"),
                   description="d", problem_solved=f("노후 객실 매출 정체"),
                   solution=f("저자본 예술 전환"), target_customer=f("중소 호텔 오너"),
                   sell_value_props=[ValueProp.revenue_growth])


def _judge_req():
    return JudgeRequest(vantage=Vantage.seller, objective=Objective.exploration_budget,
                        self_profile=_prof("다이브인"), counterpart_profile=_prof("상대사"),
                        intent=Intent(value_props=[ValueProp.revenue_growth]))


def _mk_result(decision: DecisionType) -> JudgeResult:
    return JudgeResult(
        category_judgments=[CategoryJudgment(
            dimension=Dimension.industry_fit, verdict=VerdictType.fit, rationale="r")],
        risks=[], reasoning_moves=["risk_triage"], trajectory="t",
        decision=decision, decision_rationale="원 근거",
        fit_reasons=["근거1"],
        match_summary=MatchSummary(problem_solution="p→s",
                                   value_proposition="매출", reference="first_case"))


def _settings(tau=0.6):
    return types.SimpleNamespace(judge_agreement_threshold=tau)


class TestVoting:
    def test_single_sample_no_voting(self, monkeypatch):
        monkeypatch.setattr(J, "_llm_judge",
                            lambda req, ex, deep=True: _mk_result(DecisionType.recommend))
        result, agreement = J._vote_llm_judge(None, object(), False, samples=1)
        assert agreement is None            # 미계측 — 단일 표본
        assert result.decision == DecisionType.recommend

    def test_majority_vote_and_agreement(self, monkeypatch):
        seq = [DecisionType.recommend, DecisionType.recommend, DecisionType.hold]
        it = iter(seq)
        monkeypatch.setattr(J, "_llm_judge",
                            lambda req, ex, deep=True: _mk_result(next(it)))
        result, agreement = J._vote_llm_judge(None, object(), False, samples=3)
        assert result.decision == DecisionType.recommend   # 2/3 다수결
        assert agreement == 2 / 3

    def test_unanimous_agreement_one(self, monkeypatch):
        monkeypatch.setattr(J, "_llm_judge",
                            lambda req, ex, deep=True: _mk_result(DecisionType.hold))
        _, agreement = J._vote_llm_judge(None, object(), False, samples=4)
        assert agreement == 1.0


class TestConsistencyGate:
    def test_high_agreement_keeps_decision(self):
        r = _mk_result(DecisionType.recommend)
        J._apply_consistency_gate(r, 0.9, _settings())
        assert r.confidence_band == ConfidenceBand.high
        assert r.needs_human is False
        assert r.decision == DecisionType.recommend

    def test_low_agreement_caps_soft_yes_to_hold(self):
        """L3 — 저합의 자동추천은 hold로 캡 + needs_human."""
        r = _mk_result(DecisionType.recommend)
        J._apply_consistency_gate(r, 0.4, _settings(0.6))
        assert r.needs_human is True
        assert r.decision == DecisionType.hold
        assert r.confidence_band == ConfidenceBand.low
        assert "일치율" in r.decision_rationale

    def test_low_agreement_conditional_also_capped(self):
        r = _mk_result(DecisionType.conditional)
        J._apply_consistency_gate(r, 0.5, _settings(0.6))
        assert r.needs_human is True and r.decision == DecisionType.hold

    def test_low_agreement_terminate_flags_but_not_capped(self):
        """terminate는 소프트-예가 아니라 캡 대상이 아님(보류로 완화하면 안 됨)."""
        r = _mk_result(DecisionType.terminate)
        J._apply_consistency_gate(r, 0.4, _settings())
        assert r.needs_human is True
        assert r.decision == DecisionType.terminate   # 캡 안 됨

    def test_none_agreement_is_noop(self):
        """미계측(단일 표본)이면 게이트 발동 안 함 — 측정 없는 확신 만들지 않음."""
        r = _mk_result(DecisionType.recommend)
        J._apply_consistency_gate(r, None, _settings())
        assert r.needs_human is False
        assert r.decision == DecisionType.recommend
        assert r.confidence_band is None            # 변경 안 됨

    def test_medium_band_at_threshold(self):
        r = _mk_result(DecisionType.conditional)
        J._apply_consistency_gate(r, 0.7, _settings(0.6))
        assert r.confidence_band == ConfidenceBand.medium
        assert r.needs_human is False


class TestMockPathUnaffected:
    def test_mock_judge_leaves_agreement_none(self):
        """Mock(규칙) 경로는 투표 없이 결정적 — sample_agreement=None, needs_human=False."""
        result = J.judge(_judge_req())
        assert result.sample_agreement is None
        assert result.needs_human is False


class TestUngroundedClaimStripping:
    """fit_reasons 환각 주장 코드 집행 — 입력에 없는 수치·영문 고유명사는 제거.

    compose가 claim_trace로 fit_reasons를 인용하므로, 환각 주장이 남으면
    콜드메일에 근거 없는 수치가 실린다(가장 비싼 실패 지점).
    """

    def _req(self):
        from app.engine.pool import SEED_POOL
        from app.schemas import (Intent, JudgeRequest, Objective, Vantage)
        return JudgeRequest(
            vantage=Vantage.seller, objective=Objective.exploration_budget,
            self_profile=SEED_POOL[0].profile,
            counterpart_profile=SEED_POOL[1].profile,
            intent=Intent(value_props=["revenue_growth"], target_region="베트남"))

    def _result(self, fit_reasons):
        from app.engine.judge import judge
        from app.schemas import JudgeResult
        base = judge(self._req())          # 규칙 경로 결과를 템플릿으로
        d = base.model_dump()
        d["fit_reasons"] = fit_reasons
        return JudgeResult.model_validate(d)

    def test_hallucinated_number_removed(self):
        from app.engine.judge import _strip_ungrounded_claims
        r = self._result(["상대는 연 매출 987억 원 규모라 구매력이 충분하다",
                          "보완성이 명확하다"])
        removed = _strip_ungrounded_claims(r, self._req(), None)
        assert removed == 1
        assert r.fit_reasons == ["보완성이 명확하다"]

    def test_hallucinated_latin_name_removed(self):
        from app.engine.judge import _strip_ungrounded_claims
        r = self._result(["Bosch와의 기존 계약이 신뢰를 보증한다"])
        removed = _strip_ungrounded_claims(r, self._req(), None)
        assert removed == 1
        assert r.fit_reasons == ["판단 근거 부족 — 접촉으로 확인 필요"]   # 전량 제거 폴백

    def test_grounded_claims_survive(self):
        from app.engine.judge import _strip_ungrounded_claims
        # 입력에 실재하는 서술(일반 한국어)은 패러프레이즈여도 건드리지 않는다
        r = self._result(["노후 객실 문제를 겪는 상대라 보완성이 맞물린다"])
        removed = _strip_ungrounded_claims(r, self._req(), None)
        assert removed == 0
        assert len(r.fit_reasons) == 1

    def test_judge_vocab_whitelisted(self):
        """판단 어휘(enum·차원명·업계 약어)는 입력에 없어도 환각이 아니다 —
        실측 오탐(반증 조건 문장 속 'fit'·'conditional') 회귀."""
        from app.engine.judge import _strip_ungrounded_claims
        r = self._result(["conditional 결정은 demonstrability가 fit로 바뀌면 상향된다"])
        removed = _strip_ungrounded_claims(r, self._req(), None)
        assert removed == 0
