"""LLM 어댑터 — Claude(Anthropic Messages API) 하나 (ING-05).

2026-10: 외부 모델 API를 Claude로 통일했다. 예전 공급자(K-EXAONE/Friendli·OpenAI·
로컬 OpenAI 호환)는 지웠다 — 공급자마다 다른 방어 코드(2단계 추론→구조화, json_schema
미지원 폴백, finish=length 폴백)가 Claude 경로에선 쓰이지 않는 채로 남아 있었다.
키가 없으면 조용히 대체하지 않고 실패한다. 인터페이스는 extract_json / complete_text 둘 —
파인튜닝 모델(JDG-12)로 교체할 때도 이 인터페이스만 구현하면 된다.
"""
import json
import re
import time
from typing import Optional, Protocol

from .. import progress
from ..config import Settings
from ..errors import EngineError


class Extractor(Protocol):
    def extract_json(self, system: str, user: str, schema: dict,
                     deep: bool = False) -> dict: ...
    def complete_text(self, system: str, user: str) -> str: ...


class AnthropicExtractor:
    """Claude (Anthropic Messages API) — 기본 서빙 모델.

    - 모델: ANTHROPIC_MODEL(기본 claude-opus-5-5). tier="fast"는 ANTHROPIC_MODEL_FAST
      (비면 같은 모델 — 설정하지 않으면 아무것도 바뀌지 않는다).
    - 사고: Opus 5.5는 항상 켜져 있고(adaptive) 끌 수 없다 — effort로만 조절한다.
      기본은 medium(이 모델의 기본값), deep 경로(자료 다층 독해·판정)는 high.
    - 스트리밍: 추론 + 구조화 출력이 길어도 HTTP 타임아웃에 걸리지 않게
      stream + get_final_message.
    - 거절 대비: 안전 분류기가 거절하면 같은 호출 안에서 다른 모델이 이어 받는다
      (server-side fallback "default"). 그래도 거절이면 llm_refusal로 정직하게 실패.
    - 캐싱: 시스템 프롬프트는 prompts.py에 고정 — top-level cache_control로 접두어 캐시.
    - 본문: 사고 블록이 앞에 올 수 있으므로 content[0]이 아니라 text 블록만 모은다.
    """

    _FALLBACK_BETA = "server-side-fallback-2026-07-01"
    # 서버 측 거절 대비를 받는 모델(Haiku 5.5는 지원하지 않는다)
    _FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

    def __init__(self, settings: Settings, tier: str = "default"):
        from anthropic import Anthropic   # 키 없는 환경에서도 모듈 import 가능하도록 지연
        self._client = Anthropic(api_key=settings.anthropic_api_key,
                                 timeout=settings.llm_timeout, max_retries=3)
        self._model = (settings.anthropic_model_fast
                       if tier == "fast" and settings.anthropic_model_fast
                       else settings.anthropic_model)
        self._effort = settings.anthropic_effort
        self._effort_deep = settings.anthropic_effort_deep

    def _call(self, system: str, user: str, *, effort: str, max_tokens: int,
              schema: Optional[dict] = None) -> str:
        import anthropic
        output_config: dict = {"effort": effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict = {
            "model": self._model, "max_tokens": max_tokens,
            "system": system, "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "adaptive"}, "output_config": output_config,
            "cache_control": {"type": "ephemeral"},
        }
        if self._model in self._FALLBACK_MODELS:
            kwargs.update(betas=[self._FALLBACK_BETA], fallbacks="default")
        t0 = time.time()
        progress.log("Claude", f"호출 시작 — {self._model} · effort {effort}"
                                f"{' · 구조화' if schema else ''}")
        try:
            with self._client.beta.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except anthropic.RateLimitError as e:
            raise EngineError(429, "rate_limited", f"LLM 레이트리밋: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise EngineError(502, "llm_error",
                              f"LLM 호출 실패({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise EngineError(502, "llm_error", f"LLM 연결 실패: {e}") from e
        if msg.stop_reason == "refusal":
            cat = getattr(getattr(msg, "stop_details", None), "category", None)
            raise EngineError(502, "llm_refusal",
                              f"LLM이 요청을 거절했습니다{f' ({cat})' if cat else ''}.")
        if msg.stop_reason == "max_tokens":
            raise EngineError(502, "llm_error", "LLM 출력이 잘렸습니다 — 자료를 줄여 재시도하세요.")
        text = "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        progress.log("Claude", f"완료 {time.time() - t0:.1f}s — {msg.model} · 입력 "
                               f"{u.input_tokens}(캐시 {u.cache_read_input_tokens or 0}) · "
                               f"출력 {u.output_tokens}")
        return text

    def extract_json(self, system: str, user: str, schema: dict,
                     deep: bool = False, effort: Optional[str] = None) -> dict:
        # effort: 짧은 정렬·채점(scorer_client 비교 모드)처럼 깊이가 필요 없는 호출은 low
        text = self._call(system, user, schema=schema, max_tokens=64000,
                          effort=effort or (self._effort_deep if deep else self._effort))
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise EngineError(502, "llm_error", f"구조화 출력이 JSON이 아닙니다: {e}") from e

    def complete_text(self, system: str, user: str) -> str:
        return self._call(system, user, effort=self._effort, max_tokens=16000)


# 코드 레벨 정화 — 약한/작은 모델은 프롬프트를 덜 지키므로 사후에도 방어한다.
#
# 두 문자군을 분리한다(2026-08 정규화). 원래는 하나로 묶여 있었는데, 해외 리드
# 발굴을 붙이자 그게 사고가 됐다 — 일본 기업명·현지어 검색어·일본어 메일이
# 통째로 지워졌다(실측: 株式会社ヤマト飲料 → '').
#   · 제어문자·대체문자(�): 어떤 언어에서도 쓰레기 → 항상 제거
#   · 한자·가나: 한국어 서술에서는 EXAONE 환각 노이즈지만, 해외 기업 데이터에서는
#     정상 값 → keep_cjk=True일 때 보존
_CONTROL_CHARS = re.compile(r"[�\x00-\x08\x0b\x0c\x0e-\x1f]")
_CJK_CHARS = re.compile(r"[一-鿿㐀-䶿぀-ヿ]")


def _clean_text(s: str, *, keep_cjk: bool = False) -> str:
    """깨진 글자를 제거한다. keep_cjk=False면 한자·가나까지 제거해 순수 한국어를
    보장한다 (이슈 #3). 해외 데이터 경로는 keep_cjk=True로 호출한다."""
    cleaned = _CONTROL_CHARS.sub("", s)
    if not keep_cjk:
        cleaned = _CJK_CHARS.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def sanitize(obj, *, keep_cjk: bool = False):
    """파싱된 출력의 모든 문자열 값을 재귀 정화. dict/list/str 모두 처리."""
    if isinstance(obj, str):
        return _clean_text(obj, keep_cjk=keep_cjk)
    if isinstance(obj, list):
        return [sanitize(x, keep_cjk=keep_cjk) for x in obj]
    if isinstance(obj, dict):
        return {k: sanitize(v, keep_cjk=keep_cjk) for k, v in obj.items()}
    return obj


def get_extractor(settings: Settings, tier: str = "default") -> Extractor:
    """Claude 어댑터. tier="fast"는 **판정이 아닌 작업**(자료에 적힌 회사 이름 추리기,
    표기 정리)용 — ANTHROPIC_MODEL_FAST가 비어 있으면 기본 모델 그대로.

    mock 제거 (2026-07): 키가 없을 때 규칙 기반 결과로 조용히 대체하던 경로는 '가짜 결과가
    진짜처럼 보이는' 통로였다. 키가 없으면 즉시 실패한다."""
    if settings.llm_provider != "anthropic":
        raise EngineError(500, "config_error",
                          f"LLM_PROVIDER={settings.llm_provider}는 더 이상 지원하지 않습니다 — "
                          "Claude(anthropic)만 씁니다. .env에서 LLM_PROVIDER를 지우거나 "
                          "anthropic으로 두세요")
    if settings.anthropic_api_key:
        return AnthropicExtractor(settings, tier=tier)
    raise EngineError(500, "config_error",
                      "ANTHROPIC_API_KEY가 없습니다 (.env 확인) — 조용한 대체 없음")
