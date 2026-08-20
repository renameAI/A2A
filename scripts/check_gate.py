#!/usr/bin/env python3
"""tests/GATE 목록이 실제 테스트 파일과 어긋났는지 검사한다.

CI 게이트를 워크플로 안에 손으로 나열해 두었더니 이 저장소에서 새 테스트
12개가 CI 밖에 남았다 — 로컬에서만 돌고 있었고 아무도 몰랐다. 목록을 파일로
빼고, 목록에 없는 새 테스트가 생기면 여기서 실패시킨다.

게이트에 넣을 수 없는 테스트(느리거나 네트워크·LLM 의존)는 EXCLUDE에
**이유와 함께** 적는다. 조용히 빠지는 것과 이유를 밝히고 빠지는 것은 다르다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 게이트 밖 — 이유가 없으면 여기 들어올 수 없다 (태스크 #41 스위트 위생)
EXCLUDE = {
    "tests/test_bench": "벤치 스위트 — 실 LLM 호출",
    "tests/test_judge": "judge 스위트 — 실 LLM 호출",
    # 아래는 지금 빨간불이다(실측 2026-08). 게이트에 넣으면 상수 빨간불이
    # 되어 그물 구실을 못 하므로 이유를 적고 뺀다 — 태스크 #41에서 하나씩
    # 고쳐 GATE로 옮긴다. 조용히 빠지는 것과 이유를 밝히고 빠지는 것은 다르다.
    "tests/test_a2a.py": "#41 — 14건 실패 (구 A2A 계약)",
    "tests/test_api.py": "#41 — 19건 실패 (구 /v1 API 계약)",
    "tests/test_adversarial_fixes.py": "#41 — 4/29 실패",
    "tests/test_consultant.py": "#41 — 4/5 실패",
    "tests/test_crawler.py": "#41 — 2/6 실패 (네트워크 의존)",
    "tests/test_ingest.py": "#41 — 3/10 실패 (네트워크 의존)",
    "tests/test_job_persistence.py": "#41 — 7/13 실패",
    "tests/test_persistence.py": "#41 — 1/6 실패",
    "tests/test_pipeline.py": "#41 — 5건 실패",
    "tests/test_question_axioms.py": "#41 — 1/8 실패",
    "tests/test_robustness.py": "#41 — 2/11 실패",
    "tests/test_compose_public_output.py": "#41 — 수집 오류",
    "tests/test_product.py": "#41 — 수집 오류",
    "tests/test_represent_retrieve_contracts.py": "#41 — 수집 오류",
    "tests/test_scout.py": "#41 — 수집 오류",
    "tests/test_vision.py": "#41 — 수집 오류",
}


def main() -> int:
    gate = {l.strip() for l in (ROOT / "tests/GATE").read_text().splitlines()
            if l.strip()}
    actual = {str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob("test_*.py")}
    excluded = {a for a in actual
                if any(a.startswith(e) for e in EXCLUDE)}
    missing = actual - gate - excluded
    stale = gate - actual

    bad = False
    if missing:
        bad = True
        print("게이트에 없는 테스트 파일:")
        for m in sorted(missing):
            print(f"  - {m}")
        print("\n  tests/GATE에 추가하거나, 게이트 밖이어야 하면")
        print("  scripts/check_gate.py의 EXCLUDE에 이유와 함께 적으세요.")
    if stale:
        bad = True
        print("게이트 목록에만 있고 실제로 없는 파일:")
        for s in sorted(stale):
            print(f"  - {s}")
    if not bad:
        print(f"게이트 {len(gate)}개 파일 — 누락 없음")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
