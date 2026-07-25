import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import interview_test as agent


def candidate(kind):
    pool = agent._global_candidates() + agent._track_candidates("A", secondary=False)
    return next(item for item in pool if item.kind == kind)


def semantic_response(*, role, fit, evidence, fields, redirect_path=None,
                      missing_reason=None, confidence=0.95):
    return json.dumps({
        "activate_track_b": False,
        "assessment": {
            "answer_role": role,
            "field_fit": fit,
            "redirect_path": redirect_path,
            "missing_reason": missing_reason,
            "confidence": confidence,
            "evidence": evidence,
        },
        "fields": fields,
    }, ensure_ascii=False)


class SemanticValidationTests(unittest.TestCase):
    def setUp(self):
        self.state = agent.init_state("크레아큐브")

    def test_end_user_benefit_does_not_resolve_buyer_budget_driver(self):
        path = "strategy_tracks.A.purchase_logic.why_budget"
        answer = "아이들 학습을 위해서지"
        raw = semantic_response(
            role="end_user_benefit",
            fit="mismatch",
            evidence=answer,
            missing_reason="최종 사용자 효익이며 구매 조직의 예산 배경이 아님",
            fields=[{
                "path": path,
                "status": "supported",
                "evidence": answer,
                "normalized_candidate": answer,
                "missing_detail": None,
            }],
        )
        with patch.object(agent, "chat", return_value=raw):
            result = agent.apply_answer(
                self.state, None, question_id="I004", candidate=candidate("purchase"),
                question="예산 배경은 무엇인가요?", answer=answer,
            )
        self.assertEqual("semantically_insufficient", result["resolution_status"])
        self.assertIsNone(agent.get_path(self.state, path))
        self.assertEqual("end_user_benefit", result["semantic_assessment"]["answer_role"])
        retry = agent.build_semantic_retry_question(
            self.state, candidate("purchase"), "기본 재질문"
        )
        self.assertIn("최종 사용자가 얻는 효과", retry)
        self.assertIn("내부 사업상 이유", retry)

    def test_generic_feature_does_not_resolve_differentiator(self):
        path = "offer.differentiator"
        answer = "AI를 활용한 학습이 가능"
        raw = semantic_response(
            role="generic_feature",
            fit="partial",
            evidence=answer,
            missing_reason="기능은 있으나 경쟁 대안 대비 차이가 없음",
            fields=[{
                "path": path,
                "status": "partial",
                "evidence": answer,
                "normalized_candidate": answer,
                "missing_detail": "비교 기준 필요",
            }],
        )
        with patch.object(agent, "chat", return_value=raw):
            result = agent.apply_answer(
                self.state, None, question_id="I005", candidate=candidate("differentiator"),
                question="다른 점은 무엇인가요?", answer=answer,
            )
        self.assertEqual("semantically_insufficient", result["resolution_status"])
        self.assertIsNone(agent.get_path(self.state, path))
        retry = agent.build_semantic_retry_question(
            self.state, candidate("differentiator"), "기본 재질문"
        )
        self.assertIn("실제로 만들어지는", retry)

    def test_ai_can_redirect_customer_question_to_reviewer_field(self):
        target_path = "strategy_tracks.A.target.organization_type"
        reviewer_path = "strategy_tracks.A.recipient.first_reviewer"
        answer = "조달 책임자가 먼저 봅니다"
        raw = semantic_response(
            role="reviewer_department",
            fit="redirect",
            redirect_path=reviewer_path,
            evidence="조달 책임자",
            missing_reason="조직 유형이 아니라 검토 직무",
            fields=[{
                "path": reviewer_path,
                "status": "supported",
                "evidence": "조달 책임자",
                "normalized_candidate": "조달 책임자",
                "missing_detail": None,
            }],
        )
        with patch.object(agent, "chat", return_value=raw):
            result = agent.apply_answer(
                self.state, None, question_id="I003", candidate=candidate("target"),
                question="고객 조직 유형은 무엇인가요?", answer=answer,
            )
        self.assertEqual("partially_resolved", result["resolution_status"])
        self.assertIsNone(agent.get_path(self.state, target_path))
        self.assertEqual("조달 책임자", agent.get_path(self.state, reviewer_path))

    def test_low_confidence_exact_is_kept_partial(self):
        path = "strategy_tracks.A.purchase_logic.why_budget"
        answer = "공공 조달 예산으로 검토합니다"
        raw = semantic_response(
            role="buyer_budget_driver",
            fit="exact",
            evidence=answer,
            confidence=0.4,
            fields=[{
                "path": path,
                "status": "supported",
                "evidence": answer,
                "normalized_candidate": answer,
                "missing_detail": None,
            }],
        )
        with patch.object(agent, "chat", return_value=raw):
            result = agent.apply_answer(
                self.state, None, question_id="I004", candidate=candidate("purchase"),
                question="예산 배경은 무엇인가요?", answer=answer,
            )
        self.assertEqual("partially_resolved", result["resolution_status"])
        self.assertIn(path, self.state["interview_state"]["partial_paths"])

    def test_valid_business_budget_driver_resolves(self):
        path = "strategy_tracks.A.purchase_logic.why_budget"
        answer = "공공 교육 디지털 전환 사업 예산으로 구매를 검토합니다"
        raw = semantic_response(
            role="buyer_budget_driver",
            fit="exact",
            evidence=answer,
            fields=[{
                "path": path,
                "status": "supported",
                "evidence": answer,
                "normalized_candidate": answer,
                "missing_detail": None,
            }],
        )
        with patch.object(agent, "chat", return_value=raw):
            result = agent.apply_answer(
                self.state, None, question_id="I004", candidate=candidate("purchase"),
                question="예산 배경은 무엇인가요?", answer=answer,
            )
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual(answer, agent.get_path(self.state, path))

    def test_natural_unknown_judged_by_ai_is_preserved_as_explicit_unknown(self):
        path = "strategy_tracks.A.proof_strategy.primary_proof"
        answer = "지금 떠오르는 자료는 따로 없네요"
        raw = semantic_response(
            role="explicit_unknown",
            fit="explicit_unknown",
            evidence=answer,
            missing_reason="현재 제시할 증거가 없음",
            fields=[],
        )
        with patch.object(agent, "chat", return_value=raw):
            result = agent.apply_answer(
                self.state, None, question_id="I007", candidate=candidate("proof"),
                question="대표 실적은 무엇인가요?", answer=answer,
            )
        self.assertEqual("explicit_unknown", result["resolution_status"])
        self.assertIn(path, self.state["interview_state"]["explicit_unknown_paths"])
        self.assertEqual("사용자 확인: 현재 미정·없음", agent.display_path(self.state, path))


if __name__ == "__main__":
    unittest.main()
