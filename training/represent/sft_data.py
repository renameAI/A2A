"""E11 SFT 변환 — 교사 라벨 JSONL → chat SFT 행 + 회사분리 split (torch 무관).

스코어러(11토큰 분류)와 달리 represent는 **생성 태스크**라 표준 chat SFT다:
  system: 고정 지시(스키마 계약)
  user  : 리서치 원문(예산 내 절단)
  assistant: canonical JSON (키 순서 고정·compact — 학습 타겟)

회사분리 held-out: E8의 교훈(회사 수 부족 → 암기) 그대로 — held 회사는 학습에
한 글자도 안 들어간다. 라벨 품질 신호(r1_demoted)는 필터가 아니라 통계로 보고.
"""
import argparse
import json
import random
from pathlib import Path

SYSTEM = (
    "너는 B2B 매칭 엔진의 represent 모듈이다. 기업 리서치 원문을 읽고 아래 "
    "스키마의 JSON 하나만 출력한다. 원문에 명시된 것은 provenance=stated, "
    "역추론은 inferred로 정직하게 구분한다. portrait는 자료가 보여주는 결과에서 "
    "전략·처지를 역추론한 상(像)이다 — 모르면 모른다고 쓴다.\n"
    '스키마: {"problem_solved":{"value","provenance"},"solution":{...},'
    '"target_customer":{...},"portrait":{"identity","business_model","edge",'
    '"stage_narrative","assets","gaps","risk_signals"}}')

FIELD_ORDER = ["problem_solved", "solution", "target_customer", "portrait"]
PORTRAIT_ORDER = ["identity", "business_model", "edge", "stage_narrative",
                  "assets", "gaps", "risk_signals"]


def canonical_target(t: dict) -> str:
    """키 순서 고정 + compact — 같은 내용이면 같은 문자열 (학습 타겟 안정화)."""
    out = {}
    for f in FIELD_ORDER[:3]:
        out[f] = {"value": t[f]["value"], "provenance": t[f]["provenance"]}
    p = t.get("portrait")
    out["portrait"] = ({k: p.get(k, "") for k in PORTRAIT_ORDER} if p else None)
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def build_rows(raw_path, max_input_chars):
    rows = []
    for line in Path(raw_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if not r.get("target") or not r["target"].get("portrait"):
            continue                               # portrait 없는 교사 산출은 제외
        rows.append({
            "company": r["company"],
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": r["research_text"][:max_input_chars]},
                {"role": "assistant", "content": canonical_target(r["target"])},
            ],
            "r1_demoted": r.get("r1_demoted", 0),
        })
    return rows


def split_by_company(rows, held_frac, seed):
    names = sorted({r["company"] for r in rows})
    rng = random.Random(seed)
    rng.shuffle(names)
    n_held = max(1, int(len(names) * held_frac))
    held_set = set(names[:n_held])
    train = [r for r in rows if r["company"] not in held_set]
    held = [r for r in rows if r["company"] in held_set]
    return train, held


def main():
    ap = argparse.ArgumentParser(description="E11 SFT 변환 + 회사분리 split")
    ap.add_argument("--raw", default="dataset/represent_sft_raw.jsonl")
    ap.add_argument("--out-train", default="dataset/represent_train.jsonl")
    ap.add_argument("--out-held", default="dataset/represent_held.jsonl")
    ap.add_argument("--max-input-chars", type=int, default=6000)
    ap.add_argument("--held-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    rows = build_rows(a.raw, a.max_input_chars)
    train, held = split_by_company(rows, a.held_frac, a.seed)
    for path, part in ((a.out_train, train), (a.out_held, held)):
        with open(path, "w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    demoted = sum(1 for r in rows if r["r1_demoted"] > 0)
    in_lens = [len(r["messages"][1]["content"]) for r in rows]
    out_lens = [len(r["messages"][2]["content"]) for r in rows]
    print(f"[변환] 유효 {len(rows)} · train {len(train)} · held {len(held)} "
          f"(회사분리, 누수 0)")
    print(f"[라벨 품질] R1 강등 있었던 교사 산출: {demoted}/{len(rows)} "
          f"({100*demoted/max(1,len(rows)):.0f}%) — 필터 안 함, 기록만")
    print(f"[길이] 입력 중앙값 {sorted(in_lens)[len(in_lens)//2]}자 · "
          f"타겟 중앙값 {sorted(out_lens)[len(out_lens)//2]}자")


if __name__ == "__main__":
    main()
