import io
import json
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import interview_test as agent


class AdaptiveInterviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.output_dir = ROOT / "tests_strategy" / ".tmp_output"
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def tearDown(self):
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_full_interview_uses_core_execution_and_final_review(self):
        answers = iter([
            "ESG 캠페인입니다.",
            "미국입니다.",
            "대기업과 공공기관입니다.",
            "CSR 마케팅 예산을 사용합니다.",
            "장애인 아티스트 협업으로 CSR 의미와 아트 마케팅을 함께 제공합니다.",
            "ESG팀과 마케팅팀이 처음 검토합니다.",
            "IR 자료에 국내 대기업과 공공기관 협업 레퍼런스가 있습니다.",
            "직접 연락과 광고 에이전시를 모두 활용합니다.",
            "15분 소개 미팅을 요청합니다.",
            "캠페인 아이디어 제안, IR 자료 전달, PoC 논의 순서입니다.",
            "맞습니다",
        ])

        def fake_chat(client, system, messages, **kwargs):
            payload = json.loads(messages[-1]["content"])
            if system == agent.SEGMENT_EXAMPLE_SYS:
                return json.dumps({"examples": ["대기업", "공공기관", "브랜드"]}, ensure_ascii=False)
            self.assertEqual(agent.ANSWER_EXTRACT_SYS, system)
            kind = payload["question_kind"]
            responses = {
                "offer": [
                    ("offer.chosen_solution", "ESG 캠페인", "ESG 캠페인"),
                ],
                "differentiator": [
                    ("offer.differentiator", "장애인 아티스트 협업으로 CSR 의미와 아트 마케팅을 함께 제공", "장애인 아티스트 협업으로 CSR 의미와 아트 마케팅을 함께 제공"),
                ],
                "market": [("strategy_tracks.A.market.country_or_region", "미국", "미국")],
                "target": [
                    ("strategy_tracks.A.target.organization_type", "대기업·공공기관", "대기업과 공공기관"),
                ],
                "purchase": [
                    ("strategy_tracks.A.purchase_logic.why_budget", "CSR 마케팅 예산", "CSR 마케팅 예산"),
                ],
                "recipient": [("strategy_tracks.A.recipient.first_reviewer", "ESG팀·마케팅팀", "ESG팀과 마케팅팀")],
                "proof": [
                    ("strategy_tracks.A.proof_strategy.primary_proof", "IR 자료의 국내 대기업·공공기관 협업 레퍼런스", "IR 자료에 국내 대기업과 공공기관 협업 레퍼런스"),
                ],
                "entry": [
                    ("strategy_tracks.A.entry_strategy.primary_channel", "direct_end_customer", "직접 연락"),
                    ("strategy_tracks.A.entry_strategy.alternative_channel", "agency", "광고 에이전시"),
                ],
                "cta": [
                    ("strategy_tracks.A.cta_strategy.primary_cta", "meeting_15_30min", "15분 소개 미팅"),
                ],
                "conversion": [
                    ("strategy_tracks.A.cta_strategy.conversion_flow", ["캠페인 아이디어 제안", "IR 자료 전달", "PoC 논의"], "캠페인 아이디어 제안, IR 자료 전달, PoC 논의"),
                ],
            }
            updates = [
                {"path": path, "normalized": value, "source_text": source}
                for path, value, source in responses[kind]
            ]
            return json.dumps({"has_valid_value": True, "activate_track_b": False, "updates": updates}, ensure_ascii=False)

        state = agent.init_state("통합테스트")
        with patch.object(agent, "chat", side_effect=fake_chat), \
             patch.object(agent, "read_human_answer", side_effect=lambda: next(answers)), \
             patch.object(agent, "CEO_MODE", "human"), \
             patch.object(agent, "TARGET_COMPANY", "integration_test"), \
             patch.object(agent, "OUT_DIR", self.output_dir):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                transcript = agent.phase_interview(None, state, "mock research")
                agent.phase_output(state, transcript)

        interviewer_count = sum(item["role"] == "interviewer" for item in transcript)
        self.assertEqual(11, interviewer_count)
        self.assertEqual(11, state["interview_state"]["question_count"])
        self.assertTrue(state["completion"]["anchor_complete"])
        self.assertTrue(state["completion"]["strategy_ready"])
        self.assertTrue(state["completion"]["final_confirmed"])
        self.assertEqual("ESG 캠페인", agent.get_path(state, "offer.chosen_solution"))
        self.assertEqual("대기업과 공공기관", agent.get_path(state, "strategy_tracks.A.target.organization_type"))
        self.assertIn("협업 레퍼런스", agent.get_path(state, "strategy_tracks.A.proof_strategy.primary_proof"))
        self.assertFalse(state["completion"]["followup_questions"])

        normalized_path = self.output_dir / "integration_test_strategy_normalized.json"
        normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
        schema = json.loads(agent.SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(normalized, schema)
        self.assertTrue((self.output_dir / "integration_test_strategy_summary.md").exists())

    def test_ten_core_questions_and_final_review_finish_before_hard_cap(self):
        state = agent.init_state("전체순회테스트")

        def value_for(path):
            if path == "offer.transaction_unit":
                return "service"
            if path == "offer.differentiator":
                return "기존 대안보다 빠른 도입"
            if path == "offer.maturity_stage":
                return "sellable"
            if path == "transaction_strategy.primary_goal":
                return "poc"
            if path == "transaction_strategy.goal_sequence":
                return ["poc", "paid_project"]
            if path.endswith("entry_strategy.primary_channel"):
                return "direct_end_customer"
            if path.endswith("entry_strategy.alternative_channel"):
                return "agency"
            if path.endswith("cta_strategy.primary_cta"):
                return "meeting_15_30min"
            if path.endswith("cta_strategy.conversion_flow"):
                return ["소개 미팅", "PoC", "계약"]
            if path.endswith("purchase_logic.key_benefit_priority"):
                return ["problem_solving"]
            if path.endswith("purchase_logic.why_budget"):
                return "혁신 사업 예산"
            if path.endswith("proof_strategy.sample_or_demo_availability"):
                return "immediate"
            if path.endswith("proof_strategy.verification_status"):
                return "verified"
            if path.endswith("market.country_or_region"):
                return "미국"
            if path.endswith("target.organization_type"):
                return "대기업"
            if path.endswith("recipient.first_reviewer"):
                return "마케팅 부서"
            if path.endswith("purchase_logic.purchase_reason"):
                return "캠페인 실행 품질 향상"
            if path.endswith("proof_strategy.primary_proof"):
                return "대기업 유료 협업 프로젝트"
            if path == "offer.chosen_solution":
                return "ESG 캠페인"
            return "확인된 내용"

        def fake_apply(state, client, *, question_id, candidate, question, answer):
            updates = {path: value_for(path) for path in candidate.required_paths}
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

        with patch.object(agent, "apply_answer", side_effect=fake_apply), \
             patch.object(agent, "read_human_answer", return_value="맞습니다"), \
             patch.object(agent, "generate_segment_examples", return_value=["대기업", "공공기관"]), \
             patch.object(agent, "CEO_MODE", "human"):
            with redirect_stdout(io.StringIO()):
                transcript = agent.phase_interview(None, state, "mock research")

        variants = [
            item["question_variant"]
            for item in state["question_states"].values()
            if item["kind"] != "final_review"
        ]
        self.assertEqual(10, len(variants))
        self.assertEqual(11, state["interview_state"]["question_count"])
        self.assertLessEqual(state["interview_state"]["question_count"], agent.MAX_TOTAL_QUESTIONS)
        self.assertTrue(state["completion"]["final_confirmed"])
        schema = json.loads(agent.SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(state, schema)


if __name__ == "__main__":
    unittest.main()
