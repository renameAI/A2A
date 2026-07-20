"""Judge 프롬프트용 온톨로지 참고 힌트 — app/ontology/materials/의 실 산업 사례에서
가장 가까운 도메인의 실증 신호 루브릭 한 줄을 뽑아 판단 입력에 덧붙인다.

이것은 사실이 아니라 참고다: 구매측은 전부 AI 시뮬레이션([구성])이므로, 힌트에는
그 고지를 항상 함께 붙인다(README_judge_ontology_simulation.md §6). LLM에게
"이 산업은 이런 신호로 검증됐던 전례가 있다"는 구조적 참고만 주고, 내용을 사실로
베끼지 말라는 것이 목적 — JUDGE_SYSTEM의 demonstrability 지침("상대 시장 기준")을
구체 사례로 보강하는 것이지 대체하는 것이 아니다.

겹침이 낮으면 조용히 None — 억지로 채우지 않는다(정직성).
"""
import functools

from ..engine.common import overlap
from .extract import MATERIALS_DIR, Case, parse_file

_MIN_OVERLAP = 0.15   # 실측 보정 — 정답 도메인 0.2~0.5, 무관 질의는 항상 0.0 (buyer_label
                      # 대신 domain에만 매칭: 고유명사·외국어 인명이 섞이면 짧은 질의에서
                      # 우연 일치가 임계를 넘는다. 도메인 라벨 5개는 짧고 서로 판이해
                      # 매칭이 훨씬 깨끗하다)


@functools.lru_cache(maxsize=1)
def _all_cases() -> tuple[Case, ...]:
    cases: list[Case] = []
    for path in sorted(MATERIALS_DIR.glob("*_judge_ontology_material.md")):
        c, _, _ = parse_file(path)
        cases += c
    return tuple(cases)


def domain_hint(*industry_texts: str) -> "str | None":
    """산업·설명 텍스트와 가장 가까운 케이스의 demonstrability 루브릭을 참고 힌트로.

    복수 케이스가 같은 도메인이면(예: 키뮤 10케이스) 그 중 rubric이 채워진
    첫 케이스를 대표로 쓴다 — 도메인당 한 줄이면 충분하고, 여러 줄을 붙이면
    "그 도메인 사례를 통째로 암기하라"는 신호가 돼 버린다(프롬프트 철학과 상충).
    """
    query = " ".join(t for t in industry_texts if t and t != "미상").strip()
    if not query:
        return None
    best: "Case | None" = None
    best_score = _MIN_OVERLAP
    for c in _all_cases():
        score = overlap(query, c.domain)
        if score > best_score:
            best_score, best = score, c
    if best is None:
        return None
    demo = next((d for d in best.dimensions
                if d.dimension_raw == "demonstrability" and d.rubric), None)
    if demo is None:
        return None
    return (f"{best.domain} 도메인 시뮬레이션 사례 — 실증 신호: {demo.rubric} "
           f"(구매측은 AI 시뮬레이션 — 구조만 참고, 내용을 사실로 쓰지 말 것)")
