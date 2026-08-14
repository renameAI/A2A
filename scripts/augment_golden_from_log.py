#!/usr/bin/env python3
"""B5 — 실전 스니펫 로그를 골든셋 케이스 후보로 변환한다.

    python scripts/augment_golden_from_log.py            # 후보 나열
    python scripts/augment_golden_from_log.py --emit     # 스켈레톤 생성

동작: dataset/snippet_log.jsonl에서 (1) 신호가 추출된 히트와 (2) 추출은
됐지만 신호가 0건인 히트를 골라 골든 케이스 스켈레톤으로 만든다.
category는 "TODO_사람이_라벨"로 비워 둔다 — **모델의 출력을 골드로 쓰면
그 모델의 오류가 정답이 되는 순환 논증**이므로, 모델 출력은 '검토용 참고'
필드(model_said)로만 붙인다. 사람이 라벨을 확정한 뒤 signal_golden.json의
cases에 수동으로 옮긴다.

출력: app/eval/signal_golden_staging.json (기존 파일에 이어 붙임, 중복 URL 제외)
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "dataset" / "snippet_log.jsonl"
STAGING = ROOT / "app" / "eval" / "signal_golden_staging.json"
GOLDEN = ROOT / "app" / "eval" / "signal_golden.json"


def load_log():
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue   # 잘린 마지막 줄 등 — 캡처가 벤치를 죽이면 안 된다
    return out


def existing_urls():
    urls = set()
    if STAGING.exists():
        urls |= {c.get("source_url", "") for c in
                 json.loads(STAGING.read_text(encoding="utf-8"))["cases"]}
    return urls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true", help="스켈레톤을 staging에 기록")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    rows = load_log()
    if not rows:
        print(f"로그 없음: {LOG} — /search를 먼저 실행하세요")
        return
    seen_urls = existing_urls()
    # 관심 후보: 추출 성공 히트(신호 유무 모두 값지다 — 신호 0건은 negative 후보)
    cands, dedup = [], set()
    for r in rows:
        if not r.get("extracted") or not r.get("snippet"):
            continue
        if r["url"] in seen_urls or r["url"] in dedup:
            continue
        dedup.add(r["url"])
        cands.append(r)
    cands = cands[: args.limit]

    print(f"로그 {len(rows)}건 → 골든 후보 {len(cands)}건 "
          f"(추출 성공 · staging 미포함 기준)")
    skeletons = []
    for i, r in enumerate(cands):
        ex = r["extraction"] or {}
        sig_n = len(r.get("ontology_signals", []))
        print(f"  [{i+1:02d}] {'신호 ' + str(sig_n) + '건' if sig_n else 'negative 후보'}"
              f" · {ex.get('name', '?')[:30]} · {r['query'][:40]}")
        skeletons.append({
            "id": f"R{i+1:02d}_TODO",
            "lang": "TODO_사람이_라벨",
            "source_url": r["url"],
            "source_query": r["query"],
            "material": {
                "name": ex.get("name", ""),
                "what": ex.get("what", ""),
                # 실전 노이즈 보존 — 원시 스니펫이 이 파이프라인의 존재 이유다
                "signal": r["snippet"],
            },
            "gold": {"signals": [
                {"category": "TODO_사람이_라벨", "anchor": "TODO", "has_date": False}
            ]},
            "model_said": {   # 검토 참고용 — 골드가 아니다
                "extraction_signal": ex.get("signal", ""),
                "ontology_signals": r.get("ontology_signals", []),
            },
            "note": "실전 스니펫 (B5) — 라벨 확정 후 signal_golden.json으로 이동, "
                    "model_said는 삭제",
        })

    if args.emit and skeletons:
        prev = (json.loads(STAGING.read_text(encoding="utf-8"))["cases"]
                if STAGING.exists() else [])
        STAGING.write_text(json.dumps(
            {"_doc": ["사람 라벨 대기 중인 실전 스니펫 케이스 — 골드 아님. "
                      "라벨 확정 후 signal_golden.json으로 옮기고 여기서 삭제."],
             "cases": prev + skeletons},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n{len(skeletons)}건을 {STAGING.name}에 기록 — 라벨을 확정해 주세요")
    elif skeletons:
        print("\n--emit 으로 스켈레톤을 생성합니다")


if __name__ == "__main__":
    main()
