"""Candidate Insight (이슈 #6-E, 기획서 §7) — Retrieve와 Compose 사이 근거 계층.

적합·부적합을 결정하지 않는다. 후보의 공개 정보에서 관측된 수요 신호와
요청 기업 솔루션의 연결점만 만든다. 관측(원문에 있는 것)과 추론을 분리하고,
확인 안 된 것은 uncertainties로 넘겨 Compose가 본문에서 빼게 한다.
"""
from ..schemas import CandidateInsight, Intent, Profile
from .prompts import HARD_RULES

INSIGHT_SYSTEM = HARD_RULES + """

당신은 B2B 리드 리서처다. 후보 기업의 공개 정보에서 **관측 가능한 잠재 수요**와
요청 기업 솔루션의 연결점을 만든다. 이것은 판정이 아니다 — 적합/부적합을 말하지 않는다.

규율:
- observed_needs: 후보 자료에 실제로 나타난 수요 신호만. 없으면 빈 배열.
- need_evidence: 각 수요의 근거가 된 원문 문장의 요지. 지어내면 환각이다.
- value_bridge: "후보의 문제 X ↔ 요청 기업의 솔루션 Y" 형태의 연결 문장.
  요청 기업이 실제로 제공하는 솔루션만 연결한다.
- personalization_hooks: 이메일 첫 문장에 쓸 수 있는 후보의 구체적 사실 1~3개.
- uncertainties: 사실로 확인되지 않아 이메일에서 단정하면 안 되는 것들.
  비워두지 마라 — 부분 프로필에는 반드시 미확인 항목이 있다.
- outreach(아웃리치 킷): 실제로 연락이 닿게 하는 네 가지.
    to_role      누구에게 — 자료에 드러난 역할(파트너십 담당·구매·대표 등).
                 이름을 지어내지 마라. 역할이 안 보이면 빈 문자열.
    channel      어디로 — 아래 [접점]에 실제로 있는 채널만(대표 메일·문의 폼·
                 파트너 모집 페이지·영업팀…). 없으면 빈 문자열.
    channel_value 그 채널의 값(주소·URL). [접점]에 있는 값 그대로.
    why_now      왜 지금인가 — [타이밍 신호]에서 가장 최근·구체적인 것 하나를
                 한 문장으로. 신호가 없으면 빈 문자열(억지로 만들지 마라).
    hook         첫 문장 — 상대가 '우리를 봤구나' 느낄 구체적 사실 하나.
    hook_url     그 사실을 읽은 페이지 주소. 위 [타이밍 신호]의 '출처'에 있는
                 주소만 쓴다 — 없으면 빈 문자열. 메일이 "여기서 봤습니다"라며
                 링크를 다는 데 쓰이므로, 지어낸 주소는 상대가 열어보는 순간
                 어긋난다.
  이 넷은 전부 [접점]·[타이밍 신호]·[후보 자료]에서만 나온다. 자료에 없는
  채널·역할·사실을 채우면 이메일이 거짓말이 된다."""

_OUTREACH_KEYS = ("to_role", "channel", "channel_value", "why_now",
                  "hook", "hook_url")

INSIGHT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["observed_needs", "need_evidence", "value_bridge",
                 "personalization_hooks", "uncertainties", "outreach"],
    "properties": {
        **{k: {"type": "array", "items": {"type": "string"}}
           for k in ("observed_needs", "need_evidence", "value_bridge",
                     "personalization_hooks", "uncertainties")},
        "outreach": {"type": "object", "additionalProperties": False,
                     "required": list(_OUTREACH_KEYS),
                     "properties": {k: {"type": "string"} for k in _OUTREACH_KEYS}},
    },
}


def _ontology_block(ont: "dict | None") -> str:
    """심층 판독 결과를 프롬프트 재료로. 없으면 빈 문자열 — 없는 것을 있는 척 안 한다."""
    if not ont:
        return "\n[접점] 없음 (사이트 미판독)\n[타이밍 신호] 없음"
    contacts = ont.get("contacts") or []
    signals = ont.get("signals") or []
    axes = ont.get("axes") or {}
    c_lines = [f"- {c.get('channel','')}: {c.get('value','')}"
               + (f" ({c.get('role_hint')})" if c.get("role_hint") else "")
               for c in contacts] or ["없음"]
    s_lines = [f"- [{s.get('category','')}] {s.get('evidence','')}"
               + (f" ({s.get('observed_at')})" if s.get("observed_at") else "")
               + (f" · 출처 {s.get('source_url')}" if s.get("source_url") else "")
               for s in signals[:8]] or ["없음"]
    a_lines = [f"- {k}: {v.get('value','')}" for k, v in axes.items()
               if v.get("status") == "confirmed" and v.get("value")][:8]
    wn = (ont.get("why_now") or "").strip()
    wn_block = (f"\n[왜 지금 — 판독이 이미 고른 근거. 특별한 이유가 없으면 "
                f"이것을 그대로 쓴다]\n{wn}"
                + (f"\n출처 {ont.get('why_now_source')}"
                   if ont.get("why_now_source") else "")) if wn else ""
    return (wn_block
            + "\n[접점 — 여기 있는 채널만 쓴다]\n" + "\n".join(c_lines)
            + "\n[타이밍 신호 — 최근 관측]\n" + "\n".join(s_lines)
            + ("\n[확인된 축]\n" + "\n".join(a_lines) if a_lines else ""))


def insight_user(requester: Profile, intent: Intent, candidate: Profile,
                 pain_signal: str, source_urls: list[str],
                 ontology: "dict | None" = None) -> str:
    return (f"[요청 기업]\n{requester.basic.name} — {requester.description}\n"
            f"솔루션: {requester.solution.value}\n"
            f"제안 내용: {intent.notes or intent.proposal_type or '미지정'}\n\n"
            f"[후보 기업]\n{candidate.basic.name} ({candidate.basic.country}, "
            f"{candidate.basic.industry})\n{candidate.description}\n"
            f"관측된 수요 신호: {pain_signal or '없음'}\n"
            f"출처: {', '.join(source_urls) or '없음'}"
            + _ontology_block(ontology))


def build_insight(extractor, candidate_id: str, requester: Profile,
                  intent: Intent, candidate: Profile,
                  pain_signal: str = "",
                  source_urls: "list[str] | None" = None,
                  ontology: "dict | None" = None) -> CandidateInsight:
    urls = source_urls or []
    data = extractor.extract_json(
        INSIGHT_SYSTEM,
        insight_user(requester, intent, candidate, pain_signal, urls, ontology),
        INSIGHT_SCHEMA, deep=False,
        allow_foreign=True)   # 판정이 아니라 정리 — 얕은 경로면 충분
    kit = {k: (data.get("outreach") or {}).get(k, "") or "" for k in _OUTREACH_KEYS}
    # 인용 계약 — 채널 값은 접점 목록에 실제로 있어야 한다. 모델이 그럴듯한
    # 메일 주소를 지어내면 이메일이 허공으로 간다.
    known = {(c.get("value") or "").strip()
             for c in ((ontology or {}).get("contacts") or [])}
    if kit["channel_value"] and kit["channel_value"].strip() not in known:
        kit["channel_value"] = ""
        kit["channel"] = ""
    # 근거 링크도 같은 계약 — 판독이 실제로 읽은 페이지만 인용한다.
    seen_urls = {(g.get("source_url") or "").strip()
                 for g in ((ontology or {}).get("signals") or [])} | set(urls)
    if kit["hook_url"] and kit["hook_url"].strip() not in seen_urls:
        kit["hook_url"] = ""
    data["outreach"] = kit
    return CandidateInsight(candidate_id=candidate_id, source_urls=urls, **data)
