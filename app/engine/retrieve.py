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

_STRONG_THRESHOLD = 0.12   # 이하이면 "강한 후보 없음" (RET-06). 실 LLM 캘리브레이션에서
                           # well-defined 매칭(0.176~) vs 노이즈(≤0.070) 청정갭 정중앙 확인.
_MARGIN_BAND = 0.03        # 임계 근처 |s-τ| — 재실행 시 뒤집힐 위험이 큰 경계 후보
_ANCHOR_MIN = 0.05         # pool-max ov_anchor 하한 — 미만이면 과소정의 프로필(저신뢰)
_API_RERANK_MAX = 5        # E9 부재 시 API(236B) 폴백 재랭킹 상한 — 개별 호출이라
                           # 후보당 ~1.5s. 상위 5개면 판단 대상(캐스케이드 top3)을 덮는다.
_TIE_SPREAD = 0.15         # 상위 head의 상대 분산 (max-min)/max 이 미만이면 "휴리스틱
                           # 동점" — 이때만 listwise가 개입한다. 실측 근거: 휴리스틱이
                           # 건강한 골든 케이스 0.423 vs 앵커가 오염됐던 공감만세 0.079.


def score_spread(scores: list[float]) -> float:
    """상위 후보 점수의 상대 분산 (max-min)/max — listwise 개입 여부를 가르는 신호.

    listwise는 **휴리스틱이 의견이 없을 때만** 개입해야 한다. 실측 사고: E9 부재면
    무조건 재랭킹하게 뒀더니 골든 top1이 1.000→0.400으로 무너졌다. 휴리스틱 점수는
    의도(지역 가산·산업 인접·경쟁사 강등)를 담는데 LLM은 그 구조를 못 보고
    '교과서적 보완 사례'를 밀어 지역 steering을 지운다. 반대로 점수가 뭉갠 경우엔
    휴리스틱에 의견이 없으니 LLM이 동점을 깨는 게 이득이다.

    실측 근거 — 휴리스틱이 건강한 골든 R-02 [0.355, 0.300, 0.267, 0.205] = 0.423 vs
    앵커가 오염됐던 공감만세 [0.1040, 0.1038, 0.1038, 0.0997, 0.0958] = 0.079.
    절대차가 아니라 상대비인 이유: 두 상황의 점수 규모 자체가 3배 넘게 다르다.
    """
    if not scores or max(scores) <= 0:
        return 0.0
    return (max(scores) - min(scores)) / max(scores)


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
        if p.portrait is not None:
            # SYNTH_SYSTEM이 "상이 있으면 gaps·stage_narrative를 반영하라"고 지시하는데
            # 여태 직렬화가 없어 죽은 지시였다 — 상을 실제로 전달한다
            profile_text += (f"\n결핍(사는 쪽 얼굴): {p.portrait.gaps}"
                             f"\n단계와 절실함: {p.portrait.stage_narrative}")
        from .prompts import vp_ko
        intent_text = (f"가치제안 {vp_ko(req.intent.value_props)}, "
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
    #
    # 기각된 대안 — IDF 가중(2026-07-27). 풀 939건에서 '회사개요·솔루션·해외
    # 레퍼런스·성과' 같은 증류 템플릿 섹션 제목이 933건(99%)에 박혀 모든 후보에게
    # 같은 점수를 주고, 그래서 상위 점수가 뭉갠다는 관찰은 사실이었다. IDF를 넣으니
    # 상위5 상대분산이 0.200→0.433으로 좋아졌다. **그러나 라벨이 있는 골든에서
    # top1이 1.000→0.533으로 무너졌다** — IDF는 코퍼스가 커야 성립하는데 시드풀에선
    # 변별 신호마저 흔하다는 이유로 0으로 깎였다. 분산은 대리 지표일 뿐이고,
    # 코퍼스 크기에 따라 거동이 달라지는 점수 함수는 그 자체로 취약하다.
    ov_synth, ov_anchor = overlap(synth, target), overlap(anchor, target)
    base = 0.5 * ov_synth + 0.5 * ov_anchor
    score = 0.7 * base

    # 온톨로지 보정 (6.2-b): 벡터가 흐릿한 곳을 구조로 잡는다.
    # 단 보완성 신호(혼합 base)가 있을 때만 보정한다 — 보너스가 신호를 만들어내면
    # "신축 럭셔리 호텔"(노후 문제 없음)이 지역·산업만으로 올라온다.
    # 게이트는 혼합 base 기준이다. 실 LLM 8회×2프로필 캘리브레이션(QC 교차검증)에서
    # 앞선 max 게이트(RET-01 과교정)는 well-defined 통과 24/24로 이득이 0이면서
    # sparse에서 ov_synth 스파이크 하나(anchor=0)로 위양성 5건을 만들었다 — base
    # 게이트로 되돌리면 well-defined 24/24 보존(run8 환각 구제는 강한 앵커로 mix≥0.10
    # 유지) + sparse 위양성 5→0. 신호 실재 판정은 '두 신호의 결합'이 옳다.
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


def _intent_tier(req: RetrieveRequest, rec: CandidateRecord) -> tuple[int, int]:
    """재랭커가 **덮어써서는 안 되는** 의도 제약. 클수록 우선. 정렬 키의 맨 앞에 온다.

    실측 사고(2026-07-27): E9(학습 1.2B, held-out ρ=0.789)와 listwise(236B API)가
    **서로 독립인데 골든의 같은 케이스에서 똑같이 실패**했다 — R-02 태국·R-03 모로코
    지역 steering, R-05 방향전환. top1이 둘 다 0.400, 재랭킹을 끄면 1.000.

    두 모델이 같은 자리에서 무너지면 모델 품질이 아니라 구조 문제다. 원인: 둘 다
    (쿼리텍스트, 후보텍스트)만 받고 intent.target_region도, '이 후보가 내 경쟁사인가'도
    못 본다. 휴리스틱은 그걸 알고 지역 가산(+0.15)·경쟁사 강등(×0.2)으로 순서에
    새겨 두는데, 재랭커가 의도를 모르는 유사도로 그 순서를 통째로 덮어쓴다.

    그래서 재랭커를 끄는 대신 **권한을 나눈다**: 의도 충족 여부는 코드가 정하고,
    재랭커는 같은 티어 안에서만 순서를 매긴다. E9의 보완성 신호는 그대로 쓰면서
    지역·경쟁사 제약은 잃지 않는다.
    """
    not_competitor = 1
    if req.direction == RetrieveDirection.sell_outreach:
        same_industry = (req.requester_profile.basic.industry
                         == rec.profile.basic.industry)
        same_solution = overlap(req.requester_profile.solution.value,
                                rec.profile.solution.value) > 0.35
        not_competitor = 0 if (same_industry or same_solution) else 1
    region = req.intent.target_region
    # 지역 미지정이면 이 축은 판단하지 않는다 — 전원 동률이라 재랭커에 온전히 맡긴다
    region_ok = 1 if (region and region in rec.profile.basic.country) else 0
    return (not_competitor, region_ok)


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

        # 앵커 강도 기권 신호 (QC 캘리브레이션 권고) — 앵커가 풀 어느 후보와도
        # 거의 안 겹치면(추상·과소정의 프로필) 검색은 synth 노이즈로만 굴러가 코인플립이
        # 된다. 실측: sparse 프로필 pool-max ov_anchor=0.035에서 통과율이 런마다 요동.
        # τ 조정으로 못 고치는 '입력 품질' 문제라 정직하게 저신뢰로 플래그한다.
        pool_max_anchor = max((overlap(anchor, _search_text(r, req.direction))
                               for r in records), default=0.0)
        underdefined = pool_max_anchor < _ANCHOR_MIN
        if underdefined:
            progress.log("검색", f"⚠ 앵커가 풀과 거의 안 겹침(max ov_anchor="
                                 f"{pool_max_anchor:.3f}<{_ANCHOR_MIN}) — 과소정의 프로필, "
                                 f"검색 결과 저신뢰. represent 보강 질문으로 프로필을 채우세요.")

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
        strong_ids = {r.company_id for r, _ in strong}   # τ 통과분 — weak 표기의 기준
        weak_fallback = False
        if not strong:
            if not req.allow_weak:
                raise NoStrongCandidate()   # 재현율 우선이되, 정직성 (RET-06)
            # allow_weak=True — 억지로 채우되 CandidateOut.weak=True로 정직하게 표시.
            # 강한 후보와 섞어 조용히 승격하지 않는다(RET-06 정신은 유지).
            weak_fallback = True
            strong = scored[: max(req.k, 1)]
            progress.log("검색", f"⚠ 강한 후보 0건 — allow_weak=True로 상위 {len(strong)}건을 "
                                 f"'약한 후보'로 표시해 반환")
        elif req.allow_weak and len(strong) < req.k:
            # 부분 패딩 — 강한 후보가 k 미만이면 차순위를 후보별 weak=True로 채운다.
            # 실측(데모 호텔 풀 25건): τ 통과 1건이면 화면에 1장만 떠서 top-5 비교가
            # 불가능했다. 전량 폴백(strong=0)과 달리 여기선 강·약이 섞이므로
            # weak 표기는 응답 전역 플래그가 아니라 후보별로 단다.
            pad = [(r, s) for r, s in scored
                   if r.company_id not in strong_ids][: req.k - len(strong)]
            if pad:
                strong = strong + pad
                progress.log("검색", f"강한 후보 {len(strong_ids)}건 < k={req.k} — "
                                     f"차순위 {len(pad)}건을 '약한 후보' 표기로 채움")

        # 학습 스코어러 재랭킹 (선택적) — 게이트는 위 휴리스틱 τ가 이미 결정했고,
        # 여기서는 통과 후보의 '순서'만 학습 점수로 다시 매긴다. 서버 부재 시
        # score_batch가 None → 휴리스틱 순서 그대로 (정직 폴백, 조용한 대체 없음).
        from .scorer_client import (api_rank_listwise, api_score_batch, profile_facts,
                                     score_batch_timed)
        rb = req.requester_profile.basic
        req_facts = profile_facts(rb.name, rb.industry, rb.country,
                                  req.requester_profile.description)
        window = strong[:64]           # 재랭킹 창 — 지연 상한 (초과분은 휴리스틱 순서)
        if len(strong) > len(window):
            progress.log("검색", f"학습 재랭킹 창 초과 — 상위 {len(window)}건만 재랭킹, "
                                 f"나머지 {len(strong) - len(window)}건은 휴리스틱 순서")
        pairs = [
            (req_facts, profile_facts(r.profile.basic.name, r.profile.basic.industry,
                                      r.profile.basic.country, r.profile.description))
            for r, _ in window]
        learned, e9_ms = score_batch_timed(pairs)
        # 비교 모드: API(K-EXAONE-236B)로도 같은 창을 채점 (순서엔 안 씀, 표시만)
        api_scores, api_ms = (None, None)
        if req.compare_api:
            progress.log("검색", "API 비교 모드 — K-EXAONE-236B로 동일 후보 재채점(느림)")
            api_scores, api_ms = api_score_batch(pairs)
        api_by_cid = {}
        if api_scores is not None:
            api_by_cid = {window[i][0].company_id: api_scores[i]
                          for i in range(len(window))}
        if learned is not None:
            # 의도 티어가 정렬 키의 맨 앞 — 학습 점수는 같은 티어 안에서만 순서를 정한다
            ranked = sorted(
                ((r, s, l) for (r, s), l in zip(window, learned)),
                key=lambda x: (tuple(-v for v in _intent_tier(req, x[0])),
                               -x[2], -x[1], x[0].company_id))
            ranked += [(r, s, None) for r, s in strong[len(window):]]
            progress.log("검색", f"학습 스코어러 재랭킹 적용 — {len(window)}건 "
                                 f"(순서=의도 티어 → 학습 점수, 게이트=휴리스틱 τ 유지)")
        else:
            # E9 부재 폴백 — 휴리스틱(bigram 겹침)만 남으면 "유사도≠보완성"이 무너져
            # 키워드 매칭으로 후퇴한다(실측: 공감만세→광고사·SI가 상위). API(236B)로
            # 같은 보완성 기준을 채점해 순서를 되살린다. E9(로컬 ms급)와 달리 개별
            # 호출이라 느려 상위 _API_RERANK_MAX개만. 게이트는 여전히 휴리스틱 τ.
            head = window[:_API_RERANK_MAX]          # window와 pairs는 같은 순서
            spread = score_spread([s for _, s in head])
            order = None
            if head and spread >= _TIE_SPREAD:
                progress.log("검색", f"학습 스코어러 부재 — 다만 휴리스틱 변별 충분"
                                     f"(상대분산 {spread:.3f}≥{_TIE_SPREAD}) → 순서 유지")
            elif head:
                progress.log("검색", f"⚠ 학습 스코어러 부재 + 휴리스틱 동점"
                                     f"(상대분산 {spread:.3f}<{_TIE_SPREAD}) — "
                                     f"API(236B) listwise로 동점 해소 {len(head)}건")
                # RankGPT(arXiv:2304.09542): 후보별 절대점수(pointwise, N회 호출)보다
                # 한 번에 놓고 상대 비교하는 listwise 순열이 호출 1회 + 변별력이 낫다.
                # 쿼리는 synth(이상적 상대의 상) — 이미 보완성으로 변환된 문장이라
                # '그 상과의 유사도 = 우리에 대한 보완성'이 된다(HyDE와 같은 구조).
                order, ms = api_rank_listwise(synth, [b for _, b in pairs[:len(head)]])
                if order is not None and sorted(order) != list(range(len(head))):
                    # 길이·범위가 head와 안 맞는 순열 — 파서 계약상 나올 수 없지만,
                    # 여기서 그대로 인덱싱하면 IndexError로 요청 전체가 죽는다.
                    # 이 코드베이스의 모든 재랭킹 실패는 '휴리스틱 순서 유지'다.
                    progress.log("검색", f"⚠ listwise 순열이 후보 수({len(head)})와 "
                                         f"불일치 — 폐기하고 휴리스틱 순서 유지")
                    order = None
                if order is not None:
                    api_ms = ms
                    progress.log("검색", f"listwise 순열 적용 — 1회 호출 {ms}ms "
                                         f"(pointwise였다면 {len(head)}회)")
            if order is not None:
                # 순열은 순서만 준다 — 점수가 아니므로 learned/api 점수칸은 비운다
                # (랭크를 0~10 점수로 위장하면 UI가 없는 신뢰도를 표시하게 된다).
                # E9와 같은 제약: listwise도 의도를 못 보므로 티어를 덮어쓰지 못한다.
                # 안정 정렬이라 같은 티어 안에서는 LLM 순열이 그대로 유지된다.
                ranked = [(head[i][0], head[i][1], None) for i in order]
                ranked.sort(key=lambda x: tuple(-v for v in _intent_tier(req, x[0])))
                ranked += [(r, s, None) for r, s in strong[len(head):]]
            else:
                ranked = [(r, s, None) for r, s in strong]

        # 정직 표기: ranked의 3번째 원소는 E9(learned)일 때만 learned_relatedness에
        # 담는다. API 폴백 경로의 점수는 api_relatedness(api_by_cid)로만 나간다 —
        # API 점수를 learned로 흘리면 UI가 "🧠 1.2B"로 잘못 표시한다(조용한 대체 금지).
        learned_ranked = learned is not None
        candidates = []
        for r, s, l in ranked[: req.k]:
            av = api_by_cid.get(r.company_id)
            candidates.append(CandidateOut(
                company_id=r.company_id,
                profile_ref=r.company_id,
                pool=r.pool,
                match_points=_match_points(synth, anchor, r),
                retrieval_score=s,
                learned_relatedness=(round(l, 2)
                                     if learned_ranked and l is not None else None),
                api_relatedness=round(av, 2) if av is not None else None,
                weak=r.company_id not in strong_ids,   # τ 통과 못한 후보만 — 후보별 정직 표기
            ))
    return RetrieveResponse(candidates=candidates, synthesized_counterpart=synth,
                            scorer_latency_ms=e9_ms, api_latency_ms=api_ms,
                            weak_fallback=weak_fallback)
