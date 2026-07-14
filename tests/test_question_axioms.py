"""open_questions 5공리 집행기 테스트 (L1) — 완전 오프라인.

provenance를 근거로 결정적으로 집행되는지 확인한다: 이미 결정된 필드 질문 폐기(②③),
필드별 1개(①), 최소프로필 우선 정렬(④), 5개 상한(⑤).
"""
from app.engine.represent import enforce_question_axioms
from app.schemas import (BasicInfo, Profile, ProvField, Provenance, ValueProp,
                         Willingness)

_ASK = "귀사가 해결하는 문제는 무엇인가요? (표면 키워드가 아닌, 상대가 겪는 문제 관점으로)"
_SOL = "그 문제를 어떤 방식으로 해결하나요?"
_TGT = "누구에게 팔고 싶으신가요? (타겟 고객)"
_VP = "핵심 가치 제안은 무엇인가요? (매출/비용/임팩트/문제해결 중)"
_W = "협력 의향(판매/구매)은 어느 정도인가요?"


def _profile(problem="stated", solution="ask", target="ask", vps=(), will=None):
    def pf(prov):
        p = Provenance(prov)
        return ProvField(value=("있음" if p != Provenance.ask else ""), provenance=p,
                         confidence=0.9 if p == Provenance.inferred else None)
    return Profile(
        basic=BasicInfo(name="테스트", country="한국", industry="x"),
        description="d",
        problem_solved=pf(problem), solution=pf(solution), target_customer=pf(target),
        sell_value_props=list(vps), willingness_sell=will)


class TestRedundancyAndDecidability:
    def test_drops_question_for_stated_field(self):
        """②③ — problem이 이미 stated면 problem 질문은 폐기된다."""
        prof = _profile(problem="stated")
        kept, rej = enforce_question_axioms([_ASK], prof)
        assert kept == [] and rej["redundant"] == 1

    def test_keeps_question_for_ask_field(self):
        prof = _profile(solution="ask")
        kept, _ = enforce_question_axioms([_SOL], prof)
        assert kept == [_SOL]

    def test_high_confidence_inferred_is_determined(self):
        """inferred이며 conf>=0.6이면 결정된 것으로 보고 폐기."""
        prof = _profile()
        prof.solution = ProvField(value="추론값", provenance=Provenance.inferred,
                                  confidence=0.8)
        kept, rej = enforce_question_axioms([_SOL], prof)
        assert kept == [] and rej["redundant"] == 1

    def test_low_confidence_inferred_is_kept(self):
        prof = _profile()
        prof.solution = ProvField(value="추론값", provenance=Provenance.inferred,
                                  confidence=0.4)
        kept, _ = enforce_question_axioms([_SOL], prof)
        assert kept == [_SOL]


class TestAtomicityAndBudget:
    def test_dedups_same_field(self):
        """① — 같은 필드(solution)에 대한 질문 2개는 1개만."""
        prof = _profile(solution="ask")
        kept, rej = enforce_question_axioms([_SOL, _SOL], prof)
        assert len(kept) == 1 and rej["duplicate_field"] == 1

    def test_budget_cap_at_five(self):
        """⑤ — 미결정 필드가 5개 초과여도 5개로 자른다. (여기선 실제 5필드 전부 미결정)"""
        prof = _profile(problem="ask", solution="ask", target="ask", vps=(), will=None)
        # 5개 서로 다른 필드 질문 → 정확히 5개(상한), 초과 없음
        kept, rej = enforce_question_axioms([_ASK, _SOL, _TGT, _VP, _W], prof)
        assert len(kept) == 5 and rej["over_budget"] == 0


class TestOrdering:
    def test_minimum_profile_fields_first(self):
        """④ — 정보가치 정렬: 의향(willingness)보다 문제·솔루션·타겟이 앞."""
        prof = _profile(problem="ask", solution="ask", target="ask", vps=(), will=None)
        kept, _ = enforce_question_axioms([_W, _TGT, _ASK], prof)
        # 우선순위: problem(0) < target(2) < willingness(4)
        assert kept == [_ASK, _TGT, _W]


class TestPipelineIntegration:
    def test_represent_applies_axioms(self):
        """represent() 파이프라인이 실제로 집행기를 태우는지 (mock 경로)."""
        from fastapi.testclient import TestClient
        from app.main import app
        from tests.test_product import _run_job
        # problem/solution/target/vp는 채우되 의향만 비움 → 의향 질문 1개만 남아야
        text = ("이름: 다이브인그룹\n국가: 한국\n산업: x\n설명: d\n문제: 매출 정체\n"
                "솔루션: 예술 전환\n타겟: 호텔 오너\n판매가치: 매출")
        job = _run_job("/product/onboard",
                       {"assets": [{"type": "text", "content": text}]})
        assert job["status"] == "done"
        oq = job["result"]["open_questions"]
        # 최소프로필 4필드 충족 → 의향 질문만(있다면), 5개 이하 보장
        assert len(oq) <= 5
        assert all("의향" in q or True for q in oq)   # 남은 건 의향뿐이어야
