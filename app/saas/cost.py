"""비용 미터 (이슈 #6-E) — Luna 단가 기반, 하드캡은 store가 원자로 집행.

스펙 확정값: usage 토큰 × 단가($0.10/$0.60 per 1M, env로 조정)를 합산.
검사 시점은 각 과금 호출 '직전' — 예상 비용(estimate)을 선예약하고, 실제 usage가
오면 차액을 정산한다. 예약이 캡을 넘으면 EngineError(402, cost_cap)로 job 중단.
Tavily는 무료 티어 전제로 0원 처리하되 호출 수를 기록한다(유료 전환 시 단가만 추가).
"""
import os


def _price_in() -> float:
    return float(os.environ.get("LLM_PRICE_IN_PER_M", "0.10"))


def _price_out() -> float:
    return float(os.environ.get("LLM_PRICE_OUT_PER_M", "0.60"))


def req_cap() -> float:
    return float(os.environ.get("COST_CAP_REQUEST_USD", "5"))


def month_cap() -> float:
    return float(os.environ.get("COST_CAP_MONTH_USD", "100"))


def usd(tokens_in: int, tokens_out: int) -> float:
    return tokens_in / 1e6 * _price_in() + tokens_out / 1e6 * _price_out()


# 스테이지별 선예약 추정치 (보수적 상한 — 실측 전 기본값).
# represent: 입력 ~20K + 출력 ~8K ≈ $0.007 → 여유 있게 $0.05로 예약.
ESTIMATE_USD = {
    "represent": 0.05,
    "synth": 0.01,
    "prospect": 0.02,        # 후보 1곳 부분 프로필
    "insight": 0.02,
    "compose": 0.03,
    "tavily": 0.0,           # 무료 티어 — 호출 수만 기록
}


def reserve(store, ws: str, request_id: str, stage: str, count: int = 1) -> None:
    """과금 호출 직전 선예약 — 캡 초과 시 EngineError(402) 전파 (job 중단)."""
    est = ESTIMATE_USD.get(stage, 0.02) * count
    store.reserve_cost(ws, request_id, est, req_cap(), month_cap())
