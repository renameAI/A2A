"""실전 웹사이트 크롤러 (ING-01·ING-08·ING-09).

방법 서치 결과(2025 본문 추출 벤치마크): trafilatura가 F1 0.945로 1위,
readability-lxml 0.922 — trafilatura를 1차 추출기로, BeautifulSoup 휴리스틱을
폴백으로 쓰는 계단식 구성이 권장 패턴이다.

동작:
1. 루트 페이지 수집 → 본문 추출
2. 같은 도메인의 회사소개·제품·서비스류 우선순위 링크를 따라 최대 N페이지 추가 수집
3. robots.txt 준수 — 차단 경로는 건너뛰고 로그 (ING-08)
4. 24시간 디스크 캐시 — 같은 URL 재수집 방지 (ING-09)
5. JS 렌더링 SPA(빈 껍데기)는 감지해 정직한 안내 에러 (조용한 빈 프로필 방지)
"""
import hashlib
import json
import os
import re
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .. import progress
from ..config import Settings
from ..errors import EngineError

_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# 회사의 상(像)에 기여하는 페이지 우선순위 키워드 (href·앵커 텍스트 매칭)
_PRIORITY_KEYWORDS = [
    "about", "company", "회사", "소개", "product", "제품", "service", "서비스",
    "solution", "솔루션", "business", "사업", "team", "팀", "portfolio",
    "포트폴리오", "vision", "비전", "ir", "customer", "고객", "case", "사례",
]

_CACHE_TTL_SECONDS = 24 * 3600   # ING-09


class CrawlFailed(EngineError):
    def __init__(self, url: str, reason: str):
        super().__init__(502, "fetch_failed", f"웹사이트 크롤링 실패: {url}",
                         {"url": url, "reason": reason})


# ── 캐시 (ING-09) ───────────────────────────────────────────────────

def _cache_dir() -> Path:
    override = os.environ.get("A2A_CACHE_DIR")
    return Path(override) if override else \
        Path(__file__).resolve().parent.parent.parent / "cache"


def _cache_get(url: str) -> str | None:
    path = _cache_dir() / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")
    try:
        data = json.loads(path.read_text())
        if time.time() - data["ts"] < _CACHE_TTL_SECONDS:
            return data["text"]
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def _cache_put(url: str, text: str) -> None:
    try:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        path = _cache_dir() / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")
        path.write_text(json.dumps({"ts": time.time(), "url": url, "text": text},
                                   ensure_ascii=False))
    except OSError:
        pass   # 캐시 실패가 수집을 막지 않는다


# ── 본문 추출 — trafilatura 1차, BeautifulSoup 폴백 ─────────────────

def extract_main_text(html: str, url: str = "") -> str:
    try:
        import trafilatura
        text = trafilatura.extract(html, url=url or None,
                                   include_comments=False, include_tables=True)
        if text and len(text.strip()) >= 80:
            return text.strip()
    except Exception:   # noqa: BLE001 — 폴백으로
        pass
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript", "header",
                     "aside", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return re.sub(r"\n{3,}", "\n\n", main.get_text(separator="\n")).strip()


def _looks_like_js_shell(html: str, text: str) -> bool:
    """CSR SPA 빈 껍데기 감지 — 본문이 거의 없고 스크립트 참조만 많은 페이지."""
    if len(text) >= 200:
        return False
    script_refs = html.count("<script")
    has_spa_root = bool(re.search(r'id=["\'](?:root|app|__next|__nuxt)["\']', html))
    return script_refs >= 3 or has_spa_root


# ── robots.txt (ING-08) ─────────────────────────────────────────────

def _robots(client: httpx.Client, base: str) -> urllib.robotparser.RobotFileParser:
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = client.get(urljoin(base, "/robots.txt"))
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        else:
            rp.parse([])          # robots 없음 → 전부 허용
    except httpx.HTTPError:
        rp.parse([])
    return rp


# ── 링크 우선순위 ───────────────────────────────────────────────────

def _priority_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc
    scored: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].split("#")[0])
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https") or parsed.netloc != base_host:
            continue   # 외부 도메인·비HTTP 제외
        if href.rstrip("/") == base_url.rstrip("/"):
            continue
        haystack = (parsed.path + " " + a.get_text()).lower()
        score = sum(1 for kw in _PRIORITY_KEYWORDS if kw in haystack)
        if score > 0:
            scored[href] = max(scored.get(href, 0), score)
    return [u for u, _ in sorted(scored.items(), key=lambda x: -x[1])]


# ── 메인 크롤 ───────────────────────────────────────────────────────

def crawl_website(url: str, settings: Settings,
                  client: httpx.Client | None = None) -> str:
    cached = _cache_get(url)
    if cached is not None:
        progress.log("수집", f"캐시 적중 (24h) — {url}")
        return cached

    own = client is None
    client = client or httpx.Client(timeout=settings.fetch_timeout,
                                    follow_redirects=True,
                                    headers=_BROWSER_HEADERS)
    try:
        base = url if url.startswith(("http://", "https://")) else f"https://{url}"
        rp = _robots(client, base)
        ua = _BROWSER_HEADERS["User-Agent"]

        if not rp.can_fetch(ua, base):
            raise CrawlFailed(url, "robots.txt가 수집을 금지 (ING-08 준수)")

        try:
            resp = client.get(base)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise CrawlFailed(url, str(e))

        root_html = resp.text
        root_text = extract_main_text(root_html, base)
        if _looks_like_js_shell(root_html, root_text):
            raise CrawlFailed(
                url, "JS 렌더링 사이트(SPA)로 보임 — 본문이 비어 있습니다. "
                     "사이트 내용을 '직접 입력'으로 붙여넣거나 기사 URL을 사용하세요.")

        pages: list[tuple[str, str]] = [(base, root_text)]
        progress.log("수집", f"루트 페이지 수집 — {len(root_text):,}자")

        max_pages = getattr(settings, "crawl_max_pages", 5)
        for link in _priority_links(root_html, base):
            if len(pages) >= max_pages:
                break
            if not rp.can_fetch(ua, link):
                progress.log("수집", f"robots.txt 차단 — 건너뜀: {urlparse(link).path}")
                continue
            try:
                sub = client.get(link)
                sub.raise_for_status()
            except httpx.HTTPError:
                continue   # 하위 페이지 실패는 전체를 막지 않는다
            sub_text = extract_main_text(sub.text, link)
            if len(sub_text) >= 80:
                pages.append((link, sub_text))
                progress.log("수집",
                             f"하위 페이지 수집 — {urlparse(link).path} ({len(sub_text):,}자)")

        combined = "\n\n".join(f"[페이지: {u}]\n{t}" for u, t in pages)
        if len(combined) < 100:
            raise CrawlFailed(url, "수집된 본문이 너무 적음")
        progress.log("수집", f"크롤 완료 — {len(pages)}페이지 · 총 {len(combined):,}자")
        _cache_put(url, combined)
        return combined
    finally:
        if own:
            client.close()
