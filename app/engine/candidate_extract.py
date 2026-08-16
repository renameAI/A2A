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
업종·지역·언어는 건마다 다르다 — 특정 업종을 가정하지 마라. 판단 기준은
[찾는 상대]에 적힌 내용뿐이다.

판정 절차 — 항목마다 순서대로 적용한다:
① 이 페이지의 **주체**가 기업인가?
   - 기업의 공식 사이트는 채택 후보다. 그 경우 페이지 제목·도메인이 곧
     그 회사가 스스로 쓰는 이름인 경우가 많다 — 그것을 쓴다.
   - 기사·블로그·백과사전·쇼핑몰·SNS·논문·기관 안내 페이지 자체는 기업이
     아니다. 단, 그런 페이지가 **다른 실존 기업들을 명시적으로 소개**하면
     그 기업들을 채택할 수 있다(디렉터리·회원사 목록·비교 기사가 그렇다).
② 회사 이름을 알 수 있는가? 제목이 기사 헤드라인이라 이름이 아니면 본문에서
   찾고, 끝내 없으면 그 항목만 버린다. **없는 이름을 지어내는 것만 금지다** —
   페이지에 있는 이름을 옮기는 것은 추측이 아니다. 법인 등기명일 필요는 없다.
③ [찾는 상대]의 역할과 **명백히 다른 업인가?** 그때만 버린다. 스니펫은
   단편이라 세부(지역·규모·단계)까지는 안 보인다 — **확인 불가는 불일치가
   아니다.** 업이 맞으면 채택하고, 세부 검증은 다음 단계가 한다.
- 같은 회사가 여러 번 나오면 한 번만 남긴다.
- 빈 배열은 ①~③을 다 거치고도 채택할 기업이 **하나도 없을 때**의 결과다.
  "확신이 부족하다"는 이유로 비우지 마라 — 실존 기업을 버리는 것은 없는
  기업을 만드는 것만큼 큰 오류다.

각 기업의 기록 형식 (형식을 다 못 채운다는 이유로 기업을 버리지 마라 —
빈 문자열이 허용되는 항목은 비워 두면 된다):
- name: 자료에 적힌 그대로의 회사 이름. **원어 유지** — 번역·음역하면 검색과
  연락 때 그 회사를 못 찾는다.
- what: 그 회사가 무엇을 하는지 한 문장. 자료가 짧으면 짧게 쓰면 된다.
- signal: 요청 기업과 연결될 만한 **관측된 신호**(거래·모집·공고·최근 움직임).
  자료에 없으면 빈 문자열 — 비어 있는 것이 정상이고, 지어내는 것만 금지다.
- url: 근거가 된 검색 결과의 URL (반드시 입력에 있던 것 그대로)"""

EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["companies"],
    "properties": {
        "companies": {
            "type": "array", "maxItems": 30,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "what", "signal", "url"],
                "properties": {
                    "name": {"type": "string"},
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
        # name_ko(한국어 표기)는 추출에서 만들지 않는다 — 번역·독음은 별도의
        # 앱 계층 관심사다. 추출 프롬프트에 섞으면 "표기를 못 만들겠다"가
        # 기업 폐기로 이어지는 결합이 생긴다(실측: 영문권 전멸 사고의 일부).
        out.append({"name": name, "name_ko": name,
                    "what": what, "signal": sig, "url": url})
    return out
