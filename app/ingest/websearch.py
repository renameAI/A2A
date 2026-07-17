"""웹 검색 클라이언트 — Scout의 후보 충원용 (기획서 6.4 외부 풀 충원 트랙 v0).

키 없는 DuckDuckGo HTML 엔드포인트를 쓴다 — 기존 크롤러와 같은 '공개 웹' 범주
(API 키·계약 없음). 상용 검색 API(Tavily·Serper·Bing)로 올릴 때는 AXR팀 협의 후
이 모듈의 web_search만 갈아끼우면 된다(호출부 계약 동일).

크롤러와 동일한 방어: 24h 디스크 캐시(A2A_CACHE_DIR) · 타임아웃 · 정직한 실패
(검색 불가 시 빈 리스트 + 로그 — Scout 전체를 막지 않는다).
"""
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from .. import progress
from ..config import Settings

_SEARCH_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; A2A-MatchingEngine/0.1; +scout)"}
_CACHE_TTL_SECONDS = 24 * 3600


def _cache_dir() -> Path:
    override = os.environ.get("A2A_CACHE_DIR")
    return Path(override) if override else \
        Path(__file__).resolve().parent.parent.parent / "cache"


def _cache_key(query: str) -> Path:
    return _cache_dir() / ("ws_" + hashlib.sha256(query.encode()).hexdigest()[:24] + ".json")


def _cache_get(query: str) -> "list[dict] | None":
    try:
        data = json.loads(_cache_key(query).read_text())
        if time.time() - data["ts"] < _CACHE_TTL_SECONDS:
            return data["hits"]
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def _cache_put(query: str, hits: list[dict]) -> None:
    try:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        _cache_key(query).write_text(
            json.dumps({"ts": time.time(), "hits": hits}, ensure_ascii=False))
    except OSError:
        pass


def _real_url(href: str) -> str:
    """DDG 결과 링크는 /l/?uddg=<인코딩된 원본 URL> 리다이렉트 — 원본을 복원한다."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(uddg)
    return href


def parse_search_html(html: str, max_results: int = 8) -> list[dict]:
    """DDG HTML → [{title, url, snippet, domain}]. 파싱만 분리(테스트 픽스처용)."""
    soup = BeautifulSoup(html, "html.parser")
    hits: list[dict] = []
    for res in soup.select(".result"):
        a = res.select_one(".result__a")
        if a is None:
            continue
        url = _real_url(a.get("href", ""))
        if not url.startswith("http"):
            continue
        snippet_el = res.select_one(".result__snippet")
        domain = urlparse(url).netloc.replace("www.", "")
        hits.append({
            "title": a.get_text(" ", strip=True),
            "url": url,
            "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            "domain": domain,
        })
        if len(hits) >= max_results:
            break
    return hits


def web_search(query: str, settings: Settings, max_results: int = 8,
               client: "httpx.Client | None" = None) -> list[dict]:
    """검색어 → 히트 목록. 실패는 빈 리스트 + 로그 (Scout는 계속 진행)."""
    cached = _cache_get(query)
    if cached is not None:
        progress.log("검색", f"캐시 적중 — \"{query[:40]}\" ({len(cached)}건)")
        return cached[:max_results]

    own = client is None
    client = client or httpx.Client(timeout=settings.fetch_timeout,
                                    follow_redirects=True, headers=_HEADERS)
    try:
        resp = client.post(_SEARCH_URL, data={"q": query, "kl": "kr-kr"})
        if resp.status_code != 200:
            progress.log("검색", f"⚠ 검색 실패({resp.status_code}) — \"{query[:40]}\" 건너뜀")
            return []
        hits = parse_search_html(resp.text, max_results=max_results)
        _cache_put(query, hits)
        progress.log("검색", f"\"{query[:40]}\" → {len(hits)}건")
        return hits
    except httpx.HTTPError as e:
        progress.log("검색", f"⚠ 검색 불가 — \"{query[:40]}\" ({type(e).__name__})")
        return []
    finally:
        if own:
            client.close()
