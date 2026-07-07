"""비전 질문 위치 탐지 — 엑사원 질문을 IR덱 페이지 이미지에서 찾아 bbox로 반환.

Simsa(cts_screening 검토 SaaS)에서 검증된 패턴 재사용: Gemini에 페이지 이미지를
주고 box_2d(0~1000 정규화, [ymin,xmin,ymax,xmax])로 위치를 직접 받는다.
텍스트 추출(llm.py)과는 완전히 독립된 선택 기능 — GEMINI_API_KEY가 없으면
get_vision_extractor()가 None을 반환하고, 호출부는 이 기능 없이 정상 동작한다.

계약 강제는 프롬프트(BBOX_SYSTEM)와 이 파일의 검증기가 이중으로 한다 —
프롬프트가 선언한 규칙(좌표계·관련도 임계·인용 대조)을 코드가 실제로 집행한다:
  validate_box()      기하 계약 — 순서·범위·최소크기·최대면적
  grounding_score()   인용 계약 — quote ⊆ 페이지 텍스트 레이어 (문자 3-gram 포함도)
프롬프트만의 규칙은 규칙이 아니다. 위반 항목은 폐기되고 폐기 사유가 로그에 남는다.
"""
import base64
import json
import re
import time
from typing import Optional

import httpx

from .. import progress
from ..config import Settings
from ..errors import EngineError
from .llm import sanitize
from .prompts import BBOX_SCHEMA, BBOX_SYSTEM, bbox_user

# 계약 임계값 — BBOX_SYSTEM 프롬프트에 선언된 수치와 반드시 일치해야 한다
MIN_SIDE = 4              # 변 최소 길이 (0~1000 좌표계)
MAX_AREA = 500_000        # 최대 면적 = 페이지의 50%
REL_THRESHOLD = 0.5       # relevance 기권 임계 (미만 폐기)
GROUND_THRESHOLD = 0.6    # 인용 그라운딩 포함도 임계 (미만 폐기)


def validate_box(box) -> bool:
    """기하 계약: [y0,x0,y1,x1], 0≤·≤1000, y0<y1, x0<x1, 변≥4, 면적≤50%."""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    try:
        y0, x0, y1, x1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return False
    if not all(0 <= v <= 1000 for v in (y0, x0, y1, x1)):
        return False
    if (y1 - y0) < MIN_SIDE or (x1 - x0) < MIN_SIDE:
        return False
    return (y1 - y0) * (x1 - x0) <= MAX_AREA


def _norm_chars(s: str) -> str:
    """공백·구두점 제거 + 소문자 — 렌더링/추출 표기 차이에 불변인 문자열."""
    return re.sub(r"[\s\W_]+", "", s or "", flags=re.UNICODE).lower()


def grounding_score(quote: str, page_text: str) -> Optional[float]:
    """인용 계약: quote가 실제로 그 페이지에 있는가.

    포함도(containment) = |3-gram(quote) ∩ 3-gram(page)| / |3-gram(quote)| ∈ [0,1].
    비대칭 척도를 쓰는 이유: quote는 페이지의 부분집합이어야 하므로 Jaccard가 아니라
    quote 기준 포함도가 맞다. 정규화 부분문자열이면 1.0 조기 반환.
    페이지에 텍스트 레이어가 없으면(스캔·이미지 PDF) None = '검증 불가' — 기각하지
    않는다 (검증 불가와 검증 실패는 다르다).
    """
    q, p = _norm_chars(quote), _norm_chars(page_text)
    if not q:
        return 0.0
    if not p:
        return None
    if q in p:
        return 1.0
    grams_q = {q[i:i + 3] for i in range(len(q) - 2)} or {q}
    grams_p = {p[i:i + 3] for i in range(len(p) - 2)} or {p}
    return len(grams_q & grams_p) / len(grams_q)


def pin_score(relevance: float, grounding: Optional[float]) -> float:
    """핀 결합 점수 s = r · g — 질문당 상위 K개 선별에 쓴다.
    검증 불가(g=None)는 g=0.75로 간주: 검증 실패(폐기)보다 높고 완전 검증(1.0)보다 낮게."""
    return relevance * (0.75 if grounding is None else grounding)


_RETRYABLE = {429, 500, 502, 503}
_MAX_ATTEMPTS = 3


class GeminiBBoxExtractor:
    """VLM 전송 어댑터 — 배치·재시도·토큰 계측을 이 층에서 전담한다.

    설계 원칙 (A2A 프로토콜 차용):
    - 배치 전송: 한 요청에 [질문 텍스트 part] + [PAGE n 라벨 part + 이미지 part]×k.
      프롬프트(질문 목록)가 요청마다 중복되므로, 페이지를 묶을수록 토큰이 절약된다.
    - 토큰 계측: 응답 usageMetadata를 읽어 누적한다. 추정이 아니라 실측.
    - 토큰 예산: 누적 totalTokens가 예산을 넘으면 남은 배치를 포기하고 사유를 로그.
    - 재시도: 429/5xx는 지수 백오프(1→2→4초, Retry-After 우선)로 최대 3회.
    """

    def __init__(self, api_key: str, model: str, timeout: float = 60.0,
                 token_budget: int = 300_000):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._url = (f"https://generativelanguage.googleapis.com/v1beta/"
                    f"models/{model}:generateContent")
        self.token_budget = token_budget
        self.tokens_used = 0          # usageMetadata.totalTokenCount 누적 (실측)
        self.calls = 0

    @property
    def budget_exhausted(self) -> bool:
        return self.tokens_used >= self.token_budget

    def _post_with_retry(self, payload: dict, label: str) -> Optional[httpx.Response]:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = httpx.post(self._url, params={"key": self._api_key},
                                  json=payload, timeout=self._timeout)
            except httpx.HTTPError as e:
                if attempt == _MAX_ATTEMPTS:
                    progress.log("비전", f"⚠ {label} 네트워크 실패 {attempt}회 — 포기 ({e})")
                    return None
                time.sleep(2 ** (attempt - 1))
                continue
            if resp.status_code in _RETRYABLE and attempt < _MAX_ATTEMPTS:
                retry_after = resp.headers.get("retry-after")
                wait = float(retry_after) if (retry_after or "").replace(".", "", 1).isdigit() \
                    else 2 ** (attempt - 1)
                progress.log("비전", f"{label} {resp.status_code} — {wait:.0f}초 후 재시도 "
                                    f"({attempt}/{_MAX_ATTEMPTS})")
                time.sleep(min(wait, 30))
                continue
            return resp
        return None

    def locate_batch(self, pages: list[tuple[int, bytes, str]],
                     questions: list[str]) -> list[dict]:
        """페이지 배치 [(page_no, image_bytes, mime), ...] + 질문 → 위치 목록.

        각 dict: question_index/page/quote/box_2d/relevance. 실패는 이 배치만
        건너뛰고 빈 리스트 — 질문 시각화는 보조 기능이라 온보딩을 막지 않는다.
        """
        if self.budget_exhausted:
            progress.log("비전", f"⚠ 토큰 예산 소진({self.tokens_used:,}/"
                                f"{self.token_budget:,}) — 배치 건너뜀")
            return []
        page_nos = [n for n, _, _ in pages]
        parts: list[dict] = [
            {"text": BBOX_SYSTEM + "\n\n" + bbox_user(questions, page_nos)}]
        img_bytes = 0
        for n, img, mime in pages:
            parts.append({"text": f"[PAGE {n}]"})
            parts.append({"inline_data": {"mime_type": mime,
                                          "data": base64.b64encode(img).decode()}})
            img_bytes += len(img)
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": BBOX_SCHEMA,
            },
        }
        label = f"배치 p.{page_nos[0]}~{page_nos[-1]}"
        t0 = time.time()
        resp = self._post_with_retry(payload, label)
        if resp is None:
            return []
        if resp.status_code >= 400:
            progress.log("비전", f"⚠ {label} 실패({resp.status_code}) — 건너뜀: "
                                f"{resp.text[:200]}")
            return []
        try:
            data = resp.json()
            usage = data.get("usageMetadata", {})
            self.tokens_used += int(usage.get("totalTokenCount") or 0)
            self.calls += 1
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            locations = sanitize(json.loads(text)).get("locations", [])
        except (KeyError, IndexError, json.JSONDecodeError, ValueError) as e:
            progress.log("비전", f"⚠ {label} 응답 파싱 실패 — 건너뜀 ({e})")
            return []
        progress.log("비전",
                     f"{label} 완료 — {time.time() - t0:.1f}초 · 페이지 {len(pages)}장"
                     f"({img_bytes / 1024:.0f}KB) · 위치 {len(locations)}건 · "
                     f"토큰 {usage.get('promptTokenCount', '?')}+"
                     f"{usage.get('candidatesTokenCount', '?')} "
                     f"(누적 {self.tokens_used:,}/{self.token_budget:,})")
        return locations


def make_batches(pages, max_pages: int, max_bytes: int) -> list[list]:
    """전송 배치 구성 — 페이지 수(max_pages)와 이미지 총량(max_bytes) 이중 상한.

    pages: (page_no, image_bytes, mime) 리스트. 단일 페이지가 max_bytes를 넘어도
    혼자서 한 배치는 된다 (아예 못 보내는 것보다 낫다).
    """
    batches: list[list] = []
    cur: list = []
    cur_bytes = 0
    for p in pages:
        size = len(p[1])
        if cur and (len(cur) >= max_pages or cur_bytes + size > max_bytes):
            batches.append(cur)
            cur, cur_bytes = [], 0
        cur.append(p)
        cur_bytes += size
    if cur:
        batches.append(cur)
    return batches


def get_vision_extractor(settings: Settings) -> Optional[GeminiBBoxExtractor]:
    if not settings.vision_enabled:
        return None
    return GeminiBBoxExtractor(settings.gemini_api_key, settings.gemini_model,
                               token_budget=settings.vision_token_budget)
