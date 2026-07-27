"""포스트닥급 적대적 검토에서 확정된 결함들의 수정 회귀 테스트.

각 테스트는 검토가 재현한 결함 시나리오 그대로를 고정한다 — 재발하면 여기서 깨진다.
발견 ID는 리뷰 워크플로(wf_90a45ba3) 기준.
"""
import types

import app.engine.judge as J
from app.engine.represent import (enforce_question_axioms, ground_profile,
                                  _question_field)
from app.engine.vision import grounding_score, pin_score, GROUND_THRESHOLD
from app.eval.variance import variance_report
from app.schemas import (BasicInfo, DecisionType, Profile, ProvField,
                         Provenance, ValueProp)
from tests.test_judge_consistency import _mk_result, _settings

ENGLISH_SOURCE = ("DiveIn Group converts aging hotel rooms into art-experience "
                  "products. Revenue stagnation of old rooms is the core problem. "
                  "Target customers are small hotel owners.")

CONSULTANT_QS = [
    "지금까지 돈을 낸 고객 중 가장 만족한 곳은 어디였고, 그들은 무엇 때문에 냈나요?",
    "경쟁사가 따라올 수 없다고 자신하는 한 가지는 무엇인가요?",
    "지난 6개월간 가장 많은 시간을 쓴 일은 무엇이었나요?",
]


def _prof(**kw):
    prov = kw.get("prov", "stated")
    def f(v):
        p = Provenance(prov)
        return ProvField(value=v, provenance=p,
                         confidence=0.9 if p == Provenance.inferred else None)
    return Profile(basic=BasicInfo(name="다이브인그룹", country="한국",
                                   industry="hospitality"),
                   description="d",
                   problem_solved=f("노후 호텔 객실 매출 정체"),
                   solution=f("저자본 예술 전환"),
                   target_customer=f("중소 호텔 오너"),
                   sell_value_props=[ValueProp.revenue_growth])


class TestH1ConsultantQuestionsSurvive:
    def test_unclassified_questions_preserved(self):
        """H1 — 컨설턴트형 자유 질문은 problem으로 오분류·학살되지 않고 전부 보존."""
        assert all(_question_field(q) is None for q in CONSULTANT_QS)
        kept, rej = enforce_question_axioms(CONSULTANT_QS, _prof())
        assert kept == CONSULTANT_QS          # 3개 전부 생존, 순서 유지(공리④)
        assert rej["redundant"] == 0 and rej["duplicate_field"] == 0

    def test_canonical_questions_still_classified(self):
        """정준형 질문의 기존 분류는 유지 (회귀 없음)."""
        assert _question_field("그 문제를 어떤 방식으로 해결하나요?") == "solution"
        assert _question_field("귀사가 해결하는 문제는 무엇인가요? (표면 키워드가 아닌, "
                               "상대가 겪는 문제 관점으로)") == "problem"

    def test_budget_still_applies_to_unclassified(self):
        many = [f"자유 질문 {i}번은 무엇인가요?" for i in range(8)]
        kept, rej = enforce_question_axioms(many, _prof())
        assert len(kept) == 5 and rej["over_budget"] == 3


class TestH2CrossLanguageGrounding:
    def test_korean_value_english_source_not_demoted(self):
        """H2 — 영어 원문의 정당한 한국어 stated 값은 '검증 불가'로 라벨 유지."""
        prof = _prof()
        tally = ground_profile(prof, ENGLISH_SOURCE)
        assert tally["demoted"] == 0
        assert tally["unverifiable"] == 3     # 3필드 전부 교차언어 판정
        assert prof.problem_solved.provenance == Provenance.stated

    def test_korean_source_still_demotes_hallucination(self):
        """한국어 원문에서는 R1 강등이 여전히 동작 (회귀 없음)."""
        prof = _prof()
        prof.problem_solved = ProvField(value="제주도 리조트 부지 확보 지연",
                                        provenance=Provenance.stated)
        tally = ground_profile(prof, "다이브인그룹은 노후 호텔 객실의 매출 정체 문제를 "
                                     "저자본 예술 전환으로 해결한다. 타겟은 중소 호텔 오너다.")
        assert tally["demoted"] == 1          # 환각 문제 필드만 — 나머지 2필드는 유지


class TestF2TieBreak:
    def test_tie_resolves_to_hold_regardless_of_order(self, monkeypatch):
        """F2 — {hold×2, terminate×2} 동점이 표본 순서와 무관하게 hold+사람 라우팅."""
        for seq in ([DecisionType.hold, DecisionType.terminate_structural,
                     DecisionType.terminate_structural, DecisionType.hold],
                    [DecisionType.terminate_structural, DecisionType.hold,
                     DecisionType.hold, DecisionType.terminate_structural]):
            it = iter(seq)
            monkeypatch.setattr(J, "_llm_judge",
                                lambda req, ex, deep=True: _mk_result(next(it)))
            result, agreement = J._vote_llm_judge(None, object(), False, samples=4)
            assert result.decision == DecisionType.hold
            assert result.needs_human is True
            assert "동점" in result.decision_rationale
            assert agreement == 0.5

    def test_recommend_terminate_tie_also_holds(self, monkeypatch):
        """hold 표본이 없어도 동점이면 hold로 유보 (코인플립 확정 금지)."""
        seq = [DecisionType.recommend, DecisionType.terminate_structural]
        it = iter(seq)
        monkeypatch.setattr(J, "_llm_judge",
                            lambda req, ex, deep=True: _mk_result(next(it)))
        result, _ = J._vote_llm_judge(None, object(), False, samples=2)
        assert result.decision == DecisionType.hold


class TestF3AuditPreGate:
    def test_audit_preserves_pre_gate_decision(self, monkeypatch):
        """F3 — 게이트가 덮어써도 감사에 원 결정·일치율·근거가 남는다."""
        captured = {}
        import app.audit as audit_mod
        monkeypatch.setattr(audit_mod, "record",
                            lambda kind, payload: captured.update(payload))
        r = _mk_result(DecisionType.recommend)
        r.sample_agreement = 0.4
        J._apply_consistency_gate(r, 0.4, _settings(0.6))
        assert r.decision == DecisionType.hold          # 게이트 발동
        J._audit_judge(None if False else _req_stub(), r,
                       pre_gate_decision="recommend")
        assert captured["decision"] == "hold"
        assert captured["decision_pre_gate"] == "recommend"   # 원 결정 보존
        assert captured["sample_agreement"] == 0.4
        assert captured["needs_human"] is True
        assert "decision_rationale" in captured


def _req_stub():
    from tests.test_judge_consistency import _judge_req
    return _judge_req()


class TestF4F5VarianceClassifier:
    def test_high_cardinality_short_strings_are_text(self):
        """F4 — 3회 실행 전부 다른 짧은 문자열은 categorical이 아니라 text."""
        outs = [{"name": "알파소재"}, {"name": "베타식품"}, {"name": "감마부품"}]
        f = variance_report(outs)["fields"]["name"]
        assert f["type"] == "text"

    def test_enum_like_still_categorical(self):
        outs = [{"decision": "recommend"}, {"decision": "recommend"},
                {"decision": "hold"}]
        f = variance_report(outs)["fields"]["decision"]
        assert f["type"] == "categorical" and f["stability"] == round(2 / 3, 4)

    def test_partial_presence_penalizes_stability(self):
        """F5 — 5회 중 1회만 등장한 필드는 stability 1.0이 아니라 등장률만큼 감점."""
        outs = [{"a": 1, "rare": 0.7}, {"a": 1}, {"a": 1}, {"a": 1}, {"a": 1}]
        f = variance_report(outs)["fields"]["rare"]
        assert f["presence"] == 0.2
        assert f["stability"] == 0.2          # 1.0(단일값) × 0.2(등장률)


class TestVisGroundingFixes:
    def test_short_quote_unverifiable_not_perfect(self):
        """VIS-01 — 정규화 후 6자 미만 인용은 g=1.0이 아니라 None(검증 불가)."""
        assert grounding_score("40%", "매출이 40배 성장") is None
        assert grounding_score("의", "회의록") is None
        # 정상 길이 인용은 여전히 판정
        assert grounding_score("노후 호텔 객실 매출", "노후 호텔 객실 매출 정체") == 1.0

    def test_verified_pin_never_below_unverifiable(self):
        """VIS-02 — 같은 relevance에서 검증 통과(g≥0.6) 핀 ≥ 검증 불가(None) 핀."""
        assert pin_score(0.9, 0.65) >= pin_score(0.9, None)
        assert pin_score(0.9, None) == 0.9 * GROUND_THRESHOLD


class TestRetrieveFixes:
    def test_empty_profile_rejected_at_engine_boundary(self):
        """RET-03 — 핵심 3필드가 전부 빈 프로필은 /v1/retrieve 경계에서 400."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        empty = {"value": "", "provenance": "ask"}
        res = client.post("/v1/retrieve", json={
            "requester_profile": {
                "basic": {"name": "빈회사", "country": "한국", "industry": "x"},
                "description": "", "problem_solved": empty, "solution": empty,
                "target_customer": empty},
            "intent": {"value_props": ["revenue_growth"]},
            "direction": "sell_outreach", "pool": "external", "k": 3})
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "invalid_input"


class TestA2AFixes:
    def test_array_params_returns_invalid_params(self):
        """A2A-1 — 배열 params가 -32603 내부오류 대신 -32602."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        res = client.post("/a2a", json={"jsonrpc": "2.0", "id": 1,
                                        "method": "tasks/get", "params": [1, 2]})
        assert res.json()["error"]["code"] == -32602

    def test_idempotent_send_with_client_request_id(self):
        """A2A-2 — 같은 client_request_id 재전송은 같은 Task를 반환."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        body = {"jsonrpc": "2.0", "id": "i", "method": "message/send",
                "params": {"message": {"role": "user", "kind": "message",
                    "messageId": "m", "parts": [{"kind": "data", "data": {
                        "skill": "represent",
                        "input": {"client_request_id": "crid-멱등-1",
                                  "assets": [{"type": "text",
                                              "content": "이름: 다이브인그룹\n국가: 한국\n"
                                                         "산업: h\n설명: d\n문제: p\n솔루션: s\n"
                                                         "타겟: t\n판매가치: 매출"}]}}}]}}}
        id1 = client.post("/a2a", json=body).json()["result"]["id"]
        id2 = client.post("/a2a", json=body).json()["result"]["id"]
        assert id1 == id2

    def test_cancel_canceled_task_is_idempotent(self):
        """A2A-3 — canceled 표시 Task 재취소는 -32002가 아니라 canceled Task 반환."""
        import app.a2a as a2a_mod
        from app.jobs import store as job_store
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        job, _ = job_store.create()
        a2a_mod._task_meta[job.job_id] = {"skill": "represent",
                                          "contextId": job.job_id, "history": []}
        a2a_mod._canceled.add(job.job_id)
        from app.schemas import JobStatus
        job.status = JobStatus.done          # raw 상태는 종료 — 예전엔 -32002
        res = client.post("/a2a", json={"jsonrpc": "2.0", "id": 1,
                                        "method": "tasks/cancel",
                                        "params": {"id": job.job_id}})
        body = res.json()
        assert "error" not in body
        assert body["result"]["status"]["state"] == "canceled"

    def test_basexception_marks_job_error(self):
        """A2A-6 — 작업이 BaseException으로 죽어도 running 고착 없이 error 수렴."""
        from app.jobs import store as job_store, Job
        from app.schemas import JobStatus
        job = Job("t-base")

        def boom():
            raise SystemExit(1)
        try:
            job_store.run(job, boom)
        except SystemExit:
            pass
        assert job.status == JobStatus.error   # running 고착 아님


class TestEvidenceGate:
    """L3 근거 게이트 — fit 차원 0개면 recommend 불가 (실측 오판: 공감만세×AdForUs).

    5차원이 전부 caution인데 LLM이 '전략적 잠재력'을 이유로 recommend를 낸 사례.
    판단은 차원 근거의 집계(JDG-02)라, 근거 없는 낙관이 최상위 추천으로 나가면 안 된다.
    """

    @staticmethod
    def _result(verdicts, decision):
        from app.schemas import (CategoryJudgment, ConfidenceBand, DecisionType,
                                 Dimension, JudgeResult, MatchSummary, VerdictType)
        dims = [CategoryJudgment(dimension=d, verdict=v, rationale="r")
                for d, v in zip([Dimension.BB1_purpose_fit, Dimension.BB1_purpose_fit,
                                 Dimension.BB3_substitute,
                                 Dimension.BB7_timing,
                                 Dimension.BB5_evidence], verdicts)]
        return JudgeResult(
            decision=decision, decision_rationale="전략적 잠재력",
            category_judgments=dims, risks=[], fit_reasons=["x"], gap_factors=[],
            match_summary=MatchSummary(problem_solution="ps", value_proposition="vp",
                                       reference="ref"),
            confidence_band=ConfidenceBand.medium, reasoning_moves=[], trajectory="t")

    def test_all_caution_recommend_is_capped_to_conditional(self):
        from app.engine.judge import _apply_evidence_gate
        from app.schemas import DecisionType, VerdictType
        r = self._result([VerdictType.caution] * 5, DecisionType.recommend)
        _apply_evidence_gate(r)
        assert r.decision == DecisionType.conditional
        assert "전략적 잠재력" in r.decision_rationale   # 원 근거 보존 (감사 가능)

    def test_one_fit_keeps_recommend(self):
        from app.engine.judge import _apply_evidence_gate
        from app.schemas import DecisionType, VerdictType
        r = self._result([VerdictType.fit] + [VerdictType.caution] * 4,
                         DecisionType.recommend)
        _apply_evidence_gate(r)
        assert r.decision == DecisionType.recommend

    def test_non_recommend_decisions_untouched(self):
        from app.engine.judge import _apply_evidence_gate
        from app.schemas import DecisionType, VerdictType
        for dec in (DecisionType.conditional, DecisionType.hold):
            r = self._result([VerdictType.caution] * 5, dec)
            _apply_evidence_gate(r)
            assert r.decision == dec        # 근거 부족 ≠ unfit — 과교정 금지

    def test_empty_dimensions_not_overcorrected(self):
        from app.engine.judge import _apply_evidence_gate
        from app.schemas import DecisionType, VerdictType
        r = self._result([VerdictType.caution] * 5, DecisionType.recommend)
        r.category_judgments = []
        _apply_evidence_gate(r)
        assert r.decision == DecisionType.recommend


class TestFoundedYearValidation:
    """명백한 오파싱은 코드가 막는다 — 실측: "4년간 모금액 23배 성장"에서
    founded_year=4가 나왔다. 스키마상 정수면 통과하므로 LLM에 맡길 수 없다.
    범위 밖은 None으로 버린다 — 틀린 값을 그럴듯하게 남기느니 '미상'이 정직하다."""

    @staticmethod
    def _year(v):
        from app.schemas import BasicInfo
        return BasicInfo(name="x", country="한국", industry="saas",
                         founded_year=v).founded_year

    def test_implausible_years_dropped(self):
        for bad in (4, 0, -1, 1799, 2100):
            assert self._year(bad) is None, f"{bad}이 통과됨"

    def test_plausible_years_kept(self):
        import datetime
        for good in (1800, 1999, 2020, datetime.date.today().year):
            assert self._year(good) == good

    def test_none_stays_none(self):
        assert self._year(None) is None


class TestIndustryNormalization:
    """자유서술 산업명 → 통제 어휘. 소비처(industry_adjacent)가 통제 어휘를
    기대하는데 생산처(LLM·리서치 풀)는 자유서술을 쓰던 계약 불일치를 코드가 흡수.
    실측: 풀 1,140개 중 통제 어휘 해당이 6개(0.5%)뿐이라 +0.10 온톨로지 보너스가
    99%에서 원천 불가였다 → 텍스트 유도 정규화로 80.8%까지 회복."""

    def test_freeform_maps_to_controlled_vocab(self):
        from app.engine.common import normalize_industry as N
        assert N("고향사랑기부_민간_1위_플랫폼_운영") == "govtech"
        assert N("소프트웨어 개발 및 공급업") == "software"
        assert N("특수 목적용 기계 제조업") == "manufacturing"
        assert N("금융 지원 서비스업") == "finance"
        assert N("hospitality") == "hospitality"

    def test_unconfident_stays_unknown(self):
        """확신 없으면 unknown — 억지 매칭보다 무가산이 안전."""
        from app.engine.common import normalize_industry as N
        for t in ("", "unknown", "미상", "기타", "자연과학 및 공학 연구개발업"):
            assert N(t) == "unknown"

    def test_unknown_is_never_adjacent(self):
        """실측 폭탄: a == b면 True라 industry='unknown'인 후보 933개가 서로 인접
        판정을 받았다. +0.10은 τ=0.12를 단독으로 넘길 수 있는 크기라 요청자 산업이
        미상이면 풀 전체가 가산을 받는 구조였다. 모르는 것끼리는 '판단 불가'다."""
        from app.engine.common import industry_adjacent as adj
        assert adj("unknown", "unknown") is False
        assert adj("", "") is False
        assert adj("미상", "미상") is False
        assert adj("unknown", "software") is False

    def test_real_adjacency_still_works(self):
        from app.engine.common import industry_adjacent as adj
        assert adj("고향사랑기부 플랫폼", "결제 정산 서비스") is True   # govtech↔finance
        assert adj("소프트웨어 개발", "소프트웨어 공급") is True         # 동일
        assert adj("고향사랑기부 플랫폼", "특수 목적용 기계 제조업") is False
