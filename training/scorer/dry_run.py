"""GPU 없이 로컬에서 데이터·설정을 검증한다 — 서버 붙기 전 정합성·규모 점검.

torch를 import하지 않는다(순수 파이썬). 검증하는 것:
  · 페어 데이터 유효성 + 점수 버킷 히스토그램(불균형 진단)
  · 회사 단위 분할이 누수 없이 되는지
  · 계층 샘플링 후 버킷 균형
  · 스텝 수 / 예상 시간·비용 개략 추정(가정값 기반 — 실제는 서버 토크나이즈 후 확정)
"""
import argparse
import json

from .config import ScorerConfig
from .data import (histogram, load_pairs, split_by_company, stratified_sample,
                   validate)


def estimate(n_examples, cfg, avg_seq_len, tok_per_s):
    """개략 추정 — avg_seq_len은 가정값. 정직하게 '추정'임을 밝힌다."""
    eff_batch = cfg.per_device_batch_size * cfg.grad_accum_steps
    steps = int(n_examples / max(eff_batch, 1) * cfg.num_epochs)
    tokens = n_examples * avg_seq_len * cfg.num_epochs
    hours = tokens / max(tok_per_s, 1) / 3600
    return {"optimizer_steps": steps, "approx_tokens": int(tokens),
            "approx_gpu_hours": round(hours, 2)}


def main():
    ap = argparse.ArgumentParser(description="스코어러 데이터·설정 dry-run (GPU 불필요)")
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--avg-seq-len", type=int, default=1200,
                    help="예제당 평균 토큰 수 가정값(추정용)")
    ap.add_argument("--tok-per-s", type=int, default=2000,
                    help="학습 처리량 가정값 tok/s (8xH100 기준 대략)")
    a = ap.parse_args()
    cfg = ScorerConfig()

    raw = load_pairs(a.pairs)
    rep = validate(raw)
    pairs = rep["valid"]
    print(f"[유효] {len(pairs)}쌍 · [불량] {len(rep['errors'])}건")
    if rep["errors"][:3]:
        print("  예:", rep["errors"][:3])

    print(f"[점수 버킷 — 전체] {histogram(pairs)}")
    train, held, dropped = split_by_company(pairs, cfg.held_frac, cfg.seed)
    # 누수 검사 — held 회사가 train에 등장하면 안 됨 (0이어야 정상)
    held_companies = {p.a_id for p in held} | {p.b_id for p in held}
    leak = [p for p in train
            if p.a_id in held_companies or p.b_id in held_companies]
    print(f"[분할] train {len(train)} · held {len(held)} · "
          f"교차폐기 {len(dropped)} · 누수 {len(leak)}건")

    sampled, samp = stratified_sample(train, cfg.per_bucket_cap, cfg.seed)
    print(f"[계층 샘플링] {samp['before']} → {samp['after']}  (총 {samp['total']})")

    est = estimate(len(sampled), cfg, a.avg_seq_len, a.tok_per_s)
    print(f"[추정] {json.dumps(est, ensure_ascii=False)}  "
          f"(avg_seq_len={a.avg_seq_len}·{a.tok_per_s}tok/s 가정 — 서버서 확정)")

    if len(sampled) < 500:
        print("⚠ 표본이 500쌍 미만 — 풀 파인튜닝엔 얇다. "
              "페어 라벨을 더 모으거나 증강(app/augment.py 계보)을 검토.")


if __name__ == "__main__":
    main()
