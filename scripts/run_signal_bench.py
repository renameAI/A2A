#!/usr/bin/env python3
"""B1 타이밍 신호 벤치마크 실행 — 프로덕션 경로(read_company)를 그대로 잰다.

    LLM_PROVIDER=openai python scripts/run_signal_bench.py           # 1회
    LLM_PROVIDER=openai python scripts/run_signal_bench.py --k 3     # 안정성 포함
    python scripts/run_signal_bench.py --json                        # 원자료

프로덕션 경로를 재는 이유: 신호 추출만 떼어 재면 벤치는 좋아지고 제품은
그대로인 속임수가 된다. read_company는 10축+신호+접점을 한 콜에 뽑으므로
축 판독의 부하가 신호 정확도에 주는 간섭까지 포함해 재는 것이 정직하다.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings                      # noqa: E402
from app.engine.company_ontology import read_company     # noqa: E402
from app.engine.llm import get_extractor                 # noqa: E402
from app.eval.signal_bench import aggregate, load_cases, score_case  # noqa: E402


def run_once(cases, extractor, verbose=False):
    rows = []
    for case in cases:
        m = case["material"]
        try:
            ont = read_company(extractor,
                               {"name": m["name"], "name_ko": "",
                                "what": m.get("what", ""),
                                "signal": m.get("signal", ""), "url": ""},
                               region="")
            preds = [{"category": s.category.value, "evidence": s.evidence,
                      "observed_at": s.observed_at} for s in ont.signals]
        except Exception as e:
            print(f"  ⚠ {case['id']} 판독 실패({type(e).__name__}) — 0건으로 채점")
            preds = []
        row = score_case(case, preds)
        rows.append(row)
        if verbose:
            flag = ""
            if row["spurious"]:
                flag += f" spurious={row['spurious']}"
            if row["unfaithful"]:
                flag += f" unfaithful={row['unfaithful']}"
            print(f"  {case['id']}: gold {row['gold_n']} pred {row['pred_n']} "
                  f"match {row['matched']}{flag}")
            for p in preds:
                if row["is_negative"] or row["spurious"]:
                    print(f"      [{p['category']}] {p['evidence'][:60]}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cases = load_cases()
    extractor = get_extractor(get_settings())
    provider = os.environ.get("LLM_PROVIDER", "?")

    reports = []
    for i in range(args.k):
        t0 = time.time()
        rows = run_once(cases, extractor, verbose=args.verbose and i == 0)
        rep = aggregate(rows)
        rep["elapsed_s"] = round(time.time() - t0, 1)
        reports.append(rep)
        if not args.json:
            print(f"\n=== run {i+1}/{args.k} · provider={provider} · "
                  f"{len(cases)}케이스 · {rep['elapsed_s']}s ===")
            print(f"  micro P/R/F1  {rep['micro_precision']:.3f} / "
                  f"{rep['micro_recall']:.3f} / {rep['micro_f1']:.3f}")
            print(f"  spurious(negative {rep['negative_cases']}건에서) "
                  f"{rep['spurious_on_negatives']}건")
            print(f"  evidence 비충실 {rep['unfaithful_evidence']}건 / "
                  f"예측 {rep['predicted_total']}건")
            print(f"  날짜 재현율 {rep['date_recall']}")
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    elif args.k > 1:
        f1s = [r["micro_f1"] for r in reports]
        print(f"\nk={args.k} F1 범위: {min(f1s):.3f} ~ {max(f1s):.3f}")


if __name__ == "__main__":
    main()
