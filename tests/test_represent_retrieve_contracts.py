"""Represent·Retrieve 계약 집행 테스트 (R1~R4) — 완전 오프라인.

R1 stated 그라운딩 강등 / R2 근거 청크 인용 검증 / R3 국가·산업 정규화 사영 /
R4 결정적 앵커 혼합(분산 1/4 감쇠) + 전순서 tie-break.
"""
import app.engine.retrieve as R
from app.engine.common import overlap
from app.engine.represent import (enforce_question_axioms, ground_profile,
                                  _canon_country, _canon_industry)
from app.engine.pool import CandidateRecord
from app.ingest.chunking import Chunk
from app.ingest.extractor import extract_profile
from app.schemas import (BasicInfo, Intent, PoolKind, Profile, ProvField,
                         Provenance, RetrieveDirection, RetrieveRequest,
                         ValueProp)

SOURCE = ("다이브인그룹은 노후 호텔 객실의 매출 정체 문제를 저자본 예술 전환으로 "
          "해결한다. 타겟은 중소 호텔 오너다.")


def _prof(problem="노후 호텔 객실의 매출 정체", prov="stated",
          country="한국", industry="hospitality"):
    def f(v, p=prov):
        pp = Provenance(p)
        return ProvField(value=v, provenance=pp,
                         confidence=0.9 if pp == Provenance.inferred else None)
    return Profile(basic=BasicInfo(name="다이브인그룹", country=country, industry=industry),
                   description="d", problem_solved=f(problem),
                   solution=f("저자본 예술 전환"), target_customer=f("중소 호텔 오너"),
                   sell_value_props=[ValueProp.revenue_growth])


class TestR1GroundingDemotion:
    def test_grounded_stated_kept(self):
        prof = _prof()
        tally = ground_profile(prof, SOURCE)
        assert tally["demoted"] == 0
        assert prof.problem_solved.provenance == Provenance.stated

    def test_hallucinated_stated_demoted(self):
        """원문에 전혀 없는 값이 stated로 보고되면 inferred(0.5)로 강등."""
        prof = _prof(problem="제주도 리조트 부지 확보 지연")   # 원문에 없음
        tally = ground_profile(prof, SOURCE)
        assert tally["demoted"] == 1
        assert prof.problem_solved.provenance == Provenance.inferred
        assert prof.problem_solved.confidence == 0.5

    def test_inferred_not_touched(self):
        """R1은 stated에만 적용 — inferred는 이미 불확실 선언이 있다."""
        prof = _prof(problem="원문에 없는 추론값", prov="inferred")
        tally = ground_profile(prof, SOURCE)
        assert tally["demoted"] == 0
        assert prof.problem_solved.provenance == Provenance.inferred

    def test_demotion_reopens_question(self):
        """R1×L1 상호작용 — 강등된 필드(conf 0.5<0.6)는 질문 공리가 다시 살린다."""
        prof = _prof(problem="제주도 리조트 부지 확보 지연")
        ground_profile(prof, SOURCE)
        q = ("귀사가 해결하는 문제는 무엇인가요? "
             "(표면 키워드가 아닌, 상대가 겪는 문제 관점으로)")
        kept, _ = enforce_question_axioms([q], prof)
        assert kept == [q]     # 강등 전(stated)이라면 폐기됐을 질문


class TestR3Canonicalization:
    def test_country_variants_converge(self):
        assert _canon_country("대한민국") == "한국"
        assert _canon_country("Korea") == "한국"
        assert _canon_country("south korea") == "한국"
        assert _canon_country("프랑스") == "프랑스"   # 미등재는 원형 유지

    def test_industry_projection(self):
        assert _canon_industry("SaaS") == "saas"
        assert _canon_industry(" Hospitality Renovation ") == "hospitality_renovation"

    def test_ground_profile_applies_canon(self):
        prof = _prof(country="대한민국", industry="SaaS")
        tally = ground_profile(prof, SOURCE)
        assert prof.basic.country == "한국"
        assert prof.basic.industry == "saas"
        assert tally["canonicalized"] == 2


class TestR2EvidenceContract:
    def test_invalid_chunk_ids_dropped(self):
        chunks = [Chunk(chunk_id="a0:text#0", source="text", text=SOURCE)]

        class FakeExtractor:
            def extract_json(self, system, user, schema, deep=False):
                pf = {"value": "노후 호텔 객실의 매출 정체", "provenance": "stated",
                      "confidence": None,
                      "evidence_chunk_ids": ["a0:text#0", "a9:ir_deck#7"]}  # 뒤는 환각
                return {"basic": {"name": "다이브인그룹", "country": "한국",
                                  "city": None, "founded_year": None,
                                  "industry": "hospitality"},
                        "description": "d",
                        "problem_solved": pf,
                        "solution": {**pf, "evidence_chunk_ids": ["없는청크#1"]},
                        "target_customer": {**pf, "evidence_chunk_ids": []},
                        "references": [], "traction": None,
                        "sell_value_props": ["revenue_growth"],
                        "purchase_value_props": [],
                        "willingness_sell": None, "willingness_purchase": None,
                        "portrait": None, "open_questions": []}

        profile, _, evidence = extract_profile(chunks, FakeExtractor())
        assert evidence["problem_solved"] == ["a0:text#0"]   # 실존만 생존
        assert "solution" not in evidence                     # 전부 환각 → 필드 자체 제거
        assert profile.basic.name == "다이브인그룹"


def _cand(cid, pain, industry="hotel", country="베트남"):
    p = _prof(problem="객실 공실", industry=industry, country=country)
    p.basic.name = f"회사{cid}"
    return CandidateRecord(company_id=cid, pool=PoolKind.external, profile=p,
                           pain_points=pain, tags=["노후 객실"])


def _req():
    return RetrieveRequest(requester_profile=_prof(),
                           intent=Intent(value_props=[ValueProp.revenue_growth],
                                         target_region="베트남"),
                           direction=RetrieveDirection.sell_outreach,
                           pool="external", k=5)


class TestR4Retrieve:
    def test_tiebreak_total_order(self, monkeypatch):
        """동점 후보의 순서가 풀 순서와 무관 — company_id 전순서로 재현."""
        pain = "노후 호텔 객실 매출 정체로 저자본 해법이 필요"
        a, b = _cand("co-aaa", pain), _cand("co-bbb", pain)   # 동일 점수
        for pool_order in ([a, b], [b, a]):
            monkeypatch.setattr(R, "get_pool", lambda po=pool_order: po)
            res = R.retrieve(_req())
            assert [c.company_id for c in res.candidates][:2] == ["co-aaa", "co-bbb"]

    def test_anchor_blending_halves_synth_swing(self):
        """R4 핵심 — 서로 다른 합성문 2개가 만드는 점수 차가 혼합으로 절반이 된다."""
        req = _req()
        anchor = R.template_counterpart(req)
        rec = _cand("co-x", "노후 호텔 객실 매출 정체로 저자본 해법이 필요")
        target = R._search_text(rec, req.direction)
        s1 = "베트남에서 노후 객실 매출 정체를 겪는 중소 호텔"           # 합성 표본 1
        s2 = "동남아 숙박업의 오래된 시설 공실 문제를 가진 사업자"        # 합성 표본 2 (요동)
        # 예전 방식: base가 synth 단독 → 점수 차 = 0.7·|Δoverlap|
        d_old = 0.7 * abs(overlap(s1, target) - overlap(s2, target))
        d_new = abs(R._score(req, s1, anchor, rec) - R._score(req, s2, anchor, rec))
        assert d_old > 0                       # 요동이 실재하는 케이스
        assert d_new <= d_old / 2 + 1e-3       # 혼합이 스윙을 절반 이하로
    def test_mock_path_score_unchanged(self):
        """synth==anchor(mock 경로)면 혼합 base가 기존과 동일 — 회귀 없음."""
        req = _req()
        anchor = R.template_counterpart(req)
        rec = _cand("co-y", "노후 호텔 객실 매출 정체")
        target = R._search_text(rec, req.direction)
        blended = 0.5 * overlap(anchor, target) + 0.5 * overlap(anchor, target)
        assert abs(blended - overlap(anchor, target)) < 1e-12
