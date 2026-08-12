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
  비워두지 마라 — 부분 프로필에는 반드시 미확인 항목이 있다."""

INSIGHT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["observed_needs", "need_evidence", "value_bridge",
                 "personalization_hooks", "uncertainties"],
    "properties": {k: {"type": "array", "items": {"type": "string"}}
                   for k in ("observed_needs", "need_evidence", "value_bridge",
                             "personalization_hooks", "uncertainties")},
}


def insight_user(requester: Profile, intent: Intent, candidate: Profile,
                 pain_signal: str, source_urls: list[str]) -> str:
    return (f"[요청 기업]\n{requester.basic.name} — {requester.description}\n"
            f"솔루션: {requester.solution.value}\n"
            f"제안 내용: {intent.notes or intent.proposal_type or '미지정'}\n\n"
            f"[후보 기업]\n{candidate.basic.name} ({candidate.basic.country}, "
            f"{candidate.basic.industry})\n{candidate.description}\n"
            f"관측된 수요 신호: {pain_signal or '없음'}\n"
            f"출처: {', '.join(source_urls) or '없음'}")


def build_insight(extractor, candidate_id: str, requester: Profile,
                  intent: Intent, candidate: Profile,
                  pain_signal: str = "",
                  source_urls: "list[str] | None" = None) -> CandidateInsight:
    urls = source_urls or []
    data = extractor.extract_json(
        INSIGHT_SYSTEM,
        insight_user(requester, intent, candidate, pain_signal, urls),
        INSIGHT_SCHEMA, deep=False)   # 판정이 아니라 정리 — 얕은 경로면 충분
    return CandidateInsight(candidate_id=candidate_id, source_urls=urls, **data)
