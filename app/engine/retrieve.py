"""Retrieve — 2단 구조: 상대 합성 → 하이브리드 검색 (RET-01~07, 기획서 6장).

핵심 원리: 유사도 ≠ 보완성. 회사를 임베딩해 최근접을 찾으면 경쟁사가 나온다.
"이상적 상대의 상(像)"을 먼저 합성하고, 후보의 '겪는 문제' 면을 향해 검색한다 (RET-02).

v0: 합성 = 템플릿, 검색 = bigram overlap + 온톨로지 보정.
Phase 2: 합성 = LLM 1회(저렴·캐시), 검색 = 벡터DB(OpenSearch) + 온톨로지 융합.
"""
from ..errors import NoStrongCandidate
from ..schemas import (CandidateOut, PoolChoice, RetrieveDirection,
                       RetrieveRequest, RetrieveResponse)
from .common import industry_adjacent, infer_stage, overlap, profile_pain_text
from .pool import CandidateRecord, get_pool

_STRONG_THRESHOLD = 0.12   # 이하이면 "강한 후보 없음" (RET-06)


def synthesize_counterpart(req: RetrieveRequest) -> str:
    """1단 — 이상적 상대상 합성. Strategy Input은 하드 필터가 아니라 씨앗 (RET-07).
    LLM이 켜져 있으면 실제 합성, 아니면 템플릿."""
    from ..config import get_settings
    from .llm import get_extractor
    from .prompts import SYNTH_SYSTEM, synth_user

    p = req.requester_profile
    extractor = get_extractor(get_settings())
    if extractor is not None:
        profile_text = (f"{p.basic.name} ({p.basic.industry}, {p.basic.country}) — "
                        f"{p.description}\n푸는 문제: {p.problem_solved.value}\n"
                        f"솔루션: {p.solution.value}\n타겟: {p.target_customer.value}")
        intent_text = (f"가치제안 {[v.value for v in req.intent.value_props]}, "
                       f"지역 {req.intent.target_region or '미지정'}, "
                       f"유형 {req.intent.proposal_type or '미지정'}")
        return extractor.complete_text(
            SYNTH_SYSTEM, synth_user(profile_text, intent_text, req.direction.value))

    region = req.intent.target_region or "글로벌"
    if req.direction == RetrieveDirection.sell_outreach:
        # 판매 요청 → 이상적 '구매자'의 상: 내 솔루션이 푸는 문제를 겪는 상대
        return (f"{region}에서 {p.problem_solved.value} 문제를 겪고 있어 "
                f"{p.solution.value} 같은 해법이 필요한 {p.target_customer.value}")
    # 구매 요청 → 이상적 '판매자'의 상
    return (f"{region}에서 {p.problem_solved.value}를 해결해 줄 솔루션을 "
            f"보유·공급하는 기업")


def _search_text(rec: CandidateRecord, direction: RetrieveDirection) -> str:
    """검색이 향하는 면 (RET-02): 판매 요청이면 상대의 '겪는 문제', 구매 요청이면 '솔루션'."""
    if direction == RetrieveDirection.sell_outreach:
        return f"{rec.pain_points} {rec.profile.description}"
    return f"{rec.profile.solution.value} {rec.profile.description}"


def _score(req: RetrieveRequest, synth: str, rec: CandidateRecord) -> float:
    base = overlap(synth, _search_text(rec, req.direction))
    score = 0.7 * base

    # 온톨로지 보정 (6.2-b): 벡터가 흐릿한 곳을 구조로 잡는다.
    # 단 보완성 신호(base)가 있을 때만 보정한다 — 보너스가 신호를 만들어내면
    # "신축 럭셔리 호텔"(노후 문제 없음)이 지역·산업만으로 올라온다.
    if base >= 0.10:
        if req.intent.target_region and req.intent.target_region in rec.profile.basic.country:
            score += 0.15
        if industry_adjacent(req.requester_profile.basic.industry, rec.profile.basic.industry):
            score += 0.10
    stages = {infer_stage(req.requester_profile), infer_stage(rec.profile)}
    if "enterprise" in stages and ({"seed", "startup"} & stages):
        score -= 0.4   # 조달 미스매치 배제 (기획서 6.2 예시)

    # 동종 경쟁사 강등 (RET-02 검증 지표의 핵심): 판매 아웃리치에서
    # 나와 같은 산업(같은 면)이거나 같은 솔루션을 파는 상대는 구매자가 아니라 경쟁사다.
    if req.direction == RetrieveDirection.sell_outreach:
        same_industry = req.requester_profile.basic.industry == rec.profile.basic.industry
        same_solution = overlap(req.requester_profile.solution.value,
                                rec.profile.solution.value) > 0.35
        if same_industry or same_solution:
            score *= 0.2
    return round(max(score, 0.0), 4)


def _match_points(synth: str, rec: CandidateRecord) -> list[str]:
    """합성 상과의 보완성 근거 (RET-03)."""
    points = [t for t in rec.tags if overlap(t, synth) > 0.3]
    return points or rec.tags[:1] or ["프로필 유사 신호"]


def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    from .. import progress
    with progress.node("synth", "이상적 상대상 합성 (1단)"):
        progress.log("합성", "1단 — 이상적 상대상 합성 시작 (보완성 검색의 검색어)")
        synth = synthesize_counterpart(req)
        progress.log("합성", f"합성 완료 — \"{synth[:80]}...\"")
    with progress.node("search", "하이브리드 검색 (2단)"):
        records = [r for r in get_pool()
                   if req.pool == PoolChoice.both or r.pool.value == req.pool.value]
        # 자기 자신은 후보에서 제외
        records = [r for r in records
                   if r.profile.basic.name != req.requester_profile.basic.name]

        scored = sorted(((r, _score(req, synth, r)) for r in records),
                        key=lambda x: x[1], reverse=True)
        strong = [(r, s) for r, s in scored if s >= _STRONG_THRESHOLD]
        progress.log("검색", f"2단 — 하이브리드 검색 완료: {len(records)}건 중 "
                             f"강한 후보 {len(strong)}건 (경쟁사·무관 후보 강등)")
        if not strong:
            raise NoStrongCandidate()   # 재현율 우선이되, 정직성 (RET-06)

        candidates = [
            CandidateOut(
                company_id=r.company_id,
                profile_ref=r.company_id,
                pool=r.pool,
                match_points=_match_points(synth, r),
                retrieval_score=s,
            )
            for r, s in strong[: req.k]
        ]
    return RetrieveResponse(candidates=candidates, synthesized_counterpart=synth)
