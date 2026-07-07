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


class GeminiBBoxExtractor:
    def __init__(self, api_key: str, model: str, timeout: float = 60.0):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._url = (f"https://generativelanguage.googleapis.com/v1beta/"
                    f"models/{model}:generateContent")

    def locate(self, image_png: bytes, questions: list[str],
              page: int) -> list[dict]:
        """한 페이지 이미지 + 엑사원 질문들 → 그 페이지에서 찾은 질문 위치(list of dict).

        각 dict: question_index/quote/box_2d([ymin,xmin,ymax,xmax]).
        VLM은 위치만 찾는다 — 무엇이 불명확한지는 엑사원이 이미 질문으로 정했다.
        호출 실패는 이 페이지만 건너뛰고 빈 리스트를 반환한다 — 질문 시각화는
        보조 기능이라 실패가 온보딩 전체를 막으면 안 된다.
        """
        payload = {
            "contents": [{"parts": [
                {"text": BBOX_SYSTEM + "\n\n" + bbox_user(questions)},
                {"inline_data": {"mime_type": "image/png",
                                "data": base64.b64encode(image_png).decode()}},
            ]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": BBOX_SCHEMA,
            },
        }
        t0 = time.time()
        try:
            resp = httpx.post(self._url, params={"key": self._api_key},
                              json=payload, timeout=self._timeout)
        except httpx.HTTPError as e:
            progress.log("비전", f"⚠ p.{page} 호출 실패 — 건너뜀 ({e})")
            return []
        if resp.status_code >= 400:
            progress.log("비전", f"⚠ p.{page} 호출 실패({resp.status_code}) — 건너뜀: "
                                f"{resp.text[:200]}")
            return []
        try:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = sanitize(json.loads(text))
            locations = parsed.get("locations", [])
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            progress.log("비전", f"⚠ p.{page} 응답 파싱 실패 — 건너뜀 ({e})")
            return []
        progress.log("비전", f"p.{page} 완료 — {time.time() - t0:.1f}초 · "
                            f"질문 위치 {len(locations)}건")
        return locations


def get_vision_extractor(settings: Settings) -> Optional[GeminiBBoxExtractor]:
    if not settings.vision_enabled:
        return None
    return GeminiBBoxExtractor(settings.gemini_api_key, settings.gemini_model)
