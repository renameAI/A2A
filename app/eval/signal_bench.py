"""B1 — 타이밍 신호 추출 벤치마크 채점기.

평가 프로토콜 (근거 논문은 signal_golden.json 상단 _doc):
- 매칭: 예측 신호가 골드 신호와 매치 = category 일치(복수 허용 alt 포함)
  AND 예측 evidence가 골드 anchor를 포함(공백 제거 후 부분문자열).
  Frontiers 2026의 soft-matching(â ⊆ a)을 우리 방향으로 뒤집었다 —
  우리 evidence는 문장 전사(轉寫)이므로 anchor ⊆ â 가 맞는 방향이다.
- 충실도: 예측 evidence가 원문(what+signal)에 실재하는가. FaithJudge의
  환각 정의를 문자열 수준으로 낮춘 것 — 스니펫 전사라서 이게 성립한다.
  공백 제거 후 부분문자열이면 faithful. LLM이 요약·의역하면 위반으로 센다
  (프롬프트가 "문장을 그대로 옮긴다"를 요구하므로 의역은 계약 위반이 맞다).
- 정밀도/재현율은 케이스 합산 micro. hard negative 5건의 spurious율을
  별도 보고한다 — "빈 배열이 정직한 상태"가 지켜지는지가 이 벤치의 요점이다.

읽는 법: F1이 높아도 spurious율이 높으면 실패다. 상태·연혁·의지를 사건으로
승격시키는 순간 신호 축 전체가 소음이 된다.
"""
import json
import re
import unicodedata
from pathlib import Path

GOLDEN = Path(__file__).parent / "signal_golden.json"


def _norm(s: str) -> str:
    """비교용 정규화 — NFKC(전각/반각 통일) 후 공백 제거."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def load_cases() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def score_case(case: dict, predicted: list[dict]) -> dict:
    """한 케이스 채점. predicted: [{category, evidence, observed_at}]."""
    src = _norm(case["material"].get("what", "") + case["material"].get("signal", ""))
    gold = case["gold"]["signals"]
    matched_gold: set[int] = set()
    matched_pred: set[int] = set()
    date_hits, date_total = 0, 0

    for gi, g in enumerate(gold):
        ok_cats = {g["category"], *g.get("alt", [])}
        for pi, p in enumerate(predicted):
            if pi in matched_pred:
                continue
            if p["category"] in ok_cats and _norm(g["anchor"]) in _norm(p["evidence"]):
                matched_gold.add(gi)
                matched_pred.add(pi)
                if g.get("has_date"):
                    date_total += 1
                    if (p.get("observed_at") or "").strip():
                        date_hits += 1
                break

    unfaithful = sum(1 for p in predicted if _norm(p["evidence"]) not in src)
    return {
        "id": case["id"],
        "gold_n": len(gold),
        "pred_n": len(predicted),
        "matched": len(matched_gold),
        "spurious": len(predicted) - len(matched_pred),
        "unfaithful": unfaithful,
        "date_hits": date_hits,
        "date_total": date_total,
        "is_negative": len(gold) == 0,
    }


def aggregate(rows: list[dict]) -> dict:
    tp = sum(r["matched"] for r in rows)
    pred = sum(r["pred_n"] for r in rows)
    gold = sum(r["gold_n"] for r in rows)
    prec = tp / pred if pred else 1.0
    rec = tp / gold if gold else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    negs = [r for r in rows if r["is_negative"]]
    dt = sum(r["date_total"] for r in rows)
    return {
        "micro_precision": round(prec, 3),
        "micro_recall": round(rec, 3),
        "micro_f1": round(f1, 3),
        # 케이스 단위가 아니라 신호 단위 — negative에서 1건이라도 만들면 그 수만큼
        "spurious_on_negatives": sum(r["pred_n"] for r in negs),
        "negative_cases": len(negs),
        "unfaithful_evidence": sum(r["unfaithful"] for r in rows),
        "predicted_total": pred,
        "gold_total": gold,
        "date_recall": round(sum(r["date_hits"] for r in rows) / dt, 3) if dt else None,
    }
