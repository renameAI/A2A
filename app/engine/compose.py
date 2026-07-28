"""Compose — 엔진 추론 → 사람이 쓸 글 (CMP-01~07, 기획서 8장).

핵심 주장은 Judge 적합근거에 추적 가능해야 한다 (CMP-02, claim_trace).
메시지는 '수신자가 사는 가치'의 언어로 (CMP-03 — 내가 파는 가치 ≠ 상대가 사는 가치).
Sales=밀어내기(A/B 다수·톤 있음) / Purchase=끌어당기기(1안·톤 없음) 비대칭 (CMP-05).
사람 승인 없는 발송 절대 금지 — 초안까지만 (CMP-06, send_blocked 항상 true).
"""
import re

from ..config import get_settings
from ..errors import EngineError
from ..schemas import (ClaimTrace, ComposeMode, ComposeRequest, ComposeResponse,
                       ComposedMessage, Lens)
from .llm import get_extractor
from .prompts import COMPOSE_SCHEMA, COMPOSE_SYSTEM, compose_user

_TONES = ["정중하고 신뢰감 있게", "간결하고 직접적으로"]
_TRACE_PAREN_RE = re.compile(
    r"\s*(?:"
    r"\(\s*fit_reason_ref\s*:\s*fit_reasons\s*\[\s*\d+\s*\]\s*\)"
    r"|\[\s*fit_reason_ref\s*:\s*fit_reasons\s*\[\s*\d+\s*\]\s*\]"
    r"|\(\s*reference(?:_used)?\s*:[^)]*\)"
    r"|\[\s*reference(?:_used)?\s*:[^\]]*\]"
    r")",
    re.IGNORECASE,
)
_FIT_REASON_TOKEN_RE = re.compile(
    r"fit_reasons\s*\[\s*(\d+)\s*\]", re.IGNORECASE)
_INTERNAL_LABEL_RE = re.compile(
    r"\b(?:fit_reason_ref|claim_trace|reference_used|variant_label)\b\s*:?",
    re.IGNORECASE)
_REFERENCE_LABEL_RE = re.compile(r"\breference\s*:\s*", re.IGNORECASE)
_VARIANT_HEADER_RE = re.compile(
    r"^\s*(?:변형\s+)?(?:variant[_\s-]*)?[A-E]\s*[—–:-]",
    re.IGNORECASE,
)
_TRACE_REF_RE = re.compile(r"^fit_reasons\[(\d+)\]$")


def _public_body(body: str) -> str:
    """메일 본문에서 엔진 내부 추적 표식만 제거한다.

    reference 자체는 수신자에게 필요한 신뢰 근거이므로 자연어 문장은 보존하고,
    ``(reference: ...)``·``(fit_reason_ref: ...)`` 같은 기계용 주석만 없앤다.
    일반 괄호 문장까지 지우지 않도록 내부 키 이름이 있는 패턴으로 한정한다.
    """
    lines = body.strip().splitlines()
    if lines and _VARIANT_HEADER_RE.match(lines[0]):
        if len(lines) > 1:
            lines = lines[1:]
        else:
            lines[0] = _VARIANT_HEADER_RE.sub("", lines[0], count=1)
    cleaned = "\n".join(lines)
    cleaned = _TRACE_PAREN_RE.sub("", cleaned)
    cleaned = _FIT_REASON_TOKEN_RE.sub("", cleaned)
    cleaned = _INTERNAL_LABEL_RE.sub("", cleaned)
    cleaned = _REFERENCE_LABEL_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+([,.!?])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _finalize_response(req: ComposeRequest,
                       response: ComposeResponse) -> ComposeResponse:
    """LLM 출력을 공개 본문과 내부 근거 메타데이터로 최종 분리한다."""
    from .. import progress

    limit = req.variants if req.lens == Lens.sell else 1
    messages = response.messages[:limit] or response.messages
    separated = 0
    reason_count = len(req.judge_result.fit_reasons)
    for index, message in enumerate(messages):
        message.variant_label = chr(ord("A") + index)
        public_body = _public_body(message.body)
        if public_body != message.body.strip():
            separated += 1
        if not public_body:
            raise EngineError(
                502, "compose_invalid_output",
                "메일 본문에서 내부 표식을 분리한 뒤 공개할 문장이 남지 않았습니다.",
            )
        message.body = public_body
        message.claim_trace = [
            trace for trace in message.claim_trace
            if (match := _TRACE_REF_RE.fullmatch(trace.fit_reason_ref))
            and int(match.group(1)) < reason_count
        ]
    if separated:
        progress.log("Compose", f"메일 {separated}건의 내부 판단 표식을 본문 밖으로 분리")
    return ComposeResponse(messages=messages, send_blocked=True)


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
    return _finalize_response(
        req, ComposeResponse(messages=messages, send_blocked=True))


def _send_gate(resp: ComposeResponse) -> ComposeResponse:
    from .. import progress
    with progress.node("sendgate", "사람 승인 게이트 (CMP-06)"):
        progress.log("Compose", f"초안 {len(resp.messages)}건 생성 — "
                                "자동 발송하지 않음, 검토 후 사람이 직접 발송")
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
    return _send_gate(_finalize_response(
        req, ComposeResponse(messages=messages, send_blocked=True)))   # CMP-06
