"""실데이터 파이프라인 (완전 무API) — 공개 상장사 → EXAONE 리서치·채점 → 학습쌍.

디렉터 스펙 1단계를 외부 API 없이:
  1) 공개 CC0 데이터셋(KOSPI/KOSDAQ 상장사 2,618개, name+sector) 로드
  2) 로컬 EXAONE-32B로 기업별 리서치 텍스트 생성 (내부지식 — 대기업 정확, 소형주 추정)
  3) 하드 포지티브(같은 섹터=보완 가능성) + 네거티브 혼합 샘플링으로 페어 구성
  4) 로컬 EXAONE-32B로 페어 0~10 보완 관련도 채점 → RelatednessPair JSONL

산출은 training/scorer/data.py가 그대로 소비 → train.py로 학습.
GPU 필요(EXAONE 로드) — 합성 학습이 GPU를 비운 뒤 실행한다.
결정성: 회사 샘플링·페어 샘플링은 seed 기반. 생성은 temperature>0라 재현 불가(정직).
"""
import argparse
import json
import random
import sqlite3
import time
from pathlib import Path


def load_seed(limit, seed):
    """공개 CC0 상장사 데이터 → [{name, sector}]. 결정적 셔플 후 limit."""
    from datasets import load_dataset
    ds = load_dataset("ThunderDrag/South-Korea-Stock-Symbols-and-Metadata",
                      split="train")
    rows = [{"name": r["name"], "sector": r.get("sector") or "unknown"}
            for r in ds if r.get("name")]
    random.Random(seed).shuffle(rows)
    return rows[:limit]


def gen_research(companies, exa, db_path):
    """기업별 리서치 텍스트 생성 → SQLite (멱등: 이미 생성된 건 skip)."""
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS companies("
                 "name TEXT PRIMARY KEY, sector TEXT, research_text TEXT, ts TEXT)")
    done = {r[0] for r in conn.execute(
        "SELECT name FROM companies WHERE research_text IS NOT NULL")}
    todo = [c for c in companies if c["name"] not in done]
    print(f"[리서치] 생성 예정 {len(todo)} (완료 {len(done)})", flush=True)
    for i, c in enumerate(todo):
        text = exa.research(c["name"], c["sector"])
        conn.execute("INSERT OR REPLACE INTO companies VALUES(?,?,?,?)",
                     (c["name"], c["sector"], text,
                      time.strftime("%Y-%m-%dT%H:%M:%S")))
        conn.commit()
        if (i + 1) % 25 == 0:
            print(f"  … {i + 1}/{len(todo)}", flush=True)
    return conn


def mine_pairs(rows, n_pairs, seed):
    """하드 포지티브(같은 섹터) 55% + 무작위 네거티브 — 불균형 방지."""
    rng = random.Random(seed)
    by_sec = {}
    for i, r in enumerate(rows):
        by_sec.setdefault(r["sector"], []).append(i)
    pos, neg, seen = [], [], set()
    n_pos_target = int(n_pairs * 0.55)
    idx = list(range(len(rows)))
    tries = 0
    while (len(pos) + len(neg)) < n_pairs and tries < n_pairs * 30:
        tries += 1
        if len(pos) < n_pos_target:               # 같은 섹터 쌍
            sec = rng.choice([s for s, v in by_sec.items() if len(v) >= 2])
            i, j = rng.sample(by_sec[sec], 2)
        else:                                     # 무작위 쌍
            i, j = rng.sample(idx, 2)
        key = tuple(sorted([i, j]))
        if key in seen:
            continue
        seen.add(key)
        (pos if rows[i]["sector"] == rows[j]["sector"] else neg).append(key)
    return pos + neg


def score_pairs(conn, exa, pairs, rows, out_path, mode):
    """페어 채점 → RelatednessPair JSONL."""
    tmap = {r[0]: r for r in conn.execute(
        "SELECT name, sector, research_text FROM companies "
        "WHERE research_text IS NOT NULL")}
    names = [r["name"] for r in rows]
    written = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for i, j in pairs:
            na, nb = names[i], names[j]
            if na not in tmap or nb not in tmap:
                continue
            ta, tb = tmap[na][2], tmap[nb][2]
            r = exa.score_pair(na, ta, nb, tb)
            if not r:
                continue
            f.write(json.dumps({
                "a_id": na, "a_text": ta, "b_id": nb, "b_text": tb,
                "score": r["score"], "mode": mode,
                "source": "exaone-32b-selfdistill", "reason": r["reason"],
            }, ensure_ascii=False) + "\n")
            written += 1
            if written % 50 == 0:
                print(f"  … 채점 {written}/{len(pairs)}", flush=True)
    return written


def main():
    ap = argparse.ArgumentParser(
        description="실데이터 파이프라인 (무API — 공개데이터 + 로컬 EXAONE)")
    ap.add_argument("--model-id", required=True, help="EXAONE 로컬 경로")
    ap.add_argument("--companies", type=int, default=600,
                    help="리서치할 기업 수 (2618 상장사 중)")
    ap.add_argument("--pairs", type=int, default=4000)
    ap.add_argument("--db", default="dataset/real_research.db")
    ap.add_argument("--out", default="dataset/scorer_pairs_real.jsonl")
    ap.add_argument("--mode", default="research", choices=["research", "ontology"])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true",
                    help="EXAONE 로드 없이 계획만 (회사·페어 수)")
    a = ap.parse_args()
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)

    rows = load_seed(a.companies, a.seed)
    pairs = mine_pairs(rows, a.pairs, a.seed)
    same = sum(1 for i, j in pairs if rows[i]["sector"] == rows[j]["sector"])
    print(f"[계획] 기업 {len(rows)} · 페어 {len(pairs)} "
          f"(같은섹터 {same} · 다른섹터 {len(pairs) - same})", flush=True)
    if a.dry_run:
        print("[dry-run] EXAONE 미로드 — 계획만 검증", flush=True)
        return

    print(f"[EXAONE 로딩] {a.model_id} ...", flush=True)
    from .local_llm import LocalExaone
    exa = LocalExaone(a.model_id)
    conn = gen_research(rows, exa, a.db)
    n = score_pairs(conn, exa, pairs, rows, a.out, a.mode)
    print(f"[완료] 실데이터 {n} 페어 → {a.out}", flush=True)


if __name__ == "__main__":
    main()
