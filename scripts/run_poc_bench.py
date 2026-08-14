#!/usr/bin/env python3
"""B3 — PoC readiness 벤치마크.

① innovation_receptivity 축 3태 보정 (B2 ds와 같은 프로토콜, purpose=poc로 판독)
② 업종 제안 모드 발산: 같은 요청자로 revenue/poc 두 번 제안을 받아
   라벨 토큰 Jaccard를 잰다 — 목적이 달라졌는데 같은 업종이 나오면
   purpose 배선은 장식이다. 임계 0.3 미만이어야 통과.

    LLM_PROVIDER=openai python scripts/run_poc_bench.py [-v] [--k N] [--no-seg]
"""
import argparse, json, os, re, sys, time, unicodedata
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings                       # noqa: E402
from app.engine.company_ontology import read_company      # noqa: E402
from app.engine.llm import get_extractor                  # noqa: E402
from app.engine.keywords import tokenize                  # noqa: E402


def _norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()


def run_axis(cases, extractor, verbose=False):
    correct = over_claim = over_abstain = wrong = 0
    for case in cases:
        m = case["material"]
        try:
            ont = read_company(extractor,
                               {"name": m["name"], "name_ko": "",
                                "what": m.get("what", ""),
                                "signal": m.get("signal", ""), "url": ""},
                               region="", purpose="poc")
            ax = ont.axes.get("innovation_receptivity")
            status, value = (ax.status.value, ax.value) if ax else ("unknown", "")
        except Exception as e:
            print(f"  ⚠ {case['id']} 판독 실패({type(e).__name__})")
            status, value = "unknown", ""
        g = case["gold"]["receptivity"]
        if g == "absent":
            ok = status == "unknown"
            if ok:
                correct += 1
            else:
                over_claim += 1
        else:
            if status == "unknown":
                over_abstain += 1
                ok = False
            else:
                ok = any(_norm(a) in _norm(value) for a in case["gold"]["anchor"])
                if ok:
                    correct += 1
                else:
                    wrong += 1
        if verbose:
            print(f"  {case['id']}: gold={g:6} pred=[{status}] "
                  f"{'O' if ok else 'X'}  {value[:56]}")
    return {"correct": correct, "over_claim": over_claim,
            "over_abstain": over_abstain, "wrong_value": wrong,
            "n": len(cases)}


def run_divergence():
    """같은 요청자, revenue vs poc — 업종 제안이 실제로 갈리는가."""
    from app.engine.retrieve import propose_segments
    from app.schemas import (RetrieveRequest, Profile, BasicInfo, ProvField,
                             Provenance, Intent, ValueProp, RetrieveDirection)
    p = Profile(
        basic=BasicInfo(name="귤메달", country="한국", industry="food_beverage"),
        description="제주 감귤로 만든 무가당 건강음료를 생산한다.",
        problem_solved=ProvField(value="설탕 없는 건강 음료 선택지 부족",
                                 provenance=Provenance.stated),
        solution=ProvField(value="감귤 무가당 건강음료 제조",
                           provenance=Provenance.stated),
        target_customer=ProvField(value="해외 식품 유통사",
                                  provenance=Provenance.stated))
    toks = {}
    for purpose in ("revenue", "poc"):
        req = RetrieveRequest(
            requester_profile=p,
            intent=Intent(value_props=[ValueProp.revenue_growth],
                          target_region="일본", purpose=purpose),
            direction=RetrieveDirection.sell_outreach)
        labels = [s["label"] for s in propose_segments(req)]
        toks[purpose] = set().union(*(tokenize(x) for x in labels)) if labels else set()
        print(f"  {purpose}: {labels}")
    inter = toks["revenue"] & toks["poc"]
    union = toks["revenue"] | toks["poc"]
    j = len(inter) / len(union) if union else 1.0
    print(f"  라벨 토큰 Jaccard = {j:.3f} ({'통과 <0.3' if j < 0.3 else '실패 ≥0.3'})")
    return j


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--no-seg", action="store_true", help="발산 측정 생략")
    args = ap.parse_args()
    cases = json.loads((Path(__file__).resolve().parent.parent /
                        "app/eval/poc_golden.json").read_text())["cases"]
    extractor = get_extractor(get_settings())
    provider = os.environ.get("LLM_PROVIDER", "?")
    for i in range(args.k):
        t0 = time.time()
        r = run_axis(cases, extractor, verbose=args.verbose and i == 0)
        acc = r["correct"] / r["n"]
        print(f"\n=== run {i+1}/{args.k} · provider={provider} · "
              f"{r['n']}케이스 · {round(time.time()-t0, 1)}s ===")
        print(f"  receptivity 정확 {acc:.3f} · 과잉단정 {r['over_claim']} · "
              f"과잉회피 {r['over_abstain']} · 값 오답 {r['wrong_value']}")
    if not args.no_seg:
        print("\n── 업종 제안 발산 (revenue vs poc) ──")
        run_divergence()


if __name__ == "__main__":
    main()
