"""아웃리치 킷 — 누구에게·어디로·왜 지금·훅. 심층 판독의 접점·신호에서만 나온다.

채널 값은 인용 계약: 접점 목록에 없는 주소를 모델이 그럴듯하게 지어내면
이메일이 허공으로 간다 — 코드가 검사해 지운다.
"""
from app.engine.candidate_insight import (_ontology_block, build_insight,
                                          insight_user)
from app.schemas import BasicInfo, Intent, Profile, Provenance, ProvField


def _p(name="A"):
    return Profile(basic=BasicInfo(name=name, country="한국", industry="x"),
                   description="d",
                   problem_solved=ProvField(value="p", provenance=Provenance.stated, confidence=.9),
                   solution=ProvField(value="s", provenance=Provenance.stated, confidence=.9),
                   target_customer=ProvField(value="t", provenance=Provenance.stated, confidence=.9))


class _Canned:
    def __init__(self, kit): self.kit = kit; self.src = None
    def extract_json(self, system, src, schema, **k):
        self.src = src
        return {"observed_needs": [], "need_evidence": [], "value_bridge": [],
                "personalization_hooks": [], "uncertainties": ["x"], "outreach": self.kit}


ONT = {"contacts": [{"channel": "문의 폼", "value": "https://a.com/contact", "role_hint": ""}],
       "signals": [{"category": "partnership", "evidence": "Microsoft와 계약", "observed_at": "2025"}],
       "axes": {"demand_side": {"value": "개발사", "status": "confirmed", "evidence": ""}}}
INTENT = Intent(value_props=["revenue_growth"], lead_count=5)


def test_kit_passes_through_when_channel_is_cited():
    x = _Canned({"to_role": "파트너십 담당", "channel": "문의 폼",
                 "channel_value": "https://a.com/contact", "why_now": "MS 계약", "hook": "h"})
    ins = build_insight(x, "c1", _p(), INTENT, _p("B"), ontology=ONT)
    assert ins.outreach["channel_value"] == "https://a.com/contact"
    assert ins.outreach["to_role"] == "파트너십 담당"
    assert "[접점 — 여기 있는 채널만 쓴다]" in x.src and "https://a.com/contact" in x.src
    assert "[타이밍 신호" in x.src and "Microsoft와 계약" in x.src


def test_invented_channel_value_is_stripped():
    x = _Canned({"to_role": "", "channel": "대표 메일",
                 "channel_value": "sales@a.com", "why_now": "", "hook": ""})
    ins = build_insight(x, "c1", _p(), INTENT, _p("B"), ontology=ONT)
    assert ins.outreach["channel_value"] == "" and ins.outreach["channel"] == ""


def test_no_ontology_says_so_and_kit_can_be_empty():
    x = _Canned({k: "" for k in ("to_role", "channel", "channel_value", "why_now", "hook")})
    ins = build_insight(x, "c1", _p(), INTENT, _p("B"), ontology=None)
    assert "[접점] 없음 (사이트 미판독)" in x.src
    assert all(v == "" for v in ins.outreach.values())


def test_missing_outreach_in_model_output_becomes_empty_kit():
    class NoKit:
        def extract_json(self, *a, **k):
            return {"observed_needs": [], "need_evidence": [], "value_bridge": [],
                    "personalization_hooks": [], "uncertainties": []}
    ins = build_insight(NoKit(), "c1", _p(), INTENT, _p("B"), ontology=ONT)
    assert set(ins.outreach) == {"to_role", "channel", "channel_value",
                                 "why_now", "hook", "hook_url"}


def test_ontology_block_prefers_confirmed_axes_only():
    blk = _ontology_block({"contacts": [], "signals": [],
                           "axes": {"a": {"value": "확정값", "status": "confirmed"},
                                    "b": {"value": "추정값", "status": "assumed"}}})
    assert "확정값" in blk and "추정값" not in blk


class TestEvidenceLinkAndLanguage:
    """메일이 '무엇을 보고 연락하는지'를 링크로 밝히고, 상대의 말로 쓰인다."""

    def test_hook_url_must_be_a_url_the_reader_actually_saw(self):
        ont = {"contacts": [], "signals": [
            {"category": "partnership", "evidence": "e",
             "source_url": "https://a.com/news"}], "axes": {}}
        x = _Canned({"to_role": "", "channel": "", "channel_value": "",
                     "why_now": "", "hook": "h", "hook_url": "https://a.com/news"})
        assert build_insight(x, "c", _p(), INTENT, _p("B"),
                             ontology=ont).outreach["hook_url"] == "https://a.com/news"

    def test_invented_hook_url_is_stripped(self):
        """상대가 열어보는 순간 어긋나는 링크는 없느니만 못하다."""
        ont = {"contacts": [], "signals": [
            {"category": "partnership", "evidence": "e",
             "source_url": "https://a.com/news"}], "axes": {}}
        x = _Canned({"to_role": "", "channel": "", "channel_value": "",
                     "why_now": "", "hook": "h", "hook_url": "https://a.com/made-up"})
        assert build_insight(x, "c", _p(), INTENT, _p("B"),
                             ontology=ont).outreach["hook_url"] == ""

    def test_signal_source_urls_reach_the_prompt(self):
        ont = {"contacts": [], "axes": {}, "signals": [
            {"category": "investment", "evidence": "시리즈 B",
             "source_url": "https://a.com/press"}]}
        x = _Canned({k: "" for k in ("to_role", "channel", "channel_value",
                                     "why_now", "hook", "hook_url")})
        build_insight(x, "c", _p(), INTENT, _p("B"), ontology=ont)
        assert "출처 https://a.com/press" in x.src

    def test_compose_is_told_to_quote_the_link(self):
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM, _kit_lines
        assert "무엇을 보고 연락하는지 밝힌다" in COMPOSE_LEAD_SYSTEM
        assert "근거 링크(본문에 그대로 인용): https://a.com/x" in _kit_lines(
            {"hook": "h", "hook_url": "https://a.com/x"})
        assert "근거 링크" not in _kit_lines({"hook": "h", "hook_url": ""})

    def test_language_codes_are_normalised_and_unknown_falls_back(self):
        from app.engine.company_ontology import _lang_code
        assert _lang_code("English") == "en" and _lang_code("ja-JP") == "ja"
        assert _lang_code("일본어") == "ja" and _lang_code("우주어") == ""

    def test_cited_url_requires_the_page_to_be_in_the_material(self):
        from app.engine.company_ontology import _cited_url
        text = "[페이지: https://a.com/news]\n본문"
        assert _cited_url("https://a.com/news", text) == "https://a.com/news"
        assert _cited_url("https://a.com/other", text) == ""
        assert _cited_url("news", text) == ""
