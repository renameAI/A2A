"""엔진 공통 유틸 — v0(Mock) 추론의 토대.

Phase 2에서 실제 LLM/임베딩 어댑터로 교체되는 부분:
  - char_bigram 유사도 → 학습된 도메인 임베딩 (기획서 13.2)
  - 키워드 stage/industry 추론 → Represent LLM 추론
교체가 쉽도록 순수 함수로만 구성한다.
"""
import hashlib
import re

from ..schemas import Profile

_CLEAN = re.compile(r"[^\w가-힣]")


def bigrams(text: str) -> set[str]:
    t = _CLEAN.sub("", text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def overlap(a: str, b: str) -> float:
    """문자 bigram overlap coefficient (0~1). 한국어 조사 변형에 견고한 v0 유사도."""
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def pseudo_embedding(text: str, dim: int = 16) -> list[float]:
    """결정적 placeholder 임베딩. Phase 2에서 실제 임베딩 모델로 교체."""
    vec = [0.0] * dim
    for bg in bigrams(text):
        h = int(hashlib.md5(bg.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [round(v / norm, 6) for v in vec]


# ── 온톨로지 v0 — 키워드 기반 앵커 추론 ─────────────────────────────

STAGE_KEYWORDS = [
    ("enterprise", ["대기업", "엔터프라이즈", "글로벌 제조", "글로벌 기업"]),
    ("chain", ["체인", "프랜차이즈"]),
    ("seed", ["시드"]),
    ("startup", ["스타트업"]),
]

# 산업 인접성 — 판매자 산업 → 매칭 가능한 상대 산업 (Retrieve 온톨로지 제약, 6.2-b)
INDUSTRY_ADJACENCY: dict[str, set[str]] = {
    "hospitality_renovation": {"hospitality", "hotel", "hospitality_renovation"},
    "hospitality": {"hospitality_renovation", "hospitality", "travel"},
    "saas": {"saas", "commerce", "manufacturing", "finance"},
}


def infer_stage(profile: Profile) -> str:
    text = f"{profile.description} {profile.traction or ''}"
    for stage, kws in STAGE_KEYWORDS:
        if any(kw in text for kw in kws):
            return stage
    return "sme"


def industry_adjacent(a: str, b: str) -> bool:
    if a == b:
        return True
    return b in INDUSTRY_ADJACENCY.get(a, set()) or a in INDUSTRY_ADJACENCY.get(b, set())


def profile_pain_text(profile: Profile) -> str:
    """상대의 '겪는 문제' 면 — buy-side 검색·판단이 향하는 텍스트 (RET-02)."""
    return f"{profile.description} {profile.problem_solved.value}"
