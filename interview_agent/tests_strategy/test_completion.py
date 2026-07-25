import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import interview_test as agent


def apply_user_values(state, values):
    aid = agent.add_answer_record(
        state, question_id="TEST", target_paths=list(values), value=values,
        origin="user_stated", source_type="test", is_canonical=True,
    )
    agent.apply_updates(state, values, origin="user_stated", answer_id=aid, invalidate=False)


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.state = agent.init_state()
        apply_user_values(self.state, {
            "offer.chosen_solution": "ESG 캠페인",
            "offer.differentiator": "사회적 가치와 아트 마케팅을 함께 제공",
            "transaction_strategy.primary_goal": "poc",
            "strategy_tracks.A.market.country_or_region": "프랑스",
            "strategy_tracks.A.target.organization_type": "대기업",
            "strategy_tracks.A.recipient.first_reviewer": "ESG팀",
            "strategy_tracks.A.purchase_logic.why_budget": "CSR 마케팅 예산",
            "strategy_tracks.A.proof_strategy.primary_proof": "삼성전자 유료 협업 프로젝트",
            "strategy_tracks.A.entry_strategy.primary_channel": "agency",
            "strategy_tracks.A.cta_strategy.primary_cta": "meeting_15_30min",
        })

    def test_missing_conversion_keeps_anchor_and_strategy_incomplete(self):
        completion = agent.compute_completion(self.state, interview_finished=True)
        self.assertFalse(completion["anchor_complete"])
        self.assertFalse(completion["strategy_ready"])
        self.assertTrue(completion["followup_questions"])

    def test_conversion_completes_v4_strategy(self):
        apply_user_values(self.state, {
            "strategy_tracks.A.cta_strategy.conversion_flow": ["15분 미팅", "제안서 검토"],
        })
        completion = agent.compute_completion(self.state, interview_finished=True)
        self.assertTrue(completion["anchor_complete"])
        self.assertTrue(completion["strategy_ready"])

    def test_unknown_market_is_resolved_conversationally_but_not_actionable(self):
        state = agent.init_state()
        answer_id = agent.add_answer_record(
            state, question_id="TEST", target_paths=[], value="미정",
            origin="user_stated", source_type="test", is_canonical=True,
        )
        agent.mark_explicit_unknown(
            state, ["strategy_tracks.A.market.country_or_region"], answer_id=answer_id,
        )
        self.assertTrue(agent.is_user_resolved(state, "strategy_tracks.A.market.country_or_region"))
        completion = agent.compute_completion(state, interview_finished=True)
        self.assertIn("strategy_tracks.A.market.country_or_region", completion["missing_anchor_paths"])
        self.assertFalse(completion["anchor_complete"])


if __name__ == "__main__":
    unittest.main()
