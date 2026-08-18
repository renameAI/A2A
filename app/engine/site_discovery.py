"""기사·디렉터리에서 발굴된 회사의 공식 사이트를 찾는다.

배경(프로덕션 실측): 심층 판독 대상 10곳 중 5곳이 mention·directory 출처라
읽을 사이트가 없었다. 그 URL은 그 회사 것이 아니므로 크롤하면 안 되고, 회사
이름으로 공식 사이트를 찾아야 접점·신호를 읽을 수 있다.

판정=모델, 결정=코드: 검색 결과 중 어느 것이 그 회사의 사이트인지는 모델이
고른다(도메인 규칙으로는 못 가른다 — 동명 회사·리셀러·리뷰 사이트). 후보를
_NON_COMPANY 도메인으로 미리 거르고, 모델이 고른 URL이 후보 안에 있는지는
코드가 검사한다(인용 계약). 확신이 낮으면 빈 값 — 남의 사이트를 그 회사로
읽는 것이 못 읽는 것보다 나쁘다.
"""
from urllib.parse import urlparse

from .candidate_extract import _NON_COMPANY, _site_of
from .prompts import HARD_RULES

DISCOVER_SYSTEM = HARD_RULES + """

당신은 회사 이름과 한 줄 설명을 보고, 검색 결과 중 **그 회사 자신의 공식
사이트**를 고른다.

규칙:
- 후보 중 그 회사가 직접 운영하는 사이트(회사소개·제품 페이지의 화자가 그
  회사)만 답이다. 기사·디렉터리·리뷰·리셀러·동명이인 회사는 답이 아니다.
- 확신이 없으면 url을 빈 문자열로 둔다. 잘못 고르면 시스템이 남의 사이트를
  이 회사의 것으로 읽는다 — 못 찾는 편이 낫다.
- p는 고른 URL이 이 회사의 공식 사이트일 확률(정직한 추정치)."""

DISCOVER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["url", "p", "why"],
    "properties": {"url": {"type": "string"},
                   "p": {"type": "number", "minimum": 0, "maximum": 1},
                   "why": {"type": "string"}},
}

# 채택 임계 — 오채택 비용이 크다(남의 사이트를 이 회사로 판독). 추출의 τ=0.2와
# 달리 하류 필터가 없으므로 높게 둔다.
_ACCEPT = 0.6


def find_official_site(extractor, settings, name: str, what: str = "",
                       exclude_site: str = "") -> "tuple[str, float]":
    """(공식 사이트 URL 또는 "", p). 검색 1회 + 판정 1회."""
    from ..ingest.websearch import web_search
    # 1순위: 이름만(실측으로 잘 듣는다). 이름이 일반명사('Mati')라 결과가
    # 없거나 후보가 안 남을 때만 업을 한 조각 붙인 2순위로 넘어간다 — 힌트를
    # 항상 붙이면 검색엔진이 따옴표+긴 문장을 못 다뤄 0건이 난다(실측: 한·영
    # 힌트 모두 0건, 이름만은 8건).
    hint = " ".join((what or "").split()[:3])
    queries = [f'"{name}" official website']
    if hint:
        queries.append(f"{name} {hint} official site")
    hits = []
    for q in queries:
        hits = web_search(q, settings, max_results=8)
        if hits:
            break
    cands = []
    for h in hits:
        url = h.get("url") or ""
        host = urlparse(url).netloc.lower()
        if not host or any(b in url.lower() for b in _NON_COMPANY):
            continue
        if exclude_site and _site_of(url) == exclude_site:
            continue                       # 발굴된 그 기사·디렉터리 자체는 제외
        cands.append(h)
    if not cands:
        return "", 0.0
    listing = "\n".join(
        f"[{i + 1}] {h.get('title', '')}\n    URL: {h.get('url', '')}\n"
        f"    내용: {(h.get('snippet') or '')[:200]}"
        for i, h in enumerate(cands))
    data = extractor.extract_json(
        DISCOVER_SYSTEM,
        f"[회사] {name}\n[하는 일] {what or '미상'}\n\n[검색 결과]\n{listing}",
        DISCOVER_SCHEMA, deep=False, allow_foreign=True)
    url = (data.get("url") or "").strip()
    p = float(data.get("p") or 0)
    valid = {h.get("url") for h in cands}
    if not url or url not in valid or p < _ACCEPT:   # 인용 계약 + 임계
        return "", p
    return url, p
