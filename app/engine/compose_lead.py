"""Compose V2 (이슈 #6-E, 기획서 §8) — JudgeResult 없는 아웃리치 초안.

기존 compose와의 관계: judge_result가 필수인 기존 경로는 그대로 두고(§8.1 —
가짜 판정을 만들어 넘기지 않는다), SaaS 경로는 CandidateInsight를 근거 입력으로
받는 이 모듈을 쓴다. send_blocked=True 고정 — 자동 발송 경로는 존재하지 않는다.
"""
from ..schemas import (ClaimTrace, ComposeLeadRequest, ComposeLeadResponse,
                       LeadEmailDraft)
from .prompts import HARD_RULES

COMPOSE_LEAD_SYSTEM = HARD_RULES + """

당신은 B2B 아웃리치 작성자다. 요청 기업이 후보 기업에게 보낼 첫 메일 초안을 쓴다.

규율:
- 근거는 [인사이트]의 내용만 쓴다. observed_needs·value_bridge·personalization_hooks
  밖의 사실·수치·고객명을 만들면 환각이다.
- uncertainties에 있는 내용은 본문에서 단정하지 마라 — 아예 빼는 것이 기본이다.
- 첫 문장은 personalization_hooks 중 하나로 시작한다 — 템플릿 인사말 금지.
- **무엇을 보고 연락하는지 밝힌다.** 킷에 근거 링크가 있으면 첫 단락에서
  그 주소를 본문에 그대로 적는다("귀사의 https://… 페이지에서 …를 보았습니다").
  링크 없이 "보았습니다"라고만 하면 상대는 확인할 방법이 없어 대량 발송으로
  읽는다. 링크가 없으면 무엇을 보았는지 구체적으로 쓰되 지어낸 주소는 절대
  넣지 마라.
- CTA는 과하지 않게 하나만 (예: 30분 온라인 소개).
- 지정 언어로 쓰되, 회사명 등 고유명사는 원어 유지. 다만 **우리 회사 상호는
  [요청 기업]에 적힌 표기를 그대로** 쓴다 — 한국어가 아닌 메일에 한글 상호를
  넣으면 상대가 읽지 못한다(실측: 일본어 본문에 "弊社の귤메달"). 상대 회사명은
  상대의 표기를 따른다.
- claim_trace: 본문의 구체적 주장(수치·고유명사·사실 서술이 든 문장)마다
  그 근거가 된 인사이트 항목을 짝지어 기록한다.
- subject_ko / body_ko: 작성한 메일의 **한국어 대역**. 보내는 사람이 내용을
  확인하고 승인해야 하므로, 읽을 수 없는 메일을 그대로 내보내면 안 된다.
  지정 언어가 한국어면 subject·body와 같게 쓴다. 그 밖의 언어면 대역을
  **반드시** 채운다 — 빈 대역은 사용자가 내용을 모른 채 보내게 만든다.
  대역은 요약이 아니라 같은 뜻의 한국어 문장이어야 하고, 본문에 넣은 링크는
  대역에도 같은 주소로 남긴다."""

COMPOSE_LEAD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["drafts"],
    "properties": {
        "drafts": {
            "type": "array", "minItems": 1, "maxItems": 3,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["variant_label", "subject", "body",
                             "subject_ko", "body_ko",
                             "call_to_action", "claims"],
                "properties": {
                    "variant_label": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "subject_ko": {"type": "string"},
                    "body_ko": {"type": "string"},
                    "call_to_action": {"type": "string"},
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["claim", "evidence"],
                            "properties": {"claim": {"type": "string"},
                                           "evidence": {"type": "string"}},
                        },
                    },
                },
            },
        },
    },
}


def _kit_lines(kit: dict) -> str:
    """아웃리치 킷 → 작성 지시. 있는 것만 적는다 — 없는 채널·역할을 본문에서
    가정하게 만들면 안 된다."""
    if not kit:
        return ""
    lines = []
    if kit.get("to_role"):
        lines.append(f"받는 사람의 역할: {kit['to_role']} — 그 역할에게 말하듯 쓴다")
    if kit.get("why_now"):
        lines.append(f"왜 지금인가(최근 신호): {kit['why_now']} — 첫 단락에서 이것을 짚는다")
    if kit.get("hook"):
        lines.append(f"첫 문장 훅: {kit['hook']}")
    if kit.get("hook_url"):
        lines.append(f"근거 링크(본문에 그대로 인용): {kit['hook_url']}")
    if kit.get("channel"):
        lines.append(f"보낼 채널: {kit['channel']} — 폼이면 폼에 맞게 짧게")
    return ("[아웃리치 킷 — 심층 판독에서 읽은 것]\n" + "\n".join(lines) + "\n") if lines else ""


def _user(req: ComposeLeadRequest) -> str:
    ins = req.candidate_insight
    b = req.requester_profile.basic
    # 한국어가 아닌 메일에는 상대가 읽을 수 있는 상호를 쓴다. 로마자 표기가
    # 있으면 그것을, 없으면 원 상호를 그대로 둔다(지어내지 않는다).
    sender = (b.name_latin or b.name) if req.language != "ko" else b.name
    return (f"[요청 기업] {sender} — "
            f"{req.requester_profile.solution.value}\n"
            + (f"[표기 주의] 본문에서 우리 회사는 '{sender}'로 적는다. "
               f"다른 표기(원어·음역)를 섞지 마라 — 상대가 같은 회사인지 모른다.\n"
               if sender != b.name else "")
            + f"레퍼런스: {', '.join(req.requester_profile.references[:3]) or '없음'}\n"
            f"[후보] {req.candidate_profile.basic.name} "
            f"({req.candidate_profile.basic.country})\n"
            f"[인사이트]\n"
            f"관측된 수요: {'; '.join(ins.observed_needs) or '없음'}\n"
            f"연결점: {'; '.join(ins.value_bridge) or '없음'}\n"
            f"개인화 훅: {'; '.join(ins.personalization_hooks) or '없음'}\n"
            f"단정 금지(미확인): {'; '.join(ins.uncertainties) or '없음'}\n"
            + _kit_lines(ins.outreach)
            + f"[지시] 언어={req.language} · {req.variants}개 안 · "
            f"어조={req.tone or '정중하고 간결'} · "
            f"CTA={req.intent.call_to_action or '30분 온라인 소개'}")


def compose_lead(extractor, req: ComposeLeadRequest) -> ComposeLeadResponse:
    data = extractor.extract_json(COMPOSE_LEAD_SYSTEM, _user(req),
                                  COMPOSE_LEAD_SCHEMA, deep=False,
                                  allow_foreign=True)
    ins = req.candidate_insight
    drafts = []
    for d in data["drafts"][: req.variants]:
        drafts.append(LeadEmailDraft(
            variant_label=d["variant_label"],
            subject=d["subject"],
            body=d["body"],
            subject_ko=d.get("subject_ko") or d["subject"],
            body_ko=d.get("body_ko") or d["body"],
            call_to_action=d["call_to_action"],
            claim_trace=[ClaimTrace(claim=c["claim"], fit_reason_ref=c["evidence"])
                         for c in d.get("claims", [])],
            sources_used=list(ins.source_urls),
            # 정직 표기 — 미확인이라 본문에서 뺀 것을 사용자에게 그대로 보여준다
            warnings=[f"미확인이라 본문에서 제외: {u}" for u in ins.uncertainties],
        ))
    return ComposeLeadResponse(drafts=drafts, send_blocked=True)
