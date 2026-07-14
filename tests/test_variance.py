"""분산 계측 하네스 테스트 (L0) — 합성 출력을 먹여 지표를 검증한다.

실 LLM 없이 완전 오프라인. 필드 유형별 지표(범주형 일치율·스칼라 cv·집합 Jaccard·
텍스트 프록시)가 올바른 통계를 내는지 확인한다.
"""
from app.eval.variance import (agreement_rate, avg_pairwise_jaccard, mode,
                               norm_tokens, scalar_stats, variance_report)


class TestCategoricalPrimitives:
    def test_mode_and_agreement(self):
        assert mode(["a", "a", "b"]) == ("a", 2)
        assert agreement_rate(["a", "a", "b"]) == 2 / 3
        assert agreement_rate(["x", "x", "x"]) == 1.0
        assert agreement_rate([]) == 0.0

    def test_mode_empty(self):
        assert mode([]) == (None, 0)


class TestScalarStats:
    def test_constant_scalar_is_stable(self):
        s = scalar_stats([0.7, 0.7, 0.7])
        assert s["std"] == 0.0 and s["stability"] == 1.0

    def test_variable_scalar_lowers_stability(self):
        s = scalar_stats([0.1, 0.9, 0.5])
        assert s["std"] > 0 and s["stability"] < 1.0
        assert s["mean"] == 0.5

    def test_single_value(self):
        assert scalar_stats([0.4])["stability"] == 1.0


class TestSetMetric:
    def test_identical_sets_full_jaccard(self):
        assert avg_pairwise_jaccard([{1, 2}, {1, 2}, {1, 2}]) == 1.0

    def test_disjoint_sets_zero(self):
        assert avg_pairwise_jaccard([{1, 2}, {3, 4}]) == 0.0

    def test_partial_overlap(self):
        # {1,2} vs {2,3}: |∩|=1, |∪|=3 → 1/3
        assert abs(avg_pairwise_jaccard([{1, 2}, {2, 3}]) - 1 / 3) < 1e-9


class TestTextProxy:
    def test_norm_tokens(self):
        assert norm_tokens("매출 증대, 비용!") == {"매출", "증대", "비용"}


class TestVarianceReport:
    def test_identical_outputs_full_stability(self):
        out = {"decision": "recommend", "confidence": 0.8,
               "value_props": ["revenue_growth"], "note": "안정적인 서술"}
        rep = variance_report([out, out, out])
        assert rep["overall_stability"] == 1.0
        assert rep["least_stable"] == []

    def test_categorical_field_measured_by_agreement(self):
        outs = [{"decision": "recommend"}, {"decision": "recommend"},
                {"decision": "hold"}]
        rep = variance_report(outs)
        f = rep["fields"]["decision"]
        assert f["type"] == "categorical"
        assert f["stability"] == round(2 / 3, 4)   # 다수결 일치율
        assert f["mode"] == "recommend"

    def test_scalar_field_measured_by_cv(self):
        outs = [{"confidence": 0.2}, {"confidence": 0.8}, {"confidence": 0.5}]
        f = variance_report(outs)["fields"]["confidence"]
        assert f["type"] == "scalar" and f["stability"] < 1.0

    def test_set_field_measured_by_jaccard(self):
        outs = [{"refs": ["a", "b"]}, {"refs": ["b", "c"]}]
        f = variance_report(outs)["fields"]["refs"]
        assert f["type"] == "set" and abs(f["stability"] - 1 / 3) < 1e-3

    def test_nested_paths_flattened(self):
        outs = [{"basic": {"name": "X", "country": "KR"}},
                {"basic": {"name": "Y", "country": "KR"}}]
        rep = variance_report(outs)
        assert rep["fields"]["basic.country"]["stability"] == 1.0     # 불변
        assert rep["fields"]["basic.name"]["stability"] == 0.5        # 2개 중 1개 합의

    def test_least_stable_ranking(self):
        outs = [{"a": "x", "b": "p"}, {"a": "x", "b": "q"}, {"a": "x", "b": "r"}]
        rep = variance_report(outs)
        # a는 완전 안정(제외), b만 불안정 목록에
        fields = {f["field"] for f in rep["least_stable"]}
        assert "b" in fields and "a" not in fields
