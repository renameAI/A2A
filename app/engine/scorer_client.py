"""학습 스코어러 HTTP 클라이언트 — retrieve 랭킹 백엔드 (training/scorer/serve.py 대응).

GPU 서버의 FastAPI 서빙(/score-batch)을 SSH 터널로 호출한다. 역할 분리:
  - τ 게이트(강한 후보 판정)는 기존 휴리스틱이 그대로 담당 (캘리브레이션 보존, RET-06)
  - 학습 스코어러는 게이트 통과 후보의 '순서'만 다시 매긴다

정직 폴백: 서버 부재·타임아웃·비정상 응답이면 None을 반환하고 로그를 남긴다 —
호출측(retrieve)은 휴리스틱 순서를 유지한다. 조용한 대체 없음.
"""
import time
from typing import Optional

import httpx

from .. import progress
from ..config import get_settings

# API 채점(K-EXAONE-236B) 프롬프트 — 학습 스코어러와 같은 보완성 기준.
_API_SYS = (
    "너는 B2B 매칭 애널리스트다. 두 기업이 '사업 파트너로서 얼마나 관련(보완) "
    "있는가'를 0~10으로 매긴다. 유사도가 아니라 보완성 — 한쪽의 산출물/역량이 "
    "다른 쪽의 결핍/수요를 메우면 높다. 동종 경쟁사는 낮다.\n"
    "0~2=무관/경쟁, 3~5=약한 접점, 6~7=뚜렷한 보완, 8~10=강한 보완.\n"
    '반드시 JSON 하나로만: {"score": <0~10 정수>, "reason": "<한 문장>"}')


def profile_facts(name: str, industry: str, country: str, description: str) -> str:
    """엔진 프로필 → 학습 분포와 같은 facts 형식 (build_real_data.facts_text 대응).

    스코어러는 "이름 — 섹터/시장 실사실" 형식으로 학습됐다. 서빙 입력도 같은
    형식으로 맞춰야 분포 이탈(OOD)로 인한 점수 왜곡을 줄인다."""
    desc = f" {description}" if description else ""
    return f"{name} — 산업 섹터: {industry}, 국가: {country}.{desc}"


def score_batch(pairs: list[tuple[str, str]]) -> Optional[list[float]]:
    """(a_text, b_text) 쌍들을 일괄 채점 → 기댓값 점수 리스트. 실패 시 None.

    순서는 입력 순서 그대로. 부분 실패는 없다 — 전부 오거나 None."""
    scores, _ = score_batch_timed(pairs)
    return scores


def score_batch_timed(pairs) -> tuple[Optional[list[float]], Optional[int]]:
    """score_batch + 지연(ms). E9(1.2B 로컬) 노드용. 실패 시 (None, None)."""
    s = get_settings()
    if not s.scorer_url:
        return None, None
    try:
        t0 = time.time()
        r = httpx.post(f"{s.scorer_url.rstrip('/')}/score-batch",
                       json={"pairs": [{"a_text": a, "b_text": b}
                                       for a, b in pairs]},
                       timeout=s.scorer_timeout)
        r.raise_for_status()
        ms = int((time.time() - t0) * 1000)
        scores = [item["score"] for item in r.json()["scores"]]
        if len(scores) != len(pairs):
            raise ValueError(f"응답 수 불일치 {len(scores)} != {len(pairs)}")
        return scores, ms
    except Exception as e:  # 연결거부·타임아웃·형식오류 — 전부 정직 폴백
        progress.log("검색", f"⚠ 학습 스코어러 폴백(휴리스틱 순서 유지) — "
                             f"{type(e).__name__}: {e}")
        return None, None


def _parse_score(text: str) -> Optional[int]:
    import json
    import re
    try:
        i, j = text.find("{"), text.rfind("}")
        return max(0, min(10, int(json.loads(text[i:j + 1])["score"])))
    except Exception:                              # noqa: BLE001
        m = re.search(r"\b([0-9]|10)\b", text)
        return int(m.group(1)) if m else None


def api_score_batch(pairs) -> tuple[Optional[list[float]], Optional[int]]:
    """API(K-EXAONE-236B, Friendli)로 같은 쌍을 채점 → (점수, 지연ms).

    비교용 — 학습 스코어러(E9)와 나란히 놓는다. 개별 호출(API는 배치 없음)이라
    E9의 배치 1회 대비 지연이 크다(그게 비교의 핵심). 실패 시 (None, None)."""
    s = get_settings()
    if not (s.friendli_token and s.friendli_endpoint_id):
        return None, None
    url = "https://api.friendli.ai/dedicated/v1/chat/completions"
    hdr = {"Authorization": f"Bearer {s.friendli_token}"}
    scores = []
    t0 = time.time()
    try:
        with httpx.Client(timeout=s.scorer_timeout) as client:
            for a, b in pairs:
                r = client.post(url, headers=hdr, json={
                    "model": s.friendli_endpoint_id, "temperature": 0.2,
                    "max_tokens": 200, "messages": [
                        {"role": "system", "content": _API_SYS},
                        {"role": "user", "content":
                         f"[기업 A]\n{a[:1200]}\n\n[기업 B]\n{b[:1200]}\n\n"
                         "JSON으로 답하라."}],
                    "chat_template_kwargs": {"enable_thinking": False}})
                r.raise_for_status()
                sc = _parse_score(r.json()["choices"][0]["message"]["content"])
                scores.append(float(sc) if sc is not None else None)
        return scores, int((time.time() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        progress.log("검색", f"⚠ API 스코어러 폴백 — {type(e).__name__}: {e}")
        return None, None
