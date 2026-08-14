"""Tavily 검색 커넥터 (이슈 #6-D) — key-ready.

계약 (스펙 확정값 + 2026-08 정정):
- TAVILY_API_KEY가 있으면 Tavily, **5xx·타임아웃·네트워크 오류만** DuckDuckGo로
  폴백하고 로그에 표기한다 (조용한 대체가 아니라 표기된 폴백).
- **401/403/429는 폴백하지 않고 올린다.** 키가 죽었거나 쿼터가 소진된 것은
  일시적 장애가 아니라 운영자가 고쳐야 하는 상태다. 이걸 DDG로 덮으면
  "후보 0곳"이라는 정상 응답으로 위장되고, 그 0건이 캐시에 굳는다
  (감사 확정 medium).
- 키가 없으면 처음부터 DuckDuckGo — SaaS 프로덕션에선 키가 있는 것이 정상이며,
  키 부재는 로그로 드러난다.
- 반환 형식은 기존 websearch.web_search와 동일: [{"title","url","snippet"}].
  Tavily의 content 요약은 snippet에 담아 후보 리서치 비용을 아낀다.
"""
import os

import httpx

from .. import progress
from ..config import Settings
from ..errors import EngineError
from ..ingest.websearch import web_search as ddg_search

_TAVILY_URL = "https://api.tavily.com/search"


def search(query: str, settings: Settings, max_results: int = 8) -> list[dict]:
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        progress.log("검색", "TAVILY_API_KEY 없음 — DuckDuckGo로 검색 (표기된 폴백)")
        return ddg_search(query, settings, max_results=max_results)
    try:
        resp = httpx.post(_TAVILY_URL, json={
            "api_key": key, "query": query, "max_results": max_results,
            "include_answer": False,
        }, timeout=settings.fetch_timeout)
        if resp.status_code in (401, 403):
            raise EngineError(
                502, "search_unavailable",
                "Tavily 인증 실패 — TAVILY_API_KEY를 확인하세요. "
                "검색 없이 후보를 만들 수는 없어 여기서 멈춥니다.")
        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After", "")
            raise EngineError(
                502, "search_rate_limited",
                "Tavily 요청 한도를 넘었습니다"
                + (f" — {retry}초 뒤 다시 시도하세요." if retry else " — 잠시 뒤 다시 시도하세요."))
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError("5xx", request=resp.request, response=resp)
        resp.raise_for_status()
        rows = resp.json().get("results", [])
        out = [{"title": r.get("title", ""), "url": r.get("url", ""),
                "snippet": (r.get("content") or "")[:500]}
               for r in rows if r.get("url")]
        progress.log("검색", f"Tavily {len(out)}건 — \"{query[:50]}\"")
        return out
    except httpx.HTTPError as e:
        progress.log("검색", f"⚠ Tavily 실패({type(e).__name__}) — DuckDuckGo 폴백")
        return ddg_search(query, settings, max_results=max_results)
