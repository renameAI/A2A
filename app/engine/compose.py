"""Compose — 엔진 추론 → 사람이 쓸 글 (CMP-01~07, 기획서 8장).

핵심 주장은 Judge 적합근거에 추적 가능해야 한다 (CMP-02, claim_trace).
메시지는 '수신자가 사는 가치'의 언어로 (CMP-03 — 내가 파는 가치 ≠ 상대가 사는 가치).
Sales=밀어내기(A/B 다수·톤 있음) / Purchase=끌어당기기(1안·톤 없음) 비대칭 (CMP-05).
사람 승인 없는 발송 절대 금지 — 초안까지만 (CMP-06, send_blocked 항상 true).
"""
from ..config import get_settings
from ..schemas import (ClaimTrace, ComposeMode, ComposeRequest, ComposeResponse,
                       ComposedMessage, Lens)
from .llm import get_extractor
from .prompts import COMPOSE_SCHEMA, COMPOSE_SYSTEM, compose_user

_TONES = ["정중하고 신뢰감 있게", "간결하고 직접적으로"]


def _outreach_body(req: ComposeRequest, tone: str) -> tuple[str, list[ClaimTrace]]:
    jr = req.judge_result
    counter = req.counterpart_profile
    claims: list[ClaimTrace] = []
    lines = [f"{counter.basic.name} 담당자님께,", ""]
    # 첫 문단: 상대가 겪는 문제 관점 (수신자가 사는 가치의 언어, CMP-03)
    lines.append(f"귀사가 겪고 계실 '{counter.problem_solved.value}' 문제에 대해 "
                 f"구체적인 제안을 드리고자 연락드립니다.")
    # 본문 주장: fit_reasons 에서만 가져온다 — 근거 없는 주장 금지 (CMP-02)
    for i, reason in enumerate(jr.fit_reasons[:3]):
        claim = f"저희가 도울 수 있는 이유: {reason}"
        lines.append(f"- {claim}")
        claims.append(ClaimTrace(claim=claim, fit_reason_ref=f"fit_reasons[{i}]"))
    # Reference 신뢰장치 (CMP-07)
    ref = jr.match_summary.reference
    if ref == "first_case":
        lines.append("귀사가 이 시장의 첫 사례가 됩니다 — 그만큼 검증 장치(소규모 PoC·"
                     "성과 데이터 공유)를 함께 제안드립니다.")
    else:
        lines.append(f"유사 사례로 '{ref}'를 공유드릴 수 있습니다.")
    if jr.deal_structure:
        lines.append(f"시작 제안: {jr.deal_structure}")
    lines += ["", "짧은 미팅으로 자세히 설명드리고 싶습니다.",
              f"({tone} 어조 초안)"]
    return "\n".join(lines), claims


def _summary_body(req: ComposeRequest) -> tuple[str, list[ClaimTrace]]:
    jr = req.judge_result
    claims: list[ClaimTrace] = []
    lines = [f"[추천 요약] {req.counterpart_profile.basic.name}",
             f"결정: {jr.decision.value} — {jr.decision_rationale}", "",
             "적합 근거:"]
    for i, reason in enumerate(jr.fit_reasons):
        lines.append(f"- {reason}")
        claims.append(ClaimTrace(claim=reason, fit_reason_ref=f"fit_reasons[{i}]"))
    if jr.gap_factors:
        lines.append("부족 요인:")
        lines += [f"- {g}" for g in jr.gap_factors]
    if jr.risks:
        lines.append("확인 리스크:")
        lines += [f"- ({r.type.value}) {r.description}" for r in jr.risks]
    if jr.deal_structure:
        lines.append(f"딜 구조: {jr.deal_structure}")
    return "\n".join(lines), claims


def _llm_compose(req: ComposeRequest, extractor) -> ComposeResponse:
    from .. import progress
    progress.log("Compose", f"{req.mode.value} 초안 작성 시작 — "
                            f"수신자가 사는 가치의 언어로 번역")
    data = extractor.extract_json(COMPOSE_SYSTEM, compose_user(req), COMPOSE_SCHEMA)
    messages = [ComposedMessage.model_validate(m) for m in data["messages"]]
    limit = req.variants if req.lens == Lens.sell else 1   # CMP-05 비대칭 강제
    return ComposeResponse(messages=messages[:limit] or messages,
                           send_blocked=True)              # CMP-06 항상 차단


def _send_gate(resp: ComposeResponse) -> ComposeResponse:
    from .. import progress
    with progress.node("sendgate", "사람 승인 게이트 (CMP-06)"):
        progress.log("Compose", f"초안 {len(resp.messages)}건 생성 — "
                                "send_blocked=true, 발송은 사람 승인 후")
    return resp


def compose(req: ComposeRequest) -> ComposeResponse:
    from .. import progress
    extractor = get_extractor(get_settings())
    if extractor is not None:
        with progress.node("compose.llm", "메시지 생성 (LLM)"):
            resp = _llm_compose(req, extractor)
        return _send_gate(resp)

    # Purchase = 끌어당기기: 1안·톤 없음 (CMP-05)
    with progress.node("compose.template", "메시지 생성 (템플릿·Mock)"):
        variants = req.variants if req.lens == Lens.sell else 1
        messages: list[ComposedMessage] = []

        for v in range(variants):
            tone = (req.tone or _TONES[v % len(_TONES)]) if req.lens == Lens.sell else "표준"
            if req.mode == ComposeMode.outreach:
                body, claims = _outreach_body(req, tone)
                title = (f"{req.counterpart_profile.problem_solved.value} — "
                         f"{req.self_profile.basic.name}의 제안")
            else:
                body, claims = _summary_body(req)
                title = f"추천 요약: {req.counterpart_profile.basic.name}"
            messages.append(ComposedMessage(
                variant_label=chr(ord("A") + v),
                title=title,
                body=body,
                claim_trace=claims,
                reference_used=req.judge_result.match_summary.reference,
            ))
    return _send_gate(
        ComposeResponse(messages=messages, send_blocked=True))   # CMP-06
