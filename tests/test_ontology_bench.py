"""B1~B3 채점기 유닛 테스트 — LLM 없이 채점 논리만 검증한다.

벤치마크 실행(LLM_PROVIDER=openai scripts/run_*_bench.py)과 별개로, 채점기
자체가 무너지면 모든 측정이 무의미하므로 채점 규칙을 게이트로 둔다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.eval.signal_bench import (aggregate as sig_agg, load_cases as sig_load,
                                   score_case as sig_score)
from app.eval.contact_bench import (load_cases as ct_load,
                                    score_case as ct_score)


def _sig(id_):
    return {c["id"]: c for c in sig_load()}[id_]


def _ct(id_):
    return {c["id"]: c for c in ct_load()}[id_]


def test_signal_perfect_match_nfkc_and_date():
    r = sig_score(_sig("S01"), [{
        "category": "expansion",
        "evidence": "2026年3月、埼玉県に第3温度帯対応の新物流センターを開設した。",
        "observed_at": "2026年3月"}])
    assert r["matched"] == 1 and r["unfaithful"] == 0 and r["date_hits"] == 1


def test_signal_paraphrase_is_unfaithful():
    r = sig_score(_sig("S01"), [{"category": "expansion",
                                 "evidence": "새 물류센터를 열었다",
                                 "observed_at": ""}])
    assert r["matched"] == 0 and r["unfaithful"] == 1


def test_signal_negative_spurious():
    r = sig_score(_sig("N01"), [{"category": "expansion",
                                 "evidence": "전국 3곳에 물류센터를 보유하고 있으며",
                                 "observed_at": ""}])
    assert r["spurious"] == 1 and r["is_negative"]


def test_signal_alt_category_allowed():
    r = sig_score(_sig("S16"), [{
        "category": "expansion",
        "evidence": "일본 판매 파트너십 체결을 위해 오사카 건강식품 전시회에 참가한다고 공지했다.",
        "observed_at": ""}])
    assert r["matched"] == 1


def test_signal_aggregate_micro():
    rows = [sig_score(_sig("S01"), []),          # miss
            sig_score(_sig("N04"), [])]          # honest empty
    agg = sig_agg(rows)
    assert agg["micro_recall"] == 0.0 and agg["spurious_on_negatives"] == 0


def test_contact_hallucination_and_three_state():
    r = ct_score(_ct("C04"),
                 [{"channel": "문의 폼", "value": "https://x.co/contact",
                   "role_hint": ""}],
                 {"value": "", "status": "unknown"})
    assert r["hallucinated"] == 1 and r["ds_correct"] == 1
    r2 = ct_score(_ct("C01"), [], {"value": "영업부", "status": "assumed"})
    assert r2["over_claim"] == 1
    r3 = ct_score(_ct("C02"), [], {"value": "", "status": "unknown"})
    assert r3["over_abstain"] == 1


def test_contact_anchor_any_of():
    # 한국어 축 값 ↔ 원어 앵커 — any-of가 없으면 전부 오답이 된다(실측 결함)
    r = ct_score(_ct("C05"), [], {"value": "분기별 심사에서 결정한다",
                                  "status": "confirmed"})
    assert r["ds_correct"] == 1
