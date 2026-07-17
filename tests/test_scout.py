"""Scout 테스트 — 지식 분리·가설 계약·검색 파싱·숏리스트 쿼터. 완전 오프라인.

웹 검색은 fake 주입(search_fn) 또는 픽스처 HTML 파싱으로 대체 — 네트워크 0.
"""
from app.engine.scout import (_enforce_hypothesis_contract, _shortlist,
                              scout, split_knowledge)
from app.ingest.websearch import parse_search_html
from app.schemas import (BasicInfo, CompanyPortrait, HypothesisTrack, Intent,
                         KnowledgeKind, PartnerHypothesis, Profile, ProvField,
                         Provenance, ScoutRequest, ValueProp)


def _profile(with_portrait=True):
    def f(v, prov="stated", conf=None):
        return ProvField(value=v, provenance=Provenance(prov), confidence=conf)
    portrait = CompanyPortrait(
        identity="노후 공간을 경험형 상품으로 바꾸는 전환 파트너",
        business_model="매출 쉐어 — 선투자 없이 성과 연동",
        edge="무철거 저자본 시공",
        stage_narrative="첫 해외 레퍼런스 확보가 절실한 단계",
        assets="성수 전환 사례, 자체 시공 인력",
        gaps="현지 운영 파트너와 해외 유통 채널 부재",
        risk_signals="특이 신호 없음") if with_portrait else None
    return Profile(
        basic=BasicInfo(name="다이브인그룹", country="한국", industry="hospitality_renovation"),
        description="노후 호텔 객실을 예술 경험형 상품으로 전환",
        problem_solved=f("노후 호텔 객실의 매출 정체와 리뉴얼 자본 부담"),
        solution=f("저자본 무철거 예술 전환, 매출 쉐어"),
        target_customer=f("노후 객실을 보유한 중소 호텔 오너", prov="inferred", conf=0.7),
        references=["성수 Poco Hotel 전환"],
        sell_value_props=[ValueProp.revenue_growth],
        portrait=portrait)


def _req(**kw):
    return ScoutRequest(profile=_profile(), intent=Intent(
        value_props=[ValueProp.revenue_growth], target_region="베트남"), **kw)


class TestKnowledgeSplit:
    def test_stated_is_explicit_inferred_is_tacit(self):
        items = split_knowledge(_profile())
        by_field = {i.field: i for i in items}
        assert by_field["problem_solved"].kind == KnowledgeKind.explicit
        assert by_field["solution"].kind == KnowledgeKind.explicit
        assert by_field["target_customer"].kind == KnowledgeKind.tacit
        assert by_field["target_customer"].confidence == 0.7
        assert by_field["references"].kind == KnowledgeKind.explicit

    def test_portrait_is_all_tacit(self):
        """회사의 상은 정의상 역추론 — 7항목 전부 암묵지."""
        items = split_knowledge(_profile())
        portrait_items = [i for i in items if i.field.startswith("portrait.")]
        assert len(portrait_items) == 7
        assert all(i.kind == KnowledgeKind.tacit for i in portrait_items)

    def test_ask_fields_excluded(self):
        p = _profile()
        p.solution = ProvField(value="", provenance=Provenance.ask)
        fields = {i.field for i in split_knowledge(p)}
        assert "solution" not in fields


class TestHypothesisContract:
    def _knowledge(self):
        return split_knowledge(_profile())

    def test_exploit_grounded_in_tacit_rejected(self):
        """정석 가설이 암묵지에 기대면 계약 위반 — 폐기."""
        bad = PartnerHypothesis(track=HypothesisTrack.exploit, hypothesis="h",
                                grounded_in=["portrait.gaps"],
                                search_query="q", partner_type="t")
        kept, rej = _enforce_hypothesis_contract([bad], self._knowledge())
        assert kept == [] and rej["exploit_tacit"] == 1

    def test_explore_without_tacit_rejected(self):
        """모험 가설에 암묵지 근거가 없으면 폐기."""
        bad = PartnerHypothesis(track=HypothesisTrack.explore, hypothesis="h",
                                grounded_in=["problem_solved"],
                                search_query="q", partner_type="t")
        kept, rej = _enforce_hypothesis_contract([bad], self._knowledge())
        assert kept == [] and rej["explore_no_tacit"] == 1

    def test_valid_hypotheses_kept(self):
        good = [
            PartnerHypothesis(track=HypothesisTrack.exploit, hypothesis="정석",
                              grounded_in=["problem_solved"], search_query="q1",
                              partner_type="t1"),
            PartnerHypothesis(track=HypothesisTrack.explore, hypothesis="모험",
                              grounded_in=["portrait.gaps"], search_query="q2",
                              partner_type="t2"),
        ]
        kept, rej = _enforce_hypothesis_contract(good, self._knowledge())
        assert len(kept) == 2 and sum(rej.values()) == 0


DDG_FIXTURE = """
<div class="results">
  <div class="result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fhanoi-hotels.vn%2Fpartners&rut=x">
      하노이 중소 호텔 연합회</a>
    <a class="result__snippet">노후 객실 리노베이션 파트너를 찾는 하노이 지역 호텔 운영사 모임</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://vietnamtourism.gov.vn/list">베트남 관광청 등록 숙박업소</a>
    <a class="result__snippet">중소 호텔 목록</a>
  </div>
  <div class="result"><a class="result__a" href="javascript:void(0)">광고</a></div>
</div>"""


class TestSearchParsing:
    def test_parse_and_unwrap_redirect(self):
        hits = parse_search_html(DDG_FIXTURE)
        assert len(hits) == 2                       # javascript 링크는 제외
        assert hits[0]["url"] == "https://hanoi-hotels.vn/partners"   # uddg 복원
        assert hits[0]["domain"] == "hanoi-hotels.vn"
        assert "리노베이션" in hits[0]["snippet"]

    def test_max_results_cap(self):
        assert len(parse_search_html(DDG_FIXTURE, max_results=1)) == 1


def _hyp(track, query="노후 호텔 파트너"):
    return PartnerHypothesis(
        track=track, hypothesis="노후 객실 매출 정체를 겪는 중소 호텔이 수요자다",
        grounded_in=["problem_solved" if track == HypothesisTrack.exploit
                     else "portrait.gaps"],
        search_query=query, partner_type="중소 호텔")


def _hit(domain, title="노후 호텔 객실 리노베이션 파트너", snippet="중소 호텔 매출"):
    return {"title": title, "url": f"https://{domain}/x", "snippet": snippet,
            "domain": domain}


class TestShortlist:
    def test_domain_dedup_and_noise_filter(self):
        hyp = _hyp(HypothesisTrack.exploit)
        hits = [_hit("a.com"), _hit("a.com"), _hit("wikipedia.org"), _hit("b.com")]
        out = _shortlist([(hyp, hits)], k=6, explore_ratio=0.34)
        domains = [c.domain for c in out]
        assert domains.count("a.com") == 1          # dedup
        assert "wikipedia.org" not in domains       # noise 필터

    def test_explore_quota_allocated(self):
        """JDG-09 — explore 쿼터가 실제로 배분된다 (k=6, ratio 0.34 → 2)."""
        ex_hyp, xp_hyp = _hyp(HypothesisTrack.exploit), _hyp(HypothesisTrack.explore)
        ex_hits = [_hit(f"ex{i}.com") for i in range(6)]
        xp_hits = [_hit(f"xp{i}.com") for i in range(6)]
        out = _shortlist([(ex_hyp, ex_hits), (xp_hyp, xp_hits)], k=6, explore_ratio=0.34)
        assert len(out) == 6
        assert sum(1 for c in out if c.track == HypothesisTrack.explore) == 2
        assert sum(1 for c in out if c.track == HypothesisTrack.exploit) == 4

    def test_backfill_when_track_short(self):
        """한 트랙이 부족하면 다른 트랙에서 채운다."""
        ex_hyp, xp_hyp = _hyp(HypothesisTrack.exploit), _hyp(HypothesisTrack.explore)
        out = _shortlist([(ex_hyp, [_hit("only-ex.com")]),
                          (xp_hyp, [_hit(f"xp{i}.com") for i in range(5)])],
                         k=4, explore_ratio=0.25)
        assert len(out) == 4                        # exploit 1 + explore 3 백필
        assert sum(1 for c in out if c.track == HypothesisTrack.exploit) == 1

    def test_irrelevant_hits_cut(self):
        hyp = _hyp(HypothesisTrack.exploit)
        junk = _hit("junk.com", title="qqqq zzzz", snippet="wwww")   # overlap≈0
        out = _shortlist([(hyp, [junk])], k=6, explore_ratio=0.34)
        assert out == []


class TestScoutEndToEnd:
    def test_mock_scout_with_fake_search(self):
        """mock 가설 + fake 검색으로 전체 파이프라인 — 결정적."""
        def fake_search(query, settings, max_results=8):
            return [_hit("partner-a.vn"), _hit("partner-b.vn")]
        res = scout(_req(), search_fn=fake_search)
        assert res.engine_mode == "mock"
        assert res.web_search_used is True
        assert res.knowledge and res.hypotheses
        assert len(res.shortlist) >= 1
        assert all(c.relevance > 0 for c in res.shortlist)

    def test_search_failure_is_honest(self):
        """검색 전멸 시 — 숏리스트 비고 web_search_used=False, 가설은 유효."""
        res = scout(_req(), search_fn=lambda q, s, max_results=8: [])
        assert res.web_search_used is False
        assert res.shortlist == []
        assert res.hypotheses                        # 가설 자체는 산출물

    def test_product_api_scout(self):
        """POST /product/scout 엔드투엔드 (fake 검색 주입)."""
        import app.engine.scout as scout_mod
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app.main import app
        from tests.test_product import DIVEIN_TEXT, _run_job
        client = TestClient(app)
        onboard = _run_job("/product/onboard", {
            "assets": [{"type": "text", "content": DIVEIN_TEXT}]})
        cid = onboard["result"]["company_id"]
        with patch.object(scout_mod, "scout", wraps=scout_mod.scout) as _:
            with patch("app.ingest.websearch.web_search",
                       lambda q, s, max_results=8: [_hit("api-hit.vn")]):
                job = _run_job("/product/scout", {
                    "company_id": cid,
                    "intent": {"value_props": ["revenue_growth"],
                               "target_region": "베트남"}})
        assert job["status"] == "done", job.get("error")
        assert job["result"]["engine_mode"] == "mock"
        assert job["result"]["hypotheses"]
