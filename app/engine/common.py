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
    "software": {"software", "saas", "commerce", "manufacturing", "finance",
                 "govtech", "logistics"},
    "finance": {"finance", "saas", "software", "commerce", "govtech"},
    "govtech": {"govtech", "software", "finance", "saas"},
    "manufacturing": {"manufacturing", "saas", "software", "logistics",
                      "materials"},
    "materials": {"materials", "manufacturing"},
    "logistics": {"logistics", "commerce", "manufacturing", "software"},
    "commerce": {"commerce", "saas", "software", "logistics"},
    "bio": {"bio", "healthcare"},
    "healthcare": {"healthcare", "bio", "software"},
    "media": {"media", "commerce", "software"},
}

# 자유서술 산업명 → 통제 어휘 (LLM은 "고향사랑기부_민간_1위_플랫폼_운영" 같은 홍보
# 문구를, 리서치 풀은 표준산업분류명을 쓴다). 실측: 풀 1,140개 중 통제 어휘에 맞는
# 건 6개(0.5%) — 온톨로지 보너스(+0.10)가 99%에서 원천 불가였다. 규칙은 보수적으로:
# 확신이 없으면 unknown으로 두고 가산을 포기한다(억지 매칭보다 무가산이 안전).
_INDUSTRY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("hospitality_renovation", ("객실 전환", "리모델링", "인테리어 시공", "renovation")),
    ("hospitality", ("호텔", "숙박", "리조트", "관광", "여행", "hospitality", "hotel")),
    ("govtech", ("govtech", "공공", "지자체", "행정", "기부", "지역화폐")),
    ("finance", ("금융", "결제", "핀테크", "은행", "보험", "증권", "여신", "정산",
                 "신용", "fintech", "payment", "finance")),
    ("bio", ("바이오", "제약", "의약", "진단", "bio", "pharma")),
    ("healthcare", ("의료", "헬스케어", "병원", "healthcare", "medical")),
    ("logistics", ("물류", "운송", "배송", "창고", "logistics")),
    ("commerce", ("커머스", "유통", "소매", "쇼핑", "commerce", "retail")),
    ("media", ("미디어", "광고", "콘텐츠", "방송", "media", "advertis")),
    ("materials", ("소재", "화학", "금속", "섬유", "materials", "chemical")),
    ("manufacturing", ("제조", "기계", "부품", "장비", "생산", "manufactur")),
    ("software", ("소프트웨어", "솔루션", "시스템", "플랫폼", "앱", "정보통신",
                  "software", "saas", "platform", "it 서비스")),
]

_UNKNOWN_INDUSTRY = {"", "unknown", "미상", "n/a", "none", "기타"}


def normalize_industry(text: str) -> str:
    """자유서술 산업명 → 통제 어휘. 매칭 실패 시 'unknown'(가산 없음).

    소비처가 통제 어휘를 기대하는데 생산처가 자유서술을 쓰는 계약 불일치를 코드가
    흡수한다 — LLM에게 enum을 지키라고 시키는 것보다 결정적이고 검증 가능하다."""
    t = (text or "").strip().lower()
    if not t or t in _UNKNOWN_INDUSTRY:
        return "unknown"
    if t in INDUSTRY_ADJACENCY:          # 이미 통제 어휘
        return t
    for canon, kws in _INDUSTRY_RULES:   # 구체적인 규칙이 앞에 온다(순서 유의)
        if any(k in t for k in kws):
            return canon
    return "unknown"


def infer_stage(profile: Profile) -> str:
    text = f"{profile.description} {profile.traction or ''}"
    for stage, kws in STAGE_KEYWORDS:
        if any(kw in text for kw in kws):
            return stage
    return "sme"


def industry_adjacent(a: str, b: str) -> bool:
    """정규화 후 인접 판정. **unknown은 무엇과도(자기 자신과도) 인접하지 않는다.**

    실측 폭탄: 예전엔 a == b면 True라 industry='unknown'인 후보 933개가 서로 인접
    판정을 받았다. _score는 base>=0.10일 때 +0.10을 주는데 이는 τ=0.12를 단독으로
    넘길 수 있는 크기라, 요청자 산업이 미상이면 풀 전체가 가산을 받는 구조였다.
    모르는 것끼리는 '같다'가 아니라 '판단 불가'다."""
    na, nb = normalize_industry(a), normalize_industry(b)
    if na == "unknown" or nb == "unknown":
        return False
    if na == nb:
        return True
    return nb in INDUSTRY_ADJACENCY.get(na, set()) \
        or na in INDUSTRY_ADJACENCY.get(nb, set())


def profile_pain_text(profile: Profile) -> str:
    """상대의 '겪는 문제' 면 — buy-side 검색·판단이 향하는 텍스트 (RET-02)."""
    return f"{profile.description} {profile.problem_solved.value}"
