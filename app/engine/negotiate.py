"""A2A 협상 오케스트레이션 (NEG-01~08, 기획서 7-A장).

제안 → 구매자 Judge 검토 → 거절+구조화 사유 → 손잡이 묶음 조정 → 재제안 → 종료 3종.
- 거절 분류 선행 (NEG-03): 풀리는 거절만 재제안, 못 푸는 거절은 종료 (#03 분류 테이블)
- 재제안 = 손잡이 묶음의 동시 조정 (NEG-04): 주 손잡이 + 부 손잡이
- 판매자 최저선 이하 제안 금지 (NEG-06)
- 무한 루프 금지 — 라운드 상한 내 종료 보장 (NEG-05)
"""
from dataclasses import dataclass

from ..errors import DealBreaker
from ..schemas import (DecisionType, Dimension, Intent, JudgeRequest,
                       KnobAdjustment, NegotiateRequest, NegotiationResult,
                       NegotiationRound, Objective, RejectionInfo, RoundResponse,
                       TerminationType, Vantage, VerdictType)
from .judge import judge

# 못 푸는 거절 (#03 PART B): 자체 실행 선택·실행 의지 부재 → 추격 중단/결렬
_UNRECOVERABLE_MARKERS = ["실행 의지 없음", "직접 하겠다", "자체 실행"]

# 거절 차원 → 손잡이 묶음 매핑 (주 손잡이 + 부 손잡이, 7-A.3)
_KNOB_BUNDLES: dict[Dimension, list[tuple[str, str, str]]] = {
    Dimension.purpose_alignment: [
        ("concept", "일반 제안", "상대 고객층 트렌드 맞춤 컨셉 재설계"),
        ("rooms", "표준 규모", "소규모 조정")],
    Dimension.demonstrability: [
        ("proof", "레퍼런스 소개", "실측 데이터·포트폴리오 제시 + PoC 축소"),
        ("share", "7:3", "8:2 (레퍼런스 가치 반영 양보)")],
    Dimension.resource_complementarity: [
        ("scope", "표준 범위", "상대 결핍에 맞춘 범위 재정의"),
        ("rooms", "표준 규모", "소규모 조정")],
    Dimension.stage_compatibility: [
        ("rooms", "표준 규모", "PoC 최소 규모"),
        ("proof", "레퍼런스 소개", "원상 복구 보장 추가")],
    Dimension.substitute_comparison: [
        ("proof", "레퍼런스 소개", "대안 대비 비교우위 자료 제시"),
        ("share", "7:3", "조건 미세조정")],
    Dimension.opportunity_cost: [
        ("rooms", "표준 규모", "PoC 최소 규모 + 원상 복구 보장"),
        ("share", "7:3", "조건 미세조정")],
    Dimension.industry_fit: [
        ("concept", "일반 제안", "교차 도메인 가치 재설명"),
        ("proof", "레퍼런스 소개", "유사 도메인 사례 제시")],
}

# 손잡이별 판매자 최저선 (NEG-06) — 사전정보 키 "최저선:<knob>" 값이 있으면 그 이하 금지
_FLOOR_PREFIX = "최저선:"


@dataclass
class _State:
    knobs: dict[str, str]
    resolved: set[str]


def _buyer_review(req: NegotiateRequest, adjusted_note: str | None) -> "JudgeResult":
    """구매자 렌즈 Judge 호출 (NEG-01: 왕복의 수신측 검토).
    협상은 라운드마다 판단이 돌므로 fast 경로(deep=False) — 3라운드 15분 방지."""
    intent = req.intent if adjusted_note is None else Intent(
        **{**req.intent.model_dump(), "notes": adjusted_note})
    return judge(deep=False, req=JudgeRequest(
        vantage=Vantage.buyer,
        objective=Objective.willingness_gate,
        self_profile=req.buyer_profile,
        self_private_state=req.buyer_private_state,
        counterpart_profile=req.seller_profile,
        counterpart_private_state=req.seller_private_state,
        intent=intent,
    ))


def _find_concern(judge_result, resolved: set[str]) -> Dimension | None:
    """아직 해소되지 않은 가장 중요한 '주의' 차원 (7-A.3: 거절은 막힌 차원을 찍는다)."""
    priority = [Dimension.purpose_alignment, Dimension.demonstrability,
                Dimension.substitute_comparison, Dimension.opportunity_cost,
                Dimension.resource_complementarity, Dimension.stage_compatibility,
                Dimension.industry_fit]
    cautions = {d.dimension: d for d in judge_result.category_judgments
                if d.verdict != VerdictType.fit}
    for dim in priority:
        if dim in cautions and dim.value not in resolved:
            return dim
    return None


def _unrecoverable_reason(req: NegotiateRequest) -> str | None:
    for item in req.buyer_private_state.items:
        text = f"{item.key} {item.value}"
        if any(m in text for m in _UNRECOVERABLE_MARKERS):
            return f"{item.key}: {item.value}"
    return None


def _proposal_text(req: NegotiateRequest, state: _State, round_no: int) -> str:
    knobs = ", ".join(f"{k}={v}" for k, v in state.knobs.items())
    return (f"[R{round_no}] {req.seller_profile.basic.name} → "
            f"{req.buyer_profile.basic.name} 제안 ({knobs})")


def _floors(req: NegotiateRequest) -> dict[str, str]:
    return {i.key.removeprefix(_FLOOR_PREFIX): i.value
            for i in req.seller_private_state.items if i.key.startswith(_FLOOR_PREFIX)}


def negotiate(req: NegotiateRequest) -> NegotiationResult:
    state = _State(knobs={"concept": "일반 제안", "share": "7:3",
                          "rooms": "표준 규모", "proof": "레퍼런스 소개"},
                   resolved=set())
    floors = _floors(req)
    rounds: list[NegotiationRound] = []
    pending_adjustments: list[KnobAdjustment] = []

    from .. import progress
    for round_no in range(1, req.max_rounds + 1):
        proposal = _proposal_text(req, state, round_no)
        progress.log("협상", f"R{round_no} 제안 발신 → 구매자 렌즈 검토 시작")

        # 수신측(구매자) 검토 — deal-breaker면 즉시 결렬 (7-A.4)
        try:
            review = _buyer_review(
                req, adjusted_note=f"재제안 반영: {state.resolved}" if state.resolved else None)
        except DealBreaker as e:
            rounds.append(NegotiationRound(
                round=round_no, proposal=proposal, response=RoundResponse.reject,
                rejection=RejectionInfo(
                    dimension=Dimension(e.details["dimension"]),
                    reason=e.details["reason"], recoverable=False),
                knobs_adjusted=pending_adjustments))
            return NegotiationResult(rounds=rounds,
                                     termination=TerminationType.breakdown,
                                     rounds_used=round_no)

        # 못 푸는 거절 먼저 분류 (NEG-03: 재제안 전 거절 분류 선행)
        hard_reason = _unrecoverable_reason(req)
        if hard_reason:
            rounds.append(NegotiationRound(
                round=round_no, proposal=proposal, response=RoundResponse.reject,
                rejection=RejectionInfo(dimension=Dimension.purpose_alignment,
                                        reason=f"못 푸는 거절 — {hard_reason}",
                                        recoverable=False),
                knobs_adjusted=pending_adjustments))
            return NegotiationResult(rounds=rounds,
                                     termination=TerminationType.breakdown,
                                     rounds_used=round_no)

        concern = _find_concern(review, state.resolved)
        progress.log("협상", f"R{round_no} 검토 결과 — {review.decision.value}"
                     + (f" / 막힌 차원: {concern.value}" if concern else " / 미해소 차원 없음"))
        accepted = (review.decision in {DecisionType.recommend, DecisionType.conditional}
                    and concern is None)
        if accepted:
            progress.log("협상", f"R{round_no} ✅ 합의 — 종료")
            rounds.append(NegotiationRound(
                round=round_no, proposal=proposal, response=RoundResponse.accept,
                knobs_adjusted=pending_adjustments))
            return NegotiationResult(rounds=rounds,
                                     termination=TerminationType.agreement,
                                     rounds_used=round_no)
        if review.decision == DecisionType.terminate or concern is None:
            rounds.append(NegotiationRound(
                round=round_no, proposal=proposal, response=RoundResponse.reject,
                rejection=RejectionInfo(dimension=concern or Dimension.resource_complementarity,
                                        reason=review.decision_rationale,
                                        recoverable=False),
                knobs_adjusted=pending_adjustments))
            return NegotiationResult(rounds=rounds,
                                     termination=TerminationType.breakdown,
                                     rounds_used=round_no)

        # 풀리는 거절 → 손잡이 묶음 동시 조정 후 재제안 (NEG-04)
        bundle = _KNOB_BUNDLES[concern]
        adjustments: list[KnobAdjustment] = []
        for knob, _default, to_value in bundle:
            current = state.knobs[knob]
            if current == to_value:
                continue
            # 판매자 최저선 검사 (NEG-06): 최저선에 도달한 손잡이는 더 움직이지 않는다
            if knob in floors and current == floors[knob]:
                continue
            adjustments.append(KnobAdjustment(knob=knob, **{"from": current}, to=to_value))
            state.knobs[knob] = to_value
        state.resolved.add(concern.value)
        progress.log("협상", f"R{round_no} 풀리는 거절 — 손잡이 묶음 조정: "
                     + (", ".join(f"{a.knob}→{a.to}" for a in adjustments) or "조정 없음"))

        rounds.append(NegotiationRound(
            round=round_no, proposal=proposal, response=RoundResponse.counter,
            rejection=RejectionInfo(
                dimension=concern,
                reason=f"막힌 차원: {concern.value} — 손잡이 조정으로 해소 가능",
                recoverable=True),
            knobs_adjusted=pending_adjustments))
        pending_adjustments = adjustments   # 다음 라운드 제안에 반영된 조정 기록

    # 라운드 상한 도달 (NEG-05) — 무한 협상 방지
    return NegotiationResult(rounds=rounds,
                             termination=TerminationType.round_limit,
                             rounds_used=req.max_rounds)
