"""콜드메일 법적 고지 — 초안에서 발송으로 넘어온 순간 생긴 의무.

참고 자료(운영 중인 아웃리치 프롬프트 5종) 중 4종이 수신 거부·발신자
주소·준거법 고지를 **고정 문구**로 갖고 있었다. 우리 프롬프트에는 하나도
없었고, 그 상태로 실제 발송을 시작했다. 고지는 모델이 매번 새로 쓸 글이
아니라 코드가 붙일 것이다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import compliance

FULL = {"legal_name": "DayOne Lab Inc.",
        "postal_address": "서울 강남구 테헤란로 7길 22",
        "contact_email": "tools@renamecorp.com"}


class TestFooter:
    def test_missing_address_blocks_the_footer(self):
        """부분적으로 채운 고지는 고지가 아니라 위장이다."""
        assert compliance.footer({"legal_name": "A", "contact_email": "b@c.d"},
                                 language="en") == ""

    def test_missing_fields_are_named(self):
        assert compliance.missing_fields({"legal_name": "A"}) == [
            "postal_address", "contact_email"]

    def test_footer_carries_address_and_optout(self):
        f = compliance.footer(FULL, language="ko", country="KR")
        assert "테헤란로" in f and "수신 거부" in f

    def test_language_follows_the_mail(self):
        """수신자가 못 읽는 고지는 고지의 목적을 이루지 못한다."""
        assert "Abmelden" in compliance.footer(FULL, language="de", country="DE")
        assert "Désinscription" in compliance.footer(FULL, language="fr")

    def test_unknown_language_falls_back_to_english(self):
        assert "unsubscribe" in compliance.footer(FULL, language="sw")


class TestApplicableLaw:
    def test_named_per_country(self):
        assert compliance.law_for("US") == "CAN-SPAM Act"
        assert compliance.law_for("DE") == "GDPR"
        assert compliance.law_for("JP") == "特定電子メール法"

    def test_unknown_country_claims_no_law(self):
        """적용되지도 않는 법을 대며 정당성을 주장하는 것은 그 자체로 거짓이다."""
        assert compliance.law_for("ZZ") == ""
        assert "[" not in compliance.footer(FULL, language="en", country="ZZ")

    def test_law_is_stated_when_known(self):
        assert "[GDPR]" in compliance.footer(FULL, language="de", country="DE")


class TestSendGate:
    def test_prepare_refuses_without_identity(self):
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_prepare)
        assert "identity_required" in src
        assert "compliance.missing_fields" in src

    def test_footer_is_appended_to_the_body(self):
        import inspect
        from app.saas import router
        assert "compliance.footer" in inspect.getsource(router.outreach_prepare)


class TestFabricatedUrl:
    """실측: 모델이 인용 주소를 한 글자 바꿔 적어 404가 됐다
    (…-la-agricultura → …-la-agriculture). 인용이 신뢰를 부수는 순간이다."""

    def _req(self, srcs):
        from app.schemas import (BasicInfo, CandidateInsight,
                                 ComposeLeadRequest, Intent, Profile,
                                 ProvField, Provenance)
        pf = ProvField(value="x", provenance=Provenance.inferred, confidence=0.5)
        prof = Profile(basic=BasicInfo(name="A", country="KR", industry="i"),
                       description="", problem_solved=pf, solution=pf,
                       target_customer=pf)
        return ComposeLeadRequest(
            requester_profile=prof, intent=Intent(value_props=["revenue_growth"]),
            candidate_profile=prof,
            candidate_insight=CandidateInsight(candidate_id="c1",
                                               source_urls=srcs),
            language="de")

    def test_altered_url_is_flagged(self):
        from app.engine.compose_lead import _url_warnings
        w = _url_warnings(
            self._req(["https://fkur.com/es/a-la-agricultura"]),
            {"paragraphs": ["Auf https://fkur.com/es/a-la-agriculture gesehen."]})
        assert w and "지어낸" in w[0]

    def test_exact_url_passes(self):
        from app.engine.compose_lead import _url_warnings
        assert _url_warnings(
            self._req(["https://fkur.com/es/a"]),
            {"paragraphs": ["Auf https://fkur.com/es/a gesehen."]}) == []

    def test_trailing_slash_is_not_a_fabrication(self):
        """정규화가 없으면 오탐이 쌓여 사용자가 경고를 무시하게 된다."""
        from app.engine.compose_lead import _url_warnings
        assert _url_warnings(
            self._req(["https://fkur.com/es/a/"]),
            {"paragraphs": ["Auf https://fkur.com/es/a gesehen."]}) == []

    def test_no_known_urls_means_no_claim(self):
        """대조할 것이 없으면 판정하지 않는다 — 모르면 모른다고 한다."""
        from app.engine.compose_lead import _url_warnings
        assert _url_warnings(self._req([]),
                             {"paragraphs": ["https://x.com/y"]}) == []

    def test_send_is_blocked_on_these(self):
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_prepare)
        assert "draft_needs_fix" in src and "지어낸 주소" in src
