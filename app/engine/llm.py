"""LLM 어댑터 — 키만 넣으면 켜지는 구조 (ING-05).

우선순위: K-EXAONE(Friendli dedicated, 소버린 트랙) > Anthropic > None(Mock degrade).
인터페이스는 extract_json / complete_text 둘. 파인튜닝 모델(JDG-12)로 교체할 때도
이 인터페이스만 구현하면 된다.
"""
import json
import re
import time
from typing import Optional, Protocol

import httpx

from .. import progress
from ..config import Settings
from ..errors import EngineError


class Extractor(Protocol):
    def extract_json(self, system: str, user: str, schema: dict,
                     deep: bool = False) -> dict: ...
    def complete_text(self, system: str, user: str) -> str: ...


class AnthropicExtractor:
    """Anthropic Messages API + 구조화 출력(output_config.format) 추출기."""

    def __init__(self, settings: Settings):
        from anthropic import Anthropic   # 키 없는 환경에서도 모듈 import 가능하도록 지연
        self._client = Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    def extract_json(self, system: str, user: str, schema: dict,
                     deep: bool = False) -> dict:
        # deep은 K-EXAONE용 힌트 — Claude는 adaptive thinking이 자체 조절하므로 무시
        import anthropic
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.RateLimitError as e:
            raise EngineError(429, "rate_limited", f"LLM 레이트리밋: {e}")
        except anthropic.APIStatusError as e:
            raise EngineError(502, "llm_error", f"LLM 호출 실패({e.status_code}): {e.message}")
        except anthropic.APIConnectionError as e:
            raise EngineError(502, "llm_error", f"LLM 연결 실패: {e}")

        if response.stop_reason == "refusal":
            raise EngineError(502, "llm_refusal", "LLM이 요청을 거절했습니다.")
        if response.stop_reason == "max_tokens":
            raise EngineError(502, "llm_error", "LLM 출력이 잘렸습니다 — 자료를 줄여 재시도하세요.")
        return json.loads(response.content[0].text)

    def complete_text(self, system: str, user: str) -> str:
        import anthropic
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIStatusError as e:
            raise EngineError(502, "llm_error", f"LLM 호출 실패({e.status_code}): {e.message}")
        except anthropic.APIConnectionError as e:
            raise EngineError(502, "llm_error", f"LLM 연결 실패: {e}")
        if response.stop_reason == "refusal":
            raise EngineError(502, "llm_refusal", "LLM이 요청을 거절했습니다.")
        return response.content[0].text


_FORMAT_SYSTEM = ("당신은 구조화 변환기다. 주어진 전문가 분석을 지시된 JSON 스키마로 "
                  "옮긴다. 분석에 없는 내용을 새로 지어내지 말고, 있는 내용을 빠뜨리지도 "
                  "마라. 각 필드에는 [전문가 분석]에서 찾은 실제 값을 넣는다 — 스키마 규칙 "
                  "설명에 나온 예시 문구·플레이스홀더('주체 회사' 등)를 값으로 복사하지 마라. "
                  "모든 값은 완전한 한국어 문장으로 쓴다 — 한자·외국 문자·깨진 글자 혼입 금지, "
                  "중간에 끊긴 문장 금지. 고유명사(회사명·제품명)만 원어 허용.")

# 코드 레벨 정화 — 약한/작은 모델은 프롬프트를 덜 지키므로 사후에도 방어한다.
# 한국어 비즈니스 서술에 원래 없는 문자군(한자·가나·대체문자·제어문자)을 제거.
_GARBAGE_CHARS = re.compile(
    r"[一-鿿㐀-䶿぀-ヿ�\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean_text(s: str) -> str:
    """문장 속 한자·가나·깨진 글자를 제거해 순수 한국어 출력을 보장 (이슈 #3)."""
    cleaned = _GARBAGE_CHARS.sub("", s)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def sanitize(obj):
    """파싱된 출력의 모든 문자열 값을 재귀 정화. dict/list/str 모두 처리."""
    if isinstance(obj, str):
        return _clean_text(obj)
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    return obj


class _OpenAICompatExtractor:
    """OpenAI 호환 /chat/completions 어댑터 베이스.

    K-EXAONE(Friendli)·로컬 모델(Ollama 등)이 공유한다. 약한 모델을 고려한 방어:
    - deep=True: "깊게 추론(자유 서술) → 구조화(스키마)" 2단계 — 약한 모델일수록
      추론과 형식화를 분리하면 스키마 준수·환각 억제가 좋아진다.
    - json_schema 미지원 시 프롬프트 JSON 강제 폴백.
    - finish_reason=length 자동 폴백 + 파싱 실패 1회 재시도 + 출력 정화(sanitize).
    """

    def __init__(self, url: str, token: str, model: str, timeout: float,
                 provider_label: str, *, thinking_kwargs: bool = False):
        self._url = url
        self._token = token
        self._model = model
        self._timeout = timeout
        self._label = provider_label          # 로그 표시용
        self._thinking_kwargs = thinking_kwargs  # Friendli EXAONE만 True

    def _post(self, payload: dict) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            return httpx.post(self._url, headers=headers, json=payload,
                              timeout=self._timeout)
        except httpx.ConnectError as e:
            raise EngineError(502, "llm_unreachable",
                              f"{self._label} 서버에 연결할 수 없습니다 ({self._url}). "
                              f"오프라인 로컬 모델이면 서버(예: Ollama)가 실행 중인지 확인하세요.")
        except httpx.HTTPError as e:
            raise EngineError(502, "llm_error", f"{self._label} 연결 실패: {e}")

    def _chat(self, system: str, user: str, *, schema: Optional[dict] = None,
              thinking: bool = False, max_tokens: int = 8192) -> str:
        progress.log(self._label,
                     f"호출 시작 — reasoning {'ON(깊은 추론)' if thinking else 'OFF'}"
                     f"{' · 스키마 강제' if schema else ''} · 입력 {len(system) + len(user):,}자")
        t0 = time.time()
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
        }
        if self._thinking_kwargs:
            payload["chat_template_kwargs"] = {"enable_thinking": thinking}
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": schema}}
        resp = self._post(payload)

        # 구조화 출력 미지원 폴백 — 프롬프트로 JSON 강제
        if resp.status_code in (400, 422) and schema is not None:
            payload.pop("response_format")
            payload["messages"][1]["content"] += (
                "\n\n[출력 형식 — 반드시 준수] 아래 JSON 스키마에 맞는 JSON 객체 "
                "하나만 출력한다. 스키마 외 텍스트·설명·마크다운·코드펜스 금지.\n"
                + json.dumps(schema, ensure_ascii=False))
            resp = self._post(payload)

        if resp.status_code == 401:
            raise EngineError(502, "llm_error", f"{self._label} 인증 실패 — 토큰 확인")
        if resp.status_code == 429:
            raise EngineError(429, "rate_limited", f"{self._label} 레이트리밋 — 잠시 후 재시도")
        if resp.status_code >= 400:
            raise EngineError(502, "llm_error",
                              f"{self._label} 호출 실패({resp.status_code}): {resp.text[:300]}")

        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        usage = data.get("usage", {})
        progress.log(self._label,
                     f"응답 수신 — {time.time() - t0:.1f}초 · "
                     f"완료 토큰 {usage.get('completion_tokens', '?')} · "
                     f"finish={choice.get('finish_reason')}")
        # reasoning이 예산을 다 먹어 본문이 잘렸으면 thinking 끄고 1회 재시도
        if choice.get("finish_reason") == "length" and thinking and self._thinking_kwargs:
            progress.log(self._label, "⚠ reasoning이 토큰 예산 소진 — thinking OFF로 재시도")
            return self._chat(system, user, schema=schema, thinking=False,
                              max_tokens=max_tokens)
        if choice.get("finish_reason") == "length":
            raise EngineError(502, "llm_error",
                              f"{self._label} 출력이 잘렸습니다 — 입력 자료를 줄여 재시도하세요.")
        return content

    @staticmethod
    def _parse_json(text: str) -> dict:
        # <think> 블록·코드펜스 제거 후 JSON 추출 → 정화
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
            try:
                return sanitize(json.loads(candidate))
            except (json.JSONDecodeError, ValueError):
                continue
        raise EngineError(502, "llm_error",
                          f"JSON 파싱 실패 — 응답 앞부분: {text[:200]}")

    def extract_json(self, system: str, user: str, schema: dict,
                     deep: bool = False) -> dict:
        if deep:
            with progress.node("llm.reason", "깊은 추론 (reasoning ON)"):
                progress.log("추론", "1단계 — 깊은 추론 시작 (자유 서술, 수 분 소요될 수 있음)")
                analysis = self._chat(
                    system,
                    user + "\n\n지시된 절차대로 깊게 추론한 뒤, 요구된 모든 항목의 내용을 "
                           "하나도 빠짐없이 자연어로 서술하라. (JSON이 아니라 서술문으로. "
                           "빈 항목을 남기지 말 것 — 모르면 '미상'과 그 이유를 쓴다. "
                           "자료에 없는 고유명사·수치·날짜를 절대 지어내지 마라.)",
                    thinking=True, max_tokens=16384)
                progress.log("추론", f"1단계 완료 — 분석 {len(analysis):,}자 생성")
            with progress.node("llm.format", "구조화 (스키마 강제)"):
                progress.log("추론", "2단계 — 구조화 시작 (스키마 강제)")
                format_user = (f"[스키마 규칙 원문]\n{system}\n\n[전문가 분석]\n{analysis}\n\n"
                               "위 분석을 스키마 JSON으로 구조화하라. 분석에 없는 내용을 "
                               "추가하지 마라.")
                return self._retry_json(_FORMAT_SYSTEM, format_user, schema)
        with progress.node("llm.format", "구조화 (단일 호출)"):
            return self._retry_json(system, user, schema)

    def _retry_json(self, system: str, user: str, schema: dict) -> dict:
        for attempt in (1, 2):
            try:
                return self._parse_json(self._chat(
                    system, user, schema=schema, thinking=False, max_tokens=8192))
            except EngineError as e:
                if attempt == 2 or e.code == "llm_unreachable":
                    raise
                progress.log("추론", "⚠ 파싱 실패 — 1회 재시도")

    def complete_text(self, system: str, user: str) -> str:
        return _clean_text(
            self._chat(system, user, thinking=False, max_tokens=1024).strip())


class FriendliExtractor(_OpenAICompatExtractor):
    """K-EXAONE-236B (Friendli dedicated) — controllable reasoning 사용."""

    def __init__(self, settings: Settings):
        super().__init__(
            "https://api.friendli.ai/dedicated/v1/chat/completions",
            settings.friendli_token, settings.friendli_endpoint_id,
            settings.llm_timeout, "K-EXAONE", thinking_kwargs=True)


class LocalExtractor(_OpenAICompatExtractor):
    """로컬/저사양 OpenAI 호환 모델 (Ollama·llama.cpp 등) — 오프라인 실행.

    thinking_kwargs=False — 범용 모델은 EXAONE의 reasoning 토글이 없다. deep 경로의
    2단계 분리는 그대로 작동하므로 약한 모델도 스키마 준수·환각 억제 효과를 받는다.
    """

    def __init__(self, settings: Settings):
        super().__init__(
            settings.local_base_url, settings.local_token,
            settings.local_model, settings.llm_timeout,
            f"Local({settings.local_model})", thinking_kwargs=False)


def get_extractor(settings: Settings) -> Optional[Extractor]:
    """LLM_PROVIDER에 고정된 어댑터만 사용 (다른 모델 개입 없음). 없으면 None(→ Mock)."""
    provider = settings.llm_provider
    if provider == "friendli":
        if settings.friendli_token and settings.friendli_endpoint_id:
            return FriendliExtractor(settings)
        return None
    if provider == "local":
        if settings.local_base_url and settings.local_model:
            return LocalExtractor(settings)
        return None
    if provider == "anthropic":
        if settings.anthropic_api_key:
            return AnthropicExtractor(settings)
        return None
    if provider == "mock":
        return None
    raise EngineError(500, "config_error",
                      f"알 수 없는 LLM_PROVIDER: {provider} "
                      "(friendli|local|anthropic|mock)")
