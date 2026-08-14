"""B2 — 접점·결정구조 벤치마크 채점기.

측정 축 (근거는 contact_golden.json _doc + docs/BENCH_CONTACTS.md):
- 접점 재현율: 자료에 있는 접촉 경로를 뽑았는가 (value_anchor ⊆ 예측 value)
- 접점 환각률: 예측 value가 원문에 실재하지 않는 비율 — **이 벤치의 요점.**
  존재하지 않는 문의 폼을 만들면 사용자가 없는 문을 두드린다.
- role_hint 정확도: 자료에 부서·직함이 있을 때 그것을 옮겼는가
- decision_structure 3태 보정 (2507.16199의 양방향 오용 측정):
  자료에 결정 구조가 있는데 unknown → 과잉회피(over-abstain)
  자료에 없는데 값을 채움 → 과잉단정(over-claim)
"""
import json
import re
import unicodedata
from pathlib import Path

GOLDEN = Path(__file__).parent / "contact_golden.json"


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()


def load_cases() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def score_case(case: dict, contacts: list[dict], decision: dict) -> dict:
    """contacts: [{channel, value, role_hint}], decision: {value, status}."""
    m = case["material"]
    src = _norm(m.get("what", "") + m.get("signal", "") + m.get("name", ""))
    gold_contacts = case["gold"]["contacts"]

    matched_gold, matched_pred = set(), set()
    role_hits, role_total = 0, 0
    for gi, g in enumerate(gold_contacts):
        for pi, p in enumerate(contacts):
            if pi in matched_pred:
                continue
            if _norm(g["value_anchor"]) in _norm(p.get("value", "")):
                matched_gold.add(gi)
                matched_pred.add(pi)
                if g.get("role_hint_anchor"):
                    role_total += 1
                    if _norm(g["role_hint_anchor"]) in _norm(p.get("role_hint", "")):
                        role_hits += 1
                break

    # 환각 = 예측 value의 핵심(정규화)이 원문 어디에도 없다.
    hallucinated = sum(1 for p in contacts if _norm(p.get("value", "")) not in src)

    # decision_structure 3태 보정
    ds_gold = case["gold"]["decision_structure"]          # stated | absent
    ds_status = (decision or {}).get("status", "unknown")
    ds_value = _norm((decision or {}).get("value", ""))
    over_claim = over_abstain = ds_correct = 0
    if ds_gold == "absent":
        if ds_status == "unknown":
            ds_correct = 1
        else:
            over_claim = 1
    else:  # stated
        # 앵커 any-of — 축 값은 한국어 강제(HARD_RULES)라 원어 앵커만으론
        # 번역·독음 표기와 매치가 안 된다(실측: category review board→심의).
        a = case["gold"]["decision_anchor"]
        anchors = [a] if isinstance(a, str) else a
        if ds_status == "unknown":
            over_abstain = 1
        elif any(_norm(x) in ds_value for x in anchors):
            ds_correct = 1
        # status는 채웠는데 값이 앵커와 다르면 correct도 abstain도 아닌 오답 —
        # 세 카운터 모두 0으로 남는다 (합산에서 오답률로 드러남)

    return {
        "id": case["id"],
        "gold_n": len(gold_contacts), "pred_n": len(contacts),
        "matched": len(matched_gold), "hallucinated": hallucinated,
        "role_hits": role_hits, "role_total": role_total,
        "ds_correct": ds_correct, "over_claim": over_claim,
        "over_abstain": over_abstain,
        "is_negative": len(gold_contacts) == 0,
    }


def aggregate(rows: list[dict]) -> dict:
    tp = sum(r["matched"] for r in rows)
    pred = sum(r["pred_n"] for r in rows)
    gold = sum(r["gold_n"] for r in rows)
    rt = sum(r["role_total"] for r in rows)
    n = len(rows)
    return {
        "contact_recall": round(tp / gold, 3) if gold else 1.0,
        "contact_precision": round(tp / pred, 3) if pred else 1.0,
        "hallucinated_contacts": sum(r["hallucinated"] for r in rows),
        "spurious_on_negatives": sum(
            r["pred_n"] for r in rows if r["is_negative"]),
        "role_hint_accuracy": round(
            sum(r["role_hits"] for r in rows) / rt, 3) if rt else None,
        "ds_accuracy": round(sum(r["ds_correct"] for r in rows) / n, 3),
        "ds_over_claim": sum(r["over_claim"] for r in rows),
        "ds_over_abstain": sum(r["over_abstain"] for r in rows),
        "cases": n,
    }
