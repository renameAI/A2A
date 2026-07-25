import io
import json
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


def response(role, fit, evidence, fields, *, missing=None, confidence=0.8):
    return json.dumps({
        "activate_track_b": False,
        "assessment": {
            "answer_role": role,
            "field_fit": fit,
            "redirect_path": None,
            "missing_reason": missing,
            "confidence": confidence,
            "evidence": evidence,
        },
        "fields": fields,
    }, ensure_ascii=False)


class DiveinRegressionTests(unittest.TestCase):
    def setUp(self):
        self.state = agent.init_state("다이브인")

    def apply(self, kind, answer, raw):
        with patch.object(agent, "chat", return_value=raw):
            return agent.apply_answer(
                self.state, None, question_id="I001", candidate=candidate(kind),
                question="테스트 질문", answer=answer,
            )

    def test_three_star_hotel_resolves_target_without_contaminating_reviewer(self):
        target_path = "strategy_tracks.A.target.organization_type"
        reviewer_path = "strategy_tracks.A.recipient.first_reviewer"
        answer = "3성급 호텔"
        raw = response(
            "customer_organization", "partial", answer,
            [
                {
                    "path": reviewer_path, "status": "supported", "evidence": answer,
                    "normalized_candidate": answer, "missing_detail": None,
                },
                {
                    "path": target_path, "status": "partial", "evidence": answer,
                    "normalized_candidate": answer, "missing_detail": "시설 유형",
                },
            ],
            missing="호텔 등급만 제시함",
        )
        result = self.apply("target", answer, raw)
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual(answer, agent.get_path(self.state, target_path))
        self.assertIsNone(agent.get_path(self.state, reviewer_path))

    def test_research_prefill_never_moves_offer_confirmation_behind_dependencies(self):
        aid = agent.add_answer_record(
            self.state, question_id=None,
            target_paths=["offer.chosen_solution", "strategy_tracks.A.market.country_or_region"],
            value="research", origin="external_research",
            source_type="test", is_canonical=False,
        )
        agent.apply_updates(
            self.state,
            {
                "offer.chosen_solution": "아트 스테이",
                "strategy_tracks.A.market.country_or_region": "베트남",
            },
            origin="external_research", answer_id=aid, invalidate=False,
        )
        self.assertEqual("offer", agent.select_next_question(self.state).kind)

    def test_clear_cta_conversion_and_channel_short_answers_resolve(self):
        cases = [
            (
                "cta", "미팅", "recipient_next_action",
                "strategy_tracks.A.cta_strategy.primary_cta", "meeting_15_30min",
            ),
            (
                "conversion", "호텔측의 니즈 파악", "followup_action_sequence",
                "strategy_tracks.A.cta_strategy.conversion_flow", ["호텔측의 니즈 파악"],
            ),
            (
                "entry", "직접적으로 연락을 진행", "access_channel",
                "strategy_tracks.A.entry_strategy.primary_channel", "direct_end_customer",
            ),
        ]
        for index, (kind, answer, role, path, expected) in enumerate(cases, 1):
            raw = response(
                role, "partial", answer,
                [{
                    "path": path, "status": "partial", "evidence": answer,
                    "normalized_candidate": expected, "missing_detail": "구체성 부족",
                }],
                missing="더 구체적인 설명 필요",
            )
            with self.subTest(kind=kind):
                with patch.object(agent, "chat", return_value=raw):
                    result = agent.apply_answer(
                        self.state, None, question_id=f"I{index:03d}",
                        candidate=candidate(kind), question="테스트 질문", answer=answer,
                    )
                self.assertEqual("resolved", result["resolution_status"])
                self.assertEqual(expected, agent.get_path(self.state, path))

    def test_one_entry_channel_is_not_written_as_duplicate_alternative(self):
        primary = "strategy_tracks.A.entry_strategy.primary_channel"
        alternative = "strategy_tracks.A.entry_strategy.alternative_channel"
        answer = "콜드메일로 직접 연락"
        raw = response(
            "access_channel", "exact", answer,
            [{
                "path": alternative, "status": "supported", "evidence": answer,
                "normalized_candidate": "direct_end_customer", "missing_detail": None,
            }],
        )
        result = self.apply("entry", answer, raw)
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual("direct_end_customer", agent.get_path(self.state, primary))
        self.assertIsNone(agent.get_path(self.state, alternative))

    def test_user_frustration_never_overwrites_partial_strategy_value(self):
        path = "offer.differentiator"
        first = "호텔 리모델링 비용 부담을 낮추고 객실을 특색 있게 만듭니다"
        raw = response(
            "comparative_advantage", "partial", first,
            [{
                "path": path, "status": "partial", "evidence": first,
                "normalized_candidate": first, "missing_detail": "비교 방식 필요",
            }],
            missing="비용을 낮추는 방식이 필요",
        )
        self.apply("differentiator", first, raw)
        result = agent.apply_answer(
            self.state, None, question_id="I002", candidate=candidate("differentiator"),
            question="차이를 더 설명해 주세요", answer="왜 차이점이 확정되지 않았는지 모르겠네",
        )
        self.assertEqual("semantically_insufficient", result["resolution_status"])
        self.assertEqual(first, agent.get_path(self.state, path))
        self.assertNotIn("모르겠네", agent.get_path(self.state, path))

    def test_self_redirected_budget_category_is_repaired(self):
        path = "strategy_tracks.A.purchase_logic.why_budget"
        answer = "객실 리모델링"
        raw = json.dumps({
            "activate_track_b": False,
            "assessment": {
                "answer_role": "redirect",
                "field_fit": "redirect",
                "redirect_path": path,
                "missing_reason": "예산 배경과 불일치",
                "confidence": 0.9,
                "evidence": answer,
            },
            "fields": [{
                "path": path, "status": "not_mentioned", "evidence": answer,
                "normalized_candidate": answer, "missing_detail": None,
            }],
        }, ensure_ascii=False)
        result = self.apply("purchase", answer, raw)
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual(answer, agent.get_path(self.state, path))

    def test_hotel_cases_are_repaired_from_source_to_primary_proof(self):
        primary = "strategy_tracks.A.proof_strategy.primary_proof"
        source = "strategy_tracks.A.proof_strategy.source"
        answer = "기존에 진행해 온 다양한 호텔들의 사례들"
        raw = json.dumps({
            "activate_track_b": False,
            "assessment": {
                "answer_role": "redirect",
                "field_fit": "redirect",
                "redirect_path": source,
                "missing_reason": "구체적인 이름 부족",
                "confidence": 0.9,
                "evidence": answer,
            },
            "fields": [{
                "path": source, "status": "partial", "evidence": answer,
                "normalized_candidate": answer, "missing_detail": "출처 미상",
            }],
        }, ensure_ascii=False)
        result = self.apply("proof", answer, raw)
        self.assertEqual("resolved", result["resolution_status"])
        self.assertEqual(answer, agent.get_path(self.state, primary))
        self.assertIsNone(agent.get_path(self.state, source))

    def test_invalid_contract_does_not_trigger_free_text_fallback(self):
        path = "strategy_tracks.A.proof_strategy.primary_proof"
        invalid = json.dumps({
            "assessment": {
                "answer_role": "redirect",
                "field_fit": "redirect",
                "redirect_path": path,
                "missing_reason": "잘못된 계약",
                "confidence": 0.9,
                "evidence": "호텔 레퍼런스가 있습니다",
            },
            "fields": [],
        }, ensure_ascii=False)
        with patch.object(agent, "chat", return_value=invalid):
            result = agent.apply_answer(
                self.state, None, question_id="I009", candidate=candidate("proof"),
                question="대표 근거는 무엇인가요?",
                answer="호텔 레퍼런스가 있습니다",
            )
        self.assertFalse(result["local_fallback_used"])
        self.assertIsNone(agent.get_path(self.state, path))

    def test_hotel_collaboration_fact_is_preferred_for_proof_question(self):
        def seed(path, value):
            aid = agent.add_answer_record(
                self.state, question_id="SEED", target_paths=[path], value=value,
                origin="user_stated", source_type="test", is_canonical=True,
            )
            agent.apply_updates(
                self.state, {path: value}, origin="user_stated",
                answer_id=aid, invalidate=False,
            )

        seed("offer.chosen_solution", "객실 아트 스테이 리모델링")
        seed("strategy_tracks.A.target.organization_type", "3성급 호텔")
        facts = [
            {"category": "proof", "fact": "아트코리아랩 입주 및 지원 사업 선정."},
            {
                "category": "customer_collaboration",
                "fact": "호텔: 센터마크호텔, 아도니스호텔 등.",
            },
        ]
        selected = agent.select_research_context(self.state, candidate("proof"), facts)
        self.assertIn("센터마크호텔", selected)

    def test_maturity_prefill_is_normalized_to_schema_enum(self):
        value = agent.normalize_path_value(
            "offer.maturity_stage",
            "초기 단계를 지나 국내외 호텔과 파트너십을 맺고 아트룸을 개발·운영하는 성장기",
        )
        self.assertEqual("sellable", value)
        agent.apply_updates(
            self.state, {"offer.maturity_stage": value},
            origin="external_research", answer_id=None, invalidate=False,
        )
        self.assertIsNone(agent.strategy_schema_error(self.state))

    def test_final_summary_never_contains_bulk_followup_questions(self):
        lines = agent.strategy_summary_lines(self.state)
        self.assertNotIn("[후속 확인 필요]", lines)
        with patch.object(agent, "select_next_question", return_value=None), \
             patch.object(agent, "CEO_MODE", "human"):
            with redirect_stdout(io.StringIO()):
                transcript = agent.phase_interview(None, self.state, "mock")
        self.assertEqual([], transcript)
        self.assertFalse(self.state["completion"]["final_confirmed"])

    def test_partial_answer_followup_is_asked_immediately(self):
        target = candidate("target")
        calls = {"count": 0}

        def fake_apply(state, client, *, question_id, candidate, question, answer):
            calls["count"] += 1
            aid = agent.add_answer_record(
                state, question_id=question_id, target_paths=list(candidate.required_paths),
                value=answer, origin="user_stated", source_type="test", is_canonical=False,
            )
            if calls["count"] == 1:
                return {
                    "resolution_status": "partially_resolved",
                    "answer_id": aid,
                    "touched_paths": [],
                    "technical_retries": 0,
                    "extraction_status": "parsed",
                    "local_fallback_used": False,
                    "semantic_rescue_used": False,
                    "field_results": [],
                }
            touched = agent.apply_updates(
                state, {target.required_paths[0]: "3성급 호텔"},
                origin="user_stated", answer_id=aid, invalidate=False,
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

        with patch.object(agent, "select_next_question", side_effect=[target, None]), \
             patch.object(agent, "apply_answer", side_effect=fake_apply), \
             patch.object(agent, "read_human_answer", side_effect=["호텔", "3성급 호텔"]), \
             patch.object(agent, "CEO_MODE", "human"):
            with redirect_stdout(io.StringIO()):
                agent.phase_interview(None, self.state, "mock")
        kinds = [item["kind"] for item in self.state["question_states"].values()]
        self.assertEqual(["target", "target"], kinds)


if __name__ == "__main__":
    unittest.main()
