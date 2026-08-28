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
- **설득 구조는 예시로 배운다.** body는 `paragraphs` 배열의 원소 하나씩
  단락으로 내놓는다(빈 줄은 코드가 넣으므로 원소 안에 개행을 넣지 마라).
  각 단락은 2~4문장. 글자 수로 세지 마라 — 실측: "400~700자"로만 적었더니
  스페인어 초안이 519자짜리 한 덩어리로 나왔다.

  아래는 실제로 발송된 아웃리치 메일이다. **골격만 배우고 소재·표현·업종을
  가져오지 마라** — 이 예시는 아트 콜라보 제안이고, 당신이 쓸 메일은 대개
  다른 업종이다. "What if …?"라는 문형이나 호텔·아트 어휘를 그대로 옮기면
  그것이 곧 어색함이다. 배울 것은 **문단이 맡은 일의 순서**다.

  ─── 예시 (영어, 아트테크 → 부티크 호텔) ───────────────────
  Subject: 14 days. Zero structural changes. +15% ADR. - Idea for The Pearl's rooms

  Dear The Pearl Hotel Team,

  As an exceptional boutique hotel in San Diego that beautifully showcases
  mid-century modern design and authentic local artist collaborations, we
  highly respect The Pearl Hotel's status as a defining local destination.

  We are the DIVE IN team, an art-tech specialist from South Korea that
  transforms standard hotel rooms into immersive, premium art stays through
  exclusive global IP collaborations.

  While successfully maintaining a beautiful mid-century modern aesthetic,
  continuously motivating guests to bypass Online Travel Agencies and book
  directly through your website remains a crucial objective to maximize
  overall profitability amidst San Diego's competitive boutique landscape.

  What if The Pearl's classic Californian vibe met DIVE IN's fully funded
  exclusive room conversion solution? When your spaces are elevated with
  limited-edition global IPs, it creates an absolute reason for your target
  audience to book directly with you.

  Accordingly, the DIVE IN team proposes an exclusive Art Stay Conversion
  partnership designed to eliminate your initial financial burden: …

  Best regards,
  ────────────────────────────────────────────────────────

  **아래 여섯 가지는 필수다. 문장을 어떻게 쓸지는 자유지만, 이 일들이
  본문에 없으면 초안은 미완성이다.** (권고가 아니라 요건이다 — 실측: 이것을
  "가져갈 것"이라고만 적었더니 인사말과 결합 단락이 통째로 사라졌다.)
  ① **인사말과 맺음말을 반드시 넣는다.** 그 언어의 관용 인사 + 수신 회사명으로
     열고(실측: 영어 64/64, 프랑스어 50/52, 인니어 46/46), 관용 맺음말로
     닫는다. 우리 실측 출력에는 둘 다 없었다. 빠뜨리면 안 된다.
  ② **상대를 구체적으로 짚는다** — 이 회사에만 해당하는 사실로. 어느 회사에나
     붙는 칭찬은 그 문장 하나로 대량 발송임이 드러난다.
  ③ **우리가 누구인지 한 문장을 넣는다.** 상대는 우리를 모른다. 상호만 던지지
     말고 "무엇을 하는 어디 회사"인지 동격으로 붙인다.
  ④ **상대의 목표를 주어로** 과제를 세운다. "우리 제품이 도움이 될 수
     있습니다"가 아니라 "…하는 것이 귀사에 중요한 과제입니다"(실측 57/64).
     잘하고 있는 것을 먼저 인정하고, 그 자산이 아직 닿지 못한 자리 하나를
     지목한다(역접 구조 75/77). 상대가 부정하거나 정정할 것이 있어야 답장이
     온다.
  ⑤ **관측과 제안 사이에 한 단락을 반드시 둔다** — 상대의 것과 우리의 것을
     잇는 자리다(영어 64/64, 프랑스어 54/63). 관측 뒤에 소개를 바로 붙이면
     관측이 판매의 미끼로 읽힌다. **문형은 자유다** — 가정형 질문은 이 예시의
     방식일 뿐이고, 업종에 맞는 다른 방식이면 그것이 낫다. 자유인 것은 표현이지
     이 단락의 존재가 아니다.
  ⑥ **제안은 상대가 얻는 것으로 쓰고, 부담 낮은 다음 행동 하나로 닫는다.**

  ①~⑥을 다 담으면 단락은 자연히 다섯 안팎이 된다. 넷 이하로 나왔다면 무엇이
  빠졌는지 확인하라 — 대개 인사말이거나 ⑤의 결합 단락이다.

  **가져오면 안 되는 것**: 과장의 수위. 참고 메일은 "immense admiration",
  "irreplaceable" 같은 말을 쓰지만 우리는 인사이트에 근거가 있는 것만 쓴다.
  뜨겁게 쓰되 사실을 넘지 마라 — 늘려야 할 것은 형용사가 아니라 상대가
  확인할 수 있는 사실이다. **완충 표현은 문장당 하나까지**: "…처럼 보이는
  …가 관련될 수도 있어 보입니다"는 여지가 아니라 과제 자체를 지운다.
- **레퍼런스를 쓴다.** 자기소개 문장의 꼬리에 [요청 기업]의 레퍼런스
  고유명사를 종속절로 붙인다(실측 45/52) — 상호를 대는 순간 상대는 우리를
  확인 가능한 회사로 분류한다. 셋 이상 나열하지 말고, 연혁·수상 이력은
  여전히 금지다. **레퍼런스가 '없음'이면 절대 지어내지 마라** — 활동 지역·
  시장으로 대체하거나, 그것도 없으면 생략하고 작게 확인할 방법(자료·샘플·
  소량 시험)으로 신뢰를 대신한다.
- **제목도 본문과 같은 지정 언어로 쓴다.** subject는 수신자가 받은편지함에서
  보는 첫 글자다 — 여기에 한국어가 들어가면 열리지도 않는다(실측: 독일어
  메일에 "…를 위한 천연고분자 펠렛 제안"이라는 한국어 제목이 나갔다).
  한국어 제목은 subject_ko의 몫이다.
- **제목에는 수신 회사 이름을 넣는다**(실측 프랑스어 34/52, 영어 36/64).
  회사명이 빠진 "{제품 카테고리} für Ihre {용도}" 형태는 같은 업종 어느
  수신자에게나 한 글자도 고치지 않고 들어맞아, 열기 전에 대량 발송으로
  드러난다(실측: 우리 독일어 제목이 정확히 그랬다). 본문 첫 단락에서 고른
  훅과 같은 소재를 써서 제목과 도입이 같은 이야기를 하게 하라.
- **초안이 둘 이상이면 축을 갈라라.** 차이는 톤·인사말·CTA가 아니라 **우리
  솔루션이 닿는 상대의 자산**이어야 한다(참고 자료 77/77이 이 축으로 갈린다).
  variant_label에는 그 접점 이름을 적는다 — "안 A/안 B" 같은 라벨은 금지다.
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


def _readability_warnings(req: ComposeLeadRequest, d: dict) -> list:
    """수신자가 못 읽는 글자가 남았으면 경고로 띄운다.

    프롬프트로 세 번 고쳐도 새어 나오는 자리가 있다 — 상호에서 막으니
    레퍼런스로, 본문에서 막으니 제목으로 옮겨갔다. 프롬프트는 판정을 바꾸지만
    집행은 코드가 한다. 여기서 고치지는 않는다(무엇이 맞는 표기인지는 코드가
    모른다). 다만 사용자가 모르고 보내는 일은 없게 한다.
    """
    if req.language == "ko":
        return []
    out = []
    if _unreadable(d.get("subject") or ""):
        out.append(f"제목에 수신자가 못 읽는 표기가 있어요 — 보내기 전에 "
                   f"확인하세요: {d.get('subject', '')[:60]}")
    for para in (d.get("paragraphs") or []):
        if _unreadable(para):
            out.append(f"본문에 수신자가 못 읽는 표기가 있어요: {para[:60]}")
            break
    return out


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
            warnings=([f"미확인이라 본문에서 제외: {u}"
                       for u in ins.uncertainties]
                      + _readability_warnings(req, d)),
        ))
    return ComposeLeadResponse(drafts=drafts, send_blocked=True)
