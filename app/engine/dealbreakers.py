"""deal-breaker 결격 리스트 (JDG-04·05, 기획서 7.3).

⚠ TBD — 구체 결격 리스트는 Jin(BD) 확정 필요 (PRD §11).
아래 2건은 기획서 7.3의 예시를 옮긴 placeholder다. 확정 리스트가 나오면 여기만 갱신한다.
기본은 전부 소프트 플래그이고, 여기 걸리는 것만 하드 차단·비노출한다.
"""
from typing import Optional

from ..errors import DealBreaker
from ..schemas import Profile
from .common import infer_stage


def check_deal_breakers(self_profile: Profile, counterpart: Profile) -> Optional[None]:
    """결격 발견 시 DealBreaker 예외 발생. 통과 시 None."""
    text = f"{counterpart.description} {counterpart.traction or ''}"

    # 1) 법적 결격 (제재 대상·무자격 라이선스 산업)
    if "제재 대상" in text or "제재대상" in text:
        raise DealBreaker("industry_fit", "법적 결격 — 제재 대상 기업")

    # 2) 사업단계 근본 부적합 (예: 시드 스타트업 → 글로벌 대기업 부품 납품 제안)
    self_stage = infer_stage(self_profile)
    counter_stage = infer_stage(counterpart)
    if self_stage in {"seed", "startup"} and counter_stage == "enterprise":
        raise DealBreaker("stage_compatibility",
                          "사업단계 근본 부적합 — 초기 기업 ↔ 글로벌 엔터프라이즈 조달 미스매치")
    return None
