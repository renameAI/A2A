"""Judge 온톨로지·가설 라이브러리 추출 — 실제 재료 파일(체크인됨) 기반 통합 테스트.

5줄짜리 픽스처가 아니라 app/ontology/materials/의 실제 5개사 19케이스 파일을 파싱한다 —
포맷이 케이스마다 미묘하게 다르므로(1-H 두 렌즈 대조, 1-J 부제 달린 표) 합성 픽스처로는
이 파서의 진짜 실패 지점(정규식이 실제 마크다운의 변형을 놓치는 지점)을 못 잡는다.
"""
from app.ontology.extract import (KNOWN_DIMENSIONS, build, parse_file,
                                  MATERIALS_DIR)


def _all_cases():
    cases, hyps = [], []
    for path in sorted(MATERIALS_DIR.glob("*_judge_ontology_material.md")):
        c, h, _ = parse_file(path)
        cases += c
        hyps += h
    return cases, hyps


class TestRealMaterialCoverage:
    """README §1.1 종합표가 선언한 수량과 정확히 일치해야 한다 — 파서가 뭔가
    놓치거나 중복 생성하면 여기서 바로 드러난다."""

    def test_case_count_matches_readme(self):
        cases, _ = _all_cases()
        assert len(cases) == 19
        by_file = {}
        for c in cases:
            by_file[c.file] = by_file.get(c.file, 0) + 1
        assert by_file["kimu_judge_ontology_material.md"] == 10
        assert by_file["anpoly_judge_ontology_material.md"] == 2
        assert by_file["cobot_judge_ontology_material.md"] == 2
        assert by_file["mushn_judge_ontology_material.md"] == 2
        assert by_file["livecare_judge_ontology_material.md"] == 3

    def test_hypothesis_card_count(self):
        # kimu는 초기본(narrative "7-A.7 관찰")이라 구조화 카드가 없다 — 그대로 정직히 0.
        _, hyps = _all_cases()
        assert len(hyps) == 9
        by_file = {}
        for h in hyps:
            by_file[h.file] = by_file.get(h.file, 0) + 1
        assert "kimu_judge_ontology_material.md" not in by_file
        assert by_file["livecare_judge_ontology_material.md"] == 3

    def test_decision_distribution(self):
        """README §3.4: recommend 4 · conditional(전체) · hold 1 · terminate 2."""
        cases, _ = _all_cases()
        decisions = [c.decision for c in cases]
        assert decisions.count("recommend") == 4
        assert decisions.count("hold") == 1
        assert decisions.count("terminate") == 2
        assert decisions.count("conditional") == 12

    def test_every_case_has_seller_and_domain(self):
        cases, _ = _all_cases()
        for c in cases:
            assert c.seller and c.seller != "미상"
            assert c.domain and c.domain != "미상"


class TestDimensionExtraction:
    def test_known_dimensions_recognized(self):
        """7차원+게이트 전부가 실 데이터에서 known_dimension=True로 인식돼야
        온톨로지 라이브러리가 기존 Judge 스키마와 맞물린다."""
        cases, _ = _all_cases()
        seen = {d.dimension_raw for c in cases for d in c.dimensions
               if d.known_dimension}
        assert seen == KNOWN_DIMENSIONS

    def test_buy_role_marker_stripped_not_leaked_as_new_axis(self):
        """' *(buy)*' 같은 렌즈 표기가 dimension_raw에 남아 가짜 '새 축'으로
        집계되면 안 된다 — substitute_comparison은 substitute_comparison이어야."""
        cases, _ = _all_cases()
        raws = {d.dimension_raw for c in cases for d in c.dimensions}
        assert "substitute_comparison" in raws
        assert not any("(buy)" in r or "*" in r for r in raws)

    def test_case_1h_two_lens_table_skipped_honestly(self):
        """1-H는 표준 4열 표가 아니라 '두 렌즈 대조'표다 — 억지로 파싱하지 않고
        빈 dimensions로 정직하게 넘어가야 한다(실패를 숨기지 않는다)."""
        cases, _ = _all_cases()
        c1h = next(c for c in cases if c.case_id == "1-H")
        assert c1h.dimensions == []
        assert c1h.sealed_context == ""   # 1-H는 🔒 봉인 없이 케이스 1을 재참조

    def test_case_1j_subtitle_table_still_parsed(self):
        """1-J는 '## 차원별 매칭 — buy 렌즈 (비영리/공공)'처럼 부제가 붙는다 —
        헤더 매칭이 이 변형을 놓치면 안 된다."""
        cases, _ = _all_cases()
        c1j = next(c for c in cases if c.case_id == "1-J")
        assert len(c1j.dimensions) >= 5
        pa = next(d for d in c1j.dimensions
                 if d.dimension_raw == "purpose_alignment")
        assert "Impact" in pa.rubric

    def test_rationale_and_rubric_populated(self):
        cases, _ = _all_cases()
        populated = [d for c in cases for d in c.dimensions if d.rationale]
        assert len(populated) > 100   # 120건 중 대다수


class TestHypothesisCards:
    def test_recommendation_frame_enum(self):
        _, hyps = _all_cases()
        assert {h.recommendation_frame for h in hyps} == {"exploit", "explore"}

    def test_statement_and_evidence_multiline_joined(self):
        """statement/evidence_needed는 원문에서 여러 줄로 줄바꿈돼 있다 —
        하나의 공백 구분 문장으로 합쳐져야 한다(줄바꿈이 문장 중간에 남으면 안 됨)."""
        _, hyps = _all_cases()
        anpoly_a1 = next(h for h in hyps if h.case_id == "A1")
        assert "\n" not in anpoly_a1.statement
        assert "게이트 통과의 선결조건" in anpoly_a1.statement
        assert "미팅 성사율" in anpoly_a1.evidence_needed

    def test_dimensions_split_correctly(self):
        _, hyps = _all_cases()
        cobot_c1 = next(h for h in hyps if h.case_id == "C1")
        assert cobot_c1.dimensions == ["demonstrability", "substitute_comparison"]


class TestCandidateNewAxes:
    """§8 Open — 팀 결정 없이 자동으로 KNOWN_DIMENSIONS에 편입되면 안 된다."""

    def test_new_axes_surfaced_not_absorbed(self):
        cases, _ = _all_cases()
        unknown = {d.dimension_raw for c in cases for d in c.dimensions
                  if not d.known_dimension}
        assert "결정 구조" in unknown
        assert "진입 채널" in unknown
        assert not unknown & KNOWN_DIMENSIONS   # 겹치면 분류가 잘못된 것


class TestBuildPipeline:
    def test_build_writes_valid_jsonl_and_report(self, tmp_path):
        summary = build(out_dir=tmp_path)
        assert summary["cases_total"] == 19
        assert summary["hypotheses_total"] == 9
        assert (tmp_path / "cases.jsonl").exists()
        assert (tmp_path / "hypotheses.jsonl").exists()
        import json
        lines = (tmp_path / "cases.jsonl").read_text(
            encoding="utf-8").splitlines()
        assert len(lines) == 19
        for ln in lines:
            json.loads(ln)   # 전부 유효 JSON

    def test_build_deterministic(self, tmp_path):
        a = build(out_dir=tmp_path / "a")
        b = build(out_dir=tmp_path / "b")
        assert (tmp_path / "a" / "cases.jsonl").read_text() == \
               (tmp_path / "b" / "cases.jsonl").read_text()
