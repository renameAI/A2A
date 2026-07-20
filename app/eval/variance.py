"""출력 분산 계측 (FORMALIZATION.md L0) — "못 재면 못 줄인다".

동일 입력을 m회 실행한 출력들을 받아, 필드 유형별로 올바른 지표로 run-to-run
분산을 계산한다. 유형별 지표는 FORMALIZATION.md §4.3의 교정을 따른다:
- 범주형(enum·provenance·decision): 다수결 일치율 agreement = mode_count/n.
  (범주형 재현성은 σ²/n이 아니라 지수적 집중이 기대되는 축 — 그래서 평균/표준편차가 아니라 일치율.)
- 스칼라(confidence·relevance·fit_score): 표본 표준편차·변동계수.
- 집합값(references·value_props·open_questions·pins): 평균 쌍별 Jaccard.
- 자유문자열(description·서술): 정규화 토큰 Jaccard — 의미 분산의 하한만 잡는 프록시(정직히 표기).

stability ∈ [0,1]: 1=완전 재현, 0=최대 변동. 이 값이 곧 "예측가능성"의 계측치다.

L2(judge 다수결)가 mode()·agreement_rate()를 재사용한다 — 이 모듈은 dead code가 아니다.
"""
import re
import statistics
from collections import Counter
from typing import Any, Hashable


# ── 범주형 primitives (L2 재사용) ──────────────────────────────────

def mode(values: list[Hashable]) -> tuple[Any, int]:
    """최빈값과 그 빈도. 동점이면 먼저 등장한 값 (Counter.most_common 안정성).
    빈 입력은 (None, 0)."""
    if not values:
        return None, 0
    counts = Counter(values)
    top, n = counts.most_common(1)[0]
    return top, n


def agreement_rate(values: list[Hashable]) -> float:
    """다수결 일치율 = 최빈값 빈도 / n ∈ [0,1]. n회 중 몇 회가 합의했는가."""
    if not values:
        return 0.0
    _, n = mode(values)
    return n / len(values)


# ── 유형별 지표 ────────────────────────────────────────────────────

def scalar_stats(values: list[float]) -> dict:
    """스칼라 필드 통계. cv=변동계수(std/|mean|), stability=1-min(cv,1)."""
    xs = [float(v) for v in values if v is not None]
    if len(xs) < 2:
        return {"n": len(xs), "mean": xs[0] if xs else None,
                "std": 0.0, "cv": 0.0, "stability": 1.0}
    mean = statistics.mean(xs)
    std = statistics.stdev(xs)
    cv = std / abs(mean) if mean else (0.0 if std == 0 else 1.0)
    return {"n": len(xs), "mean": round(mean, 4), "std": round(std, 4),
            "cv": round(cv, 4), "stability": round(max(0.0, 1.0 - min(cv, 1.0)), 4)}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def avg_pairwise_jaccard(sets: list[set]) -> float:
    """모든 실행 쌍의 Jaccard 평균 ∈ [0,1]. 집합값 필드의 재현성."""
    if len(sets) < 2:
        return 1.0
    pairs = [(i, j) for i in range(len(sets)) for j in range(i + 1, len(sets))]
    return sum(_jaccard(sets[i], sets[j]) for i, j in pairs) / len(pairs)


_TOKEN = re.compile(r"[^\w가-힣]+", re.UNICODE)


def norm_tokens(text: str) -> set:
    """자유문자열 → 정규화 토큰 집합 (공백·구두점 분할, 소문자). 프록시 척도용."""
    return {t for t in _TOKEN.split((text or "").lower()) if t}


# ── 출력 트리 순회 + 필드별 계측 ───────────────────────────────────

def _walk(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """중첩 dict를 leaf 경로로 평탄화. list는 그 자체를 leaf로(원소 인덱스로
    재귀하지 않음 — 순서·길이가 실행마다 달라 경로 정렬이 무의미)."""
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_walk(v, f"{prefix}.{k}" if prefix else str(k)))
    else:
        out.append((prefix, obj))
    return out


def _classify(values: list[Any]) -> str:
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "null"
    if all(isinstance(v, list) for v in non_null):
        return "set"
    if all(isinstance(v, bool) for v in non_null):
        return "categorical"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        return "scalar"
    if all(isinstance(v, str) for v in non_null):
        # 짧고·공백 없고·실제 저카디널리티면 범주형(enum), 아니면 자유문자열.
        # 적대적 검토 확정(F4): 예전 조건 len(uniq)<=max(2,len(non_null))은
        # uniq⊆non_null이라 항진 — 모든 짧은 문자열이 categorical로 오분류됐다.
        uniq = set(non_null)
        low_cardinality = len(uniq) <= max(2, len(non_null) // 2 + 1)
        if all(len(v) <= 40 for v in non_null) \
                and not any(re.search(r"\s", v) for v in non_null) \
                and low_cardinality:
            return "categorical"
        return "text"
    return "categorical"


def _field_metric(values: list[Any], kind: str) -> dict:
    if kind == "scalar":
        return {"type": "scalar", **scalar_stats(values)}
    if kind == "set":
        sets = [set(map(_hashable, v)) for v in values if isinstance(v, list)]
        return {"type": "set", "n": len(sets),
                "stability": round(avg_pairwise_jaccard(sets), 4)}
    if kind == "text":
        toks = [norm_tokens(v) for v in values if isinstance(v, str)]
        return {"type": "text", "n": len(toks), "proxy": "token-jaccard",
                "stability": round(avg_pairwise_jaccard(toks), 4)}
    if kind == "null":
        return {"type": "null", "n": 0, "stability": 1.0}
    # categorical
    vals = [_hashable(v) for v in values if v is not None]
    top, cnt = mode(vals)
    return {"type": "categorical", "n": len(vals),
            "mode": top, "distinct": len(set(vals)),
            "stability": round(agreement_rate(vals), 4)}


def _hashable(v: Any) -> Hashable:
    return v if isinstance(v, Hashable) else repr(v)


def variance_report(outputs: list[dict]) -> dict:
    """m회 출력 → 필드별 분산 지표 + 전체 안정성 요약.

    반환: {"n": m, "overall_stability": μ, "least_stable": [상위 불안정 필드],
           "fields": {path: {type, stability, ...}}}
    """
    if not outputs:
        return {"n": 0, "overall_stability": 1.0, "fields": {}, "least_stable": []}
    collected: dict[str, list] = {}
    for o in outputs:
        for path, val in _walk(o):
            collected.setdefault(path, []).append(val)

    fields = {}
    for path, values in collected.items():
        # 일부 실행에서 경로가 누락되면(구조 변동) None으로 채워 길이 정렬
        if len(values) < len(outputs):
            values = values + [None] * (len(outputs) - len(values))
        metric = _field_metric(values, _classify(values))
        # 등장률 반영 (적대적 검토 확정 F5): 예전엔 None이 전 지표에서 걸러져
        # 5회 중 1회만 등장한 필드가 stability 1.0으로 보고됐다 — 구조 변동
        # 자체가 분산이므로 stability에 등장률을 곱한다. 전부 None(항상 null)은
        # 일관된 상태라 감점하지 않는다.
        n_present = sum(1 for v in values if v is not None)
        presence = n_present / len(values)
        metric["presence"] = round(presence, 4)
        if 0 < presence < 1:
            metric["stability"] = round(metric["stability"] * presence, 4)
        fields[path] = metric

    stabs = [f["stability"] for f in fields.values()]
    overall = round(statistics.mean(stabs), 4) if stabs else 1.0
    least = sorted(({"field": p, **m} for p, m in fields.items()),
                   key=lambda x: x["stability"])[:8]
    return {"n": len(outputs), "overall_stability": overall,
            "least_stable": [{"field": x["field"], "type": x["type"],
                              "stability": x["stability"]} for x in least
                             if x["stability"] < 1.0],
            "fields": fields}
