"""Buyer 페르소나 생성 — 리서치된 판매기업 + 고관련 실기업(근거) → 이상적 구매자 상.

설계(사용자 확정): 대칭 관련도 점수로 '누구와 관련되나'(이웃)를 찾고, 리서치
텍스트로 '방향'(무엇을 팔고 누가 사나)을 유도한다 — 재채점 없이 방향성 페르소나.
evidence_matches로 실제 점수쌍에 앵커해 환각을 막는다. 목적은 엔진
retrieve.synthesize_counterpart('이상적 상대의 상')의 실측 근거·평가셋.

⚠️ K-EXAONE(Friendli) API 호출 — 기본 --dry-run, 실행은 --run. 키는 환경변수로만
   (FRIENDLI_TOKEN / FRIENDLI_ENDPOINT_ID). 코드에 넣지 않는다.
"""
import argparse
import collections
import json
import os
import sqlite3
from pathlib import Path

PERSONA_SYS = """\
너는 B2B 매칭 전략가다. 어떤 '판매 기업'의 리서치와, 그 기업과 보완 관련도가 높은
실제 기업들(근거)을 보고, 이 판매 기업의 '이상적 구매자(buyer) 페르소나'를 도출한다.
- 유사도가 아니라 보완성 기준: 판매 기업이 제공하는 것을 필요로 하는(결핍이 있는)
  쪽이 buyer다. 동종 경쟁사는 buyer가 아니다.
- 리서치와 근거 기업에서 도출하고, 지어내지 마라. 불확실하면 비워라.
반드시 JSON 하나로만 답하라:
{"industries":[산업 2~4개], "pain_points":[buyer의 결핍 2~4개],
 "buying_triggers":[구매를 촉발하는 신호 1~3개],
 "solution_needed":"판매기업이 메우는 것 한 문장", "rationale":"한 문장 근거"}"""

_FIELDS = ("industries", "pain_points", "buying_triggers",
           "solution_needed", "rationale")


def load_research(db_path) -> dict:
    conn = sqlite3.connect(db_path)
    return {r[0]: r[1] for r in conn.execute(
        "SELECT name, research_text FROM companies WHERE research_text IS NOT NULL")}


def load_neighbors(pairs_path) -> dict:
    """페어 JSONL → {회사: [(상대, 점수), …]} (대칭)."""
    nb = collections.defaultdict(list)
    for line in Path(pairs_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        nb[r["a_id"]].append((r["b_id"], r["score"]))
        nb[r["b_id"]].append((r["a_id"], r["score"]))
    return nb


def _friendli_chat(system, user, max_tokens=700, timeout=120.0) -> str:
    import httpx
    token = os.environ.get("FRIENDLI_TOKEN")
    endpoint = os.environ.get("FRIENDLI_ENDPOINT_ID")
    if not (token and endpoint):
        raise SystemExit("FRIENDLI_TOKEN/FRIENDLI_ENDPOINT_ID 환경변수가 없습니다.")
    r = httpx.post(
        "https://api.friendli.ai/dedicated/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": endpoint, "temperature": 0.3, "max_tokens": max_tokens,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}],
              "chat_template_kwargs": {"enable_thinking": False}},
        timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _parse_json(text) -> "dict | None":
    try:
        s = text.find("{"); e = text.rfind("}")
        return json.loads(text[s:e + 1])
    except Exception:                              # noqa: BLE001
        return None


def build_one(name, research, neighbors, research_map, hi_thresh=6, top_k=5) -> "dict | None":
    """판매기업 1곳 → buyer 페르소나. 근거는 고관련(≥hi_thresh) 이웃 상위 top_k."""
    hi = [(o, s) for o, s in sorted(neighbors, key=lambda x: -x[1]) if s >= hi_thresh][:top_k]
    if hi:
        ev = "\n".join(f"- {o} ({s}점): {research_map.get(o, '')[:300]}" for o, s in hi)
    else:
        ev = "(고관련 이웃 없음 — 판매기업 리서치만으로 추정)"
    user = (f"[판매 기업: {name}]\n{research[:1500]}\n\n"
            f"[관련도 높은 실제 기업 (근거)]\n{ev}\n\n"
            "이 기업의 이상적 buyer 페르소나를 JSON으로 작성하라.")
    out = _parse_json(_friendli_chat(PERSONA_SYS, user))
    if not out:
        return None
    return {
        "for_company": name,
        "ideal_buyer": {k: out.get(k) for k in _FIELDS},
        "evidence_matches": [{"company": o, "score": s} for o, s in hi],
        "evidence_strength": ("strong" if len(hi) >= 2 else
                              "weak" if hi else "none"),
    }


def run(db_path, pairs_path, out_path, limit, dry_run) -> dict:
    research_map = load_research(db_path)
    nb = load_neighbors(pairs_path)
    # 이웃 많은 회사 우선 (근거 풍부) — 리서치에 있는 회사만
    companies = [c for c in research_map if c in nb]
    companies.sort(key=lambda c: -len(nb[c]))
    if limit:
        companies = companies[:limit]
    tally = {"companies": len(companies), "dry_run": dry_run}
    if dry_run:
        strong = sum(1 for c in companies
                     if any(s >= 6 for _, s in nb[c]))
        print(f"[dry-run] 페르소나 생성 예정 {len(companies)}곳 "
              f"(고관련 근거 보유 {strong}곳) — 실행하려면 --run")
        return tally

    written = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for i, name in enumerate(companies):
            try:
                p = build_one(name, research_map[name], nb[name], research_map)
            except Exception as e:                 # noqa: BLE001
                print(f"  ✗ {name}: {type(e).__name__}: {e}")
                continue
            if not p:
                continue
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
            written += 1
            if (i + 1) % 25 == 0:
                print(f"  … {i + 1}/{len(companies)} 생성")
    tally["written"] = written
    print(f"[완료] 페르소나 {written}곳 → {out_path}")
    return tally


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Buyer 페르소나 생성 (K-EXAONE) — 기본 dry-run")
    ap.add_argument("--db", default="dataset/research_e8.db")
    ap.add_argument("--pairs", default="dataset/scorer_pairs_e8.jsonl")
    ap.add_argument("--out", default="dataset/buyer_personas.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="상위 N곳만 (0=전체)")
    ap.add_argument("--run", action="store_true", help="실제 API 실행 (없으면 dry-run)")
    a = ap.parse_args()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    run(a.db, a.pairs, a.out, a.limit, dry_run=not a.run)


if __name__ == "__main__":
    main()
