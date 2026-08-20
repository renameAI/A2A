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
- **설득 구조.** 첫 메일의 목적은 소개가 아니라 **답장 한 통**이다. 상대는
  모르는 회사의 메일을 3초 안에 버릴지 정하고, 그 3초를 넘기는 것은 길이가
  아니라 "이 사람이 우리를 알고 썼다"는 신호다. body는 **빈 줄로 나눈 네
  단락**(단락 사이 개행 두 번)이고 각 단락은 2~4문장이다. 글자 수로 세지
  마라 — 실측: "400~700자"로만 적었더니 스페인어 초안이 519자짜리 한
  덩어리로 나왔다. 각 단락은 맡은 일이 다르다:

  ① **라포 — 상대를 알고 왔다는 증명.** personalization_hooks에서 이 회사만
     해당하는 사실 하나를 골라, 어디서 보았는지 주소와 함께 적는다. 어느
     회사에나 붙는 칭찬("귀사의 혁신적인 기술에 감명받았습니다")은 라포가
     아니라 그 반대다 — 상대는 그 문장 하나로 대량 발송을 알아본다. 왜 그
     사실이 눈에 띄었는지 한 문장 덧붙이면 사람이 쓴 글이 된다.
  ② **근거 — 그래서 지금 무엇이 필요해 보이는가.** observed_needs를 상대의
     말로 옮기고, why_now가 있으면 여기서 짚는다. 단정하지 말고 관측을
     보여준 뒤 "…한 상황으로 보입니다"로 여지를 남긴다. 틀렸다면 상대가
     고쳐주고, 그 정정이 곧 답장이다.
  ③ **연결 — 우리가 대는 것이 그것과 맞닿는 지점.** value_bridge 하나에
     집중한다. 우리 소개는 이 단락으로 끝이고, 연혁·수상 이력·제품군 나열은
     넣지 마라. 여기서 신뢰 장치를 함께 건다 — 비슷한 사례가 있으면 한 줄로,
     없으면 숨기지 말고 작게 확인할 방법(샘플·소량 시험·자료 공유)을 제안한다.
  ④ **문턱 낮추기 — 다음 행동 하나.** 상대가 "네" 한 마디로 응할 수 있어야
     한다. 계약·공동개발을 첫 메일에서 요구하지 않는다. 답장이 부담이면
     "관심 없으시면 회신 주지 않으셔도 됩니다" 같은 퇴로를 열어 두는 편이
     오히려 답장을 부른다.

  길이를 채우려고 같은 말을 늘리지 마라. 늘려야 할 것은 문장이 아니라 상대가
  확인할 수 있는 사실이다 — 근거가 둘뿐이면 짧은 편이 낫다.
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
    else:
        # 신호가 없을 때 모델이 "요즘·마침·최근" 같은 가짜 시의성을 만드는 것이
        # 최악이다 — 상대는 자기 회사에 그런 일이 없었음을 안다.
        lines.append("최근 신호 없음 — '요즘·마침·최근 …하신 것을 보고' 류의 "
                     "시의성 표현을 만들지 마라. 상시 제안으로 정직하게 쓴다")
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
