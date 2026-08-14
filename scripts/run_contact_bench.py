#!/usr/bin/env python3
"""B2 접점·결정구조 벤치마크 — 프로덕션 경로(read_company) 그대로.

    LLM_PROVIDER=openai python scripts/run_contact_bench.py [-v] [--k N]
"""
import argparse, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings                       # noqa: E402
from app.engine.company_ontology import read_company      # noqa: E402
from app.engine.llm import get_extractor                  # noqa: E402
from app.eval.contact_bench import aggregate, load_cases, score_case  # noqa: E402


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
            contacts = [{"channel": c.channel, "value": c.value,
                         "role_hint": c.role_hint} for c in ont.contacts]
            ds_ax = ont.axes.get("decision_structure")
            decision = {"value": ds_ax.value, "status": ds_ax.status.value} \
                if ds_ax else {"value": "", "status": "unknown"}
        except Exception as e:
            print(f"  ⚠ {case['id']} 판독 실패({type(e).__name__})")
            contacts, decision = [], {"value": "", "status": "unknown"}
        row = score_case(case, contacts, decision)
        rows.append(row)
        if verbose:
            bad = []
            if row["hallucinated"]:
                bad.append(f"환각 {row['hallucinated']}")
            if row["over_claim"]:
                bad.append("결정구조 과잉단정")
            if row["over_abstain"]:
                bad.append("결정구조 과잉회피")
            if row["is_negative"] and row["pred_n"]:
                bad.append(f"negative에 접점 {row['pred_n']}")
            mark = " ← " + "·".join(bad) if bad else ""
            print(f"  {case['id']}: 접점 {row['matched']}/{row['gold_n']} "
                  f"(pred {row['pred_n']}) ds={'O' if row['ds_correct'] else 'X'}{mark}")
            if bad:
                for c in contacts:
                    print(f"      접점: {c['channel']} → {c['value'][:50]}")
                print(f"      ds: [{decision['status']}] {decision['value'][:60]}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    cases = load_cases()
    extractor = get_extractor(get_settings())
    provider = os.environ.get("LLM_PROVIDER", "?")
    for i in range(args.k):
        t0 = time.time()
        rows = run_once(cases, extractor, verbose=args.verbose and i == 0)
        rep = aggregate(rows)
        print(f"\n=== run {i+1}/{args.k} · provider={provider} · "
              f"{rep['cases']}케이스 · {round(time.time()-t0, 1)}s ===")
        print(f"  접점 재현/정밀  {rep['contact_recall']:.3f} / "
              f"{rep['contact_precision']:.3f}")
        print(f"  접점 환각 {rep['hallucinated_contacts']}건 · "
              f"negative 접점 {rep['spurious_on_negatives']}건")
        print(f"  role_hint 정확도 {rep['role_hint_accuracy']}")
        print(f"  결정구조 정확 {rep['ds_accuracy']:.3f} · "
              f"과잉단정 {rep['ds_over_claim']} · 과잉회피 {rep['ds_over_abstain']}")


if __name__ == "__main__":
    main()
