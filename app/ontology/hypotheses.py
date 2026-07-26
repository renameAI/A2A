"""박사님 온톨로지 산출물 → 런타임 judge 프롬프트 주입 (가설 카드 + 조정 규칙).

judge_cases/의 오프라인 공장(negotiation_sim → feedback_loop)이 만들어낸 두 산출물을
런타임 판단에 앵커링한다. 코드가 아니라 산출물을 소비한다:
  · hypothesis_library.yaml — 도메인 학습 가설 카드(exploit=검증된 처방 / explore=베팅)
  · ontology_adjustments.json — feedback_loop의 "학습 없는 학습" 조정 규칙

정직성 원칙(retrieve.py와 동일):
  · 전부 constructed/simulated 기반 — "참고 구조"지 사실이 아니다. 고지를 함께 붙인다.
  · 관련도(질의 겹침)가 낮으면 조용히 생략. 조정 규칙은 도메인 스코프가 맞을 때만.
  · 파일 문제로 판단을 막지 않는다 — 실패 시 빈 결과.
"""
import functools
import json
from pathlib import Path

from ..engine.common import overlap

_JC = Path(__file__).resolve().parent.parent.parent / "judge_cases"
_LIB = _JC / "hypothesis_library.yaml"
_ADJ = _JC / "ontology_adjustments.json"

_MIN_OVERLAP = 0.12   # 질의-가설 겹침 하한 (retrieve.py 0.15보다 약간 낮게 — 카드
                      # statement가 길어 자연 겹침이 작다). 미만이면 카드 생략.
_MAX_CARDS = 2        # 도메인당 몇 장 — 많으면 "통째 암기" 신호가 된다(프롬프트 철학).


@functools.lru_cache(maxsize=1)
def _cards() -> tuple[dict, ...]:
    try:
        import yaml
        d = yaml.safe_load(_LIB.read_text(encoding="utf-8"))
        return tuple(d["library"]["cards"])
    except Exception:                             # noqa: BLE001 — 재료 문제로 판단 안 막음
        return ()


@functools.lru_cache(maxsize=1)
def _adjustments() -> tuple[dict, ...]:
    try:
        d = json.loads(_ADJ.read_text(encoding="utf-8"))
        out = []
        for key, lens in (("buyer_rules", "buy"), ("seller_rules", "sell")):
            for r in d.get(key, []):
                if r.get("status") == "active":
                    out.append({**r, "_lens": lens})
        return tuple(out)
    except Exception:                             # noqa: BLE001
        return ()


def _lens_for(vantage: str) -> str:
    return "buy" if vantage == "buyer" else "sell"


def ontology_cards(vantage: str, query: str) -> "str | None":
    """vantage(seller/buyer) 렌즈에 맞는 가설 카드 + 조정 규칙을 참고 힌트로 조립.

    가설 카드: lens 일치 + query 겹침 상위 N장. exploit는 '검증된 처방',
    explore는 '확인할 베팅(evidence_needed)'으로 구분해 판단에 근거를 준다.
    조정 규칙: 도메인 스코프가 query와 겹칠 때만(무관 산업 누수 방지).
    """
    q = (query or "").strip()
    if not q:
        return None
    lens = _lens_for(vantage)

    scored = []
    for c in _cards():
        if c.get("lens") != lens:
            continue
        text = f"{c.get('statement','')} {c.get('source',{}).get('file','')}"
        s = overlap(q, text)
        if s >= _MIN_OVERLAP:
            scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    top = [c for _, c in scored[:_MAX_CARDS]]

    lines: list[str] = []
    for c in top:
        tag = "검증된 처방" if c.get("frame") == "exploit" else "확인할 베팅"
        stmt = " ".join(c.get("statement", "").split())
        line = f"- [{tag}] {stmt}"
        if c.get("frame") == "explore" and c.get("evidence_needed"):
            line += f" (검증 필요: {c['evidence_needed']})"
        lines.append(line)

    # 조정 규칙 — 도메인 스코프가 query와 겹치는 active 규칙만(글로벌 규칙은 항상)
    for r in _adjustments():
        if r["_lens"] != lens:
            continue
        scope = r.get("scope", {})
        dom = scope.get("domain", "")
        if scope.get("global") or (dom and overlap(q, dom) >= _MIN_OVERLAP):
            lines.append(f"- [피드백 조정규칙 {r['id']}] {r.get('rule','')}")

    if not lines:
        return None
    return ("[박사님 온톨로지 가설·조정 — 구조 참고, 사실 아님(constructed/simulated)]\n"
            + "\n".join(lines))
