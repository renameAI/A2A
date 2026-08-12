"""웹 수집 후보 → CandidateRecord 변환 (이슈 #6-D, 기획서 §6.4).

prospect Represent의 부분 Profile(또는 수집 단계의 얇은 사실)을 기존 Retrieve가
채점할 수 있는 CandidateRecord로 만든다. 규칙은 스킬 score_candidates.py와 동일:
pain_points(검색이 향하는 면)는 관측된 pain_signal 우선, 없으면 설명으로 대체하되
지어내지 않는다.
"""
from ..schemas import PoolKind, Profile
from .pool import CandidateRecord


def candidate_record_from_profile(candidate_id: str, profile: Profile,
                                  source_url: str,
                                  pain_signal: str = "") -> CandidateRecord:
    pain = (pain_signal or "").strip()
    desc = (profile.description or "").strip()
    tags = [t for t in (profile.basic.industry, profile.basic.country)
            if t and t != "unknown"]
    if source_url:
        tags.append(source_url)   # 근거 태그로 원문 URL 보존 — UI가 출처 링크로 사용
    return CandidateRecord(
        company_id=candidate_id,
        pool=PoolKind.external,
        profile=profile,
        pain_points=(f"{pain} {desc}".strip()
                     or profile.problem_solved.value or profile.basic.name),
        tags=tags,
    )
