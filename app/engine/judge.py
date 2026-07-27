"""Judge — 후보 쌍 → 점수가 아닌 구조화 판단 (JDG-01~12, 기획서 7장).

두 렌즈 = 단일 로직 + 3파라미터(관점·목적함수·사전정보) 교체 (JDG-06).
온톨로지 카테고리별 자기일관성: 차원별로 따로 판정하고 모은다 (JDG-02).
차원 간 불일치 = 자동 리스크 신호 (JDG-03). Willingness는 임계값이 아니라 맥락 (JDG-08).

v0: 규칙 기반 차원 판정. Phase 2: EXAONE CoT 파인튜닝 모델 호출로 교체 (JDG-12)
— 출력 계약(JudgeResult)은 동일하므로 이 모듈의 판정 함수만 갈아끼운다.
"""
from .. import progress
from ..config import get_settings
from ..schemas import (JUDGE_DIMENSIONS, AxisStatus, ConfidenceBand,
                       DecisionType, Dimension, JudgeRequest, JudgeResult,
                       Vantage, VerdictType, Willingness)
from .dealbreakers import check_deal_breakers
from .llm import get_extractor
from .prompts import JUDGE_SCHEMA, JUDGE_SYSTEM, judge_user


_SOFT_YES = {DecisionType.recommend, DecisionType.conditional}


def _apply_evidence_gate(result: JudgeResult) -> None:
    """근거 없는 recommend 차단 (L3 하드 게이트, in-place).

    실측(공감만세×AdForUs): 5차원이 **전부 caution**인데 LLM이 "전략적 잠재력"을
    이유로 recommend를 냈다 — 근거 없는 낙관이 최상위 추천으로 나가는 오판. 판단은
    "차원 근거의 집계"라는 계약(JDG-02)상, fit이 하나도 없으면 recommend는 성립할 수
    없다. 결정을 프롬프트가 아니라 코드가 캡한다(decision_gate.py와 같은 원리).

    conditional로만 낮춘다 — 근거 부족이지 부적합 판정은 아니므로 hold/terminate로
    과교정하지 않는다(정보 부재 ≠ unfit)."""
    if result.decision != DecisionType.recommend:
        return
    dims = result.category_judgments or []
    if not dims or any(d.verdict == VerdictType.fit for d in dims):
        return
    result.decision = DecisionType.conditional
    result.decision_rationale = (
        f"차원 판정에 fit이 하나도 없음({len(dims)}개 전부 caution/unfit) — "
        f"근거 없는 자동 recommend를 조건부로 캡 (L3 근거 게이트). "
        f"원 판정 근거: {result.decision_rationale}")
    progress.log("Judge", f"⚠ L3 근거 게이트 — fit 차원 0개: "
                          f"recommend→conditional (근거 없는 추천 차단)")


def _apply_decision_gate(result: JudgeResult) -> None:
    """축 상태 → 결정을 **코드가** 유도 (judge_cases/decision_gate.py 이식, in-place).

    배경(EXAONE_이식성_검증.md): K-EXAONE은 축은 충실히 채우지만 최종 결정 라벨이
    conditional로 쏠린다(9세션 중 5/6). 결정을 프롬프트가 아니라 코드가 내리면
    모델 교체에 강건해진다 — 축 판정=모델, 결정=코드.

    이 규칙은 예전 우리 스키마에선 절반이 발화조차 못 했다: exploitation/dealbreaker
    플래그도, status=unknown도 표현할 자리가 없었기 때문이다. 축을 BB로 바꾸면서
    비로소 6개 규칙이 전부 성립한다.

    우선순위는 원본 그대로(Gemini 9세션 캘리브레이션). 다만 그 9건은 캘리브레이션
    셋 자체라 8/9 일치는 in-sample 수치임에 유의 — 일반화 근거가 아니다.
    """
    dims = result.category_judgments or []
    if not dims:
        return
    unfit = [d for d in dims if d.verdict == VerdictType.unfit]
    caution = [d for d in dims if d.verdict == VerdictType.caution]
    unknown = [d for d in dims if d.status == AxisStatus.unknown]

    if any(d.exploitation_detected for d in dims):
        new, why = DecisionType.terminate_values, "착취 신호 감지 — 관계 차단 철수"
    elif any(d.dealbreaker for d in dims):
        new, why = DecisionType.terminate_structural, "선결 게이트 deal-breaker — 구조적 미달"
    elif len(unfit) >= 2:
        new, why = (DecisionType.terminate_structural,
                    f"복수 축 부적합({len(unfit)}) — {[d.dimension.value for d in unfit[:3]]}")
    elif len(unknown) >= 3:
        new, why = DecisionType.hold, f"미검증 축 {len(unknown)}개 — 판단 재료 부족, 유보"
    elif caution or unknown or unfit:
        new, why = (DecisionType.conditional,
                    f"주의 {len(caution)}·미검증 {len(unknown)}·부적합 {len(unfit)} — "
                    f"조건·검증 계획 필요")
    else:
        new, why = DecisionType.recommend, "전 축 적합·미검증 0 — 추천"

    if new != result.decision:
        progress.log("Judge", f"⚠ 결정 게이트 — {result.decision.value}→{new.value}: {why}")
        result.decision_rationale = (f"{why} (결정 게이트: 축 상태에서 코드가 유도). "
                                     f"원 판정 근거: {result.decision_rationale}")
        result.decision = new


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
    p1, p2 = req.self_profile, req.counterpart_profile
    parts: list[str] = []
    # ① 도메인 실증 루브릭 (materials/*.md — demonstrability 한 줄)
    try:
        from ..ontology.retrieve import domain_hint
        d = domain_hint(p1.basic.industry, p1.description,
                        p2.basic.industry, p2.description)
        if d:
            parts.append(d)
    except Exception:                             # 재료 파일 문제로 판단을 막지 않는다
        pass
    # ② 박사님 가설 카드 + feedback_loop 조정 규칙 (렌즈=vantage로 매칭)
    try:
        from ..ontology.hypotheses import ontology_cards
        query = f"{p1.basic.industry} {p1.description} {p2.basic.industry} {p2.description}"
        cards = ontology_cards(req.vantage.value, query)
        if cards:
            parts.append(cards)
    except Exception:
        pass
    return "\n\n".join(parts) if parts else None


import re as _re

_NUM_TOKEN = _re.compile(r"\d[\d,.]*\s*(?:%|억|천만|만\s*원|만원|명|개사|건|배|호점)?")
_LATIN_TOKEN = _re.compile(r"[A-Za-z][A-Za-z&.-]{2,}")
# 판단 어휘(스키마 enum·축명)는 입력이 아니라 판단 언어에서 온다 — 환각 아님.
# 축·결정·판정 이름은 **enum에서 파생**한다: 예전엔 손으로 나열해 뒀는데, 축 이름이
# 바뀌면 정상 축명이 '입력에 없는 영문 토큰'으로 몰려 근거가 삭제된다(조용한 손실).
def _vocab_tokens(*enums) -> set[str]:
    out: set[str] = set()
    for e in enums:
        for m in e:
            out.update(t.lower() for t in _re.split(r"[^A-Za-z]+", m.value) if t)
    return out


_JUDGE_VOCAB = _vocab_tokens(Dimension, DecisionType, VerdictType, AxisStatus) | {
    "poc", "mou", "esg", "oem", "odm", "b2b"}


def _strip_ungrounded_claims(result: JudgeResult, req: JudgeRequest,
                             hint: "str | None") -> int:
    """fit_reasons의 환각 주장 코드 집행 (reference-guided grading의 사후 검증판).

    좁고 안전한 규칙만 쓴다: fit_reason 안의 '수치'와 '영문 고유명사'가 판단 입력
    (두 프로필 + 의도 + 온톨로지 힌트) 어디에도 없으면, 그 주장은 입력에서 나올 수
    없는 것 — 제거하고 센다. 패러프레이즈 자체를 벌하지 않도록 일반 한국어 서술은
    검사하지 않는다 (과잉 폐기가 과소 폐기보다 위험한 지점).

    fit_reasons만 대상 — compose가 claim_trace로 이 목록을 인용하므로, 환각 주장이
    남으면 콜드메일에 근거 없는 수치가 실린다(가장 비싼 실패). 반환: 제거 수.
    """
    haystack = " ".join(judge_user(req, hint).split()).lower()
    kept, removed = [], 0
    for reason in result.fit_reasons:
        tokens = ([t.strip() for t in _NUM_TOKEN.findall(reason)]
                  + [t for t in _LATIN_TOKEN.findall(reason)
                     if t.lower() not in _JUDGE_VOCAB])
        bad = [t for t in tokens
               if t and " ".join(t.split()).lower() not in haystack]
        if bad:
            removed += 1
            progress.log("검증", f"⚠ 근거 없는 주장 제거 — fit_reason에 입력에 없는 "
                                 f"토큰 {bad[:3]}: \"{reason[:50]}…\"")
            continue
        kept.append(reason)
    if removed:
        result.fit_reasons = kept or ["판단 근거 부족 — 접촉으로 확인 필요"]
    return removed


def _llm_judge(req: JudgeRequest, extractor, deep: bool = True) -> JudgeResult:
    """LLM 판단 경로 — 프롬프트가 판단 구조를, 스키마가 출력 계약을 강제한다.
    출력 계약은 규칙 경로와 동일하므로 API·테스트 구조는 그대로다.

    실측(2026-07-27, 축을 7→10개로 늘린 직후 첫 실 루프): K-EXAONE이 finish=stop으로
    스스로 멈추고 뒤 4축(BB7~BB10)을 아예 안 쓴 채 응답을 끝냈다(잘림이 아니라 모델이
    "충분하다"고 판단하고 멈춘 것). 처음엔 놓친 축을 콕 집어 재시도했는데, 그 재시도가
    deep=True 전체(추론+구조화 2단계)를 다시 돌아 173초가 걸렸다 — 이 엔진은 전체
    loop 3분 예산으로 설계됐는데(FORMALIZATION.md), 후보 하나의 축 누락이 예산 전체를
    먹어버린다. LLM을 다시 부르는 대신 **로컬에서 채운다**: 누락 축은 verdict=na,
    status=unknown으로 합성 — 지연 0ms고, 이미 검증된 decision_gate의
    "미검증≥3 → hold" 경로로 정직하게 수렴한다(casablanca 실측: 10축 전부
    unknown/caution → hold, 이 백필과 결과적으로 같은 자리)."""
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
        # 축 계약 검증 — 렌즈와 무관하게 BB 10축 전부를 요구한다(JUDGE_DIMENSIONS
        # 주석 참고). 누락은 재호출이 아니라 로컬 백필로 3분 예산을 지킨다.
        dims = {d.dimension for d in result.category_judgments}
        missing = set(JUDGE_DIMENSIONS) - dims
        if missing:
            progress.log("Judge", f"⚠ 축 누락({len(missing)}개, LLM 응답에 없음) — "
                                  f"재호출 없이 미검증으로 백필(3분 예산 보존): "
                                  f"{[d.value for d in missing]}")
            result.category_judgments += [
                CategoryJudgment(dimension=d, verdict=VerdictType.na,
                                 status=AxisStatus.unknown,
                                 rationale="모델 응답에 이 축이 없었음 — 재호출 없이 미검증 처리")
                for d in missing]
        n_stripped = _strip_ungrounded_claims(result, req, hint)
        progress.log("Judge", f"판단 완료 — 결정: {result.decision.value} "
                              f"({len(result.category_judgments)}차원 · "
                              f"리스크 {len(result.risks)}건"
                              + (f" · 환각 주장 제거 {n_stripped}건" if n_stripped
                                 else "") + ")")
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
    # mock 제거(2026-07) 이후 get_extractor는 None을 돌려주지 않는다 — 성공하거나
    # config_error로 즉시 실패한다. 그런데 여기 `if extractor is not None:` 분기와
    # 그 아래 규칙 기반 경로(57줄 + 헬퍼 4개)가 그대로 남아 **도달 불가 코드**가
    # 되어 있었다. 있지도 않은 폴백이 있는 것처럼 보이는 게 더 위험해 걷어낸다.
    extractor = get_extractor(settings)
    result, agreement = _vote_llm_judge(
        req, extractor, deep, settings.judge_samples)   # L2 자기일관성 투표
    result.sample_agreement = agreement
    pre_gate = result.decision.value                    # 감사용 원 결정 보존 (F3)
    _apply_decision_gate(result)                        # 축 상태 → 결정 (코드)
    _apply_evidence_gate(result)                        # L3 근거 게이트
    _apply_consistency_gate(result, agreement, settings)   # L3 하드 게이트
    with progress.node("audit", "감사 로그 (SYS-04)"):
        _audit_judge(req, result, pre_gate_decision=pre_gate, engine_mode="llm")
    return result


def judge_many(reqs: "list[JudgeRequest]", deep: bool = True,
               max_workers: int = 4) -> "list[JudgeResult | Exception]":
    """여러 판단을 병렬 실행 (loop 3분 목표 — judge×N 순차가 병목).

    각 판단은 독립이라 동시에 쏜다. contextvars.copy_context()로 부모 job의
    RunLog(progress 컨텍스트)를 각 워커에 전파 — 로그·llm_calls가 한 job에
    정확히 모인다(RunLog는 lock으로 thread-safe). K-EXAONE dedicated endpoint의
    동시성 한계를 고려해 max_workers 상한(기본 4).

    캐스케이드(상위 K만 판단)는 호출자가 reqs를 슬라이싱해 넘긴다 — 이 함수는
    병렬화만 담당한다. 한 판단이 던진 예외(DealBreaker 등)는 그 자리에 담아
    반환한다(배치를 죽이지 않음, 결과 순서 = 입력 순서)."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    if not reqs:
        return []
    if len(reqs) == 1:
        try:
            return [judge(reqs[0], deep=deep)]
        except Exception as e:                       # noqa: BLE001
            return [e]

    progress.log("판단", f"병렬 판단 시작 — {len(reqs)}건 "
                         f"(동시 최대 {min(len(reqs), max_workers)})")

    def _one(req):
        try:
            return judge(req, deep=deep)
        except Exception as e:                       # noqa: BLE001 — 후보별 격리
            return e

    results: "list[JudgeResult | Exception | None]" = [None] * len(reqs)
    with ThreadPoolExecutor(max_workers=min(len(reqs), max_workers)) as ex:
        futs = {}
        for i, req in enumerate(reqs):
            ctx = contextvars.copy_context()         # 각 워커 = 독립 컨텍스트, 같은 RunLog
            futs[ex.submit(ctx.run, _one, req)] = i
        for fut in futs:
            results[futs[fut]] = fut.result()
    return results
