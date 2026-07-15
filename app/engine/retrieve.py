"""Retrieve — 2단 구조: 상대 합성 → 하이브리드 검색 (RET-01~07, 기획서 6장).

핵심 원리: 유사도 ≠ 보완성. 회사를 임베딩해 최근접을 찾으면 경쟁사가 나온다.
"이상적 상대의 상(像)"을 먼저 합성하고, 후보의 '겪는 문제' 면을 향해 검색한다 (RET-02).

v0: 합성 = 템플릿, 검색 = bigram overlap + 온톨로지 보정.
Phase 2: 합성 = LLM 1회(저렴·캐시), 검색 = 벡터DB(OpenSearch) + 온톨로지 융합.

분산 제어 (FORMALIZATION.md R4): LLM 합성문은 확률적 단일표본인데 그 문자열이
전 후보의 검색 점수에 곱해진다 — 상류 한 표본이 랭킹 전체를 흔드는 구조.
결정적 앵커 혼합(base의 절반을 프로필 직접 도출 템플릿에 고정)으로 그 분산을
1/4로 감쇠하고, 동점 후보는 company_id 전순서로 재현 가능하게 정렬한다.
"""
from ..errors import NoStrongCandidate
from ..schemas import (CandidateOut, PoolChoice, RetrieveDirection,
                       RetrieveRequest, RetrieveResponse)
from .common import industry_adjacent, infer_stage, overlap, profile_pain_text
from .pool import CandidateRecord, get_pool

_STRONG_THRESHOLD = 0.12   # 이하이면 "강한 후보 없음" (RET-06)
_MARGIN_BAND = 0.03        # 임계 근처 |s-τ| — 재실행 시 뒤집힐 위험이 큰 경계 후보


def template_counterpart(req: RetrieveRequest) -> str:
    """결정적 상대상 템플릿 — 프로필 필드에서 직접 도출되는 앵커 (R4).

    LLM 합성문과 별개로 항상 계산된다. 검색 base에 앵커를 절반 혼합하면
    (base = ½·overlap(synth,·) + ½·overlap(anchor,·)) LLM 요동이 base에 미치는
    분산이 1/4로 감쇠한다: Var[(X+c)/2] = Var[X]/4 (c=상수 앵커 성분)."""
    p = req.requester_profile
    region = req.intent.target_region or "글로벌"
    if req.direction == RetrieveDirection.sell_outreach:
        # 판매 요청 → 이상적 '구매자'의 상: 내 솔루션이 푸는 문제를 겪는 상대
        return (f"{region}에서 {p.problem_solved.value} 문제를 겪고 있어 "
                f"{p.solution.value} 같은 해법이 필요한 {p.target_customer.value}")
    # 구매 요청 → 이상적 '판매자'의 상
    return (f"{region}에서 {p.problem_solved.value}를 해결해 줄 솔루션을 "
            f"보유·공급하는 기업")


def synthesize_counterpart(req: RetrieveRequest) -> str:
    """1단 — 이상적 상대상 합성. Strategy Input은 하드 필터가 아니라 씨앗 (RET-07).
    LLM이 켜져 있으면 실제 합성, 아니면 결정적 템플릿."""
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
    return template_counterpart(req)


def _search_text(rec: CandidateRecord, direction: RetrieveDirection) -> str:
    """검색이 향하는 면 (RET-02): 판매 요청이면 상대의 '겪는 문제', 구매 요청이면 '솔루션'."""
    if direction == RetrieveDirection.sell_outreach:
        return f"{rec.pain_points} {rec.profile.description}"
    return f"{rec.profile.solution.value} {rec.profile.description}"


def _score(req: RetrieveRequest, synth: str, anchor: str,
           rec: CandidateRecord) -> float:
    target = _search_text(rec, req.direction)
    # R4 결정적 앵커 혼합 — synth(확률적)와 anchor(결정적)를 절반씩.
    # synth==anchor(mock 경로)면 base는 기존과 동일하다.
    ov_synth, ov_anchor = overlap(synth, target), overlap(anchor, target)
    base = 0.5 * ov_synth + 0.5 * ov_anchor
    score = 0.7 * base

    # 온톨로지 보정 (6.2-b): 벡터가 흐릿한 곳을 구조로 잡는다.
    # 단 보완성 신호가 있을 때만 보정한다 — 보너스가 신호를 만들어내면
    # "신축 럭셔리 호텔"(노후 문제 없음)이 지역·산업만으로 올라온다.
    # 게이트는 혼합 base가 아니라 두 신호의 max로 판정한다 (적대적 검토 RET-01):
    # 혼합이 base를 희석해 어느 한쪽 단독으로는 충분했던 보너스(±0.25)를 불연속으로
    # 꺼버리는 절벽을 막는다 — 신호 실재 판정과 신호 크기 혼합은 별개 문제다.
    if max(ov_synth, ov_anchor) >= 0.10:
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


def _match_points(synth: str, anchor: str, rec: CandidateRecord) -> list[str]:
    """합성 상과의 보완성 근거 (RET-03). 점수의 절반이 앵커에서 오므로(R4 혼합)
    근거 태그도 synth·anchor 양쪽과 대조한다 (적대적 검토 RET-02) — 앵커가 점수를
    전담한 후보의 근거가 무관한 폴백 태그로 채워지는 불일치를 막는다."""
    points = [t for t in rec.tags
              if overlap(t, synth) > 0.3 or overlap(t, anchor) > 0.3]
    return points or rec.tags[:1] or ["프로필 유사 신호"]


def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    from .. import progress
    from ..errors import EngineError
    # 최소 신호 게이트 (적대적 검토 RET-03) — product 경로는 REP-06이 막지만
    # 엔진 API /v1/retrieve는 무게이트였다. 핵심 3필드가 전부 비면 앵커가 순수
    # 보일러플레이트가 되어 R4 혼합이 전 후보 점수를 노이즈로 절반 희석한다.
    p = req.requester_profile
    if not (p.problem_solved.value or p.solution.value or p.target_customer.value):
        raise EngineError(400, "invalid_input",
                          "프로필 핵심 필드(문제·솔루션·타겟)가 전부 비어 있음 — "
                          "represent로 최소 프로필을 먼저 채우세요 (REP-06)")
    with progress.node("synth", "이상적 상대상 합성 (1단)"):
        progress.log("합성", "1단 — 이상적 상대상 합성 시작 (보완성 검색의 검색어)")
        anchor = template_counterpart(req)   # 결정적 앵커 — 항상 계산 (R4)
        synth = synthesize_counterpart(req)
        progress.log("합성", f"합성 완료 — \"{synth[:80]}...\"")
        if synth != anchor:
            progress.log("합성", "결정적 앵커 혼합 활성 — LLM 합성 요동의 점수 분산 1/4 감쇠")
    with progress.node("search", "하이브리드 검색 (2단)"):
        records = [r for r in get_pool()
                   if req.pool == PoolChoice.both or r.pool.value == req.pool.value]
        # 자기 자신은 후보에서 제외
        records = [r for r in records
                   if r.profile.basic.name != req.requester_profile.basic.name]

        # R4 전순서 정렬 — 동점 후보를 company_id로 고정해 풀 순서와 무관하게 재현.
        scored = sorted(((r, _score(req, synth, anchor, r)) for r in records),
                        key=lambda x: (-x[1], x[0].company_id))
        strong = [(r, s) for r, s in scored if s >= _STRONG_THRESHOLD]
        # 경계 후보 가시화 — |s-τ|가 작으면 재실행에서 뒤집힐 위험이 크다 (정직 계측)
        border = sum(1 for _, s in scored
                     if abs(s - _STRONG_THRESHOLD) < _MARGIN_BAND)
        progress.log("검색", f"2단 — 하이브리드 검색 완료: {len(records)}건 중 "
                             f"강한 후보 {len(strong)}건 (경쟁사·무관 후보 강등)"
                             + (f" · 임계 경계 ±{_MARGIN_BAND} 이내 {border}건 — "
                                f"재실행 시 뒤집힘 위험" if border else ""))
        if not strong:
            raise NoStrongCandidate()   # 재현율 우선이되, 정직성 (RET-06)

        candidates = [
            CandidateOut(
                company_id=r.company_id,
                profile_ref=r.company_id,
                pool=r.pool,
                match_points=_match_points(synth, anchor, r),
                retrieval_score=s,
            )
            for r, s in strong[: req.k]
        ]
    return RetrieveResponse(candidates=candidates, synthesized_counterpart=synth)
