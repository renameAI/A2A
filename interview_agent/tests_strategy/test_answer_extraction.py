import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import interview_test as agent


class AnswerExtractionTests(unittest.TestCase):
    def setUp(self):
        self.state = agent.init_state()

    def test_api_extracts_concise_solution_from_long_answer(self):
        candidate = next(c for c in agent._global_candidates() if c.kind == "offer")
        answer = "이번에는 ESG 캠페인을 중심으로 프로젝트 형태로 제안하려고 합니다."
        response = {
            "has_valid_value": True,
            "activate_track_b": False,
            "updates": [
                {"path": "offer.chosen_solution", "normalized": "ESG 캠페인", "source_text": "ESG 캠페인"},
                {"path": "offer.transaction_unit", "normalized": "project", "source_text": "프로젝트"},
            ],
        }
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            result = agent.apply_answer(
                self.state, None, question_id="I001", candidate=candidate,
                question="무엇을 제안하나요?", answer=answer,
            )
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual("ESG 캠페인", agent.get_path(self.state, "offer.chosen_solution"))
        self.assertEqual("project", agent.get_path(self.state, "offer.transaction_unit"))

    def test_semantic_no_value_is_not_overridden_for_differentiator(self):
        candidate = next(c for c in agent._global_candidates() if c.kind == "differentiator")
        response = {"has_valid_value": False, "activate_track_b": False, "updates": []}
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            result = agent.apply_answer(
                self.state, None, question_id="I001", candidate=candidate,
                question="차이점은 무엇인가요?",
                answer="장애인 아티스트 협업으로 CSR 의미와 아트 마케팅을 함께 제공합니다",
            )
        self.assertEqual("semantically_insufficient", result["resolution_status"])
        self.assertFalse(result["local_fallback_used"])
        self.assertFalse(result["semantic_rescue_used"])
        self.assertIsNone(agent.get_path(self.state, "offer.differentiator"))

    def test_technical_api_failure_does_not_guess_free_text_conversion(self):
        candidate = next(c for c in agent._track_candidates("A", secondary=False) if c.kind == "conversion")
        with patch.object(agent, "chat", side_effect=RuntimeError("network")):
            result = agent.apply_answer(
                self.state, None, question_id="I001", candidate=candidate,
                question="어떤 순서로 진행하나요?",
                answer="캠페인 아이디어 제안 → IR 자료 전달 → PoC 논의",
            )
        self.assertEqual("failed", result["resolution_status"])
        self.assertTrue(result["local_fallback_used"])
        self.assertEqual(
            [],
            agent.get_path(self.state, "strategy_tracks.A.cta_strategy.conversion_flow"),
        )

    def test_explicit_unknown_is_not_technical_failure(self):
        candidate = agent._track_candidates("A", secondary=False)[4]
        prefill_id = agent.add_answer_record(
            self.state, question_id=None, target_paths=[candidate.required_paths[0]],
            value="외부 협업 프로젝트", origin="external_research",
            source_type="test_prefill", is_canonical=False,
        )
        agent.apply_updates(
            self.state, {candidate.required_paths[0]: "외부 협업 프로젝트"},
            origin="external_research", answer_id=prefill_id, invalidate=False,
        )
        result = agent.apply_answer(
            self.state, None, question_id="I001", candidate=candidate,
            question="근거가 있나요?", answer="아직 모름",
        )
        self.assertEqual("explicit_unknown", result["resolution_status"])
        self.assertIn(candidate.required_paths[0], self.state["interview_state"]["explicit_unknown_paths"])
        self.assertIsNone(agent.get_path(self.state, candidate.required_paths[0]))
        self.assertEqual("사용자 확인: 현재 미정·없음", agent.display_path(self.state, candidate.required_paths[0]))

    def test_api_can_create_a_grounded_comparison_track(self):
        candidate = next(c for c in agent._track_candidates("A", secondary=False) if c.kind == "market")
        answer = "주력 시장은 프랑스이고 비교 시장은 일본입니다."
        response = {
            "has_valid_value": True,
            "activate_track_b": True,
            "updates": [
                {
                    "path": "strategy_tracks.A.market.country_or_region",
                    "normalized": "프랑스",
                    "source_text": "프랑스",
                },
                {
                    "path": "strategy_tracks.B.market.country_or_region",
                    "normalized": "일본",
                    "source_text": "일본",
                },
            ],
        }
        with patch.object(agent, "chat", return_value=json.dumps(response, ensure_ascii=False)):
            result = agent.apply_answer(
                self.state, None, question_id="I003", candidate=candidate,
                question="시장은 어디인가요?", answer=answer,
            )
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual(["A", "B"], agent._active_track_ids(self.state))
        self.assertEqual("일본", agent.get_path(self.state, "strategy_tracks.B.market.country_or_region"))


if __name__ == "__main__":
    unittest.main()
