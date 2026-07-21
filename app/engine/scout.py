"""Scout — 지식 분리 → 파트너 가설(explore/exploit) → 웹 검색 숏리스트.

기획서 근거:
- §1 해법 ①: "누구를 타겟할지(전략·가설 수립)를 컨설팅" — 가설 수립이 엔진의 1번 기능.
- 7.4/10.5·JDG-09: 확신 후보(exploit) + 가설 검증용 불확실 후보(explore) 혼합,
  explore 비율은 파라미터. Judge의 탐색 예산 개념을 '후보 충원' 단계로 앞당겨 적용.
- 6.4: 외부 풀 충원은 별도 트랙 — 이 모듈이 그 트랙의 v0(웹 검색 기반)다.

Retrieve와의 관계: Retrieve는 '이미 아는 풀' 안에서 보완성 검색, Scout는 풀 밖(웹)에서
후보를 충원한다. 지식 분리가 두 트랙을 가른다 —
  명백지(stated·자가신고) → exploit 가설 (업계 검증된 정석 파트너 패턴)
  암묵지(inferred·회사의 상) → explore 가설 (역추론된 결핍·전략이 가리키는 비자명 파트너)

분산 제어(FORMALIZATION 원칙 계승): 가설 생성만 확률적(LLM 1호출·non-deep)이고
지식 분리·검색·스코어·쿼터 배분은 전부 결정적이다. 숏리스트 순서는 (-relevance, domain)
전순서로 재현 가능하다.
"""
from urllib.parse import urlparse

from .. import progress
from ..config import Settings, get_settings
from ..schemas import (HypothesisTrack, KnowledgeItem, KnowledgeKind,
                       PartnerHypothesis, Profile, Provenance, ScoutCandidate,
                       ScoutCompany, ScoutRequest, ScoutResponse)
from .common import overlap
from .llm import get_extractor
from .prompts import (SCOUT_EXTRACT_SCHEMA, SCOUT_EXTRACT_SYSTEM, SCOUT_SCHEMA,
                      SCOUT_SYSTEM, scout_extract_user, scout_user)

_MAX_QUERIES = 5          # 가설 수 상한 = 검색 쿼리 상한 (비용·예의)
_RESULTS_PER_QUERY = 6
_MIN_RELEVANCE = 0.06     # 가설·검색어와 전혀 무관한 히트 컷 (bigram overlap)

# 숏리스트에서 걸러낼 도메인 — 회사 홈페이지가 아니라 플랫폼·백과·뉴스 애그리게이터
_NOISE_DOMAINS = {
    "wikipedia.org", "namu.wiki", "youtube.com", "facebook.com", "instagram.com",
    "linkedin.com", "twitter.com", "x.com", "reddit.com", "quora.com",
    "tripadvisor.com", "booking.com", "agoda.com", "expedia.com",
    # 뉴스·블로그·포털 — '파트너 후보'는 사업자여야 한다. 기사·순위 블로그는
    # 단서일 뿐 후보가 아니다 (실측: 에스피지 런에서 숏리스트가 전부 기사였음).
    # 후보가 모자라면 억지로 채우지 않고 정직하게 적게 반환한다 (RET-06과 동일 철학).
    "daum.net", "naver.com", "nate.com", "zum.com", "msn.com",
    "tistory.com", "brunch.co.kr", "velog.io", "medium.com", "blogspot.com",
    "news1.kr", "newsis.com", "yna.co.kr", "yonhapnewstv.co.kr", "ytn.co.kr",
    "chosun.com", "joongang.co.kr", "donga.com", "hani.co.kr", "khan.co.kr",
    "mk.co.kr", "hankyung.com", "sedaily.com", "fnnews.com", "asiae.co.kr",
    "etnews.com", "zdnet.co.kr", "epnc.co.kr", "dt.co.kr", "ddaily.co.kr",
    "heraldcorp.com", "kmib.co.kr", "segye.com", "munhwa.com", "edaily.co.kr",
    "mt.co.kr", "inews24.com", "businesspost.co.kr", "thebell.co.kr",
}


def _is_noise(domain: str) -> bool:
    """노이즈 도메인 판정 — 서브도메인 포함 suffix 매칭 (v.daum.net, m.blog.naver.com
    같은 변형이 exact 매칭을 빠져나가던 구멍을 막는다) + 보편 뉴스 패턴."""
    d = domain.lower()
    if any(d == n or d.endswith("." + n) for n in _NOISE_DOMAINS):
        return True
    # 도메인 자체가 뉴스/미디어임을 선언하는 보편 패턴 (기업 홈페이지가 이 패턴을
    # 쓰는 경우는 사실상 없다)
    return d.startswith("news.") or d.startswith("media.") or ".news." in d


# ── 1단 — 지식 분리 (결정적: provenance 기반) ─────────────────────

def split_knowledge(profile: Profile) -> list[KnowledgeItem]:
    """프로필 → 명백지/암묵지 목록.

    명백지 = stated 필드·레퍼런스·자가신고(가치제안·의향).
    암묵지 = inferred 필드(확신도 동반)·회사의 상 7항목(정의상 역추론 — 전체 암묵지).
    ask(빈 값)는 지식이 아니므로 제외.
    """
    items: list[KnowledgeItem] = []

    def _field(name: str, f) -> None:
        if not f.value:
            return
        if f.provenance == Provenance.stated:
            items.append(KnowledgeItem(kind=KnowledgeKind.explicit,
                                       field=name, content=f.value))
        elif f.provenance == Provenance.inferred:
            items.append(KnowledgeItem(kind=KnowledgeKind.tacit, field=name,
                                       content=f.value, confidence=f.confidence))

    _field("problem_solved", profile.problem_solved)
    _field("solution", profile.solution)
    _field("target_customer", profile.target_customer)

    if profile.references:
        items.append(KnowledgeItem(kind=KnowledgeKind.explicit, field="references",
                                   content=", ".join(profile.references)))
    if profile.sell_value_props:
        items.append(KnowledgeItem(
            kind=KnowledgeKind.explicit, field="sell_value_props",
            content=", ".join(v.value for v in profile.sell_value_props)))
    if profile.traction:
        items.append(KnowledgeItem(kind=KnowledgeKind.explicit,
                                   field="traction", content=profile.traction))

    if profile.portrait is not None:
        for name in ("identity", "business_model", "edge", "stage_narrative",
                     "assets", "gaps", "risk_signals"):
            content = getattr(profile.portrait, name)
            if content:
                items.append(KnowledgeItem(kind=KnowledgeKind.tacit,
                                           field=f"portrait.{name}", content=content))
    return items


# ── 2단 — 가설 생성 (LLM 1호출 / Mock 템플릿) ──────────────────────

def _intent_text(req: ScoutRequest) -> str:
    return (f"가치제안 {[v.value for v in req.intent.value_props]}, "
            f"타겟 지역 {req.intent.target_region or '미지정'}, "
            f"제안 유형 {req.intent.proposal_type or '미지정'}")


_PRED_SUFFIXES = ("합니다", "습니다", "입니다", "됩니다", "한다", "이다", "있다")
_JOSA = ("에서", "에게", "으로", "로", "에", "을", "를", "이", "가", "은", "는")


def _query_core(text: str, limit: int = 24) -> str:
    """문장 → 검색어 핵심 명사구. 서술어절과 말미 조사를 떼어낸다 —
    '로보틱스 감속기에 집중합니다' → '로보틱스 감속기'.
    사용자가 사전 입력에 서술문을 넣어도 검색어가 무의미해지지 않게 하는 최소 방어.
    (진짜 검색어 품질은 LLM 경로의 SCOUT_SYSTEM 규칙이 담당 — 이건 mock 폴백.)"""
    core = text.split("(")[0].split(".")[0].strip()[:limit]
    words = core.split()
    while words and words[-1].endswith(_PRED_SUFFIXES):
        words.pop()
    if words:
        last = words[-1]
        for josa in _JOSA:
            if len(last) > len(josa) and last.endswith(josa):
                words[-1] = last[: -len(josa)]
                break
    return " ".join(words) if words else core


def _mock_hypotheses(knowledge: list[KnowledgeItem],
                     req: ScoutRequest) -> list[PartnerHypothesis]:
    """LLM 없이 — 명백지/암묵지에서 결정적 템플릿 가설. CI·오프라인용."""
    region = req.intent.target_region or "글로벌"
    ex = {k.field: k.content for k in knowledge if k.kind == KnowledgeKind.explicit}
    ta = {k.field: k.content for k in knowledge if k.kind == KnowledgeKind.tacit}
    out: list[PartnerHypothesis] = []
    if "target_customer" in ex:
        out.append(PartnerHypothesis(
            track=HypothesisTrack.exploit,
            hypothesis=f"{region}에서 '{ex['target_customer']}'에 해당하는 사업자가 직접 수요자다.",
            grounded_in=["target_customer"],
            search_query=f"{region} {ex['target_customer']} 업체 목록",
            partner_type=ex["target_customer"]))
    if "problem_solved" in ex:
        out.append(PartnerHypothesis(
            track=HypothesisTrack.exploit,
            hypothesis=f"'{ex['problem_solved']}' 문제를 공개적으로 겪는 사업자를 찾는다.",
            grounded_in=["problem_solved"],
            search_query=f"{region} {_query_core(ex['problem_solved'])} 기업",
            partner_type="같은 문제를 겪는 수요처"))
    gaps = ta.get("portrait.gaps")
    if gaps:
        # 검색어는 첫 절만 — 괄호 부기·후속 문장(메타 서술)이 들어가면 검색이 무의미해진다
        gap_core = gaps.split("(")[0].split(".")[0].strip()[:24]
        out.append(PartnerHypothesis(
            track=HypothesisTrack.explore,
            hypothesis=f"결핍({gaps[:40]}…)을 메워줄 수 있는 인접 업계 파트너가 비자명 후보다.",
            grounded_in=["portrait.gaps"],
            search_query=f"{region} {gap_core} 파트너",
            partner_type="결핍 보완형 파트너"))
    else:
        out.append(PartnerHypothesis(
            track=HypothesisTrack.explore,
            hypothesis=f"{region}의 인접 산업에서 같은 고객을 다른 각도로 만나는 사업자가 교차 후보다.",
            grounded_in=[k.field for k in knowledge if k.kind == KnowledgeKind.tacit][:2]
                        or ["profile"],
            search_query=f"{region} 협업 파트너십 프로그램",
            partner_type="교차 도메인 파트너"))
    return out


def _llm_hypotheses(extractor, knowledge: list[KnowledgeItem],
                    req: ScoutRequest) -> list[PartnerHypothesis]:
    data = extractor.extract_json(SCOUT_SYSTEM,
                                  scout_user(knowledge, _intent_text(req)),
                                  SCOUT_SCHEMA, deep=False)   # 가설은 1호출 — 판단이 아님
    out: list[PartnerHypothesis] = []
    for h in data.get("hypotheses", []):
        try:
            out.append(PartnerHypothesis(**h))
        except Exception:                        # noqa: BLE001 — 계약 위반 가설은 폐기
            continue
    return out


def _enforce_hypothesis_contract(hyps: list[PartnerHypothesis],
                                 knowledge: list[KnowledgeItem]) -> tuple[list, dict]:
    """가설 계약 코드 집행 (프롬프트만의 규칙은 규칙이 아니다):
    exploit은 명백지에만, explore는 암묵지를 최소 1개 근거로. 위반은 폐기+집계."""
    explicit_fields = {k.field for k in knowledge if k.kind == KnowledgeKind.explicit}
    tacit_fields = {k.field for k in knowledge if k.kind == KnowledgeKind.tacit}
    kept, rejected = [], {"exploit_tacit": 0, "explore_no_tacit": 0, "empty": 0}
    for h in hyps[:_MAX_QUERIES]:
        if not h.search_query.strip() or not h.hypothesis.strip():
            rejected["empty"] += 1
            continue
        grounded = set(h.grounded_in)
        if h.track == HypothesisTrack.exploit and grounded & tacit_fields:
            rejected["exploit_tacit"] += 1       # 정석 가설이 암묵지에 기댐 — 계약 위반
            continue
        if h.track == HypothesisTrack.explore and not (grounded & tacit_fields):
            rejected["explore_no_tacit"] += 1    # 모험 가설에 암묵지 근거 없음
            continue
        kept.append(h)
    return kept, rejected


# ── 3단 — 웹 검색 → 4단 — 숏리스트 (결정적) ────────────────────────

def _score(hit: dict, hyp: PartnerHypothesis) -> float:
    """결정적 관련도 — 히트(제목+스니펫) ↔ 가설+검색어 bigram overlap."""
    return round(overlap(f"{hit['title']} {hit['snippet']}",
                         f"{hyp.hypothesis} {hyp.search_query} {hyp.partner_type}"), 4)


def _shortlist(per_hyp_hits: list[tuple[PartnerHypothesis, list[dict]]],
               k: int, explore_ratio: float) -> list[ScoutCandidate]:
    """도메인 dedup → 트랙별 정렬 → explore 쿼터 배분(JDG-09) → 부족분 백필."""
    seen_domains: set[str] = set()
    pool: dict[HypothesisTrack, list[ScoutCandidate]] = {
        HypothesisTrack.exploit: [], HypothesisTrack.explore: []}
    n_noise = 0
    for hyp, hits in per_hyp_hits:
        for hit in hits:
            domain = hit["domain"]
            if not domain or domain in seen_domains:
                continue
            if _is_noise(domain):
                n_noise += 1                     # 정직 집계 — 조용히 삼키지 않는다
                continue
            rel = _score(hit, hyp)
            if rel < _MIN_RELEVANCE:
                continue
            seen_domains.add(domain)
            pool[hyp.track].append(ScoutCandidate(
                track=hyp.track, hypothesis=hyp.hypothesis,
                title=hit["title"], url=hit["url"], snippet=hit["snippet"],
                domain=domain, relevance=rel))
    for track in pool:
        pool[track].sort(key=lambda c: (-c.relevance, c.domain))

    n_explore = min(len(pool[HypothesisTrack.explore]),
                    max(1, round(k * explore_ratio)) if explore_ratio > 0 else 0)
    n_exploit = min(len(pool[HypothesisTrack.exploit]), k - n_explore)
    # 백필 — 한 트랙이 부족하면 다른 트랙에서 채운다 (k를 억지로 못 채우면 정직하게 적게)
    n_explore = min(len(pool[HypothesisTrack.explore]), k - n_exploit)
    picked = pool[HypothesisTrack.exploit][:n_exploit] \
        + pool[HypothesisTrack.explore][:n_explore]
    picked.sort(key=lambda c: (-c.relevance, c.domain))
    return picked, n_noise


# ── 기업 발굴 — LLM이 히트에서 사업자를 추출, 코드가 실재성을 집행 ──────

def _norm_for_match(s: str) -> str:
    return "".join(s.lower().split())


def _enforce_company_grounding(raw_companies: list[dict], hits: list[dict],
                               hyp: PartnerHypothesis,
                               self_name: str) -> tuple[list[ScoutCompany], dict]:
    """기업 추출 계약의 코드 집행 (프롬프트만의 규칙은 규칙이 아니다):
    ① 기업명이 지목한 히트의 제목+요약에 그 글자 그대로 실재해야 한다(환각 차단)
    ② source_hit 번호가 실제 히트 범위여야 한다
    ③ 요청 회사 자신은 제외
    위반은 폐기 + 사유별 집계."""
    kept: list[ScoutCompany] = []
    rejected = {"hallucinated_name": 0, "bad_hit_index": 0, "self": 0}
    self_norm = _norm_for_match(self_name)
    seen_names: set[str] = set()
    for c in raw_companies:
        name = str(c.get("name", "")).strip()
        idx = c.get("source_hit", -1)
        if not name or not isinstance(idx, int) or not (0 <= idx < len(hits)):
            rejected["bad_hit_index"] += 1
            continue
        hit = hits[idx]
        name_norm = _norm_for_match(name)
        if self_norm and (self_norm in name_norm or name_norm in self_norm):
            rejected["self"] += 1
            continue
        haystack = _norm_for_match(f"{hit['title']} {hit['snippet']}")
        if name_norm not in haystack:
            rejected["hallucinated_name"] += 1   # 히트에 없는 이름 — 지어낸 것
            continue
        if name_norm in seen_names:
            continue                             # 가설 간 중복 — 첫 근거만
        seen_names.add(name_norm)
        kept.append(ScoutCompany(
            name=name, track=hyp.track, hypothesis=hyp.hypothesis,
            summary=str(c.get("summary", ""))[:300],
            country=c.get("country") or None,
            source_url=hit["url"], source_domain=hit["domain"],
            source_title=hit["title"]))
    return kept, rejected


def _extract_companies(extractor, per_hyp_hits, self_name: str
                       ) -> tuple[list[ScoutCompany], dict]:
    """가설별 히트 묶음에서 기업 발굴 — 가설당 LLM 1콜(non-deep). 노이즈로 걸러진
    기사도 여기서는 '단서'로 소비한다: 기사 속 기업명은 실재 검증을 통과하면 후보다."""
    companies: list[ScoutCompany] = []
    tally = {"hallucinated_name": 0, "bad_hit_index": 0, "self": 0}
    seen: set[str] = set()
    for hyp, hits in per_hyp_hits:
        if not hits:
            continue
        try:
            data = extractor.extract_json(
                SCOUT_EXTRACT_SYSTEM,
                scout_extract_user(hits, self_name, hyp.hypothesis),
                SCOUT_EXTRACT_SCHEMA, deep=False)
        except Exception as e:                    # noqa: BLE001 — 발굴 실패는 치명 아님
            progress.log("발굴", f"기업 추출 실패({hyp.track.value}) — {e}")
            continue
        kept, rej = _enforce_company_grounding(
            data.get("companies", []), hits, hyp, self_name)
        for k in tally:
            tally[k] += rej[k]
        for comp in kept:
            n = _norm_for_match(comp.name)
            if n not in seen:                     # 가설 간 전역 dedup
                seen.add(n)
                companies.append(comp)
    return companies, tally


def scout(req: ScoutRequest, settings: "Settings | None" = None,
          search_fn=None) -> ScoutResponse:
    """지식 분리 → 가설 → 웹 검색 → 숏리스트. search_fn 주입은 테스트용."""
    from ..ingest.websearch import web_search
    settings = settings or get_settings()
    search_fn = search_fn or web_search

    with progress.node("knowledge.split", "지식 분리 (명백지/암묵지)"):
        knowledge = split_knowledge(req.profile)
        n_ex = sum(1 for x in knowledge if x.kind == KnowledgeKind.explicit)
        progress.log("지식", f"명백지 {n_ex}건 · 암묵지 {len(knowledge) - n_ex}건 분리")

    extractor = get_extractor(settings)
    with progress.node("hypothesize", "파트너 가설 (exploit 정석 / explore 모험)"):
        if extractor is not None:
            raw = _llm_hypotheses(extractor, knowledge, req)
            engine_mode = "llm"
        else:
            raw = _mock_hypotheses(knowledge, req)
            engine_mode = "mock"
        hyps, rejected = _enforce_hypothesis_contract(raw, knowledge)
        n_rej = sum(rejected.values())
        progress.log("가설", f"exploit {sum(1 for h in hyps if h.track == HypothesisTrack.exploit)}건 · "
                             f"explore {sum(1 for h in hyps if h.track == HypothesisTrack.explore)}건"
                             + (f" · 계약 위반 폐기 {n_rej}건 {rejected}" if n_rej else ""))

    per_hyp: list[tuple[PartnerHypothesis, list[dict]]] = []
    web_used = False
    with progress.node("websearch", "웹 검색 (풀 밖 후보 충원)"):
        for h in hyps:
            hits = search_fn(h.search_query, settings,
                             max_results=_RESULTS_PER_QUERY)
            web_used = web_used or bool(hits)
            per_hyp.append((h, hits))
        if not web_used:
            progress.log("검색", "⚠ 모든 검색 실패/차단 — 숏리스트가 비어도 가설은 유효")

    # 기업 발굴 — LLM 경로 전용. 숏리스트 필터 '전'의 원본 히트를 쓴다:
    # 뉴스는 후보로는 부적격이지만 기사 속 기업명은 단서다.
    companies: list[ScoutCompany] = []
    if extractor is not None and web_used:
        with progress.node("extract", "기업 발굴 (히트 → 사업자, 실재 검증)"):
            companies, ex_rej = _extract_companies(
                extractor, per_hyp, req.profile.basic.name)
            n_rej = sum(ex_rej.values())
            progress.log("발굴", f"기업 {len(companies)}건 발굴"
                         + (f" · 계약 위반 폐기 {n_rej}건 {ex_rej}" if n_rej else "")
                         + " — 기업명은 히트 원문 실재 검증을 통과한 것만")

    with progress.node("shortlist", "숏리스트 (explore 쿼터 배분)"):
        shortlist, n_noise = _shortlist(per_hyp, req.k, req.explore_ratio)
        if n_noise:
            progress.log("숏리스트", f"뉴스·블로그·포털 {n_noise}건 제외 — "
                                    f"후보는 사업자여야 한다 (모자라면 정직하게 적게)")
        progress.log("숏리스트", f"{len(shortlist)}건 채택 "
                                f"(exploit {sum(1 for c in shortlist if c.track == HypothesisTrack.exploit)} / "
                                f"explore {sum(1 for c in shortlist if c.track == HypothesisTrack.explore)}) · "
                                f"explore_ratio={req.explore_ratio}")

    from .. import audit
    audit.record("scout", {
        "name": req.profile.basic.name, "engine_mode": engine_mode,
        "n_explicit": sum(1 for x in knowledge if x.kind == KnowledgeKind.explicit),
        "n_tacit": sum(1 for x in knowledge if x.kind == KnowledgeKind.tacit),
        "hypotheses": [h.model_dump(mode="json") for h in hyps],
        "shortlist": [c.model_dump(mode="json") for c in shortlist],
        "companies": [c.model_dump(mode="json") for c in companies],
    })
    return ScoutResponse(knowledge=knowledge, hypotheses=hyps,
                         shortlist=shortlist, companies=companies,
                         engine_mode=engine_mode, web_search_used=web_used)
