"""Judge — 후보 쌍 → 점수가 아닌 구조화 판단 (JDG-01~12, 기획서 7장).

두 렌즈 = 단일 로직 + 3파라미터(관점·목적함수·사전정보) 교체 (JDG-06).
온톨로지 카테고리별 자기일관성: 차원별로 따로 판정하고 모은다 (JDG-02).
차원 간 불일치 = 자동 리스크 신호 (JDG-03). Willingness는 임계값이 아니라 맥락 (JDG-08).

v0: 규칙 기반 차원 판정. Phase 2: EXAONE CoT 파인튜닝 모델 호출로 교체 (JDG-12)
— 출력 계약(JudgeResult)은 동일하므로 이 모듈의 판정 함수만 갈아끼운다.
"""
from .. import progress
from ..config import get_settings
from ..schemas import (BUY_ONLY_DIMENSIONS, CategoryJudgment, ConfidenceBand,
                       DecisionType, Dimension, JudgeRequest, JudgeResult,
                       MatchSummary, Objective, PrivateState, Risk, RiskType,
                       Vantage, VerdictType, Willingness)
from .common import industry_adjacent, infer_stage, overlap, profile_pain_text
from .dealbreakers import check_deal_breakers
from .llm import get_extractor
from .prompts import JUDGE_SCHEMA, JUDGE_SYSTEM, judge_user

_FIT, _CAUTION, _UNFIT = VerdictType.fit, VerdictType.caution, VerdictType.unfit


def _judge_dimensions(req: JudgeRequest) -> list[CategoryJudgment]:
    self_p, counter_p = req.self_profile, req.counterpart_profile
    dims: list[CategoryJudgment] = []

    # [산업 적합성]
    if industry_adjacent(self_p.basic.industry, counter_p.basic.industry):
        dims.append(CategoryJudgment(dimension=Dimension.industry_fit, verdict=_FIT,
                    rationale=f"{self_p.basic.industry} ↔ {counter_p.basic.industry} 도메인이 맞물린다."))
    else:
        dims.append(CategoryJudgment(dimension=Dimension.industry_fit, verdict=_CAUTION,
                    rationale="산업 인접성이 확인되지 않음 — 교차 도메인 매칭 여부 확인 필요."))

    # [협업목적 정합] — '원하느냐(want)'. 자원 보완성과 분리 판정 (가이드 §2)
    counter_wants = set(counter_p.purchase_value_props if req.vantage == Vantage.seller
                        else counter_p.sell_value_props)
    my_offer = set(req.intent.value_props)
    if not counter_wants:
        dims.append(CategoryJudgment(dimension=Dimension.purpose_alignment, verdict=_CAUTION,
                    rationale="상대의 진짜 니즈는 외부 자료에 없다 — 접촉·질문으로 먼저 확인 필요 (외부 풀)."))
    elif my_offer & counter_wants:
        dims.append(CategoryJudgment(dimension=Dimension.purpose_alignment, verdict=_FIT,
                    rationale=f"제안 방향({', '.join(v.value for v in my_offer & counter_wants)})이 상대가 원하는 것과 일치."))
    else:
        dims.append(CategoryJudgment(dimension=Dimension.purpose_alignment, verdict=_CAUTION,
                    rationale="상대가 원하는 가치와 제안 방향이 어긋남 — 딜 구조 변형 검토."))

    # [자원 보완성] — '맞물리느냐(fit)'. 내가 푸는 문제 ↔ 상대가 겪는 문제
    comp = overlap(f"{self_p.problem_solved.value} {self_p.solution.value}",
                   profile_pain_text(counter_p))
    if comp >= 0.25:
        dims.append(CategoryJudgment(dimension=Dimension.resource_complementarity, verdict=_FIT,
                    rationale="내가 가진 것이 상대의 결핍과 강하게 맞물린다 "
                              f"(보완성 신호 {comp:.2f})."))
    elif comp >= 0.10:
        dims.append(CategoryJudgment(dimension=Dimension.resource_complementarity, verdict=_CAUTION,
                    rationale=f"보완성 신호가 약함({comp:.2f}) — 상대의 결핍을 접촉으로 확인 필요."))
    else:
        dims.append(CategoryJudgment(dimension=Dimension.resource_complementarity, verdict=_UNFIT,
                    rationale=f"상대의 문제와 내 솔루션이 맞물리지 않음({comp:.2f})."))

    # [사업단계 호환]
    s1, s2 = infer_stage(self_p), infer_stage(counter_p)
    if {"chain"} & {s1, s2} and {"seed", "startup"} & {s1, s2}:
        dims.append(CategoryJudgment(dimension=Dimension.stage_compatibility, verdict=_CAUTION,
                    rationale=f"규모 격차({s1}↔{s2}) — 조달·신뢰 프로세스 확인 필요."))
    else:
        dims.append(CategoryJudgment(dimension=Dimension.stage_compatibility, verdict=_FIT,
                    rationale=f"규모·단계({s1}↔{s2})가 현실적으로 호환."))

    # [실증 가능성] — 레퍼런스는 '구매자 시장' 기준으로 본다 (#01: 동남아 레퍼런스 0)
    seller_p = self_p if req.vantage == Vantage.seller else counter_p
    buyer_p = counter_p if req.vantage == Vantage.seller else self_p
    refs = seller_p.references
    local_ref = any(buyer_p.basic.country in r for r in refs)
    if refs and local_ref:
        dims.append(CategoryJudgment(dimension=Dimension.demonstrability, verdict=_FIT,
                    rationale="해당 시장 레퍼런스 보유."))
    elif refs:
        dims.append(CategoryJudgment(dimension=Dimension.demonstrability, verdict=_CAUTION,
                    rationale="레퍼런스는 있으나 현지 레퍼런스 부재 — 소규모 PoC 선검증 권장."))
    else:
        dims.append(CategoryJudgment(dimension=Dimension.demonstrability, verdict=_CAUTION,
                    rationale="검증 사례 없음 — 첫 사례임을 명시하고 검증 장치 필요."))

    # buy-side 전용 +2차원 (JDG-02, 기획서 7.12)
    if req.vantage == Vantage.buyer:
        if counter_p.references or counter_p.traction:
            dims.append(CategoryJudgment(dimension=Dimension.substitute_comparison, verdict=_FIT,
                        rationale="검증된 실적을 가진 상대 — 기존 대안 대비 비교우위 신호."))
        else:
            dims.append(CategoryJudgment(dimension=Dimension.substitute_comparison, verdict=_CAUTION,
                        rationale="대안 대비 비교우위 미확인 — 기존 대안(현지 업체·현상 유지)과 상대평가 필요."))
        low_downside = any(("원복" in i.value or "원상 복구" in i.value or "PoC" in i.value)
                           for i in (req.counterpart_private_state or PrivateState()).items
                           + req.self_private_state.items)
        if low_downside:
            dims.append(CategoryJudgment(dimension=Dimension.opportunity_cost, verdict=_FIT,
                        rationale="소규모 시작·원복 보장 등으로 다운사이드가 낮음 — '밑져야 본전' 성립."))
        else:
            dims.append(CategoryJudgment(dimension=Dimension.opportunity_cost, verdict=_CAUTION,
                        rationale="수용 시 묶이는 자원·포기 대안 미확인 — 소규모 PoC로 기회비용 축소 검토."))
    return dims


def _collect_risks(req: JudgeRequest, dims: list[CategoryJudgment]) -> list[Risk]:
    risks: list[Risk] = []
    # 사전정보에서 리스크 3분류 (가이드 §4)
    for item in req.self_private_state.items:
        if "권한" in item.key or "선결" in item.key:
            risks.append(Risk(type=RiskType.precondition,
                              description=f"{item.key}: {item.value} — 미충족 시 결렬.",
                              check_method="접촉 시 최우선 확인"))
        elif "통제" in item.value:
            risks.append(Risk(type=RiskType.dismissed,
                              description=f"{item.key} — 통제 가능하므로 리스크에서 기각."))
    # 차원 간 불일치 → 자동 확인 리스크 (JDG-03)
    verdicts = {d.verdict for d in dims}
    if len(verdicts) > 1:
        for d in dims:
            if d.verdict != _FIT:
                risks.append(Risk(type=RiskType.profitability,
                                  description=f"[{d.dimension.value}] {d.rationale}",
                                  check_method="진행 전 해당 차원 신호로 검증 (예: 리뷰·점유율·실데이터)"))
    return risks


def _effective_willingness(req: JudgeRequest) -> Willingness | None:
    """목적함수별로 보는 Willingness가 다르다 (7.4): 게이트=자기측, 탐색예산=상대측."""
    if req.objective == Objective.willingness_gate:
        return req.self_profile.willingness_purchase
    return req.counterpart_profile.willingness_purchase


def _decide(dims: list[CategoryJudgment], willingness: Willingness | None
            ) -> tuple[DecisionType, str]:
    cautions = sum(1 for d in dims if d.verdict == _CAUTION)
    unfits = sum(1 for d in dims if d.verdict == _UNFIT)
    open_w = willingness in {Willingness.very_high, Willingness.high, Willingness.medium}

    if unfits >= 2:
        return DecisionType.terminate, "복수 차원 부적합 — 추격 자원을 회수한다(탐색 예산 회수)."
    if unfits == 1:
        return DecisionType.hold, "부적합 차원 존재 — 해소 신호 없이는 진행 보류."
    if cautions == 0:
        return DecisionType.recommend, "전 차원 적합 — 추천."
    if cautions <= 2:
        if open_w:
            return (DecisionType.conditional,
                    "일부 차원 '주의'이나 상대의 열림 정도(Willingness)가 이를 상회 — "
                    "리스크 명시 조건부 추천 (확신 × 열림 정도의 종합 추론).")
        if willingness is None:
            return (DecisionType.conditional,
                    "일부 차원 '주의' + 상대 Willingness 미상(외부 풀) — "
                    "확인 리스크를 명시한 조건부 추천, 접촉으로 검증.")
        return (DecisionType.hold,
                "일부 차원 '주의'이고 상대가 소극적 — 노출 기준 미달로 보류.")
    if open_w:
        return DecisionType.conditional, "주의 차원 많으나 상대가 적극적 — 조건부."
    return DecisionType.hold, "주의 차원 다수 — 보류."


def _reasoning_moves(req: JudgeRequest, dims: list[CategoryJudgment],
                     risks: list[Risk], deal_structure: str | None) -> list[str]:
    moves = ["risk_triage"]   # 리스크 3분류는 항상 수행
    if deal_structure:
        moves.append("intersection_sizing")
    if req.vantage == Vantage.buyer:
        moves.append("value_asymmetry")   # 내가 파는 가치 ≠ 상대가 사는 가치 (#02)
    stage_caution = any(d.dimension == Dimension.stage_compatibility and d.verdict != _FIT
                        for d in dims)
    strategic = any("레퍼런스" in i.value or "단계" in i.value
                    for i in req.self_private_state.items)
    if stage_caution and strategic:
        moves.append("stage_override")    # 약한 차원의 전략적 역전 (#01)
    if req.intent.notes and "인바운드" in req.intent.notes:
        moves.append("inbound_authenticity_gate")
    return moves


_SOFT_YES = {DecisionType.recommend, DecisionType.conditional}


def _apply_consistency_gate(result: JudgeResult, agreement: "float | None",
                            settings) -> None:
    """소프트 판단 → 하드 코드 게이트 이전 (L3). in-place로 result를 조인다.

    두 이전:
    (a) confidence_band를 일치율에서 결정적으로 도출 — LLM 자가보고 신뢰도(미보정)를
        코드 규칙으로 대체. high≥0.8 / medium≥임계 / low<임계.
    (b) 일치율 < 임계 → needs_human=True + 자동 '추천'을 hold로 캡. 저합의 추천이 사람
        검토 없이 나가는 것을 결정적으로 차단(deal-breaker 게이트와 같은 하드 성격)."""
    if agreement is None:      # 미계측(단일 표본) — 신호 없으면 게이트 발동 안 함
        return
    tau = settings.judge_agreement_threshold
    result.confidence_band = (
        ConfidenceBand.high if agreement >= 0.8
        else ConfidenceBand.medium if agreement >= tau
        else ConfidenceBand.low)
    if agreement < tau:
        result.needs_human = True
        if result.decision in _SOFT_YES:
            result.decision = DecisionType.hold
            result.decision_rationale = (
                f"자기일관성 일치율 {agreement:.2f} < 임계 {tau:.2f} — "
                f"저합의 자동추천 차단, 사람 검토로 보류 (L3 게이트). "
                f"원 판정 근거: {result.decision_rationale}")
        progress.log("Judge", f"⚠ L3 게이트 — 일치율 {agreement:.2f}<{tau:.2f}: "
                              f"needs_human=True, decision→{result.decision.value}")


def _vote_llm_judge(req: JudgeRequest, extractor, deep: bool, samples: int
                    ) -> tuple[JudgeResult, "float | None"]:
    """자기일관성 투표 (L2) — LLM 판단을 k회 표집해 범주형 decision을 다수결.

    범주형 결정의 재현성은 σ²/n이 아니라 지수적 집중이 기대되는 축이라(FORMALIZATION.md §4.3),
    평균이 아니라 '다수결 + 일치율'을 쓴다. k=1이면 투표 없음 — 일치율은 None(미계측)이며,
    L3 게이트도 신호가 없으면 발동하지 않는다(측정 없는 확신을 만들지 않는다)."""
    from ..eval.variance import mode
    if samples <= 1:
        return _llm_judge(req, extractor, deep=deep), None

    from collections import Counter
    results: list[JudgeResult] = []
    for i in range(samples):
        progress.log("Judge", f"자기일관성 표본 {i + 1}/{samples}")
        results.append(_llm_judge(req, extractor, deep=deep))
    decisions = [r.decision for r in results]
    winner, count = mode(decisions)
    agreement = count / len(decisions)

    # 동점 = 합의 실패 (적대적 검토 확정 F2) — 예전엔 표본 도착 순서가 승자를 정해
    # {hold×2, terminate×2}가 순서에 따라 '보류'/'매칭 종료'로 갈렸고, terminate는
    # L3 캡 대상도 아니었다. 동점이면 결정을 유보(hold)하고 사람에게 강제 라우팅한다:
    # recommend(행동)도 terminate(포기)도 코인플립으로 확정할 결정이 아니다.
    tie = sum(1 for c in Counter(decisions).values() if c == count) > 1
    if tie:
        chosen = next((r for r in results if r.decision == DecisionType.hold), results[0])
        if chosen.decision != DecisionType.hold:
            chosen.decision = DecisionType.hold
        chosen.needs_human = True
        chosen.decision_rationale = (
            f"자기일관성 동점({count}/{samples}) — 합의 실패로 결정 유보, 사람 검토 필요. "
            f"표본 분포: {dict(Counter(d.value for d in decisions))}. "
            f"원 근거: {chosen.decision_rationale}")
        progress.log("Judge", f"⚠ 다수결 동점 — hold로 유보 + 사람 라우팅 "
                              f"(분포 {dict(Counter(d.value for d in decisions))})")
        return chosen, agreement

    # 승리 결정을 낸 대표 표본을 채택 (그 근거·차원판정이 다수와 정합)
    chosen = next(r for r in results if r.decision == winner)
    progress.log("Judge", f"다수결 결정: {winner.value} · 일치율 "
                          f"{agreement:.2f} ({count}/{samples})")
    return chosen, agreement


def _ontology_hint(req: JudgeRequest) -> "str | None":
    """실 산업 협상 사례에서 뽑은 참고 힌트 (app/ontology, 선택·결정적).

    judge_user(req)와 별개 함수에서 각각 호출돼도 같은 req면 항상 같은 문자열이
    나온다(순수 함수 + 캐시) — _audit_judge가 재호출해도 실제 전송 프롬프트와
    감사 로그의 input_text가 어긋나지 않는다.
    """
    try:
        from ..ontology.retrieve import domain_hint
    except Exception:                             # 재료 파일 문제로 판단을 막지 않는다
        return None
    p1, p2 = req.self_profile, req.counterpart_profile
    return domain_hint(p1.basic.industry, p1.description,
                       p2.basic.industry, p2.description)


def _llm_judge(req: JudgeRequest, extractor, deep: bool = True) -> JudgeResult:
    """LLM 판단 경로 — 프롬프트가 판단 구조를, 스키마가 출력 계약을 강제한다.
    출력 계약은 규칙 경로와 동일하므로 API·테스트 구조는 그대로다."""
    from .. import progress
    progress.log("Judge", f"{req.self_profile.basic.name} → "
                          f"{req.counterpart_profile.basic.name} 판단 시작 "
                          f"({req.vantage.value} 렌즈 · {'깊은 추론' if deep else '표준'} 경로)")
    hint = _ontology_hint(req)
    if hint:
        progress.log("Judge", f"온톨로지 참고 힌트 적용 — {hint[:70]}...")
    data = extractor.extract_json(JUDGE_SYSTEM, judge_user(req, hint), JUDGE_SCHEMA,
                                  deep=deep)   # 판단은 기본 깊은 추론 (7장 크라운 주얼)
    if not data.get("fit_reasons"):
        data["fit_reasons"] = ["판단 근거 부족 — 접촉으로 확인 필요"]
    if not data.get("reasoning_moves"):
        data["reasoning_moves"] = ["risk_triage"]
    with progress.node("validate", "차원 계약 검증 (JDG-02)"):
        result = JudgeResult.model_validate(data)
        # buy 렌즈 차원 계약 검증 — 누락 시 명시적 실패가 조용한 오판보다 낫다
        dims = {d.dimension for d in result.category_judgments}
        required = set(Dimension) if req.vantage == Vantage.buyer else \
            set(Dimension) - set(BUY_ONLY_DIMENSIONS)
        missing = required - dims
        if missing:
            from ..errors import EngineError
            raise EngineError(502, "llm_error",
                              f"판단 차원 누락: {[d.value for d in missing]} — 재시도 필요")
        progress.log("Judge", f"판단 완료 — 결정: {result.decision.value} "
                              f"({len(result.category_judgments)}차원 · "
                              f"리스크 {len(result.risks)}건)")
    return result


def _audit_judge(req: JudgeRequest, result: JudgeResult,
                 pre_gate_decision: "str | None" = None,
                 engine_mode: str = "llm") -> None:
    """감사 가능 로그 (SYS-04) — 입력·추론 궤적·결정 저장 (HITL 검토·재학습용).

    적대적 검토 확정(F3): L3 게이트가 decision을 덮어쓴 뒤 감사가 기록돼 LLM의
    원 결정이 소실됐다 — 재학습 라벨·HITL 검토에 필요한 값이라 pre-gate 결정과
    투표 신호를 함께 남긴다.

    engine_mode: 적대적 검토 추가 확정(C2) — 이게 없으면 규칙 기반(mock) 판단이
    to_sft()에서 '전문가 판단'으로 둔갑한다. mock 경로는 명시적으로 "mock"을 넘긴다."""
    from .. import audit
    audit.record("judge", {
        "engine_mode": engine_mode,
        "self": req.self_profile.basic.name,
        "counterpart": req.counterpart_profile.basic.name,
        "vantage": req.vantage.value, "objective": req.objective.value,
        "intent": req.intent.model_dump(mode="json"),
        "decision": result.decision.value,
        "decision_pre_gate": pre_gate_decision or result.decision.value,
        "decision_rationale": result.decision_rationale,
        "sample_agreement": result.sample_agreement,
        "needs_human": result.needs_human,
        "verdicts": {d.dimension.value: d.verdict.value
                     for d in result.category_judgments},
        "risks": [f"{r.type.value}: {r.description}" for r in result.risks],
        "trajectory": result.trajectory,
        # SFT 학습 자산화 — 판단 입력 프롬프트 전문 + 전체 결과 JSON (재학습 쌍).
        # _ontology_hint(req)는 결정적이라 _llm_judge가 실제로 보낸 것과 동일하다.
        "input_text": judge_user(req, _ontology_hint(req)),
        "result_json": result.model_dump(mode="json"),
    })


def judge(req: JudgeRequest, deep: bool = True) -> JudgeResult:
    # 결격 게이트 — LLM 경로에서도 하드 차단·비노출은 항상 규칙으로 보장 (JDG-04)
    with progress.node("gate.dealbreaker", "결격 게이트 (JDG-04)"):
        check_deal_breakers(req.self_profile, req.counterpart_profile)
        progress.log("게이트", "deal-breaker 없음 — 판단 진행")

    settings = get_settings()
    extractor = get_extractor(settings)
    if extractor is not None:
        result, agreement = _vote_llm_judge(
            req, extractor, deep, settings.judge_samples)   # L2 자기일관성 투표
        result.sample_agreement = agreement
        pre_gate = result.decision.value                    # 감사용 원 결정 보존 (F3)
        _apply_consistency_gate(result, agreement, settings)   # L3 하드 게이트
        with progress.node("audit", "감사 로그 (SYS-04)"):
            _audit_judge(req, result, pre_gate_decision=pre_gate, engine_mode="llm")
        return result

    with progress.node("rules.judge", "규칙 기반 판단 (Mock)"):
        dims = _judge_dimensions(req)
    risks = _collect_risks(req, dims)
    willingness = _effective_willingness(req)
    decision, rationale = _decide(dims, willingness)

    fit_reasons = [d.rationale for d in dims if d.verdict == _FIT] or \
                  ["판단 근거 부족 — 접촉으로 확인 필요"]
    gap_factors = [d.rationale for d in dims if d.verdict != _FIT]

    # 딜 구조: 실증 '주의'면 소규모 PoC — 판매자 ROI 하한 ∩ 구매자 손실 허용 상한 (#01)
    deal_structure = None
    if any(d.dimension == Dimension.demonstrability and d.verdict == _CAUTION for d in dims):
        deal_structure = ("소규모 PoC로 시작 (판매자 ROI 하한 ∩ 구매자 손실 허용 상한의 "
                          "교집합 지점, 예: 객실 4개 규모).")

    seller_p = req.self_profile if req.vantage == Vantage.seller else req.counterpart_profile
    reference = seller_p.references[0] if seller_p.references else "first_case"
    match_summary = MatchSummary(
        problem_solution=(f"{req.counterpart_profile.problem_solved.value} → "
                          f"{seller_p.solution.value}"
                          if req.vantage == Vantage.seller else
                          f"{req.self_profile.problem_solved.value} → "
                          f"{seller_p.solution.value}"),
        value_proposition=", ".join(v.value for v in req.intent.value_props),
        reference=reference,
    )

    cautions = sum(1 for d in dims if d.verdict != _FIT)
    band = (ConfidenceBand.high if cautions == 0
            else ConfidenceBand.medium if cautions <= 2 else ConfidenceBand.low)

    trajectory = "\n".join(f"[{d.dimension.value}] {d.verdict.value}: {d.rationale}"
                           for d in dims) + f"\n[결정] {decision.value}: {rationale}"

    result = JudgeResult(
        category_judgments=dims,
        risks=risks,
        reasoning_moves=_reasoning_moves(req, dims, risks, deal_structure),
        trajectory=trajectory,
        decision=decision,
        decision_rationale=rationale,
        fit_reasons=fit_reasons,
        gap_factors=gap_factors,
        match_summary=match_summary,
        deal_structure=deal_structure,
        confidence_band=band,
    )
    with progress.node("audit", "감사 로그 (SYS-04)"):
        _audit_judge(req, result, engine_mode="mock")
    return result
