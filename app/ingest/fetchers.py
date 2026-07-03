"""자산 유형별 수집기 (ING-01, ING-07, ING-08).

- website/article/portfolio: URL → 본문 텍스트 (httpx + BeautifulSoup)
- instagram: 계약된 서드파티(Apify) API 경유만 허용 — 무단 스크레이핑 금지 (ING-08).
  공식 Graph API로의 교체는 어댑터 함수 하나만 바꾸면 된다.
수집 실패는 명시적 에러(fetch_failed)로 — 빈값으로 조용히 넘어가지 않는다.
"""
import re

import httpx
from bs4 import BeautifulSoup

from ..config import Settings
from ..errors import EngineError

_APIFY_INSTAGRAM_ACTOR = "apify~instagram-profile-scraper"


class FetchFailed(EngineError):
    def __init__(self, url: str, reason: str):
        super().__init__(502, "fetch_failed", f"자료 수집 실패: {url}",
                         {"url": url, "reason": reason})


def fetch_url(url: str, settings: Settings,
              client: httpx.Client | None = None) -> str:
    """웹사이트/기사 URL → 본문 텍스트."""
    try:
        own = client is None
        client = client or httpx.Client(
            timeout=settings.fetch_timeout, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh) A2A-Engine/0.1"})
        try:
            resp = client.get(url)
            resp.raise_for_status()
        finally:
            if own:
                client.close()
    except httpx.HTTPError as e:
        raise FetchFailed(url, str(e))

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text(separator="\n")).strip()
    if not text:
        raise FetchFailed(url, "본문 텍스트 없음")
    return text


def fetch_pdf_bytes(url: str, settings: Settings,
                    client: httpx.Client | None = None) -> bytes:
    """IR덱 PDF — http(s) URL 또는 로컬 파일 경로."""
    if url.startswith(("http://", "https://")):
        try:
            own = client is None
            client = client or httpx.Client(timeout=settings.fetch_timeout,
                                            follow_redirects=True)
            try:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.content
            finally:
                if own:
                    client.close()
        except httpx.HTTPError as e:
            raise FetchFailed(url, str(e))
    from pathlib import Path
    path = Path(url)
    if not path.exists():
        raise FetchFailed(url, "파일 없음")
    return path.read_bytes()


def fetch_instagram(url_or_handle: str, settings: Settings,
                    client: httpx.Client | None = None) -> str:
    """인스타그램 프로필 + 최근 포스트 캡션. Apify 토큰 필수 (ING-08)."""
    if not settings.apify_token:
        raise EngineError(
            503, "instagram_not_configured",
            "인스타그램 수집에는 APIFY_TOKEN이 필요합니다. "
            ".env에 설정하거나 공식 Graph API 연동을 사용하세요 (ING-08).")

    handle = url_or_handle.rstrip("/").split("/")[-1].lstrip("@")
    api_url = (f"https://api.apify.com/v2/acts/{_APIFY_INSTAGRAM_ACTOR}"
               f"/run-sync-get-dataset-items")
    try:
        own = client is None
        client = client or httpx.Client(timeout=max(settings.fetch_timeout, 60))
        try:
            resp = client.post(api_url, params={"token": settings.apify_token},
                               json={"usernames": [handle]})
            resp.raise_for_status()
            items = resp.json()
        finally:
            if own:
                client.close()
    except httpx.HTTPError as e:
        raise FetchFailed(f"instagram:{handle}", str(e))

    lines: list[str] = []
    for item in items:
        if item.get("fullName"):
            lines.append(f"계정명: {item['fullName']} (@{item.get('username', handle)})")
        if item.get("biography"):
            lines.append(f"소개: {item['biography']}")
        for post in item.get("latestPosts", [])[:12]:
            if post.get("caption"):
                lines.append(f"포스트: {post['caption']}")
    if not lines:
        raise FetchFailed(f"instagram:{handle}", "프로필/포스트 데이터 없음")
    return "\n".join(lines)
