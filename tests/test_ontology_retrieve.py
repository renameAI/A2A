"""온톨로지 참고 힌트 — 실 재료 파일 기반. 오탐(무관한 산업에 힌트가 잘못 붙는 것)이
Judge 프롬프트에 노이즈를 주입하는 실제 리스크라서, 오탐 회귀 테스트를 정탐만큼 비중 있게 둔다.
"""
from app.engine.judge import _ontology_hint
from app.ontology.retrieve import domain_hint
from app.schemas import BasicInfo, JudgeRequest, Objective, ProvField, Profile, Provenance, Vantage
from app.schemas import Intent


class TestDomainHintPositive:
    def test_matches_art_domain(self):
        hint = domain_hint("art_design", "발달장애 디자이너 아트웍으로 브랜드 콜라보 상품")
        assert hint is not None
        assert "아트/디자인" in hint

    def test_matches_materials_domain(self):
        hint = domain_hint("materials", "나노셀룰로오스 친환경 고성능 자동차 소재")
        assert hint is not None
        assert "소재/화학" in hint

    def test_always_carries_simulation_caveat(self):
        """구매측이 AI 시뮬레이션이라는 고지 없이 참고를 내보내면 안 된다."""
        hint = domain_hint("materials", "나노셀룰로오스 친환경 소재")
        assert "시뮬레이션" in hint

    def test_deterministic(self):
        a = domain_hint("art_design", "발달장애 디자이너 아트웍")
        b = domain_hint("art_design", "발달장애 디자이너 아트웍")
        assert a == b


class TestDomainHintNegative:
    """회귀: 최초 구현은 buyer_label(고유명사·외국어 인명 포함)까지 매칭 대상에
    넣어 무관한 질의도 우연히 임계를 넘겼다(양자컴퓨팅 → 아트/디자인 오매칭 실측)."""

    def test_unrelated_industry_returns_none(self):
        assert domain_hint("quantum_computing", "양자 컴퓨팅 칩셋 반도체 설계") is None
        assert domain_hint("cloud_infra", "클라우드 컨테이너 오케스트레이션 플랫폼") is None

    def test_uncovered_domain_returns_none_not_false_match(self):
        """이 5개 도메인 자체가 아닌 산업(호텔)은 억지로 아무 도메인에나 붙이지 않는다."""
        assert domain_hint("hospitality", "노후 호텔 객실을 예술 경험형 상품으로 전환") is None

    def test_empty_input(self):
        assert domain_hint("", "") is None
        assert domain_hint("미상", "") is None


def _profile(industry, description):
    field = ProvField(value="x", provenance=Provenance.stated)
    return Profile(basic=BasicInfo(name="테스트", country="한국", industry=industry),
                   description=description, problem_solved=field, solution=field,
                   target_customer=field)


class TestJudgeIntegration:
    def test_ontology_hint_reaches_judge_user(self):
        """judge.py의 _ontology_hint가 실제로 도메인 매칭 결과를 낸다 — 배선 확인."""
        req = JudgeRequest(
            self_profile=_profile("art_design", "발달장애 디자이너 아트웍 스튜디오"),
            counterpart_profile=_profile("retail", "리빙 브랜드"),
            vantage=Vantage.seller, objective=Objective.exploration_budget,
            intent=Intent(value_props=["revenue_growth"]))
        hint = _ontology_hint(req)
        assert hint is not None
        assert "아트/디자인" in hint

    def test_no_hint_for_unrelated_pair(self):
        req = JudgeRequest(
            self_profile=_profile("quantum_computing", "양자 칩셋 설계"),
            counterpart_profile=_profile("cloud_infra", "클라우드 인프라"),
            vantage=Vantage.seller, objective=Objective.exploration_budget,
            intent=Intent(value_props=["revenue_growth"]))
        assert _ontology_hint(req) is None
