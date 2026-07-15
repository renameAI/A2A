#!/usr/bin/env python3
"""Retrieve τ 실증 캘리브레이션 (FORMALIZATION.md R4/F1 후속).

실 LLM synth를 m회 뽑아 전 후보의 점수 분포를 재고, τ·보너스 임계가
앵커 혼합 이후 스케일에 맞는지 데이터로 판정한다. 원자료(synth 문자열 + 점수
행렬)를 JSON으로 덤프해 QC 서브에이전트가 독립 분석하게 한다.

    LLM_PROVIDER=friendli python scripts/calibrate_retrieve.py --m 5 \
        --input examples/divein_retrieve.json --out /tmp/retrieve_calib.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import retrieve as R                       # noqa: E402
from app.engine.common import overlap                       # noqa: E402
from app.engine.pool import get_pool                        # noqa: E402
from app.schemas import PoolChoice, RetrieveRequest         # noqa: E402


def _score_old(req, synth, rec):
    """R4 이전 점수 재현 — base = ov(synth) 단독."""
    from app.engine.common import industry_adjacent, infer_stage
    target = R._search_text(rec, req.direction)
    base = overlap(synth, target)
    score = 0.7 * base
    if base >= 0.10:
        if req.intent.target_region and req.intent.target_region in rec.profile.basic.country:
            score += 0.15
        if industry_adjacent(req.requester_profile.basic.industry, rec.profile.basic.industry):
            score += 0.10
    stages = {infer_stage(req.requester_profile), infer_stage(rec.profile)}
    if "enterprise" in stages and ({"seed", "startup"} & stages):
        score -= 0.4
    if req.direction.value == "sell_outreach":
        if (req.requester_profile.basic.industry == rec.profile.basic.industry
                or overlap(req.requester_profile.solution.value,
                           rec.profile.solution.value) > 0.35):
            score *= 0.2
    return round(max(score, 0.0), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--m", type=int, default=5)
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    req = RetrieveRequest(**json.loads(Path(args.input).read_text()))
    anchor = R.template_counterpart(req)
    pool = [r for r in get_pool()
            if req.pool == PoolChoice.both or r.pool.value == req.pool.value]
    pool = [r for r in pool
            if r.profile.basic.name != req.requester_profile.basic.name]

    runs = []
    for i in range(args.m):
        t0 = time.time()
        synth = R.synthesize_counterpart(req)          # 실 LLM 호출
        dt = time.time() - t0
        print(f"  run {i + 1}/{args.m} · synth {dt:.1f}s · "
              f"\"{synth[:70]}…\"", file=sys.stderr)
        rows = []
        for rec in pool:
            target = R._search_text(rec, req.direction)
            rows.append({
                "company_id": rec.company_id,
                "name": rec.profile.basic.name,
                "ov_synth": round(overlap(synth, target), 4),
                "ov_anchor": round(overlap(anchor, target), 4),
                "score_new": R._score(req, synth, anchor, rec),   # 혼합
                "score_old": _score_old(req, synth, rec),         # synth 단독
            })
        runs.append({"synth": synth, "synth_secs": round(dt, 1), "scores": rows})

    out = {
        "anchor": anchor,
        "tau": R._STRONG_THRESHOLD,
        "bonus_gate": 0.10,
        "m": args.m,
        "pool_size": len(pool),
        "runs": runs,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n덤프 → {args.out}", file=sys.stderr)

    # 콘솔 요약 — 후보별 new/old 점수의 실행 간 범위와 τ 통과 안정성
    print("\n후보별 점수 (new=혼합 / old=synth단독) · τ={:.2f}".format(out["tau"]),
          file=sys.stderr)
    for rec in pool:
        cid = rec.company_id
        new = [r["scores"][[x["company_id"] for x in r["scores"]].index(cid)]["score_new"]
               for r in runs]
        old = [r["scores"][[x["company_id"] for x in r["scores"]].index(cid)]["score_old"]
               for r in runs]
        pass_new = sum(1 for s in new if s >= out["tau"])
        pass_old = sum(1 for s in old if s >= out["tau"])
        print(f"  {cid:24} new[{min(new):.3f}~{max(new):.3f}] τ통과 {pass_new}/{args.m}"
              f"  |  old[{min(old):.3f}~{max(old):.3f}] τ통과 {pass_old}/{args.m}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
