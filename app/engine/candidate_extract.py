"""웹 검색 결과 → 실제 기업 후보 추출 (이슈 #6-D 품질 수정).

배경(실측 2026-08): 검색 히트를 그대로 후보로 썼더니 후보 10곳 중 8곳이
기사·블로그였다 — "에너지 음료? 고카페인 음료! 이대로 괜찮은가?"가 회사명으로
떴다. 페이지 제목은 회사명이 아니고, 검색 결과는 기업이 아니다.

두 겹으로 거른다:
1) 도메인 필터(코드) — 블로그·위키·SNS·뉴스는 기업 페이지가 아니다. 결정적이라
   LLM 비용이 안 들고, 명백한 쓰레기를 LLM에 보내지 않아 추출 품질도 올라간다.
2) 기업 추출(LLM 1회) — 남은 히트에서 **실존하는 회사만** 뽑는다. 회사를 못
   찾으면 빈 배열을 반환한다(억지로 채우지 않는다 — RET-06과 같은 정신).
"""
from .prompts import HARD_RULES

# 기업 페이지가 아닌 도메인 — 부분 문자열로 검사한다.
_NON_COMPANY = (
    "blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "post.naver.com",
    "tistory.com", "brunch.co.kr", "velog.io", "medium.com",
    "wikipedia.org", "namu.wiki", "namuwiki",
    "instagram.com", "facebook.com", "youtube.com", "youtu.be",
    "twitter.com", "x.com", "tiktok.com", "threads.net", "linkedin.com/posts",
    "news.naver.com", "n.news.naver.com", "daum.net/news",
    "coupang.com", "11st.co.kr", "gmarket.co.kr", "amazon.", "aliexpress.",
    "quora.com", "reddit.com", "pinterest.",
    "slideshare.net", "issuu.com", "scribd.com",
    "dbpia.co.kr", "riss.kr", "kci.go.kr",   # 논문·리포지토리
)


def filter_company_hits(hits: list[dict]) -> tuple[list[dict], int]:
    """비기업 도메인 제거. (남은 히트, 제외 수)."""
    kept = [h for h in hits
            if not any(bad in (h.get("url") or "").lower()
                       for bad in _NON_COMPANY)]
    return kept, len(hits) - len(kept)


EXTRACT_SYSTEM = HARD_RULES + """

당신은 B2B 리드 리서처다. 웹 검색 결과에서 **실존하는 기업만** 골라낸다.

절대 규칙:
- 기사·블로그·백과사전·쇼핑몰·SNS 게시물은 기업이 아니다. 제외한다.
- 회사명은 페이지 제목이 아니라 **그 회사의 실제 상호**다. 제목이 기사 헤드라인이면
  그 항목은 버린다. 상호를 알 수 없으면 버린다 — 추측해서 만들지 마라.
- 같은 회사가 여러 번 나오면 한 번만 남긴다.
- 요청 기업이 찾는 상대([상대상]에 적힌 역할)에 해당하지 않으면 버린다.
  그 역할은 건마다 다르다 — 제조사일 수도, 병원일 수도, 시공사일 수도 있다.
- 조건에 맞는 기업이 하나도 없으면 빈 배열을 반환한다. **억지로 채우지 마라.**

각 기업에 대해:
- name: 실제 상호 (원어 유지 — 번역하면 검색·연락 때 못 찾는다)
- name_ko: 한국어 표기. 외국 상호는 **독음**을 쓴다(株式会社鈴商 → 스즈쇼).
  이미 한국 회사면 name과 같게. 대표가 원어만 보면 어떤 회사인지 모른다.
- what: 그 회사가 무엇을 하는지 한 문장 (검색 결과에 있는 내용만)
- signal: 요청 기업과 연결될 만한 **관측된 신호**. what을 되풀이하지 마라 —
  what에 없는 새 정보(거래 실적·모집/입찰 공고·최근 움직임 등 업종에 맞는 것)만
  쓴다. 없으면 빈 문자열 — 억지로 만들지 마라.
- url: 근거가 된 검색 결과의 URL (반드시 입력에 있던 것 그대로)"""

EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["companies"],
    "properties": {
        "companies": {
            "type": "array", "maxItems": 30,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "name_ko", "what", "signal", "url"],
                "properties": {
                    "name": {"type": "string"},
                    "name_ko": {"type": "string"},
                    "what": {"type": "string"},
                    "signal": {"type": "string"},
                    "url": {"type": "string"},
                },
            },
        },
    },
}


def extract_companies(extractor, hits: list[dict], counterpart: str,
                      requester_name: str = "") -> list[dict]:
    """검색 히트 → 실존 기업 목록. LLM 1회. 실패하면 빈 목록(조용한 대체 없음)."""
    if not hits:
        return []
    listing = "\n".join(
        f"[{i + 1}] {h.get('title', '')}\n    URL: {h.get('url', '')}\n"
        f"    내용: {(h.get('snippet') or '')[:300]}"
        for i, h in enumerate(hits))
    data = extractor.extract_json(
        EXTRACT_SYSTEM,
        f"[요청 기업] {requester_name or '미상'}\n"
        f"[찾는 상대]\n{counterpart}\n\n[검색 결과]\n{listing}",
        EXTRACT_SCHEMA, deep=False, allow_foreign=True)
    seen, out = set(), []
    valid_urls = {h.get("url") for h in hits}
    for c in data.get("companies", []):
        name = (c.get("name") or "").strip()
        url = (c.get("url") or "").strip()
        # 계약 검증 — 입력에 없던 URL은 환각이다 (인용 계약과 같은 원리)
        if not name or name in seen or url not in valid_urls:
            continue
        seen.add(name)
        what = (c.get("what") or "").strip()
        sig = (c.get("signal") or "").strip()
        # 신호가 설명을 되풀이하면 버린다 — 화면에 같은 문장이 두 번 뜨고
        # 검색 점수도 중복 가중돼 왜곡된다(실측: 같은 문장 3회 반복).
        if sig and (sig in what or what in sig):
            sig = ""
        out.append({"name": name, "name_ko": (c.get("name_ko") or name).strip(),
                    "what": what, "signal": sig, "url": url})
    return out
