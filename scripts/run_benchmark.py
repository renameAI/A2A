#!/usr/bin/env python3
"""평가 벤치마크 실행 (Phase 5 EVL) — 골든셋으로 엔진 정확도·안정성 채점.

    LLM_PROVIDER=mock python scripts/run_benchmark.py            # 규칙 베이스라인
    LLM_PROVIDER=friendli python scripts/run_benchmark.py --k 3  # 실추론 + 안정성

Mock은 결정적이라 k=1로 충분. LLM은 k>1로 정확도와 재현성을 함께 본다.
무료 API 주의: 실 LLM에서 케이스 수 × k만큼 호출된다. 작은 k부터.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.eval.benchmark import run_benchmark   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=1, help="케이스당 반복(안정성 계측)")
    ap.add_argument("--only", choices=["judge", "retrieve"],
                    help="한쪽만 실행 — 실 LLM에서 비싼 judge 통제용")
    ap.add_argument("--json", action="store_true", help="원 리포트 JSON 출력")
    args = ap.parse_args()

    provider = os.environ.get("LLM_PROVIDER", "mock")
    rep = run_benchmark(k=args.k, only=args.only)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return

    s = rep["summary"]
    print(f"\n=== 벤치마크 · provider={provider} · k={args.k} ===")
    print(f"judge 정확도 {s['judge_accuracy']:.2f} ({s['judge_cases']}건) · "
          f"retrieve top-1 {s['retrieve_top1_accuracy']:.2f} ({s['retrieve_cases']}건) · "
          f"전체 안정성 {s['overall_stability']:.2f}")

    print("\n[judge]")
    for r in rep["judge"]:
        exp = (r["expected"].get("decision") or r["expected"].get("polarity")
               or f"not:{r['expected'].get('polarity_not')}")
        print(f"  {r['case_id']:32} acc {r['accuracy']:.2f} stab {r['stability']:.2f}"
              f"  기대={exp} 실제극성={r['majority_polarity']} {r['decisions']}")
    print("\n[retrieve]")
    for r in rep["retrieve"]:
        print(f"  {r['case_id']:32} top1 {r['top1_accuracy']:.2f} "
              f"제외 {r['distractor_exclusion']:.2f} stab {r['stability']:.2f}  {r['top1_dist']}")


if __name__ == "__main__":
    main()
