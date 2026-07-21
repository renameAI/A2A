"""제로샷 베이스라인 — 파인튜닝 없이 라벨 생성과 동일 프로토콜로 held-out 채점.

파인튜닝(E5/E6)의 이득을 정량화하려면 '학습 안 한 같은 모델'의 기준선이 필요하다.
같은 프롬프트(PAIR_SYSTEM)·같은 temperature(0.2)·같은 held 쌍을 쓴다.

정직 주의(라벨 순환): held 라벨 자체가 EXAONE-32B(temp0.2 샘플링)의 산출이다.
  - 32B 제로샷의 ρ = 자기 일치도(샘플링 노이즈 하의 재현성) — 사실상 상한 측정
  - 1.2B·API 제로샷의 ρ = '32B 라벨과의 합치'이지 절대 품질이 아님
백엔드:
  --backend local    로컬 HF 모델 (GPU 서버) — --model 경로 필수
  --backend friendli K-EXAONE(Friendli dedicated) — FRIENDLI_TOKEN/ENDPOINT_ID 환경변수
"""
import argparse
import json
import os
import time

from .evaluate import _spearman
from .pair_protocol import PAIR_SYSTEM, pair_user, parse_score


def _friendli_scorer(timeout: float):
    import httpx
    token = os.environ["FRIENDLI_TOKEN"]
    endpoint = os.environ["FRIENDLI_ENDPOINT_ID"]

    def score(a_name, a_text, b_name, b_text):
        r = httpx.post(
            "https://api.friendli.ai/dedicated/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": endpoint, "temperature": 0.2, "max_tokens": 150,
                  "messages": [
                      {"role": "system", "content": PAIR_SYSTEM},
                      {"role": "user",
                       "content": pair_user(a_name, a_text, b_name, b_text)}],
                  "chat_template_kwargs": {"enable_thinking": False}},
            timeout=timeout)
        r.raise_for_status()
        return parse_score(r.json()["choices"][0]["message"]["content"])
    return score


def main():
    ap = argparse.ArgumentParser(description="제로샷 베이스라인 평가")
    ap.add_argument("--backend", choices=["local", "friendli"], required=True)
    ap.add_argument("--model", help="local 백엔드의 HF 모델 경로")
    ap.add_argument("--held", required=True, help="held_pairs.json")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--out", help="쌍별 결과 JSONL 저장 경로 (표·재현용)")
    ap.add_argument("--timeout", type=float, default=120.0)
    a = ap.parse_args()

    held = json.loads(open(a.held).read())[:a.limit]
    if a.backend == "local":
        from .local_llm import LocalExaone
        exa = LocalExaone(a.model)
        score = exa.score_pair
        tag = a.model
    else:
        score = _friendli_scorer(a.timeout)
        tag = "K-EXAONE(friendli)"

    true_s, pred_s, rows, failed = [], [], [], 0
    t0 = time.time()
    for i, p in enumerate(held):
        r = score(p["a_id"], p["a_text"], p["b_id"], p["b_text"])
        if r is None:                      # 파싱 실패 — 제외하고 정직하게 계수
            failed += 1
            continue
        true_s.append(p["score"])
        pred_s.append(r["score"])
        rows.append({"a_id": p["a_id"], "b_id": p["b_id"],
                     "true": p["score"], "pred": r["score"],
                     "reason": r["reason"]})
        if (i + 1) % 10 == 0:
            print(f"  … {i + 1}/{len(held)} ({time.time() - t0:.0f}s)", flush=True)

    rho = _spearman(true_s, pred_s)
    dist = {}
    for s in pred_s:
        dist[s] = dist.get(s, 0) + 1
    print(f"[제로샷] {tag} · held {len(held)}쌍 (파싱 실패 {failed})")
    print(f"  스피어만 ρ(vs 32B 라벨): {rho:.3f}")
    print(f"  예측 분포: {dict(sorted(dist.items()))}")
    print(f"  실제 분포: {dict(sorted({s: true_s.count(s) for s in set(true_s)}.items()))}")
    print("  샘플 (true → pred):")
    for row in rows[:8]:
        print(f"    {row['true']:>2} → {row['pred']}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"  저장: {a.out}")


if __name__ == "__main__":
    main()
