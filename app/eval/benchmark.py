"""평가 벤치마크 하네스 (Phase 5 EVL — '평가' 절반) — 골든 케이스로 엔진 채점.

FORMALIZATION.md의 측정 원칙을 정답 대조로 확장한다: L0(분산 계측)이 '재현성'을
쟀다면, 이 벤치마크는 '정확도'를 잰다 — 그리고 LLM 경로는 k회 실행해 정확도와
안정성(재현성)을 동시에 보고한다.

정직성: 골든셋은 외부 검증 라벨이 아니라 시드 풀의 '의도된 역할'을 인코딩한 소량 v0다
(app/eval/golden_cases.json의 _meta.honest_limits 참조). 채점의 1차 지표는 정확 decision이
아니라 **극성(engage/defer/reject)** 일치다 — recommend/conditional은 둘 다 진행이므로.

Mock vs LLM 베이스라인 비교 = 규칙 기반 대비 실추론의 이득을 데이터로 본다.
L2 파인튜닝 모델이 붙으면 같은 하네스에 provider만 바꿔 base vs tuned 비교로 확장된다.
"""
import json
from collections import Counter
from pathlib import Path
from typing import Optional

from ..engine import pool as pool_module
from ..engine.judge import judge
from ..engine.retrieve import retrieve
from ..errors import DealBreaker, EngineError, NoStrongCandidate
from ..eval.variance import agreement_rate
from ..schemas import (Intent, JudgeRequest, Objective, RetrieveDirection,
                       RetrieveRequest, Vantage)

_GOLDEN = Path(__file__).resolve().parent / "golden_cases.json"

POLARITY = {"recommend": "engage", "conditional": "engage",
            "hold": "defer", "terminate": "reject"}


def load_golden(path: Optional[str] = None) -> dict:
    return json.loads(Path(path or _GOLDEN).read_text())


def _profile(ref: str):
    rec = pool_module.find(ref)
    if rec is None:
        raise ValueError(f"골든 케이스가 참조한 풀 회사 '{ref}' 없음")
    return rec.profile


# ── judge 케이스 실행·채점 ──────────────────────────────────────────

def _run_judge_once(case: dict) -> dict:
    """judge 1회 실행 → {decision, polarity, deal_breaker}. 결격 게이트는 정상 결과."""
    req = JudgeRequest(
        vantage=Vantage(case["vantage"]), objective=Objective(case["objective"]),
        self_profile=_profile(case["self_ref"]),
        counterpart_profile=_profile(case["counterpart_ref"]),
        intent=Intent(**case["intent"]))
    try:
        result = judge(req)
    except DealBreaker:
        return {"decision": "deal_breaker", "polarity": "reject", "deal_breaker": True}
    return {"decision": result.decision.value,
            "polarity": POLARITY.get(result.decision.value, "?"),
            "deal_breaker": False}


def _score_judge(runs: list[dict], expected: dict) -> dict:
    """정확 decision·극성·기권(distractor) 정확도 + k회 안정성."""
    decisions = [r["decision"] for r in runs]
    polarities = [r["polarity"] for r in runs]
    majority_pol = Counter(polarities).most_common(1)[0][0]

    hits = []
    for r in runs:
        if "decision" in expected:            # 정확 decision 기대
            ok = r["decision"] == expected["decision"]
            if not ok and expected.get("deal_breaker_ok") and r["deal_breaker"]:
                ok = True
        elif "polarity" in expected:          # 극성 기대
            ok = r["polarity"] == expected["polarity"]
            if not ok and expected.get("deal_breaker_ok") and r["deal_breaker"]:
                ok = True
        elif "polarity_not" in expected:      # 이 극성이면 안 됨 (distractor)
            ok = r["polarity"] != expected["polarity_not"]
        else:
            ok = False
        hits.append(ok)
    return {"accuracy": sum(hits) / len(hits), "stability": agreement_rate(decisions),
            "majority_polarity": majority_pol,
            "decisions": dict(Counter(decisions))}


# ── retrieve 케이스 실행·채점 ───────────────────────────────────────

def _run_retrieve_once(case: dict) -> list[str]:
    """retrieve 1회 → 강한 후보 company_id 랭킹. NoStrongCandidate면 빈 리스트."""
    req = RetrieveRequest(
        requester_profile=_profile(case["requester_ref"]),
        intent=Intent(**case["intent"]),
        direction=RetrieveDirection(case["direction"]),
        pool="external", k=10)
    try:
        res = retrieve(req)
    except NoStrongCandidate:
        return []
    return [c.company_id for c in res.candidates]


def _score_retrieve(runs: list[list[str]], expected: dict) -> dict:
    """top-1 정확도 · distractor 제외율 + k회 안정성(top-1 일치율)."""
    top1 = [r[0] if r else None for r in runs]
    top1_hits = [t == expected["top_should_be"] for t in top1]
    excl = expected.get("should_exclude", [])
    # distractor 제외: 각 실행에서 제외 대상이 강한 후보(랭킹)에 없어야
    excl_hits = []
    for r in runs:
        strong = set(r)
        excl_hits.append(sum(1 for e in excl if e not in strong) / len(excl) if excl else 1.0)
    return {"top1_accuracy": sum(top1_hits) / len(top1_hits),
            "distractor_exclusion": sum(excl_hits) / len(excl_hits),
            "stability": agreement_rate([t or "∅" for t in top1]),
            "top1_dist": dict(Counter(t or "∅" for t in top1))}


# ── 벤치마크 오케스트레이션 ─────────────────────────────────────────

def run_benchmark(golden: Optional[dict] = None, k: int = 1,
                  only: Optional[str] = None) -> dict:
    """골든셋 실행. k>1이면 각 케이스 k회 반복(LLM 안정성 계측). 결정적 Mock은 k=1로 충분.
    only='judge'|'retrieve'로 한쪽만 — 실 LLM에서 비싼 judge를 통제 실행할 때 쓴다."""
    golden = golden or load_golden()
    judge_results, retrieve_results = [], []

    if only in (None, "judge"):
        for case in golden.get("judge_cases", []):
            runs = [_run_judge_once(case) for _ in range(k)]
            judge_results.append({"case_id": case["case_id"],
                                  "source": case.get("source", ""),
                                  "expected": case["expected"],
                                  **_score_judge(runs, case["expected"])})
    if only in (None, "retrieve"):
        for case in golden.get("retrieve_cases", []):
            runs = [_run_retrieve_once(case) for _ in range(k)]
            retrieve_results.append({"case_id": case["case_id"],
                                    "source": case.get("source", ""),
                                    "expected": case["expected"],
                                    **_score_retrieve(runs, case["expected"])})

    j_acc = ([r["accuracy"] for r in judge_results] or [0])
    r_top1 = ([r["top1_accuracy"] for r in retrieve_results] or [0])
    j_stab = [r["stability"] for r in judge_results]
    r_stab = [r["stability"] for r in retrieve_results]
    all_stab = j_stab + r_stab
    return {
        "k": k,
        "judge": judge_results,
        "retrieve": retrieve_results,
        "summary": {
            "judge_accuracy": round(sum(j_acc) / len(j_acc), 4),
            "retrieve_top1_accuracy": round(sum(r_top1) / len(r_top1), 4),
            "overall_stability": round(sum(all_stab) / len(all_stab), 4) if all_stab else 1.0,
            "judge_cases": len(judge_results),
            "retrieve_cases": len(retrieve_results),
        },
    }
