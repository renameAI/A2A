"""학습된 스코어러 평가 — held-out(처음 보는 회사쌍)에서 기댓값 readout.

'배웠는가'의 판정: 예측 점수와 실제 점수의 상관(스피어만 근사) + 고관련(≥8)과
저관련(≤2) 쌍의 예측 평균 분리. 분리가 크면 보완 구조를 배운 것이다.
"""
import argparse
import json


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
    from .infer import RelatednessScorer   # 지연 — torch 없는 환경에서 _spearman 재사용 가능
    scorer = RelatednessScorer(a.base_model, a.run_dir)

    true_s, pred_s = [], []
    for p in held:
        r = scorer.score(p["a_text"], p["b_text"])
        true_s.append(p["score"])
        pred_s.append(r["score"])
    rho = _spearman(true_s, pred_s)
    hi = [pred_s[i] for i in range(len(held)) if true_s[i] >= 8]
    lo = [pred_s[i] for i in range(len(held)) if true_s[i] <= 2]
    hi_avg = sum(hi) / len(hi) if hi else None
    lo_avg = sum(lo) / len(lo) if lo else None

    print(f"[평가] held-out {len(held)}쌍")
    print(f"  실제 점수 분포: {sorted(set(true_s))}")
    print(f"  스피어만 상관(true vs pred): {rho:.3f}")
    print(f"  고관련(true≥8) 예측 평균: "
          f"{f'{hi_avg:.2f}' if hi_avg is not None else 'n/a'}  (n={len(hi)})")
    print(f"  저관련(true≤2) 예측 평균: "
          f"{f'{lo_avg:.2f}' if lo_avg is not None else 'n/a'}  (n={len(lo)})")
    sep = (hi_avg - lo_avg) if (hi_avg is not None and lo_avg is not None) else None
    print(f"  분리(고−저): {f'{sep:.2f}' if sep is not None else 'n/a'}")
    print("  샘플 (true → pred):")
    for i in range(min(10, len(held))):
        print(f"    {true_s[i]:>2} → {pred_s[i]:.2f}")
    # 고/저관련 표본이 부족하면(좁은 라벨 분포 — 예: facts-only 데이터) 분리 판정이
    # 애초에 정의되지 않는다. 이를 '미학습'으로 오판하지 않고 정직하게 구분한다.
    if sep is None:
        verdict = f"판정 불가 — 고관련(n={len(hi)})·저관련(n={len(lo)}) 표본 부족 " \
                  f"(라벨 분포가 좁음). 스피어만 {rho:.3f}로만 참고."
    else:
        verdict = "배웠음" if (rho > 0.5 and sep > 2) else "약함/미학습"
    print(f"  판정: {verdict}")


if __name__ == "__main__":
    main()
