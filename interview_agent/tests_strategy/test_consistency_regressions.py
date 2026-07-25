import json
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import interview_test as agent


def candidate(kind):
    pool = agent._global_candidates() + agent._track_candidates("A", secondary=False)
    return next(item for item in pool if item.kind == kind)


class ConsistencyRegressionTests(unittest.TestCase):
    def setUp(self):
        self.state = agent.init_state("키뮤")

    def no_value(self):
        return json.dumps({"activate_track_b": False, "fields": []}, ensure_ascii=False)

    def test_clear_solution_survives_api_false_negative(self):
        with patch.object(agent, "chat", return_value=self.no_value()):
            result = agent.apply_answer(
                self.state, None, question_id="I001", candidate=candidate("offer"),
                question="무엇을 제안하나요?", answer="esg 캠페인입니다",
            )
        self.assertEqual("resolved", result["resolution_status"])
        self.assertTrue(result["semantic_rescue_used"])
        self.assertEqual("esg 캠페인", agent.get_path(self.state, "offer.chosen_solution"))

    def test_poc_goal_is_program_context_without_current_stage_inference(self):
        self.assertEqual("poc", agent.get_path(self.state, "transaction_strategy.primary_goal"))
        self.assertIsNone(agent.get_path(self.state, "transaction_strategy.current_stage"))
        self.assertNotIn("goal", [item.kind for item in agent.build_question_candidates(self.state)])

    def test_multiple_target_groups_are_preserved_and_resolved(self):
        with patch.object(agent, "chat", return_value=self.no_value()):
            result = agent.apply_answer(
                self.state, None, question_id="I001", candidate=candidate("target"),
                question="첫 고객은 누구인가요?",
                answer="CSR을 필요로 하는 가능한 많은 기업과 공공기관들",
            )
        path = "strategy_tracks.A.target.organization_type"
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual("기업·공공기관", agent.get_path(self.state, path))
        self.assertNotIn(path, self.state["interview_state"]["partial_paths"])

    def test_department_answer_is_redirected_and_target_is_asked_again(self):
        target = candidate("target")
        result = agent.apply_answer(
            self.state, None, question_id="I003", candidate=target,
            question="어떤 유형의 조직인가요?", answer="구매부서",
        )
        target_path = "strategy_tracks.A.target.organization_type"
        reviewer_path = "strategy_tracks.A.recipient.first_reviewer"
        self.assertEqual("partially_resolved", result["resolution_status"])
        self.assertIsNone(agent.get_path(self.state, target_path))
        self.assertEqual("구매부서", agent.get_path(self.state, reviewer_path))
        retry = agent.build_human_question(None, self.state, target, 2)
        self.assertIn("검토할 부서로 기록했습니다", retry)
        self.assertIn("실제 고객 조직", retry)

    def test_budget_question_uses_safe_organization_phrase(self):
        answer_id = agent.add_answer_record(
            self.state, question_id="SEED",
            target_paths=["strategy_tracks.A.target.organization_type"],
            value="공공기관", origin="user_stated", source_type="test",
            is_canonical=True,
        )
        agent.apply_updates(
            self.state,
            {"strategy_tracks.A.target.organization_type": "공공기관"},
            origin="user_stated", answer_id=answer_id, invalidate=False,
        )
        question = agent.build_human_question(None, self.state, candidate("purchase"))
        self.assertIn("‘공공기관’ 유형의 고객이", question)

    def test_proof_source_does_not_resolve_primary_proof(self):
        answer = "웹사이트, 뉴스기사, IR에 관련 자료가 있습니다"
        response = {
            "activate_track_b": False,
            "fields": [{
                "path": "strategy_tracks.A.proof_strategy.source",
                "status": "supported",
                "evidence": "웹사이트, 뉴스기사, IR",
                "normalized_candidate": "웹사이트, 뉴스기사, IR",
                "missing_detail": None,
            }],
        }
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            result = agent.apply_answer(
                self.state, None, question_id="I001", candidate=candidate("proof"),
                question="대표 근거는 무엇인가요?", answer=answer,
            )
        self.assertEqual("partially_resolved", result["resolution_status"])
        self.assertIsNone(agent.get_path(self.state, "strategy_tracks.A.proof_strategy.primary_proof"))
        self.assertEqual("웹사이트, 뉴스기사, IR", agent.get_path(self.state, "strategy_tracks.A.proof_strategy.source"))

    def test_ir_collaboration_references_are_accepted_as_primary_proof(self):
        answer = "웹사이트, 뉴스기사, IR에 기업과 공공기관 레퍼런스들이 있습니다"
        response = {
            "activate_track_b": False,
            "fields": [{
                "path": "strategy_tracks.A.proof_strategy.primary_proof",
                "status": "supported",
                "evidence": "웹사이트, 뉴스기사, IR에 기업과 공공기관 레퍼런스들",
                "normalized_candidate": "웹사이트, 뉴스기사, IR에 기업과 공공기관 레퍼런스들",
                "missing_detail": None,
            }],
        }
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            result = agent.apply_answer(
                self.state, None, question_id="I001", candidate=candidate("proof"),
                question="대표 근거는 무엇인가요?", answer=answer,
            )
        self.assertEqual("resolved", result["resolution_status"])
        self.assertIn(
            "레퍼런스",
            agent.get_path(self.state, "strategy_tracks.A.proof_strategy.primary_proof"),
        )

    def test_budget_background_is_saved_without_inventing_choice_reason(self):
        answer = "공공기관이나 기업은 CSR 의무 때문에 여기에 예산이 배정되어 있습니다"
        response = {
            "activate_track_b": False,
            "fields": [{
                "path": "strategy_tracks.A.purchase_logic.why_budget",
                "status": "supported",
                "evidence": "CSR 의무 때문에 여기에 예산이 배정",
                "normalized_candidate": "CSR 의무",
                "missing_detail": None,
            }],
        }
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            result = agent.apply_answer(
                self.state, None, question_id="I001", candidate=candidate("purchase"),
                question="선택 이유는 무엇인가요?", answer=answer,
            )
        self.assertEqual("resolved", result["resolution_status"])
        self.assertIsNone(agent.get_path(self.state, "strategy_tracks.A.purchase_logic.purchase_reason"))
        self.assertIn("예산", agent.get_path(self.state, "strategy_tracks.A.purchase_logic.why_budget"))

    def test_mojibake_normalized_candidate_is_replaced_by_evidence(self):
        response = {
            "activate_track_b": False,
            "fields": [{
                "path": "strategy_tracks.A.market.country_or_region",
                "status": "supported",
                "evidence": "미국",
                "normalized_candidate": "¹Ì±¹",
                "missing_detail": None,
            }],
        }
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            agent.apply_answer(
                self.state, None, question_id="I001", candidate=candidate("market"),
                question="시장은 어디인가요?", answer="미국",
            )
        self.assertEqual("미국", agent.get_path(self.state, "strategy_tracks.A.market.country_or_region"))

    def test_short_meeting_is_resolved_even_when_model_marks_it_partial(self):
        response = {
            "activate_track_b": False,
            "fields": [{
                "path": "strategy_tracks.A.cta_strategy.primary_cta",
                "status": "partial",
                "evidence": "짧은 미팅",
                "normalized_candidate": "short_meeting",
                "missing_detail": "구체적인 시간이 없음",
            }],
        }
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            result = agent.apply_answer(
                self.state, None, question_id="I009", candidate=candidate("cta"),
                question="처음 요청할 행동은 무엇인가요?", answer="짧은 미팅",
            )
        path = "strategy_tracks.A.cta_strategy.primary_cta"
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual("meeting_15_30min", agent.get_path(self.state, path))
        self.assertNotIn(path, self.state["interview_state"]["partial_paths"])
        self.assertEqual("supported", result["field_results"][0]["status"])

    def test_direct_contact_is_resolved_when_model_marks_channel_partial(self):
        response = {
            "activate_track_b": False,
            "fields": [{
                "path": "strategy_tracks.A.entry_strategy.primary_channel",
                "status": "partial",
                "evidence": "직접 연락",
                "normalized_candidate": "direct",
                "missing_detail": "접촉 수단이 구체적이지 않음",
            }],
        }
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            result = agent.apply_answer(
                self.state, None, question_id="I008", candidate=candidate("entry"),
                question="어떤 경로로 접근하나요?", answer="직접 연락",
            )
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual(
            "direct_end_customer",
            agent.get_path(self.state, "strategy_tracks.A.entry_strategy.primary_channel"),
        )

    def test_ir_and_needs_actions_resolve_conversion_without_repeating_poc(self):
        response = {
            "activate_track_b": False,
            "fields": [{
                "path": "strategy_tracks.A.cta_strategy.conversion_flow",
                "status": "partial",
                "evidence": "IR 자료 전달 및 기업의 니즈 파악",
                "normalized_candidate": ["IR 자료 전달", "기업의 니즈 파악"],
                "missing_detail": "PoC 단계가 언급되지 않음",
            }],
        }
        conversion = candidate("conversion")
        question = agent.build_human_question(None, self.state, conversion)
        self.assertNotIn("PoC", question)
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            result = agent.apply_answer(
                self.state, None, question_id="I010", candidate=conversion,
                question=question, answer="IR 자료 전달 및 기업의 니즈 파악",
            )
        path = "strategy_tracks.A.cta_strategy.conversion_flow"
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual(
            ["IR 자료 전달", "기업의 니즈 파악"],
            agent.get_path(self.state, path),
        )
        self.assertNotIn(path, self.state["interview_state"]["partial_paths"])

    def test_sample_test_conversion_question_has_no_particle_error(self):
        answer_id = agent.add_answer_record(
            self.state, question_id="SEED",
            target_paths=["strategy_tracks.A.cta_strategy.primary_cta"],
            value="sample_test_offer", origin="user_stated",
            source_type="test", is_canonical=True,
        )
        agent.apply_updates(
            self.state,
            {"strategy_tracks.A.cta_strategy.primary_cta": "sample_test_offer"},
            origin="user_stated", answer_id=answer_id, invalidate=False,
        )
        conversion = candidate("conversion")
        question = agent.build_human_question(None, self.state, conversion)
        self.assertEqual(
            "‘샘플 테스트’ 다음에는 무엇을 전달하거나 확인할 계획인가요?",
            question,
        )
        self.assertNotIn("미팅", conversion.purpose)
        self.assertNotIn("PoC", conversion.purpose)

    def test_natural_final_correction_and_question_are_both_detected(self):
        with patch.object(agent, "chat", return_value=self.no_value()):
            result = agent.apply_final_review(
                self.state, None, question_id="I010", question="요약을 확인해 주세요.",
                answer="esg 캠페인을 전면솔루션으로, 트랙a는 뭐야?",
            )
        self.assertEqual("resolved", result["resolution_status"])
        self.assertTrue(result["clarification_requested"])
        self.assertEqual("esg 캠페인", agent.get_path(self.state, "offer.chosen_solution"))

    def test_empty_list_is_shown_as_unresolved(self):
        self.assertEqual("미해결", agent.display_value([]))

    def test_offer_does_not_create_fixed_ontology_followups(self):
        parent = candidate("offer")
        answers = iter(["ESG 캠페인", "맞습니다"])
        values_by_variant = {
            "main": {"offer.chosen_solution": "ESG 캠페인"},
        }

        def fake_apply(state, client, *, question_id, candidate, question, answer):
            updates = values_by_variant[candidate.variant]
            aid = agent.add_answer_record(
                state, question_id=question_id, target_paths=list(updates), value=answer,
                origin="user_stated", source_type="test", is_canonical=True,
            )
            touched = agent.apply_updates(
                state, updates, origin="user_stated", answer_id=aid, invalidate=False,
            )
            return {
                "resolution_status": "resolved",
                "answer_id": aid,
                "touched_paths": touched,
                "technical_retries": 0,
                "extraction_status": "parsed",
                "local_fallback_used": False,
                "semantic_rescue_used": False,
                "field_results": [],
            }

        with patch.object(agent, "select_next_question", side_effect=[parent, None]), \
             patch.object(agent, "apply_answer", side_effect=fake_apply), \
             patch.object(agent, "read_human_answer", side_effect=lambda: next(answers)), \
             patch.object(agent, "CEO_MODE", "human"):
            with redirect_stdout(io.StringIO()):
                agent.phase_interview(None, self.state, "mock research")

        variants = [item["question_variant"] for item in self.state["question_states"].values()]
        self.assertEqual(
            ["main"],
            variants,
        )

    def test_final_explanation_request_does_not_repeat_full_summary(self):
        values = {
            "offer.chosen_solution": "ESG 캠페인",
            "offer.differentiator": "사회적 가치와 아트 마케팅을 함께 제공",
            "strategy_tracks.A.market.country_or_region": "미국",
            "strategy_tracks.A.target.organization_type": "대기업",
            "strategy_tracks.A.purchase_logic.why_budget": "CSR 마케팅 예산",
            "strategy_tracks.A.recipient.first_reviewer": "마케팅 부서",
            "strategy_tracks.A.proof_strategy.primary_proof": "대기업 유료 협업 프로젝트",
            "strategy_tracks.A.entry_strategy.primary_channel": "direct_end_customer",
            "strategy_tracks.A.cta_strategy.primary_cta": "meeting_15_30min",
            "strategy_tracks.A.cta_strategy.conversion_flow": ["소개 미팅", "PoC"],
        }
        answer_id = agent.add_answer_record(
            self.state, question_id="SEED", target_paths=list(values), value=values,
            origin="user_stated", source_type="test", is_canonical=True,
        )
        agent.apply_updates(
            self.state, values, origin="user_stated", answer_id=answer_id, invalidate=False,
        )
        answers = iter(["esg 캠페인을 전면솔루션으로, 트랙a는 뭐야?", "맞습니다"])
        with patch.object(agent, "chat", return_value=self.no_value()), \
             patch.object(agent, "read_human_answer", side_effect=lambda: next(answers)), \
             patch.object(agent, "CEO_MODE", "human"):
            with redirect_stdout(io.StringIO()):
                transcript = agent.phase_interview(None, self.state, "mock research")
        self.assertEqual(1, sum(item["role"] == "interviewer" for item in transcript))
        self.assertFalse(self.state["completion"]["final_confirmed"])
        self.assertTrue(self.state["completion"]["clarification_pending"])


if __name__ == "__main__":
    unittest.main()
