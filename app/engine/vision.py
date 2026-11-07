"""비전 질문 위치 탐지 — 엑사원 질문을 IR덱 페이지 이미지에서 찾아 bbox로 반환.

Simsa(cts_screening 검토 SaaS)의 box_2d 패턴 재사용: 비전 모델(Claude)에 페이지
이미지를 주고 box_2d(0~1000 정규화, [ymin,xmin,ymax,xmax])로 위치를 직접 받는다.
원래 Gemini로 검증·캘리브레이션된 패턴이며(2026-10 Claude로 전환), Claude의 박스
정밀도는 아직 그 캘리브레이션에 대해 실측되지 않았다.
텍스트 추출(llm.py)과는 독립된 선택 기능 — ANTHROPIC_API_KEY가 없으면
get_vision_extractor()가 None을 반환하고, 호출부는 이 기능 없이 정상 동작한다.

계약 강제는 프롬프트(BBOX_SYSTEM)와 이 파일의 검증기가 이중으로 한다 —
프롬프트가 선언한 규칙(좌표계·관련도 임계·인용 대조)을 코드가 실제로 집행한다:
  validate_box()      기하 계약 — 순서·범위·최소크기·최대면적
  grounding_score()   인용 계약 — quote ⊆ 페이지 텍스트 레이어 (문자 3-gram 포함도)
프롬프트만의 규칙은 규칙이 아니다. 위반 항목은 폐기되고 폐기 사유가 로그에 남는다.
"""
import base64
import json
import os
import re
import time
from typing import Optional

from .. import progress
from ..config import Settings
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


MIN_QUOTE_CHARS = 6   # 정규화 후 이 미만이면 인용 검증 자체가 무의미 (우연 매칭)


def grounding_score(quote: str, page_text: str) -> Optional[float]:
    """인용 계약: quote가 실제로 그 페이지에 있는가.

    포함도(containment) = |3-gram(quote) ∩ 3-gram(page)| / |3-gram(quote)| ∈ [0,1].
    비대칭 척도를 쓰는 이유: quote는 페이지의 부분집합이어야 하므로 Jaccard가 아니라
    quote 기준 포함도가 맞다. 정규화 부분문자열이면 1.0 조기 반환.
    None = '검증 불가' 2경우 — 기각하지 않는다 (검증 불가 ≠ 검증 실패):
    (a) 페이지 텍스트 레이어 없음(스캔 PDF), (b) 정규화 후 quote가 6자 미만 —
        적대적 검토 확정(VIS-01): '40%'→'40' 같은 초단문 인용은 페이지 어디든
        우연히 등장해 g=1.0을 받아 인용 감사가 무력화됐다.
    """
    q, p = _norm_chars(quote), _norm_chars(page_text)
    if not q:
        return 0.0
    if len(q) < MIN_QUOTE_CHARS:
        return None
    if not p:
        return None
    if q in p:
        return 1.0
    grams_q = {q[i:i + 3] for i in range(len(q) - 2)} or {q}
    grams_p = {p[i:i + 3] for i in range(len(p) - 2)} or {p}
    return len(grams_q & grams_p) / len(grams_q)


def pin_score(relevance: float, grounding: Optional[float]) -> float:
    """핀 결합 점수 s = r · g — 질문당 상위 K개 선별에 쓴다.
    검증 불가(g=None)는 통과 하한(GROUND_THRESHOLD=0.6)으로 간주 — 적대적 검토
    확정(VIS-02): 예전 0.75는 실측 g∈[0.6,0.75) '검증 통과' 핀보다 '검증 불가'
    핀을 우대하는 랭킹 역전을 만들었다. 검증됨 ≥ 검증 불가 순서를 보장한다."""
    return relevance * (GROUND_THRESHOLD if grounding is None else grounding)


class ClaudeBBoxExtractor:
    """VLM 전송 어댑터 (Claude Messages API) — 배치·재시도·토큰 계측을 이 층에서 전담한다.

    설계 원칙 (A2A 프로토콜 차용):
    - 배치 전송: 한 요청에 [PAGE n 라벨 + 이미지]×k + [질문 텍스트]. 질문 목록이
      요청마다 중복되므로, 페이지를 묶을수록 토큰이 절약된다.
    - 좌표: 저장 형식은 그대로 box_2d = [ymin,xmin,ymax,xmax], 0~1000 정규화.
      Claude는 기본적으로 픽셀 좌표로 답하는 경향이 있어, 라벨에 이미지 픽셀 크기를
      적어 주고 프롬프트(BBOX_SYSTEM)에서 정규화 값을 명시적으로 요구한다.
      정규화 값은 서버 측 이미지 축소와 무관하다는 장점도 있다. 범위·순서 위반은
      validate_box가 폐기한다(보정하지 않음).
      ⚠ Claude 박스 정밀도는 아직 Gemini 시절 캘리브레이션(임계값·여백 규칙)에 대해
      실측되지 않았다 — 임계값은 Gemini 기준 그대로다.
    - 구조화 출력: output_config.format(json_schema) — 응답이 BBOX_SCHEMA를 따르도록 강제.
    - 사고: adaptive(Opus 5.5는 항상 켜짐), 깊이는 effort로만 조절. 사고 블록이 앞에
      올 수 있으므로 text 블록만 모아 파싱한다. 스트리밍 + get_final_message로
      다중 이미지 요청의 HTTP 타임아웃을 피한다.
    - 토큰 계측: 응답 usage(input+cache+output)를 누적한다. 추정이 아니라 실측.
    - 토큰 예산: 누적 토큰이 예산을 넘으면 남은 배치를 포기하고 사유를 로그.
    - 재시도: SDK max_retries(429/408/409/5xx·연결 오류, 지수 백오프·Retry-After 존중)에
      맡긴다 — 최대 3회 시도(최초 1 + 재시도 2)로 예전 계약과 같다.
    - 실패: 배치 단위로 정직하게 건너뛴다(빈 리스트 + 사유 로그). 거절(refusal)·출력
      잘림(max_tokens)·JSON 파싱 실패도 같은 처리 — 조용히 가짜 위치를 만들지 않는다.
    """
    # 서버 측 거절 대비(fallbacks="default")를 받는 모델 — llm.py와 같은 목록
    _FALLBACK_BETA = "server-side-fallback-2026-07-01"
    _FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5",
                        "claude-sonnet-5-5"}
    _MAX_RETRIES = 2              # SDK 재시도 횟수 → 총 시도 3회
    _MAX_TOKENS = 32000           # 사고 + locations JSON — 스트리밍이라 타임아웃 걱정 없음

    def __init__(self, api_key: str, model: str, effort: str = "medium",
                 timeout: float = 300.0, token_budget: int = 300_000, client=None):
        if client is None:
            from anthropic import Anthropic   # 키 없는 환경에서도 모듈 import 가능하도록 지연
            client = Anthropic(api_key=api_key, timeout=timeout,
                               max_retries=self._MAX_RETRIES)
        self._client = client
        self._model = model
        self._effort = effort
        self.token_budget = token_budget
        self.tokens_used = 0          # usage 입력(캐시 포함)+출력 누적 (실측)
        self.calls = 0

    @property
    def budget_exhausted(self) -> bool:
        return self.tokens_used >= self.token_budget

    def _request(self, content: list[dict], label: str):
        """한 배치 전송. 실패면 None (사유는 로그) — 재시도는 SDK가 이미 했다."""
        import anthropic
        kwargs: dict = {
            "model": self._model, "max_tokens": self._MAX_TOKENS,
            "system": BBOX_SYSTEM,
            "messages": [{"role": "user", "content": content}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._effort,
                              "format": {"type": "json_schema", "schema": BBOX_SCHEMA}},
        }
        if self._model in self._FALLBACK_MODELS:
            kwargs.update(betas=[self._FALLBACK_BETA], fallbacks="default")
        try:
            with self._client.beta.messages.stream(**kwargs) as stream:
                return stream.get_final_message()
        except anthropic.APIStatusError as e:
            progress.log("비전", f"⚠ {label} 실패({e.status_code}) — 건너뜀: "
                                f"{str(e.message)[:200]}")
        except anthropic.APIConnectionError as e:   # APITimeoutError 포함
            progress.log("비전", f"⚠ {label} 네트워크 실패 {self._MAX_RETRIES + 1}회 — "
                                f"포기 ({e})")
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
        content: list[dict] = []
        img_bytes = 0
        for n, img, mime in pages:
            content.append({"type": "text", "text": _page_label(n, img)})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": mime,
                "data": base64.standard_b64encode(img).decode("utf-8")}})
            img_bytes += len(img)
        # 이미지 뒤에 지시문 — 질문 목록 + 라벨 규약
        content.append({"type": "text", "text": bbox_user(questions, page_nos)})

        label = f"배치 p.{page_nos[0]}~{page_nos[-1]}"
        t0 = time.time()
        msg = self._request(content, label)
        if msg is None:
            return []
        u = msg.usage
        in_tok = (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) \
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        self.tokens_used += in_tok + (u.output_tokens or 0)
        self.calls += 1
        if msg.stop_reason == "refusal":
            cat = getattr(getattr(msg, "stop_details", None), "category", None)
            progress.log("비전", f"⚠ {label} 모델이 거절{f'({cat})' if cat else ''} — 건너뜀")
            return []
        if msg.stop_reason == "max_tokens":
            progress.log("비전", f"⚠ {label} 출력 잘림(max_tokens) — 건너뜀")
            return []
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            data = sanitize(json.loads(text))
            locations = data.get("locations", []) if isinstance(data, dict) else []
            if not isinstance(locations, list):
                raise ValueError("locations가 배열이 아님")
        except (json.JSONDecodeError, ValueError, AttributeError) as e:
            progress.log("비전", f"⚠ {label} 응답 파싱 실패 — 건너뜀 ({e})")
            return []
        progress.log("비전",
                     f"{label} 완료 — {time.time() - t0:.1f}초 · {msg.model} · "
                     f"페이지 {len(pages)}장({img_bytes / 1024:.0f}KB) · "
                     f"위치 {len(locations)}건 · 토큰 {in_tok}+{u.output_tokens} "
                     f"(누적 {self.tokens_used:,}/{self.token_budget:,})")
        return locations


def _image_size(img: bytes) -> Optional[tuple[int, int]]:
    """이미지 픽셀 크기 (가로, 세로). 읽지 못하면 None — 라벨에서 생략할 뿐이다."""
    try:
        import fitz   # PyMuPDF — 페이지 렌더링(ingest/pdf_render.py)과 같은 의존성
        pix = fitz.Pixmap(img)
        return pix.width, pix.height
    except Exception:
        return None


def _page_label(page_no: int, img: bytes) -> str:
    size = _image_size(img)
    if size is None:
        return f"[PAGE {page_no}]"
    return f"[PAGE {page_no}] (이미지 {size[0]}×{size[1]}px — 좌표는 0~1000 정규화로)"


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


def vision_model(settings: Settings) -> str:
    """비전 모델 — ANTHROPIC_VISION_MODEL(비면 ANTHROPIC_MODEL, 기본 claude-opus-5-5)."""
    return (getattr(settings, "anthropic_vision_model", "")
            or os.environ.get("ANTHROPIC_VISION_MODEL", "")
            or settings.anthropic_model)


def get_vision_extractor(settings: Settings) -> Optional[ClaudeBBoxExtractor]:
    """ANTHROPIC_API_KEY가 있으면 켜진다. 없으면 None — 호출부는 이 기능 없이 동작."""
    if not settings.anthropic_api_key:
        return None
    return ClaudeBBoxExtractor(settings.anthropic_api_key, vision_model(settings),
                               effort=settings.anthropic_effort,
                               token_budget=settings.vision_token_budget)
