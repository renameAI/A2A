import json
import sys
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))
import interview_test as agent


class GeneralizedScenarioTests(unittest.TestCase):
    def test_five_industry_scenarios_are_ready_and_schema_valid(self):
        schema = json.loads(agent.SCHEMA_PATH.read_text(encoding="utf-8"))
        fixture_paths = sorted(FIXTURES.glob("generic_*.json"))
        self.assertEqual(5, len(fixture_paths))
        for fixture_path in fixture_paths:
            with self.subTest(fixture=fixture_path.name):
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                updates = dict(fixture["updates"])
                updates.setdefault(
                    "offer.differentiator",
                    updates.get("strategy_tracks.A.purchase_logic.purchase_reason", "기존 대안과 다른 실행 방식"),
                )
                updates.setdefault(
                    "strategy_tracks.A.purchase_logic.why_budget",
                    "기존 사업·혁신 과제 예산",
                )
                state = agent.init_state(fixture["name"])
                answer_id = agent.add_answer_record(
                    state, question_id="FIXTURE", target_paths=list(updates),
                    value=updates, origin="user_stated",
                    source_type="generalized_fixture", is_canonical=True,
                )
                touched = agent.apply_updates(
                    state, updates, origin="user_stated",
                    answer_id=answer_id, invalidate=False,
                )
                self.assertEqual(set(updates), set(touched))
                completion = agent.compute_completion(state, interview_finished=True)
                self.assertTrue(completion["anchor_complete"])
                self.assertTrue(completion["strategy_ready"])
                jsonschema.validate(agent.normalize_instance(state), schema)

    def test_active_comparison_track_must_be_coherent_for_strategy_ready(self):
        fixture = json.loads((FIXTURES / "generic_creative_company.json").read_text(encoding="utf-8"))
        updates = dict(fixture["updates"])
        updates["offer.differentiator"] = updates["strategy_tracks.A.purchase_logic.purchase_reason"]
        updates["strategy_tracks.A.purchase_logic.why_budget"] = "브랜드 캠페인 예산"
        state = agent.init_state(fixture["name"])
        answer_id = agent.add_answer_record(
            state, question_id="FIXTURE", target_paths=list(updates),
            value=updates, origin="user_stated",
            source_type="generalized_fixture", is_canonical=True,
        )
        agent.apply_updates(
            state, updates, origin="user_stated", answer_id=answer_id, invalidate=False,
        )
        agent.ensure_track(state, "B")
        completion = agent.compute_completion(state, interview_finished=True)
        self.assertTrue(completion["anchor_complete"])
        self.assertFalse(completion["strategy_ready"])
        self.assertTrue(any("비교 진출안" in question for question in completion["followup_questions"]))


if __name__ == "__main__":
    unittest.main()
