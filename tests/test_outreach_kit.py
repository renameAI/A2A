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
    assert set(ins.outreach) == {"to_role", "channel", "channel_value", "why_now", "hook"}


def test_ontology_block_prefers_confirmed_axes_only():
    blk = _ontology_block({"contacts": [], "signals": [],
                           "axes": {"a": {"value": "확정값", "status": "confirmed"},
                                    "b": {"value": "추정값", "status": "assumed"}}})
    assert "확정값" in blk and "추정값" not in blk
