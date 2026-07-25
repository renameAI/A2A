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


class ResearchContextTests(unittest.TestCase):
    def setUp(self):
        self.state = agent.init_state("키뮤")
        answer_id = agent.add_answer_record(
            self.state,
            question_id="SEED",
            target_paths=["offer.chosen_solution"],
            value="ESG 캠페인",
            origin="user_stated",
            source_type="test",
            is_canonical=True,
        )
        agent.apply_updates(
            self.state,
            {"offer.chosen_solution": "ESG 캠페인"},
            origin="user_stated",
            answer_id=answer_id,
            invalidate=False,
        )

    def test_only_document_grounded_non_inference_facts_survive(self):
        research = (
            "키뮤는 미국 전시회에서 ESG 캠페인 사례를 소개했다. "
            "국내 대기업과 ESG 캠페인 협업을 진행했다."
        )
        response = {
            "facts": [
                {
                    "category": "market_activity",
                    "fact": "미국 전시회에서 ESG 캠페인 사례를 소개했다",
                    "source_text": "키뮤는 미국 전시회에서 ESG 캠페인 사례를 소개했다",
                },
                {
                    "category": "customer_collaboration",
                    "fact": "미국이 우선 시장으로 유력하다",
                    "source_text": "미국이 우선 시장으로 유력하다",
                },
                {
                    "category": "proof",
                    "fact": "존재하지 않는 인증을 보유했다",
                    "source_text": "존재하지 않는 인증을 보유했다",
                },
            ]
        }
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            facts = agent.extract_research_facts(None, research)
        self.assertEqual(1, len(facts))
        self.assertEqual("market_activity", facts[0]["category"])

    def test_research_is_background_only_for_allowed_questions(self):
        facts = [{
            "category": "market_activity",
            "fact": "미국 전시회에서 ESG 캠페인 사례를 소개했다",
            "source_text": "미국 전시회에서 ESG 캠페인 사례를 소개했다",
        }]
        market_question = agent.add_research_context_to_question(
            self.state,
            candidate("market"),
            agent.build_human_question(None, self.state, candidate("market")),
            facts,
        )
        purchase_question = agent.add_research_context_to_question(
            self.state,
            candidate("purchase"),
            agent.build_human_question(None, self.state, candidate("purchase")),
            facts,
        )
        self.assertIn("공개 자료에서는", market_question)
        self.assertNotIn("공개 자료에서는", purchase_question)

    def test_unrelated_identity_theme_does_not_enter_differentiator_question(self):
        facts = [
            {
                "category": "solution_mechanism",
                "fact": "장애인 고용 확대를 지원한다",
                "source_text": "장애인 고용 확대를 지원한다",
            },
            {
                "category": "solution_mechanism",
                "fact": "ESG 캠페인에서 장애인 아티스트와 협업한다",
                "source_text": "ESG 캠페인에서 장애인 아티스트와 협업한다",
            },
        ]
        selected = agent.select_research_context(
            self.state, candidate("differentiator"), facts,
        )
        self.assertEqual("ESG 캠페인에서 장애인 아티스트와 협업한다", selected)
        target_selected = agent.select_research_context(
            self.state,
            candidate("target"),
            [{
                "category": "customer_collaboration",
                "fact": "장애인 고용 기관과 협력한다",
                "source_text": "장애인 고용 기관과 협력한다",
            }],
        )
        self.assertIsNone(target_selected)


if __name__ == "__main__":
    unittest.main()
