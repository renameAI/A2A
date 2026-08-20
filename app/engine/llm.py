"""LLM 어댑터 — 키만 넣으면 켜지는 구조 (ING-05).

우선순위: K-EXAONE(Friendli dedicated, 소버린 트랙) > Anthropic > None(Mock degrade).
인터페이스는 extract_json / complete_text 둘. 파인튜닝 모델(JDG-12)로 교체할 때도
이 인터페이스만 구현하면 된다.
"""
import json
import re
import threading
import time
from typing import Optional, Protocol
from urllib.parse import urlparse

import httpx

from .. import progress
from ..config import Settings
from ..errors import EngineError
from .prompts import FORMAT_SYSTEM


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


# 코드 레벨 정화 — 약한/작은 모델은 프롬프트를 덜 지키므로 사후에도 방어한다.
#
# 두 문자군을 분리한다(2026-08 정규화). 원래는 하나로 묶여 있었는데, 해외 리드
# 발굴을 붙이자 그게 사고가 됐다 — 일본 기업명·현지어 검색어·일본어 메일이
# 통째로 지워졌다(실측: 株式会社ヤマト飲料 → '').
#   · 제어문자·대체문자(�): 어떤 언어에서도 쓰레기 → 항상 제거
#   · 한자·가나: 한국어 서술에서는 EXAONE 환각 노이즈지만, 해외 기업 데이터에서는
#     정상 값 → keep_cjk=True일 때 보존
# 429·5xx 재시도 횟수. 4회면 지수 백오프로 최대 ~30초 대기 —
# job 타임아웃(900s) 안에서 충분히 회복 기회를 준다.
_MAX_HTTP_ATTEMPTS = 4

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




class _SynthResp:
    """스트리밍 SSE를 다 모은 뒤 httpx.Response의 소비 인터페이스(status_code·
    json()·text)만 합성 — _chat의 재시도·폴백 로직이 스트리밍 여부를 모르게 한다."""

    def __init__(self, status_code: int, *, data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._data = data
        self.text = text if data is None else json.dumps(data, ensure_ascii=False)

    def json(self) -> dict:
        if self._data is None:
            raise ValueError("스트리밍 오류 응답에는 JSON 본문이 없습니다")
        return self._data


class _OpenAICompatExtractor:
    """OpenAI 호환 /chat/completions 어댑터 베이스.

    K-EXAONE(Friendli)·로컬 모델(Ollama 등)이 공유한다. 약한 모델을 고려한 방어:
    - deep=True: "깊게 추론(자유 서술) → 구조화(스키마)" 2단계 — 약한 모델일수록
      추론과 형식화를 분리하면 스키마 준수·환각 억제가 좋아진다.
    - json_schema 미지원 시 프롬프트 JSON 강제 폴백.
    - finish_reason=length 자동 폴백 + 파싱 실패 1회 재시도 + 출력 정화(sanitize).
    """

    def __init__(self, url: str, token: str, model: str, timeout: float,
                 provider_label: str, *, thinking_kwargs: bool = False,
                 max_tokens_field: str = "max_tokens",
                 supports_temperature: bool = True,
                 strict_schema: bool = False):
        self._url = url
        self._token = token
        self._model = model
        self._timeout = timeout
        self._label = provider_label          # 로그 표시용
        self._thinking_kwargs = thinking_kwargs  # Friendli EXAONE만 True
        # 출력 상한 파라미터명 — GPT-5.6 계열은 max_tokens를 거부하고
        # max_completion_tokens를 요구한다(실측: 400 unsupported_parameter).
        # 다른 OpenAI 호환 서버(Ollama 등)는 max_tokens 그대로.
        self._max_tokens_field = max_tokens_field
        # GPT-5.6 계열은 temperature 커스텀을 거부한다(400 unsupported_value —
        # 기본값 1만 허용). 우리 0.5는 EXAONE 반복 루프 방어용이라 모델이
        # 안 받으면 그냥 빼면 된다 — 보내서 요청 전체를 죽이는 것보다 낫다.
        self._supports_temperature = supports_temperature
        self._strict_schema = strict_schema

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _post(self, payload: dict, *, timeout: float | None = None) -> "httpx.Response | _SynthResp":
        limit = self._timeout if timeout is None else timeout
        # 실시간 스트리밍 (데모 채팅 UI) — SSE로 델타를 받아 progress.live로 흘리고,
        # 완료 후 기존 코드가 기대하는 Response 모양(_SynthResp)으로 합성해 돌려준다.
        # _post 안에서만 처리하는 이유: 위층(_chat)의 재시도·length·빈응답·폭주 가드가
        # 전부 resp 인터페이스를 보므로, 여기서 모양만 맞추면 로직 변경이 0이다.
        import os as _os
        if _os.environ.get("LLM_STREAM", "1") != "0":
            return self._post_stream(payload, limit)
        headers = self._headers()
        try:
            return httpx.post(self._url, headers=headers, json=payload,
                              timeout=limit)
        except httpx.ConnectError as e:
            raise EngineError(502, "llm_unreachable",
                              f"{self._label} 서버에 연결할 수 없습니다 ({self._url}). "
                              f"오프라인 로컬 모델이면 서버(예: Ollama)가 실행 중인지 확인하세요.")
        except httpx.TimeoutException as e:
            kind = type(e).__name__
            progress.log(self._label, f"타임아웃 — {kind} · 제한 {limit:.0f}초")
            raise EngineError(504, "llm_timeout",
                              f"{self._label} 타임아웃({kind}) — 제한 {limit:.0f}초")
        except httpx.HTTPError as e:
            raise EngineError(502, "llm_error", f"{self._label} 연결 실패: {e}")

    def _post_stream(self, payload: dict, limit: float) -> "_SynthResp":
        """SSE 스트리밍 호출 — 델타를 progress.live로 흘리고 Response 모양으로 합성.

        타임아웃 의미 보존: httpx의 timeout은 '읽기 한 번'의 제한이라, 토큰을 계속
        뱉는 폭주는 영원히 안 걸린다. 벽시계를 직접 재서 limit 초과 시 기존과 같은
        llm_timeout으로 올린다 — _chat의 폭주 재샘플 가드가 그대로 작동한다."""
        body = dict(payload)
        body["stream"] = True
        thinking = bool(body.get("chat_template_kwargs", {}).get("enable_thinking"))
        schema_call = "response_format" in body
        label = ("깊은 추론" if thinking else "구조화" if schema_call else "작성")
        t0 = time.time()
        reason_parts: list[str] = []
        content_parts: list[str] = []
        fin: str | None = None
        usage: dict = {}
        last_push = 0.0
        try:
            with httpx.stream("POST", self._url, headers=self._headers(),
                              json=body, timeout=limit) as resp:
                if resp.status_code != 200:
                    resp.read()   # 에러 본문은 위층(400/422 폴백·오류 메시지)이 쓴다
                    return _SynthResp(resp.status_code, text=resp.text)
                for line in resp.iter_lines():
                    if limit and time.time() - t0 > limit:
                        progress.log(self._label,
                                     f"타임아웃 — 스트리밍 벽시계 · 제한 {limit:.0f}초")
                        raise EngineError(504, "llm_timeout",
                                          f"{self._label} 타임아웃(stream) — "
                                          f"제한 {limit:.0f}초")
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        d = json.loads(chunk)
                    except ValueError:
                        continue
                    if d.get("usage"):
                        usage = d["usage"]
                    for ch in d.get("choices", []):
                        delta = ch.get("delta") or {}
                        if delta.get("reasoning_content"):
                            reason_parts.append(delta["reasoning_content"])
                        if delta.get("content"):
                            content_parts.append(delta["content"])
                        if ch.get("finish_reason"):
                            fin = ch["finish_reason"]
                    # 중단 가드 없음 (2026-07-28 철회) — 반복이 보여도 끊지 않는다.
                    # 끊고 재시작하면 '느림'이 '느림 + 재시작'이 될 뿐이고(judge 1건
                    # 386초 실측), 재샘플본 품질이 더 나빴다. 모델이 스스로 끝내게
                    # 두고, 사용자에겐 스트리밍으로 진행 상황을 보여준다.
                    now = time.time()
                    if now - last_push >= 0.4 and (reason_parts or content_parts):
                        last_push = now
                        body_txt = "".join(content_parts)
                        if schema_call:
                            # 구조화 구간은 원시 JSON을 뱉는다 — 화면에 그대로 흘리면
                            # 술 같은 이스케이프가 보이고, 모델이 퇴화 반복에
                            # 빠지면("술술술…") 그 쓰레기까지 노출된다. 진행 상황만
                            # 숫자로 알리고 본문은 숨긴다(파싱된 결과가 곧 나온다).
                            progress.live_update(
                                label, "", chars=len(body_txt), quiet=True)
                        else:
                            progress.live_update(
                                label, body_txt or "".join(reason_parts),
                                thinking=not body_txt)
        except httpx.ConnectError:
            raise EngineError(502, "llm_unreachable",
                              f"{self._label} 서버에 연결할 수 없습니다 ({self._url}). "
                              f"오프라인 로컬 모델이면 서버(예: Ollama)가 실행 중인지 확인하세요.")
        except httpx.TimeoutException as e:
            kind = type(e).__name__
            progress.log(self._label, f"타임아웃 — {kind} · 제한 {limit:.0f}초")
            raise EngineError(504, "llm_timeout",
                              f"{self._label} 타임아웃({kind}) — 제한 {limit:.0f}초")
        except httpx.HTTPError as e:
            raise EngineError(502, "llm_error", f"{self._label} 연결 실패: {e}")
        finally:
            progress.live_clear()

        content = "".join(content_parts)
        # 추론(reasoning_content 델타)은 live 표시용으로만 쓰고 최종 content엔 섞지
        # 않는다 — 비스트리밍 응답도 추론을 content에 싣지 않았다(실측: 꼬리 조각만).
        # 섞으면 deep 2단계 구조화 입력이 추론 전문(수천 토큰)까지 먹어 계약이 변한다.
        return _SynthResp(200, data={
            "choices": [{"message": {"content": content},
                         "finish_reason": fin or "stop"}],
            "usage": usage})

    def _post_with_heartbeat(self, payload: dict, *, t0: float,
                             phases: list[str],
                             timeout: float | None = None) -> httpx.Response:
        run = progress.current()
        node = run.current_node() if run else None
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(20):
                elapsed = int(time.time() - t0)
                phase = phases[min(elapsed // 40, len(phases) - 1)]
                if run is not None:
                    run.add(self._label,
                            f"모델 응답 대기 — {elapsed}s · {phase}",
                            node=node)

        thread = threading.Thread(target=beat, daemon=True)
        thread.start()
        try:
            return self._post(payload, timeout=timeout)
        finally:
            stop.set()

    @staticmethod
    def _phases(thinking: bool, schema: Optional[dict]) -> list[str]:
        if thinking:
            return [
                "1층 표면 독해: 자료에 명시된 제품·서비스·주체 분리",
                "2층 기능 독해: 고객의 결핍과 돈 내는 이유 추론",
                "3층 경제 독해: 수익 구조·선투자·반복 매출 신호 확인",
                "4층 전략 독해: 현재 단계와 절실한 것 역추론",
                "5층 양면 독해: 파는 쪽과 사는 쪽의 자산·결핍 정리",
                "상(像) 정리: identity·edge·gaps·risk_signals 압축",
            ]
        if schema:
            return [
                "스키마 구조화: 필드별 값과 provenance 배치",
                "근거 매핑: evidence_chunk_ids 연결",
                "질문 선별: open_questions 원자성·중복성 점검",
            ]
        return ["텍스트 생성 대기"]

    def _reason_timeout(self, thinking: bool) -> float | None:
        """추론 호출 상한 — 기본은 걸지 않는다(None = 기본 timeout 600s).

        철회(2026-07-28): 150초 상한 + 재샘플을 넣었더니 **더 느려졌다**. 실측 —
        judge 1건이 추론 23,989자에서 150초에 잘리고 재샘플로 다시 시작해 총 386초.
        안 끊었으면 한 번에 끝났을 가능성이 높다. 게다가 재샘플본은 영어로 사고하고
        회사명이 잘리는 등(‘Le Petit Marais Bouty’) 품질도 나빴다.

        판단이 오래 걸리는 건 정상이다 — 기다리면 끝난다. 끊고 다시 굴리는 건
        '느림'을 '느림 + 재시작'으로 바꿀 뿐이었다. 체감 개선은 스트리밍(생성 중
        텍스트 노출)으로 해결할 문제지 호출을 자르는 것으로 풀 문제가 아니다.

        LLM_REASON_TIMEOUT을 명시하면 그 값으로 상한을 건다(디버깅용). 기본은 무제한
        (엔진 공통 timeout만 적용).
        """
        if not (thinking and self._thinking_kwargs):
            return None
        import os as _os
        raw = _os.environ.get("LLM_REASON_TIMEOUT", "")
        if not raw:
            return None
        return min(float(raw), self._timeout)

    @staticmethod
    def _backoff_seconds(resp, attempt: int) -> float:
        """Retry-After를 존중하고, 없으면 지수 백오프.

        지터를 넣는 이유: 웨이브 하나가 여러 호출을 병렬로 내보내므로, 같은
        간격으로 재시도하면 다시 동시에 몰려 또 429가 난다.
        """
        import random
        ra = (resp.headers.get("Retry-After") or "").strip()
        if ra.isdigit():
            return min(float(ra), 30.0)
        return min(2.0 ** attempt, 16.0) * (0.5 + random.random())

    def _chat(self, system: str, user: str, *, schema: Optional[dict] = None,
              thinking: bool = False, max_tokens: int = 8192,
              temperature: Optional[float] = None, _retry_empty: bool = True,
              _timeout_override: float | None = None, _attempt: int = 1) -> str:
        host = urlparse(self._url).netloc or self._url
        progress.tick_llm()
        progress.log(self._label,
                     f"호출 시작 — reasoning {'ON(깊은 추론)' if thinking else 'OFF'}"
                     f"{' · 스키마 강제' if schema else ''} · 입력 {len(system) + len(user):,}자"
                     f" · max_tokens {max_tokens:,} · timeout {self._timeout:.0f}s"
                     f" · endpoint {host} · model {self._model}")
        for phase in self._phases(thinking, schema):
            progress.log("추론 계획", phase)
        t0 = time.time()
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            self._max_tokens_field: max_tokens,
        }
        if temperature is not None and self._supports_temperature:
            payload["temperature"] = temperature
        if self._thinking_kwargs:
            payload["chat_template_kwargs"] = {"enable_thinking": thinking}
        if schema is not None:
            js = {"name": "output", "schema": schema}
            if self._strict_schema:
                # OpenAI 구조화 출력은 strict 없이는 스키마를 '힌트'로만 쓴다 —
                # 형태만 맞고 값이 빈 문자열로 오는 사고를 실측했다(검색어 4개가
                # 전부 ""). strict=true여야 제약 디코딩이 실제로 걸린다.
                js["strict"] = True
            payload["response_format"] = {"type": "json_schema", "json_schema": js}
        reason_limit = (_timeout_override if _timeout_override is not None
                        else self._reason_timeout(thinking))
        try:
            resp = self._post_with_heartbeat(
                payload, t0=t0, phases=self._phases(thinking, schema),
                timeout=reason_limit)
        except EngineError as e:
            # 폭주 재샘플 철회(2026-07-28) — 끊고 다시 굴리면 총 시간이 늘고
            # 재샘플본 품질이 더 나빴다(judge 386초·영어 사고·회사명 잘림).
            # LLM_REASON_TIMEOUT을 명시했을 때만 상한이 걸리고, 초과하면
            # 그냥 타임아웃으로 올린다 — 조용히 다시 굴리지 않는다.
            raise

        # 구조화 출력 미지원 폴백 — 프롬프트로 JSON 강제
        if resp.status_code in (400, 422) and schema is not None:
            payload.pop("response_format")
            payload["messages"][1]["content"] += (
                "\n\n[출력 형식 — 반드시 준수] 아래 JSON 스키마에 맞는 JSON 객체 "
                "하나만 출력한다. 스키마 외 텍스트·설명·마크다운·코드펜스 금지.\n"
                + json.dumps(schema, ensure_ascii=False))
            progress.log(self._label, "response_format 미지원 가능성 — JSON 강제 프롬프트로 재호출")
            resp = self._post_with_heartbeat(
                payload, t0=time.time(), phases=self._phases(False, schema),
                timeout=reason_limit)

        if resp.status_code == 401:
            raise EngineError(502, "llm_error", f"{self._label} 인증 실패 — 토큰 확인")
        # 429·5xx는 **재시도로 회복되는 실패**다. 백오프 없이 한 번에 포기하면
        # 웨이브 중간에 429 하나로 검색 전체가 죽는다(감사 확정 medium).
        # 인증 실패(401)는 재시도해도 같으므로 위에서 즉시 올린다.
        if resp.status_code == 429 or resp.status_code >= 500:
            if _attempt < _MAX_HTTP_ATTEMPTS:
                wait = self._backoff_seconds(resp, _attempt)
                progress.log(self._label,
                             f"⚠ {resp.status_code} — {wait:.1f}초 뒤 재시도 "
                             f"({_attempt}/{_MAX_HTTP_ATTEMPTS})")
                time.sleep(wait)
                return self._chat(system, user, schema=schema, thinking=thinking,
                                  max_tokens=max_tokens, temperature=temperature,
                                  _retry_empty=_retry_empty,
                                  _timeout_override=_timeout_override,
                                  _attempt=_attempt + 1)
            if resp.status_code == 429:
                raise EngineError(429, "rate_limited",
                                  f"{self._label} 레이트리밋 — {_MAX_HTTP_ATTEMPTS}회 "
                                  f"재시도했으나 계속 거부됩니다. 잠시 후 다시 시도하세요.")
        if resp.status_code >= 400:
            raise EngineError(502, "llm_error",
                              f"{self._label} 호출 실패({resp.status_code}): {resp.text[:300]}")

        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        usage = data.get("usage", {})
        progress.add_tokens(usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0))
        progress.log(self._label,
                     f"응답 수신 — {time.time() - t0:.1f}초 · "
                     f"완료 토큰 {usage.get('completion_tokens', '?')} · "
                     f"finish={choice.get('finish_reason')}")
        # reasoning이 예산을 다 먹은 경우 — 전체 재호출(콜 2배 → 300초 타임아웃의
        # 직접 원인)은 본문이 아예 빈 경우에만 한다. 부분 본문이 있으면 그대로 쓴다.
        if choice.get("finish_reason") == "length" and thinking and self._thinking_kwargs:
            if content.strip():
                progress.log(self._label,
                             "⚠ 추론이 예산 소진했으나 본문 존재 — 재호출 없이 사용(loop 절약)")
                return content
            progress.log(self._label, "⚠ 추론 예산 소진·본문 없음 — thinking OFF로 재시도")
            return self._chat(system, user, schema=schema, thinking=False,
                              max_tokens=max_tokens, temperature=temperature)
        if choice.get("finish_reason") == "length":
            raise EngineError(502, "llm_error",
                              f"{self._label} 출력이 잘렸습니다 — 입력 자료를 줄여 재시도하세요.")
        # 실측(할리케이 PDF 온보딩, 2026-07-27): 깊은 추론 호출이 finish_reason=stop인데
        # 완료 토큰 5·본문 0자를 반환한 사례 — 동일 입력 즉시 재시도로는 1,913자 정상
        # 생성됨(API 비결정적 실패, 예산 소진이 아님). 위 length 가드는 finish=length만
        # 잡아 이 경우를 그대로 통과시켰고, 빈 분석이 구조화 단계로 흘러가 "미상" 프로필
        # → REP-06 게이트 실패 → open_questions까지 비어 clarify 174초 호출이 통째로
        # 버려지는 막다른 에러로 끝났다. thinking 호출에 한해 1회 재시도 — 재현상 거의
        # 항상 회복된다. 구조화(schema) 호출은 _retry_json이 이미 파싱 실패로 재시도한다.
        if thinking and not content.strip() and _retry_empty:
            progress.log(self._label,
                         f"⚠ 빈 응답(finish={choice.get('finish_reason')}·"
                         f"완료 토큰 {usage.get('completion_tokens', '?')}) — 1회 재시도")
            return self._chat(system, user, schema=schema, thinking=thinking,
                              max_tokens=max_tokens, temperature=temperature,
                              _retry_empty=False)
        if thinking and not content.strip():
            raise EngineError(502, "llm_error",
                              f"{self._label} 응답이 비어 있습니다(재시도 후에도).")
        return content

    @staticmethod
    def _parse_json(text: str, *, allow_foreign: bool = False) -> dict:
        # <think> 블록·코드펜스 제거 후 JSON 추출 → 정화.
        # 닫는 태그 오타 허용: 실측으로 </thihk>가 나왔다(모델이 자기 종료 토큰을
        # 틀리게 쓰는 케이스). 정확한 </think>만 지우면 오타본은 그대로 남아 JSON
        # 파싱까지 같이 죽는다 — think 블록은 우리가 쓰지 않는 부산물이므로
        # 철자가 흔들려도 잘라낸다. 닫는 태그가 아예 없으면 열린 지점부터 버린다.
        text = re.sub(r"<think>.*?</th\w*k>", "", text, flags=re.S | re.I)
        text = re.sub(r"<think>(?![\s\S]*</th\w*k>).*?(?=\{)", "", text, flags=re.S | re.I)
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
            try:
                # 해외 리드 발굴에서는 일본어·중국어가 정상 데이터다 —
                # 정화기는 EXAONE 환각 방어용이라 CJK까지 지우면 현지 회사명·
                # 검색어·메일 본문이 통째로 날아간다(실측: 株式会社ヤマト飲料 → '').
                # 제어문자·깨진 글자는 어느 경로에서든 계속 제거한다.
                return sanitize(json.loads(candidate), keep_cjk=allow_foreign)
            except (json.JSONDecodeError, ValueError):
                continue
        raise EngineError(502, "llm_error",
                          f"JSON 파싱 실패 — 응답 앞부분: {text[:200]}")

    def extract_json(self, system: str, user: str, schema: dict,
                     deep: bool = False, allow_foreign: bool = False) -> dict:
        """allow_foreign=True면 한국어 정화기를 건너뛴다 — 해외 리드 발굴에서
        일본어·중국어 회사명·검색어·메일 본문은 정상 데이터다."""
        if deep:
            # 추론 토큰 예산 — 표준 정책은 16384(절삭 없이 끝까지 사고). 낮추지 않는다.
            # 실측(2026-07-27): .env에서 4500으로 눌러둔 채 judge를 10축으로 늘렸더니
            # 추론이 중간에 끊겨 "예산 소진·본문 없음" → thinking OFF 강등 폴백으로
            # 떨어졌다. 그 결과 축 8개가 캐스케이드로 전부 얕은 caution/unknown이 됐다
            # (품질 붕괴). 16384로 복원하니 같은 케이스가 83.8초 만에 안 끊기고 끝까지
            # 추론했고, 오히려 총 소요도 짧아졌다(thinking OFF 폴백이 더 비쌌다) —
            # 3분 loop 예산은 캐스케이드·병렬화로 지키는 것이지, 추론 예산을 깎아서
            # 지키는 게 아니다(#44). 환경변수로 낮출 수는 있으나 표준값은 16384다.
            import os as _os
            reason_max = int(_os.environ.get("LLM_REASON_MAX_TOKENS", "16384"))
            with progress.node("llm.reason", "깊은 추론 (reasoning ON)"):
                progress.log("추론", f"1단계 — 깊은 추론 시작 (예산 {reason_max:,}토큰)")
                analysis = self._chat(
                    system,
                    user + "\n\n지시된 절차대로 깊게 추론한 뒤, 요구된 모든 항목의 내용을 "
                           "하나도 빠짐없이 자연어로 서술하라. (JSON이 아니라 서술문으로. "
                           "빈 항목을 남기지 말 것 — 모르면 '미상'과 그 이유를 쓴다. "
                           "자료에 없는 고유명사·수치·날짜를 절대 지어내지 마라. "
                           "같은 근거·문장을 항목마다 반복해서 재서술하지 말고, "
                           "각 항목당 핵심만 간결하게 — 예산을 채우려고 늘리지 마라. "
                           "항목당 4~5문장 상한을 지켜라 — 자료에 사실이 많을수록 "
                           "대표성 있는 2~3개만 골라 인용하고 나머지는 생략한다. "
                           "이건 정확도를 낮추는 게 아니라 편집이다.)",
                    thinking=True, max_tokens=reason_max,
                    # 실측(E11 게이트): 온도 미지정 시 동일 입력 재실행 일치율 0.06
                    # (매번 다른 서사 창작) — 그런데 0.2로 낮추자 300초 타임아웃이
                    # 2/10→4/5로 급증(reasoning 모델의 알려진 실패모드: 온도가
                    # 너무 낮으면 사고 사슬이 반복 루프에 갇혀 안 끝남). 0.5로
                    # 완화 — 반복문 회피용 최소 엔트로피는 남기고 창의성만 낮춘다.
                    temperature=0.5)
                progress.log("추론", f"1단계 완료 — 분석 {len(analysis):,}자 생성")
            with progress.node("llm.format", "구조화 (스키마 강제)"):
                progress.log("추론", "2단계 — 구조화 시작 (스키마 강제)")
                format_user = (f"[스키마 규칙 원문]\n{system}\n\n[전문가 분석]\n{analysis}\n\n"
                               "위 분석을 스키마 JSON으로 구조화하라. 분석에 없는 내용을 "
                               "추가하지 마라.")
                return self._retry_json(FORMAT_SYSTEM, format_user, schema)
        with progress.node("llm.format", "구조화 (단일 호출)"):
            return self._retry_json(system, user, schema,
                                    allow_foreign=allow_foreign)

    def _retry_json(self, system: str, user: str, schema: dict, *,
                    allow_foreign: bool = False) -> dict:
        # 구조화 출력 상한 — 비대 프로필(자료 인용 다수)에서 judge JSON이 8192를
        # 넘겨 잘리는 사례(finish_reason=length) 실측. 기본 16384로 상향, 환경변수로
        # 조정. cap은 상한일 뿐이라 정상 출력은 EOS로 일찍 멈춰 지연·비용 영향 없음
        # (긴 꼬리만 완주). Friendli N-gram speculative(엔드포인트 토글)와 결합하면
        # 그 꼬리도 병렬 검증으로 당겨진다.
        import os as _os
        struct_max = int(_os.environ.get("LLM_STRUCT_MAX_TOKENS", "16384"))
        for attempt in (1, 2):
            try:
                return self._parse_json(self._chat(
                    system, user, schema=schema, thinking=False, max_tokens=struct_max,
                    # 0.2 → 0.5 (2026-07-28). 앞선 주석은 "스키마 강제라 반복 루프
                    # 위험이 낮다(문법 제약)"고 봤는데, 실측이 그 가정을 깼다:
                    # 구조화(단일 호출)에서 </thihk>가 8,594자 반복되고 타임아웃 2회
                    # 연속으로 났다. </thihk>는 </think>의 오타 — 모델이 자기 종료
                    # 토큰을 못 만들어 무한 재시도에 갇힌 것이다.
                    #
                    # 문법 제약이 못 막는 이유: json_schema는 **JSON 본문**만 구속하고
                    # think 블록·서두 잡담은 문법 밖이라 자유 생성이다. _parse_json이
                    # 사후에 <think>…</think>를 지우는 것도 **닫는 태그가 정상일 때만**
                    # 통하고, 오타로 닫히면 정규식이 안 걸려 파싱까지 실패한다.
                    #
                    # 온도는 이미 추론 호출에서 같은 실패모드로 0.2→0.5로 올린 전례가
                    # 있다(300초 타임아웃 2/10→4/5 급증). 같은 처방을 여기에도 적용.
                    temperature=0.5), allow_foreign=allow_foreign)
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


class OpenAIExtractor(_OpenAICompatExtractor):
    """OpenAI Chat Completions (기본 gpt-5.6-luna) — SaaS 서빙 트랙 (이슈 #6).

    thinking_kwargs=False — EXAONE의 reasoning 토글이 없다. deep 경로의 2단계
    분리(추론→구조화)는 그대로 작동해 경량 티어(Luna)의 스키마 준수를 돕는다.
    """

    def __init__(self, settings: Settings, tier: str = "default"):
        model = settings.openai_model
        if tier == "fast" and settings.openai_model_fast:
            model = settings.openai_model_fast
        super().__init__(
            settings.openai_base_url, settings.openai_api_key,
            model, settings.llm_timeout, "OpenAI",
            thinking_kwargs=False, max_tokens_field="max_completion_tokens",
            supports_temperature=False, strict_schema=True)


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


def get_extractor(settings: Settings, tier: str = "default") -> Extractor:
    """LLM_PROVIDER에 고정된 어댑터만 사용 (다른 모델 개입 없음).

    tier="fast"는 **판정이 아닌 작업**(자료에 적힌 회사 이름을 추리는 추출,
    표기 정리)에 쓴다. 그 작업들은 스키마가 좁고 정답이 자료 안에 있어 작은
    모델로 충분한데, 지금은 문턱·언어 판정과 같은 모델을 써서 웨이브1에서
    가장 오래 걸리는 구간이 됐다. OPENAI_MODEL_FAST가 비어 있으면 기본
    모델을 그대로 쓴다 — 설정을 안 하면 아무것도 바뀌지 않는다.

    mock 제거 (2026-07): 예전엔 provider=mock이거나 키가 없으면 None을 돌려주고
    호출측이 규칙 기반 결과로 조용히 대체했다. 그 경로는 '가짜 결과가 진짜처럼
    보이는' 통로였다 — 실측으로 두 번 물렸다:
      · LLM_PROVIDER=mock인데 .env가 자동 로드돼 스코어러는 실 API를 호출(비용·비결정)
      · 골든셋을 mock으로 재다가 결정적 앵커 경로만 검증하고 실제 경로를 놓칠 뻔함
    이제 키가 없으면 **조용히 대체하지 않고 즉시 실패**한다."""
    provider = settings.llm_provider
    if provider == "friendli":
        if settings.friendli_token and settings.friendli_endpoint_id:
            return FriendliExtractor(settings)
        raise EngineError(500, "config_error",
                          "LLM_PROVIDER=friendli인데 FRIENDLI_TOKEN·"
                          "FRIENDLI_ENDPOINT_ID가 없습니다 (.env 확인) — "
                          "조용한 대체 없음")
    if provider == "local":
        if settings.local_base_url and settings.local_model:
            return LocalExtractor(settings)
        raise EngineError(500, "config_error",
                          "LLM_PROVIDER=local인데 LOCAL_LLM_BASE_URL·"
                          "LOCAL_LLM_MODEL이 없습니다 — 조용한 대체 없음")
    if provider == "anthropic":
        if settings.anthropic_api_key:
            return AnthropicExtractor(settings)
        raise EngineError(500, "config_error",
                          "LLM_PROVIDER=anthropic인데 ANTHROPIC_API_KEY가 "
                          "없습니다 — 조용한 대체 없음")
    if provider == "openai":
        if settings.openai_api_key:
            return OpenAIExtractor(settings, tier=tier)
        raise EngineError(500, "config_error",
                          "LLM_PROVIDER=openai인데 OPENAI_API_KEY가 "
                          "없습니다 — 조용한 대체 없음")
    raise EngineError(500, "config_error",
                      f"알 수 없는 LLM_PROVIDER: {provider} "
                      "(friendli|local|anthropic)")
