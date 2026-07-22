"""공개 CC0 상장사 데이터셋 → 리서치할 회사 N개 목록 (research.py 입력 형식).

research.py가 소비하는 JSONL {name, hints}로 저장. hints엔 섹터·시장을 담아
Gemini 검색 리서치의 앵커로 쓴다(동명이인 방지·검색 정확도↑).
결정적: seed 기반 셔플 후 N개.
"""
import argparse
import json
import random
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="dataset/companies_200.jsonl")
    a = ap.parse_args()

    from datasets import load_dataset
    ds = load_dataset("ThunderDrag/South-Korea-Stock-Symbols-and-Metadata",
                      split="train")
    rows = [{"name": r["name"],
             "hints": f"{r.get('market') or 'KRX'} 상장, 섹터 {r.get('sector') or 'unknown'}"}
            for r in ds if r.get("name")]
    random.Random(a.seed).shuffle(rows)
    rows = rows[:a.n]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[완료] {len(rows)}개 회사 → {a.out}")
    for r in rows[:5]:
        print(f"  · {r['name']} ({r['hints']})")


if __name__ == "__main__":
    main()
