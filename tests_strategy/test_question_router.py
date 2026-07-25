import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import interview_test as agent


def seed_paths(state, paths):
    for index, (path, value) in enumerate(paths.items(), 1):
        aid = agent.add_answer_record(
            state, question_id=f"S{index:03d}", target_paths=[path], value=value,
            origin="user_stated", source_type="test_seed", is_canonical=True,
        )
        agent.apply_updates(
            state, {path: value}, origin="user_stated", answer_id=aid, invalidate=False,
        )


class QuestionRouterTests(unittest.TestCase):
    def test_question_budget_uses_soft_warning_and_hard_safety_cap(self):
        self.assertEqual(15, agent.SOFT_QUESTION_WARNING)
        self.assertEqual(40, agent.MAX_TOTAL_QUESTIONS)
        self.assertEqual(38, agent.MAX_CONTENT_QUESTIONS)

    def test_initial_router_starts_with_offer(self):
        state = agent.init_state()
        self.assertEqual(10, len(agent.build_question_candidates(state)))
        self.assertEqual("offer", agent.select_next_question(state).kind)

    def test_resolved_offer_moves_to_market_because_poc_is_program_context(self):
        state = agent.init_state()
        seed_paths(state, {"offer.chosen_solution": "ESG 캠페인"})
        self.assertEqual("market", agent.select_next_question(state).kind)
        self.assertEqual("poc", agent.get_path(state, "transaction_strategy.primary_goal"))
        self.assertEqual(
            "program_context",
            state["field_meta"]["transaction_strategy.primary_goal"]["origin"],
        )

    def test_unasked_core_item_precedes_retry(self):
        state = agent.init_state()
        state["interview_state"]["candidate_attempts"]["offer:global:main"] = 1
        self.assertEqual("market", agent.select_next_question(state).kind)

    def test_only_missing_v4_decisions_remain_after_legacy_anchors(self):
        state = agent.init_state()
        values = {
            "offer.chosen_solution": "ESG 캠페인",
            "offer.differentiator": "사회적 가치와 아트 마케팅을 함께 제공",
            "transaction_strategy.primary_goal": "poc",
            "strategy_tracks.A.market.country_or_region": "프랑스",
            "strategy_tracks.A.target.organization_type": "대기업",
            "strategy_tracks.A.recipient.first_reviewer": "ESG팀",
            "strategy_tracks.A.purchase_logic.why_budget": "CSR 마케팅 예산",
            "strategy_tracks.A.proof_strategy.primary_proof": "대기업 유료 협업 프로젝트",
            "strategy_tracks.A.entry_strategy.primary_channel": "agency",
            "strategy_tracks.A.cta_strategy.primary_cta": "meeting_15_30min",
            "strategy_tracks.A.cta_strategy.conversion_flow": ["소개 미팅", "PoC 제안"],
        }
        seed_paths(state, values)
        candidates = agent.build_question_candidates(state)
        self.assertEqual([], candidates)

    def test_all_local_fallback_questions_are_user_safe(self):
        state = agent.init_state()
        with patch.object(agent, "chat", side_effect=RuntimeError("offline")):
            for candidate in agent._global_candidates() + agent._track_candidates("A", secondary=False):
                question = agent.build_human_question(None, state, candidate)
                self.assertTrue(agent.question_is_safe(question), question)
                self.assertNotRegex(question, r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+")
                for followup in agent._followup_candidates_for(state, candidate):
                    followup_question = agent.build_human_question(None, state, followup)
                    self.assertTrue(agent.question_is_safe(followup_question), followup_question)
                    self.assertNotRegex(
                        followup_question, r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+"
                    )

    def test_differentiator_question_names_company_and_offer(self):
        state = agent.init_state("키뮤스튜디오")
        seed_paths(state, {"offer.chosen_solution": "ESG 캠페인"})
        candidate = next(
            item for item in agent._global_candidates() if item.kind == "differentiator"
        )
        question = agent.build_human_question(None, state, candidate)
        self.assertEqual(
            "‘키뮤스튜디오’의 전면 제안은 ‘ESG 캠페인’입니다. 다른 경쟁 솔루션이나 "
            "기존 대안과 비교했을 때 가장 큰 차이는 무엇인가요?",
            question,
        )


if __name__ == "__main__":
    unittest.main()
