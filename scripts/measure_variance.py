#!/usr/bin/env python3
"""분산 계측 러너 (FORMALIZATION.md L0) — 스킬을 m회 실행해 run-to-run 분산을 잰다.

    python scripts/measure_variance.py --skill represent --m 5 --input examples/divein.json
    python scripts/measure_variance.py --skill judge --m 5 --input examples/judge.json

--input은 해당 스킬의 요청 스키마(RepresentRequest/JudgeRequest 등)를 담은 JSON 파일.
Mock 모드(키 없음)면 결정적이라 stability≈1.0이 나온다 — 실 LLM 키를 넣어야 진짜 분산이 보인다.
비용 경고: 실 LLM에서 m회는 m배 비용·지연이다. 작은 m부터.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.errors import EngineError              # noqa: E402
from app.eval.variance import variance_report   # noqa: E402
from app.schemas import (ComposeRequest, JudgeRequest, RepresentRequest,  # noqa: E402
                         RetrieveRequest)

_SKILLS = {
    "represent": (RepresentRequest, "app.engine.represent", "represent"),
    "retrieve": (RetrieveRequest, "app.engine.retrieve", "retrieve"),
    "judge": (JudgeRequest, "app.engine.judge", "judge"),
    "compose": (ComposeRequest, "app.engine.compose", "compose"),
}


def _load_skill(name):
    import importlib
    model_cls, mod, fn = _SKILLS[name]
    return model_cls, getattr(importlib.import_module(mod), fn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", required=True, choices=list(_SKILLS))
    ap.add_argument("--m", type=int, default=5, help="반복 실행 횟수")
    ap.add_argument("--input", required=True, help="요청 JSON 파일 경로")
    args = ap.parse_args()

    model_cls, engine_fn = _load_skill(args.skill)
    req = model_cls(**json.loads(Path(args.input).read_text()))

    outputs = []
    gate_hits = 0
    for i in range(args.m):
        print(f"  run {i + 1}/{args.m} …", file=sys.stderr)
        try:
            outputs.append(engine_fn(req).model_dump(mode="json"))
        except EngineError as e:
            # 게이트 미달(ProfileBelowMinimum 등)은 크래시가 아니라 하나의 결과 —
            # 무료 API 호출을 낭비하지 않도록 잡아서 집계한다.
            gate_hits += 1
            print(f"    gate: {e.code} — {e.message}", file=sys.stderr)
    if not outputs:
        print(f"모든 실행이 게이트에 걸림({gate_hits}/{args.m}) — 분산 계측 불가. "
              f"입력이 최소 프로필을 넘도록 보강하세요.", file=sys.stderr)
        return
    if gate_hits:
        print(f"⚠ {gate_hits}/{args.m} 실행이 게이트 미달 — 나머지 {len(outputs)}건으로 계측",
              file=sys.stderr)

    report = variance_report(outputs)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n=== {args.skill} · m={args.m} · 전체 안정성 "
          f"{report['overall_stability']:.3f} (1=완전 재현) ===", file=sys.stderr)
    if report["least_stable"]:
        print("가장 불안정한 필드:", file=sys.stderr)
        for f in report["least_stable"]:
            print(f"  {f['stability']:.3f}  {f['field']} ({f['type']})", file=sys.stderr)


if __name__ == "__main__":
    main()
