import ast
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import interview_test as agent


class StrategyStateTests(unittest.TestCase):
    def test_initial_state_and_schema_are_valid(self):
        state = agent.init_state("테스트기업")
        schema = json.loads(agent.SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(state, schema)
        self.assertEqual(["A"], agent._active_track_ids(state))

    def test_second_track_is_created_at_most_once(self):
        state = agent.init_state()
        first = agent.ensure_track(state, "B")
        second = agent.ensure_track(state, "B")
        self.assertIs(first, second)
        self.assertEqual(2, len(state["strategy_tracks"]))
        with self.assertRaises(ValueError):
            agent.ensure_track(state, "C")

    def test_changing_solution_marks_dependent_values_stale(self):
        state = agent.init_state()
        aid = agent.add_answer_record(
            state, question_id="I001", target_paths=[], value="초기", origin="user_stated",
            source_type="test", is_canonical=True,
        )
        agent.apply_updates(state, {
            "offer.chosen_solution": "점자 프린트 솔루션",
            "strategy_tracks.A.target.organization_type": "지방자치단체",
            "strategy_tracks.A.purchase_logic.purchase_reason": "복지 서비스 개선",
            "strategy_tracks.A.proof_strategy.primary_proof": "지자체 납품 프로젝트",
        }, origin="user_stated", answer_id=aid)
        aid2 = agent.add_answer_record(
            state, question_id="I010", target_paths=[], value="수정", origin="user_stated",
            source_type="test", is_canonical=True,
        )
        agent.apply_updates(
            state, {"offer.chosen_solution": "점자 안내 키오스크"},
            origin="user_stated", answer_id=aid2,
        )
        stale = state["interview_state"]["stale_paths"]
        self.assertIn("strategy_tracks.A.target.organization_type", stale)
        self.assertIn("strategy_tracks.A.purchase_logic.purchase_reason", stale)
        self.assertIn("strategy_tracks.A.proof_strategy.primary_proof", stale)

    def test_source_has_no_project_import_dependency(self):
        tree = ast.parse((ROOT / "interview_test.py").read_text(encoding="utf-8"))
        forbidden = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                forbidden.extend(name.name for name in node.names if name.name.startswith("interview_agent"))
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("interview_agent"):
                forbidden.append(node.module)
        self.assertEqual([], forbidden)

    def test_final_market_correction_invalidates_market_dependent_strategy(self):
        state = agent.init_state()
        first_id = agent.add_answer_record(
            state, question_id="SEED", target_paths=[], value="seed", origin="user_stated",
            source_type="test", is_canonical=True,
        )
        agent.apply_updates(state, {
            "strategy_tracks.A.market.country_or_region": "프랑스",
            "strategy_tracks.A.recipient.first_reviewer": "ESG팀",
            "strategy_tracks.A.entry_strategy.primary_channel": "agency",
        }, origin="user_stated", answer_id=first_id, invalidate=False)
        api_response = {
            "has_valid_value": True,
            "activate_track_b": False,
            "updates": [{
                "path": "strategy_tracks.A.market.country_or_region",
                "normalized": "일본",
                "source_text": "일본",
            }],
        }
        with patch.object(agent, "chat", return_value=json.dumps(api_response, ensure_ascii=False)):
            result = agent.apply_final_review(
                state, None, question_id="I010", question="최종 확인",
                answer="시장=일본",
            )
        self.assertEqual("일본", agent.get_path(state, "strategy_tracks.A.market.country_or_region"))
        self.assertIn("strategy_tracks.A.recipient.first_reviewer", state["interview_state"]["stale_paths"])
        self.assertIn("strategy_tracks.A.entry_strategy.primary_channel", state["interview_state"]["stale_paths"])
        self.assertEqual("resolved", result["resolution_status"])


if __name__ == "__main__":
    unittest.main()
