"""학습된 스코어러 평가 — held-out(처음 보는 회사쌍)에서 기댓값 readout.

'배웠는가'의 판정: 예측 점수와 실제 점수의 상관(스피어만 근사) + 고관련(≥8)과
저관련(≤2) 쌍의 예측 평균 분리. 분리가 크면 보완 구조를 배운 것이다.
"""
import argparse
import json

from .infer import RelatednessScorer


def _spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1 - 6 * d2 / (n * (n * n - 1)) if n > 1 else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-model", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--held", required=True, help="held_pairs.json")
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()

    held = json.loads(open(a.held).read())[:a.limit]
    scorer = RelatednessScorer(a.base_model, a.run_dir)

    true_s, pred_s = [], []
    for p in held:
        r = scorer.score(p["a_text"], p["b_text"])
        true_s.append(p["score"])
        pred_s.append(r["score"])
    rho = _spearman(true_s, pred_s)
    hi = [pred_s[i] for i in range(len(held)) if true_s[i] >= 8]
    lo = [pred_s[i] for i in range(len(held)) if true_s[i] <= 2]
    hi_avg = sum(hi) / len(hi) if hi else float("nan")
    lo_avg = sum(lo) / len(lo) if lo else float("nan")

    print(f"[평가] held-out {len(held)}쌍")
    print(f"  스피어만 상관(true vs pred): {rho:.3f}")
    print(f"  고관련(true≥8) 예측 평균: {hi_avg:.2f}  (n={len(hi)})")
    print(f"  저관련(true≤2) 예측 평균: {lo_avg:.2f}  (n={len(lo)})")
    print(f"  분리(고−저): {hi_avg - lo_avg:.2f}")
    print("  샘플 (true → pred):")
    for i in range(min(10, len(held))):
        print(f"    {true_s[i]:>2} → {pred_s[i]:.2f}")
    verdict = "배웠음" if (rho > 0.5 and hi_avg - lo_avg > 2) else "약함/미학습"
    print(f"  판정: {verdict}")


if __name__ == "__main__":
    main()
