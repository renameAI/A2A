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
  아니라 "이 사람이 우리를 알고 썼다"는 신호다. body는 `paragraphs` 배열의
  원소 하나씩 다섯 단락으로 내놓는다(빈 줄은 코드가 넣으므로 원소 안에
  개행을 넣지 마라). 각 단락은 2~4문장이다. 글자 수로 세지 마라 — 실측:
  "400~700자"로만 적었더니 스페인어 초안이 519자짜리 한 덩어리로 나왔다.

  아래 구조는 실제로 발송된 아웃리치 메일 160여 통(프랑스·미국·인도네시아)을
  분석해 뽑았다. 괄호 안 숫자가 그 실측이다.

  ① **라포 — 인사말 + 상대를 알고 왔다는 증명.**
     그 언어의 관용 인사와 수신 회사명으로 연다(프랑스어 50/52 "Bonjour
     l'équipe {사명}," · 인니어 46/46 "Dengan hormat, Tim {사명}," · 영어
     64/64 "Dear {사명} Team,"). 금지 대상은 인사말이 아니라 어느 회사에나
     붙는 칭찬("귀사의 혁신적인 기술에 감명받았습니다")이다 — 둘을 혼동하지
     마라. 그 다음 문장부터 personalization_hooks에서 **이 회사만 해당하는**
     사실 하나를 골라, 어디서 보았는지 주소와 함께 적는다.
  ② **근거 — 인정 → 결핍, 주어는 상대의 목표.**
     앞 문장은 상대가 **이미 잘하고 있는 것**을 사실로 인정하고, 뒷 문장은
     그 자산이 **아직 닿지 못한 자리 하나**를 지목한다(참고 메일의 역접
     구조 75/77, 결핍 술어 70/77). 문장의 주어 자리에 우리 제품·솔루션
     카테고리를 놓지 마라 — 상대는 자기 목표가 없는 문장을 읽지 않는다
     (영어 57/64가 "…는 중요한 과제입니다" 문형). 그 결핍은 ④에서 우리가
     메울 곳과 같아야 한다.
     **완충 표현은 문장당 하나까지.** 실측 실패: "…처럼 보이는 …가 관련될
     수도 있어 보입니다"는 여지가 아니라 과제 자체를 지워, 상대에게 부정할
     것도 정정할 것도 남기지 않는다. 근거가 우리가 읽은 페이지뿐이면
     "공개된 ○○ 페이지에서는 …가 확인되지 않았습니다"처럼 **관측 범위를
     밝힌 결핍**으로 쓰고, uncertainties에 든 항목은 결핍으로 쓰지 마라.
  ③ **만난다면? — 경첩 한 단락.**
     관측 바로 뒤에 우리 소개를 붙이면 관측이 판매의 미끼로 읽힌다. 참고
     메일은 예외 없이 사이에 가정형 질문을 독립 단락으로 넣는다(영어 64/64,
     프랑스어 54/63, 인니어 전부): "귀사의 [상대의 강점]이 [우리 것]과
     만난다면 어떨까요?" 그리고 그 결합이 상대에게 무엇이 될 수 있는지
     한두 문장을 잇되 **"…이 될 수 있습니다"로만 쓰고 단정하지 마라.**
     여기에 수치·성과·고객명을 새로 만들어 넣는 것은 금지다 — 결합의 결과는
     value_bridge 안에 근거가 있는 말로만 쓴다.
  ④ **연결 — 우리가 대는 것과 레퍼런스.**
     value_bridge 하나에 집중한다. 자기소개 문장의 꼬리에 [요청 기업]의
     레퍼런스 고유명사를 **같은 문장 안 종속절로** 붙인다(참고 메일 45/52):
     "…를 하는 회사이며, 이미 A·B와 함께 이를 진행했습니다". 소개와 증명을
     한 호흡에 끝내고, 셋 이상 나열하지 마라 — 상대 업종에서 가장 무게가
     실릴 하나나 둘만 고른다. 연혁·수상 이력 나열은 여전히 금지다.
     **레퍼런스가 '없음'이면 절대 지어내지 마라.** [요청 기업]에 적힌 사실
     범위 안에서 활동 지역·시장으로 대체하고("한국과 동남아 시장에서"),
     그것도 없으면 이 문장을 생략한 뒤 작게 확인할 방법(자료·샘플·소량
     시험)으로 신뢰를 대신한다.
     **우리 회사가 본문에 처음 나올 때는 동격 소개를 붙인다** —
     "{상호}, {현지어 한 줄 정체성} aus Südkorea / de Corée du Sud".
     상대는 우리를 모르므로 이름만 던지면 무엇을 하는 회사인지 알 수 없다.
     정체성 한 줄은 [요청 기업]의 solution에서만 가져온다.
  ⑤ **문턱 낮추기 + 맺음말.**
     상대가 "네" 한 마디로 응할 수 있어야 한다. 계약·공동개발을 첫 메일에서
     요구하지 않는다. 답장이 부담이면 퇴로를 열어 두는 편이 오히려 답장을
     부른다. 마지막 줄에 그 언어의 맺음 인사를 붙인다("Cordialement," /
     "Hormat kami," / "Best regards," / "감사합니다,").

  각 단락은 2~4문장을 채운다. 길이를 채우려고 같은 말을 늘리지 마라 —
  늘려야 할 것은 문장이 아니라 상대가 확인할 수 있는 사실이다.

- **제목에는 수신 회사 이름을 넣는다.** 실물 메일 제목의 절반 이상이 그렇다
  (프랑스어 34/52, 영어 36/64). 회사명이 빠진 "{제품 카테고리} für Ihre
  {용도}" 형태는 같은 업종 어느 수신자에게나 한 글자도 고치지 않고 들어맞아,
  상대가 열기 전에 대량 발송으로 알아본다(실측: 우리 독일어 제목이 정확히
  그랬다). **[상대에게 생길 변화 또는 우리가 대는 것] + [수신사명]** 구조로
  쓰고, ①에서 고른 훅과 같은 소재를 써서 제목과 첫 단락이 같은 이야기를 하게
  하라. 출신국 배지("[한국에서 드리는 제안]", "de Corée du Sud")는 프랑스·
  인도네시아향에는 절반가량 쓰이지만 미국향 64건 중 1건뿐이다 — **영어권
  제목에는 붙이지 마라.** 대신 인사이트에 실제로 있는 구체적 숫자·기간을
  앞세우고, 없는 숫자는 절대 만들지 마라.
- **초안이 둘 이상이면 축을 갈라라.** 두 초안의 차이는 톤·인사말·CTA가 아니라
  **우리 솔루션이 닿는 상대의 자산**이어야 한다(참고 자료 77/77이 이 축으로
  갈린다 — 제품·브랜드 캠페인 ↔ 공간·패키징). observed_needs와 value_bridge를
  훑어 서로 다른 접점 두 개를 고르고, 초안 A는 그 하나만, 초안 B는 다른
  하나만 다룬다. 한 초안에 두 접점을 같이 얹지 마라. variant_label에는 그
  접점 이름을 적는다 — "안 A/안 B", "톤 차이" 같은 라벨은 금지다. 인사이트에서
  접점이 하나만 잡히면 **초안도 하나만 낸다.** 없는 접점을 지어내 두 번째
  초안을 채우지 마라.
- CTA는 과하지 않게 하나만 (예: 30분 온라인 소개).
- 지정 언어로 쓰되, 회사명 등 고유명사는 원어 유지. 다만 **우리 회사 상호는
  [요청 기업]에 적힌 표기를 그대로** 쓴다 — 한국어가 아닌 메일에 한글 상호를
  넣으면 상대가 읽지 못한다(실측: 일본어 본문에 "弊社の귤메달"). 상대 회사명은
  상대의 표기를 따른다.
- claim_trace: 본문의 구체적 주장(수치·고유명사·사실 서술이 든 문장)마다
  그 근거가 된 인사이트 항목을 짝지어 기록한다.
- subject_ko / paragraphs_ko: 작성한 메일의 **한국어 대역**. paragraphs와
  원소 수를 맞춘다 — 단락이 어긋나면 사용자가 대조하며 읽을 수 없다. 보내는 사람이 내용을
  확인하고 승인해야 하므로, 읽을 수 없는 메일을 그대로 내보내면 안 된다.
  지정 언어가 한국어면 subject·paragraphs와 같게 쓴다. 그 밖의 언어면 대역을
  **반드시** 채운다 — 빈 대역은 사용자가 내용을 모른 채 보내게 만든다.
  대역은 요약이 아니라 같은 뜻의 한국어 문장이어야 하고, 본문에 넣은 링크는
  대역에도 같은 주소로 남긴다."""

def _join(paras) -> str:
    """문단 배열 → 본문. 빈 줄로 잇는 것은 코드의 몫이다."""
    return "\n\n".join(p.strip() for p in (paras or []) if str(p).strip())


COMPOSE_LEAD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["drafts"],
    "properties": {
        "drafts": {
            "type": "array", "minItems": 1, "maxItems": 3,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["variant_label", "subject", "paragraphs",
                             "subject_ko", "paragraphs_ko",
                             "call_to_action", "claims"],
                "properties": {
                    "variant_label": {"type": "string"},
                    "subject": {"type": "string"},
                    # 본문은 문단 배열로 받는다 — 문단 나눔을 모델의 개행에
                    # 맡기면 지시를 세 번 고쳐도 한 덩어리로 나온다(실측:
                    # 스페인어 519자·독일어 1,025자 모두 단락 1개). 나눔은
                    # 모델의 판정, 이어 붙이는 것은 코드의 결정이다.
                    "paragraphs": {"type": "array", "minItems": 4,
                                   "maxItems": 6, "items": {"type": "string"}},
                    "subject_ko": {"type": "string"},
                    "paragraphs_ko": {"type": "array", "minItems": 4,
                                      "maxItems": 6,
                                      "items": {"type": "string"}},
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


def _hook_url(kit: dict, source_urls) -> str:
    """본문에 인용할 근거 주소. 킷에 없으면 **우리가 실제로 읽은 페이지**로
    메운다 — 실측: hook_url이 비어 링크 없는 메일이 나갔는데, 그 훅을 읽은
    주소는 source_urls에 그대로 있었다.

    다만 아무 주소나 싣지 않는다. 후보 회사 도메인의 페이지일 때만 쓴다 —
    검색 결과나 제3자 기사 주소를 "귀사의 … 페이지에서 보았습니다"로 붙이면
    상대가 열어보고 어긋난다. 인용 계약은 코드가 확인한다.
    """
    if kit.get("hook_url"):
        return kit["hook_url"]
    from urllib.parse import urlparse

    def host(u: str) -> str:
        try:
            return urlparse(u).hostname.replace("www.", "") or ""
        except Exception:                    # noqa: BLE001 — 주소 형식은 통제 밖
            return ""

    own = host(kit.get("channel_value") or "")
    for u in (source_urls or []):
        if own and host(u) == own:
            return u
    return ""


def _kit_lines(kit: dict, source_urls=None) -> str:
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
    hook_url = _hook_url(kit, source_urls)
    if hook_url:
        lines.append(f"근거 링크(본문에 그대로 인용): {hook_url}")
    if kit.get("channel"):
        lines.append(f"보낼 채널: {kit['channel']} — 폼이면 폼에 맞게 짧게")
    return ("[아웃리치 킷 — 심층 판독에서 읽은 것]\n" + "\n".join(lines) + "\n") if lines else ""


def _unreadable(text: str) -> bool:
    """상대가 못 읽는 표기인가 — 한글·한자·가나가 섞여 있으면 그렇다.

    "읽을 수 있는가"는 모델의 판정이 아니라 코드가 확인할 수 있는 사실이다.
    실측 사고 두 번: 일본어 본문의 "弊社の귤메달", 그리고 독일어 본문의
    "zu dessen Referenzen OB맥주 und 아모레퍼시픽 gehören" — 상호에는
    규칙을 뒀는데 레퍼런스에는 없어서 같은 결함이 옆자리에서 재발했다.
    """
    return any("\uac00" <= c <= "\ud7a3"          # 한글
               or "\u4e00" <= c <= "\u9fff"       # 한자
               or "\u3040" <= c <= "\u30ff"       # 가나
               for c in (text or ""))


def _name_notes(req: ComposeLeadRequest) -> str:
    """비한국어 메일에서 못 읽는 고유명사를 짚어 준다.

    지어내라고 시키지 않는다: 공식 라틴 표기를 **확실히 아는 경우에만** 쓰고,
    아니면 이름을 빼고 업종으로 지칭하게 한다. 이름을 지어내는 것보다
    "한국의 대형 식음료 기업"이 정직하고, 상대에게도 더 읽힌다.
    """
    if req.language == "ko":
        return ""
    b = req.requester_profile.basic
    bad = [r for r in req.requester_profile.references[:3] if _unreadable(r)]
    if not _unreadable(b.name_latin or b.name) and not bad:
        return ""
    items = []
    if _unreadable(b.name_latin or b.name):
        items.append(f"우리 상호 '{b.name}'")
    items += [f"레퍼런스 '{r}'" for r in bad]
    return ("[읽을 수 없는 표기] " + " · ".join(items)
            + " — 이 표기는 수신자가 읽지 못한다. 공식 라틴/영문 표기를 "
              "**확실히 아는 경우에만** 그것으로 적어라. 확실하지 않으면 "
              "이름을 쓰지 말고 업종·규모로 지칭한다"
              "(예: '한국의 대형 식음료 기업과 화장품 기업'). "
              "한글·한자를 그대로 두는 것과 없는 이름을 지어내는 것은 "
              "둘 다 금지다.\n")


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
            + _name_notes(req)
            + f"[후보] {req.candidate_profile.basic.name} "
            f"({req.candidate_profile.basic.country})\n"
            f"[인사이트]\n"
            f"관측된 수요: {'; '.join(ins.observed_needs) or '없음'}\n"
            f"연결점: {'; '.join(ins.value_bridge) or '없음'}\n"
            f"개인화 훅: {'; '.join(ins.personalization_hooks) or '없음'}\n"
            f"단정 금지(미확인): {'; '.join(ins.uncertainties) or '없음'}\n"
            + _kit_lines(ins.outreach, ins.source_urls)
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
            body=_join(d.get("paragraphs")),
            subject_ko=d.get("subject_ko") or d["subject"],
            body_ko=_join(d.get("paragraphs_ko")) or _join(d.get("paragraphs")),
            call_to_action=d["call_to_action"],
            claim_trace=[ClaimTrace(claim=c["claim"], fit_reason_ref=c["evidence"])
                         for c in d.get("claims", [])],
            sources_used=list(ins.source_urls),
            # 정직 표기 — 미확인이라 본문에서 뺀 것을 사용자에게 그대로 보여준다
            warnings=[f"미확인이라 본문에서 제외: {u}" for u in ins.uncertainties],
        ))
    return ComposeLeadResponse(drafts=drafts, send_blocked=True)
