"""결정론적 결정 게이트 — 축 상태(buyer_state) → 결정 라벨을 코드로 유도.

배경(EXAONE_이식성_검증.md): K-EXAONE은 축(BB1-10)은 충실히 채우지만 최종 결정
라벨이 conditional로 쏠린다(5/6). 결정을 프롬프트가 아니라 코드가 내리면 모델
교체에 강건해진다 — app/engine/judge.py와 같은 원리(축 판정=모델, 결정=코드).

규칙 v1 (우선순위순, Gemini 9세션으로 캘리브레이션):
  1. exploitation_detected      → terminate_values      (착취 — 관계 차단)
  2. dealbreaker(BB6)           → terminate_structural  (구조 미달 — 관계 보존)
  3. unfit ≥ 2 (게이트 축 포함) → terminate_structural
  4. unknown ≥ 3                → hold        (검증 불가 항목 다수 — 판단 유보)
  5. caution+unknown ≥ 1        → conditional (조건·검증 남음)
  6. 그 외                      → recommend

사용:
  python decision_gate.py <session.json ...>     # 세션별 게이트 결정 vs 기록 결정
"""
import json
import sys
from pathlib import Path

DECISIONS = ("recommend", "conditional", "hold",
             "terminate_structural", "terminate_values")


def derive_decision(buyer_state: dict) -> tuple:
    """축 상태 → (결정, 근거 한 줄). 순수 함수 — LLM 무관."""
    axes = {k: v for k, v in buyer_state.items() if isinstance(v, dict)}
    exploit = any(a.get("exploitation_detected") for a in axes.values())
    dealbreaker = any(a.get("dealbreaker") for a in axes.values())
    unfit = [k for k, a in axes.items() if a.get("verdict") == "unfit"]
    caution = [k for k, a in axes.items() if a.get("verdict") == "caution"]
    unknown = [k for k, a in axes.items() if a.get("status") == "unknown"]

    if exploit:
        return "terminate_values", "착취 신호 감지(SB9 대응 축) — 관계 차단 철수"
    if dealbreaker:
        return "terminate_structural", "BB6 선결 게이트 deal-breaker — 구조적 미달"
    if len(unfit) >= 2:
        return "terminate_structural", f"복수 축 부적합({len(unfit)}) — {unfit[:3]}"
    if len(unknown) >= 3:
        return "hold", f"미검증 축 {len(unknown)}개 — 판단 재료 부족, 유보"
    if caution or unknown or unfit:
        return "conditional", (f"주의 {len(caution)}·미검증 {len(unknown)}·부적합 "
                               f"{len(unfit)} — 조건·검증 계획 필요")
    return "recommend", "전 축 적합·미검증 0 — 추천"


def main() -> None:
    files = [Path(p) for p in sys.argv[1:]]
    if not files:
        files = sorted(Path(__file__).parent.glob("*session.json"))
    agree = total = 0
    print(f"{'게이트 결정':22s} {'기록된 결정':22s} {'일치':4s} 세션")
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        recorded = (d.get("buyer_decision") or {}).get("decision")
        gated, why = derive_decision(d.get("buyer_state") or {})
        # 기록이 terminate(무접미) 등 이형이면 정규화 없이 그대로 비교(정직)
        ok = gated == recorded
        agree += ok
        total += 1
        mark = "✓" if ok else "✗"
        print(f"{gated:22s} {recorded or '?':22s} {mark:4s} "
              f"{d.get('seller_company','?')}×{d.get('buyer_company','?')}")
        if not ok:
            print(f"    └ 게이트 근거: {why}")
    print(f"\n일치 {agree}/{total}")


if __name__ == "__main__":
    main()
