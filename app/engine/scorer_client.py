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


# RankGPT(arXiv:2304.09542) 스타일 listwise 재랭킹 프롬프트.
# pointwise(_API_SYS)는 후보마다 절대점수를 따로 매겨 호출이 N회 들고 점수가
# 뭉갠다(실측: 상위 후보들이 0.104/0.099/0.098로 변별 안 됨). listwise는 후보를
# 한 번에 놓고 '상대 비교'로 순열만 뱉게 해 호출 1회 + 변별력을 얻는다.
_RANK_SYS = (
    "너는 B2B 매칭 애널리스트다. 주어진 '이상적 상대의 상'에 가장 잘 맞는 순서로 "
    "후보 기업을 정렬한다. 기준은 유사도가 아니라 보완성 — 내 솔루션이 상대의 "
    "결핍/수요를 메우는가. 동종 경쟁사·공급사는 뒤로 보낸다.\n"
    "출력은 식별자 순열 하나만. 예: [2] > [1] > [3]\n"
    "설명·인사·코드펜스 금지. 모든 후보를 빠짐없이 한 번씩 포함할 것.")


def _parse_permutation(text: str, n: int) -> Optional[list[int]]:
    """'[2] > [1] > [3]' → [1, 0, 2] (0-based 원본 인덱스 순위).

    모델이 항목을 빠뜨리거나 중복·범위 밖을 뱉는 일이 잦다(RankGPT 논문도 후처리를
    전제). 정직 규칙: 중복·범위밖은 버리고, **누락된 후보는 원래 순서로 뒤에 붙인다**
    — 조용히 후보를 삭제하지 않는다. 유효 항목이 하나도 없으면 None(폴백).
    """
    import re
    seen, order = set(), []
    for tok in re.findall(r"\[(\d+)\]", text):
        i = int(tok) - 1                       # 프롬프트는 1-based
        if 0 <= i < n and i not in seen:
            seen.add(i)
            order.append(i)
    if not order:
        return None
    order += [i for i in range(n) if i not in seen]   # 누락분 보존
    return order


def api_rank_listwise(query: str, docs: list[str]
                      ) -> tuple[Optional[list[int]], Optional[int]]:
    """RankGPT listwise 재랭킹 — 1회 호출로 전체 순열. → (순위 인덱스, 지연ms).

    query: 이상적 상대의 상(이미 보완성으로 변환된 문장 — HyDE와 같은 구조)
    docs:  후보 기업 facts 텍스트
    반환:  docs의 0-based 인덱스를 '좋은 순'으로 정렬한 리스트. 실패 시 (None, None).

    후보 수가 적어(≤ 재랭킹 창) 슬라이딩 윈도우 없이 단일 호출로 끝낸다.
    """
    s = get_settings()
    if not (s.friendli_token and s.friendli_endpoint_id) or not docs:
        return None, None
    listing = "\n\n".join(f"[{i + 1}] {d[:900]}" for i, d in enumerate(docs))
    user = (f"[이상적 상대의 상]\n{query[:1500]}\n\n"
            f"[후보 {len(docs)}개]\n{listing}\n\n"
            f"위 {len(docs)}개를 보완성이 높은 순서로 정렬해 순열만 출력하라.")
    t0 = time.time()
    try:
        with httpx.Client(timeout=s.scorer_timeout) as client:
            r = client.post(
                "https://api.friendli.ai/dedicated/v1/chat/completions",
                headers={"Authorization": f"Bearer {s.friendli_token}"},
                json={"model": s.friendli_endpoint_id, "temperature": 0.0,
                      "max_tokens": 256, "messages": [
                          {"role": "system", "content": _RANK_SYS},
                          {"role": "user", "content": user}],
                      "chat_template_kwargs": {"enable_thinking": False}})
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"] or ""
        order = _parse_permutation(content, len(docs))
        if order is None:
            progress.log("검색", f"⚠ listwise 순열 파싱 실패 — 폴백 (응답: {content[:80]})")
            return None, None
        return order, int((time.time() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        progress.log("검색", f"⚠ listwise 재랭킹 폴백 — {type(e).__name__}: {e}")
        return None, None


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
