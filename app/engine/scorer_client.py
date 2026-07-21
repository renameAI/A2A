"""학습 스코어러 HTTP 클라이언트 — retrieve 랭킹 백엔드 (training/scorer/serve.py 대응).

GPU 서버의 FastAPI 서빙(/score-batch)을 SSH 터널로 호출한다. 역할 분리:
  - τ 게이트(강한 후보 판정)는 기존 휴리스틱이 그대로 담당 (캘리브레이션 보존, RET-06)
  - 학습 스코어러는 게이트 통과 후보의 '순서'만 다시 매긴다

정직 폴백: 서버 부재·타임아웃·비정상 응답이면 None을 반환하고 로그를 남긴다 —
호출측(retrieve)은 휴리스틱 순서를 유지한다. 조용한 대체 없음.
"""
from typing import Optional

import httpx

from .. import progress
from ..config import get_settings


def profile_facts(name: str, industry: str, country: str, description: str) -> str:
    """엔진 프로필 → 학습 분포와 같은 facts 형식 (build_real_data.facts_text 대응).

    스코어러는 "이름 — 섹터/시장 실사실" 형식으로 학습됐다. 서빙 입력도 같은
    형식으로 맞춰야 분포 이탈(OOD)로 인한 점수 왜곡을 줄인다."""
    desc = f" {description}" if description else ""
    return f"{name} — 산업 섹터: {industry}, 국가: {country}.{desc}"


def score_batch(pairs: list[tuple[str, str]]) -> Optional[list[float]]:
    """(a_text, b_text) 쌍들을 일괄 채점 → 기댓값 점수 리스트. 실패 시 None.

    순서는 입력 순서 그대로. 부분 실패는 없다 — 전부 오거나 None."""
    s = get_settings()
    if not s.scorer_url:
        return None
    try:
        r = httpx.post(f"{s.scorer_url.rstrip('/')}/score-batch",
                       json={"pairs": [{"a_text": a, "b_text": b}
                                       for a, b in pairs]},
                       timeout=s.scorer_timeout)
        r.raise_for_status()
        scores = [item["score"] for item in r.json()["scores"]]
        if len(scores) != len(pairs):
            raise ValueError(f"응답 수 불일치 {len(scores)} != {len(pairs)}")
        return scores
    except Exception as e:  # 연결거부·타임아웃·형식오류 — 전부 정직 폴백
        progress.log("검색", f"⚠ 학습 스코어러 폴백(휴리스틱 순서 유지) — "
                             f"{type(e).__name__}: {e}")
        return None
