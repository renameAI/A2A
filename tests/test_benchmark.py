"""평가 벤치마크 테스트 (Phase 5 EVL) — 완전 오프라인(Mock).

채점 로직을 합성 실행 결과로 검증하고, Mock 규칙 엔진이 골든셋 전체를 통과하는지
회귀 가드로 고정한다. 실 LLM 벤치마크는 무료 API 통제로 여기서 실행하지 않는다.
"""
from app.eval.benchmark import (POLARITY, _score_judge, _score_retrieve,
                                load_golden, run_benchmark)


class TestPolarityMap:
    def test_engage_defer_reject(self):
        assert POLARITY["recommend"] == "engage"
        assert POLARITY["conditional"] == "engage"
        assert POLARITY["hold"] == "defer"
        assert POLARITY["terminate"] == "reject"


class TestJudgeScoring:
    def test_exact_decision_hit(self):
        runs = [{"decision": "conditional", "polarity": "engage", "deal_breaker": False}]
        sc = _score_judge(runs, {"decision": "conditional"})
        assert sc["accuracy"] == 1.0

    def test_exact_decision_miss(self):
        runs = [{"decision": "hold", "polarity": "defer", "deal_breaker": False}]
        assert _score_judge(runs, {"decision": "conditional"})["accuracy"] == 0.0

    def test_polarity_hit(self):
        runs = [{"decision": "recommend", "polarity": "engage", "deal_breaker": False}]
        assert _score_judge(runs, {"polarity": "engage"})["accuracy"] == 1.0

    def test_deal_breaker_satisfies_reject(self):
        runs = [{"decision": "deal_breaker", "polarity": "reject", "deal_breaker": True}]
        sc = _score_judge(runs, {"polarity": "reject", "deal_breaker_ok": True})
        assert sc["accuracy"] == 1.0

    def test_polarity_not_distractor(self):
        """distractor는 engage가 아니어야 — hold(defer)면 통과, recommend면 실패."""
        good = [{"decision": "hold", "polarity": "defer", "deal_breaker": False}]
        bad = [{"decision": "recommend", "polarity": "engage", "deal_breaker": False}]
        assert _score_judge(good, {"polarity_not": "engage"})["accuracy"] == 1.0
        assert _score_judge(bad, {"polarity_not": "engage"})["accuracy"] == 0.0

    def test_stability_across_runs(self):
        """k회 중 결정이 갈리면 안정성(일치율)이 1 미만."""
        runs = [{"decision": "conditional", "polarity": "engage", "deal_breaker": False},
                {"decision": "hold", "polarity": "defer", "deal_breaker": False}]
        sc = _score_judge(runs, {"polarity": "engage"})
        assert sc["stability"] == 0.5           # 다수결 일치율
        assert sc["accuracy"] == 0.5            # 2회 중 1회만 engage


class TestRetrieveScoring:
    def test_top1_and_exclusion(self):
        runs = [["ext-livi-hanoi", "ext-bangkok-mid"]]
        sc = _score_retrieve(runs, {"top_should_be": "ext-livi-hanoi",
                                    "should_exclude": ["ext-global-aero"]})
        assert sc["top1_accuracy"] == 1.0 and sc["distractor_exclusion"] == 1.0

    def test_distractor_leak_penalized(self):
        runs = [["ext-livi-hanoi", "ext-global-aero"]]   # 제외 대상이 강한 후보에 샘
        sc = _score_retrieve(runs, {"top_should_be": "ext-livi-hanoi",
                                    "should_exclude": ["ext-global-aero"]})
        assert sc["distractor_exclusion"] == 0.0

    def test_empty_result_top1_miss(self):
        runs = [[]]                              # NoStrongCandidate
        sc = _score_retrieve(runs, {"top_should_be": "ext-livi-hanoi", "should_exclude": []})
        assert sc["top1_accuracy"] == 0.0


class TestMockBaselineRegression:
    def test_golden_set_loads(self):
        g = load_golden()
        assert g["judge_cases"] and g["retrieve_cases"]
        assert "honest_limits" in g["_meta"]     # 정직성 주석 존재

    def test_mock_engine_passes_full_golden(self):
        """회귀 가드 — Mock 규칙 엔진이 v0 골든셋 전체를 통과한다.
        (Mock 100%는 골든셋이 시드 풀 설계에서 나왔기 때문 — 규칙이 깨지면 여기서 잡힌다.)"""
        rep = run_benchmark(k=1)
        assert rep["summary"]["judge_accuracy"] == 1.0
        assert rep["summary"]["retrieve_top1_accuracy"] == 1.0
        # anchor 케이스가 CoT #01 전문가 결론(conditional)과 일치
        anchor = next(r for r in rep["judge"] if r["case_id"] == "J-cot01-divein-livi")
        assert anchor["decisions"] == {"conditional": 1}
        # deal-breaker 케이스가 결격 게이트로 reject
        db = next(r for r in rep["judge"] if r["case_id"] == "J-dealbreaker-divein-aero")
        assert db["majority_polarity"] == "reject"

    def test_only_filter(self):
        """only='retrieve'면 judge를 건너뛴다(실 LLM에서 비싼 judge 통제용)."""
        rep = run_benchmark(k=1, only="retrieve")
        assert rep["judge"] == [] and rep["retrieve"]
