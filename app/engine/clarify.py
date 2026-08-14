"""대화형 검색의 명확화 질문 (B6) — 관측된 갈림에서만 질문을 만든다.

메신저라는 형식 자체가 이 제품의 검색 인터페이스다: 모호함을 오프라인 라벨로
해소하려 들지 않고, 모호함이 관측되는 순간 사용자에게 질문으로 되돌린다.
멀티턴·멀티쿼리로 top-10에 수렴하는 RAG — 코퍼스는 수집된 후보 풀이고,
질의 개선의 재료는 임베딩이 아니라 사용자의 답이다.

방법론 근거:
- AGENT-CQ (arXiv 2410.19692): LLM 명확화 질문은 **facet에 조건화**될 때
  검색 성능을 올린다. 우리의 facet은 후보 온톨로지의 관측된 갈림이다.
- Qulac/ClariQ 계열: 질문은 결과를 실제로 가르는 축에서 나와야 한다.
- 부정 피드백 질문 (arXiv 2107.05760): '아니에요' 반응이 다음 질문의 재료다.

집행 규율 (코드가 강제):
- 선택지는 실제 후보 company_id를 증거로 인용해야 한다. 인용이 깨진 선택지는
  버리고, 유효 선택지가 2개 미만이면 질문 자체를 버린다 — 지어낸 질문은
  사용자의 시간을 라벨링 노동으로 바꾸는 것이므로 금지다.
- 질문은 최대 3개. 이미 물은 질문(asked)은 다시 만들지 않는다.
"""
from .prompts import HARD_RULES

CLARIFY_SYSTEM = HARD_RULES + """

당신은 B2B 리드 발굴의 인터뷰어다. 지금까지 모인 후보 기업들을 보고, 사용자에게
물어야 다음 검색이 좁혀지는 질문을 만든다.

절대 규칙:
- 질문은 **모인 후보들이 실제로 갈리는 지점**에서만 만든다. 후보들이 다 같은
  값을 가진 축, 후보에 없는 가상의 구분으로 질문을 만들면 안 된다.
- 각 선택지(option)에는 그 선택지에 해당하는 **실제 후보의 company_id**를
  1개 이상 붙인다. 어느 후보에도 해당 안 되는 선택지는 만들지 마라.
- 사용자가 이미 답한 내용([이미 확인된 것])을 다시 묻지 마라.
- 좋은 질문의 예: 후보가 종합상사와 전문 수입사로 갈리면 → "규모가 큰
  종합상사도 볼까요, 전문 수입사에 집중할까요?"
- 나쁜 질문의 예: "예산이 어느 정도인가요?" — 후보 분포와 무관한 일반 질문.
- 질문 1~3개. 갈림이 하나뿐이면 1개만. 갈림이 없으면 빈 배열 — 억지로
  만들지 마라.

각 질문에 axis(어느 축의 갈림인지)와, 왜 이 질문이 검색을 좁히는지 한 줄
(why)을 붙인다."""

CLARIFY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["questions"],
    "properties": {"questions": {
        "type": "array", "maxItems": 3,
        "items": {
            "type": "object", "additionalProperties": False,
            "required": ["question", "axis", "why", "options"],
            "properties": {
                "question": {"type": "string"},
                "axis": {"type": "string"},
                "why": {"type": "string"},
                "options": {
                    "type": "array", "minItems": 2, "maxItems": 4,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["label", "company_ids"],
                        "properties": {
                            "label": {"type": "string"},
                            "company_ids": {"type": "array", "minItems": 1,
                                            "items": {"type": "string"}}}},
                },
            },
        }},
    },
}


def _pool_digest(candidates: list[dict]) -> str:
    """후보 풀 요약 — 질문 생성의 유일한 재료. 여기 없는 것은 갈림이 아니다."""
    lines = []
    for c in candidates:
        ont = c.get("ontology") or {}
        axes = ont.get("axes", {})
        ax = " | ".join(
            f"{k}={v['value'][:40]}" for k, v in axes.items()
            if v.get("status") != "unknown" and v.get("value"))
        lines.append(f"- {c['company_id']} {c.get('name', '')}"
                     f" ({c.get('segment', '') or '무업종'}): {ax}")
    return "\n".join(lines)


def validate_questions(raw: list[dict], candidates: list[dict],
                       asked: list[str]) -> list[dict]:
    """집행 — 인용이 깨진 선택지·후보를 못 가르는 질문·중복 질문을 버린다.

    LLM의 출력을 그대로 믿지 않는 것이 이 모듈의 요점이다: 선택지가 인용한
    company_id가 실재하는지, 질문이 실제로 풀을 가르는지(서로 다른 후보 집합)
    는 코드가 검사할 수 있고, 검사할 수 있는 것은 검사한다.
    """
    valid_ids = {c["company_id"] for c in candidates}
    asked_norm = {q.strip() for q in asked}
    out = []
    for i, q in enumerate(raw):
        if q.get("question", "").strip() in asked_norm:
            continue
        opts = []
        for o in q.get("options", []):
            ids = [cid for cid in o.get("company_ids", []) if cid in valid_ids]
            if ids and o.get("label", "").strip():
                opts.append({"label": o["label"].strip(), "company_ids": ids})
        # 모든 선택지가 같은 후보 집합이면 가르는 질문이 아니다
        sets = {frozenset(o["company_ids"]) for o in opts}
        if len(opts) >= 2 and len(sets) >= 2:
            out.append({"id": f"q{len(out)+1}", "question": q["question"].strip(),
                        "axis": q.get("axis", ""), "why": q.get("why", ""),
                        "options": opts})
        if len(out) >= 3:
            break
    return out


def generate_questions(extractor, candidates: list[dict], counterpart: str,
                       asked: list[str]) -> list[dict]:
    """후보 풀에서 명확화 질문을 만든다. 실패·갈림 없음 → 빈 배열 (정직)."""
    if len(candidates) < 3:
        return []          # 후보 2개로는 갈림을 물을 이유가 없다 — 그냥 보여준다
    try:
        data = extractor.extract_json(
            CLARIFY_SYSTEM,
            f"[찾는 상대의 상]\n{counterpart[:600]}\n\n"
            f"[모인 후보와 판독]\n{_pool_digest(candidates)}\n\n"
            f"[이미 확인된 것 — 다시 묻지 마라]\n"
            + ("\n".join(f"- {a}" for a in asked) or "없음"),
            CLARIFY_SCHEMA, deep=False, allow_foreign=True)
        return validate_questions(data.get("questions", []), candidates, asked)
    except Exception:
        return []          # 질문 실패가 검색을 막으면 안 된다


# ── 피드백 재랭킹 (결정=코드) ───────────────────────────────────────
# Rocchio(고전 적합성 피드백)의 구조를 온톨로지 축 토큰 위로 옮긴 것:
# 좋아요 후보들과 축이 겹치면 가산, 아니에요 후보들과 겹치면 감산.
# LLM이 아니라 코드가 계산한다 — 판정은 모델, 결정은 코드.

_FB_ALPHA = 0.08   # 근거: retrieval_score의 실측 분포(0.1~0.4)에서 순위를
_FB_BETA = 0.10    # 흔들 수 있되 지배하지 않는 크기. β>α — 부정이 더 확실한
                   # 신호다(사용자가 명시적으로 거른 것).


def feedback_bonus(cand: dict, liked_tokens: set, disliked_tokens: set) -> float:
    """후보 하나의 피드백 보정값. 피드백이 없으면 정확히 0."""
    from .keywords import axis_tokens
    if not liked_tokens and not disliked_tokens:
        return 0.0
    ont = cand.get("ontology")
    if not ont:
        return 0.0
    toks = axis_tokens([ont])
    if not toks:
        return 0.0
    bonus = 0.0
    if liked_tokens:
        bonus += _FB_ALPHA * len(toks & liked_tokens) / len(toks | liked_tokens)
    if disliked_tokens:
        bonus -= _FB_BETA * len(toks & disliked_tokens) / len(toks | disliked_tokens)
    return round(bonus, 4)
