"""비전 근거 탐지 — IR덱 페이지 이미지에서 프로필 필드의 근거 위치(bbox)를 찾는다.

Simsa(cts_screening 검토 SaaS)에서 검증된 패턴 재사용: Gemini에 페이지 이미지를
주고 box_2d(0~1000 정규화, [ymin,xmin,ymax,xmax])로 근거 위치를 직접 받는다.
텍스트 추출(llm.py)과는 완전히 독립된 선택 기능 — GEMINI_API_KEY가 없으면
get_vision_extractor()가 None을 반환하고, 호출부는 이 기능 없이 정상 동작한다.
"""
import base64
import json
import time
from typing import Optional

import httpx

from .. import progress
from ..config import Settings
from ..errors import EngineError
from .llm import sanitize
from .prompts import BBOX_SCHEMA, BBOX_SYSTEM, bbox_user


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
