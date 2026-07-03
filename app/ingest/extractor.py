"""LLM 추출 — 청크들 → 프로필 필드 + provenance + 근거 청크 (ING-03, ING-04).

구조화 출력 스키마로 형식을 강제하므로 파싱 실패가 없다. 모든 자연어 필드는
한국어 표준으로 정규화한다 (ING-06, REP-08).
"""
import re

from .. import progress
from ..engine.llm import Extractor
from ..engine.prompts import EXTRACT_SYSTEM
from ..schemas import (CompanyPortrait, Profile, BasicInfo, ProvField,
                       Provenance, ValueProp, Willingness)
from .chunking import Chunk


def _norm(s: str) -> str:
    return re.sub(r"[\s·().,\-]", "", s).lower()

# 추론 가능 필드 공통 형식 — provenance + 확신도 + 근거 청크 (스키마 §3.2 확장)
_FIELD = {
    "type": "object", "additionalProperties": False,
    "required": ["value", "provenance", "confidence", "evidence_chunk_ids"],
    "properties": {
        "value": {"type": "string"},
        "provenance": {"type": "string", "enum": ["stated", "inferred", "ask"]},
        "confidence": {"type": ["number", "null"]},
        "evidence_chunk_ids": {"type": "array", "items": {"type": "string"}},
    },
}
_VALUE_PROPS = {"type": "array", "items": {
    "type": "string",
    "enum": ["revenue_growth", "cost_reduction", "impact", "problem_solving"]}}
_WILLINGNESS = {"type": ["string", "null"],
                "enum": ["very_high", "high", "medium", "low", "very_low", None]}

# 회사의 상(像) — 다층 독해의 결과물 (프롬프트 "portrait 작성 지침"과 1:1)
_PORTRAIT = {
    "type": "object", "additionalProperties": False,
    "required": ["identity", "business_model", "edge", "stage_narrative",
                 "assets", "gaps", "risk_signals"],
    "properties": {k: {"type": "string"} for k in
                   ("identity", "business_model", "edge", "stage_narrative",
                    "assets", "gaps", "risk_signals")},
}

EXTRACTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["basic", "description", "problem_solved", "solution",
                 "target_customer", "references", "traction",
                 "sell_value_props", "purchase_value_props",
                 "willingness_sell", "willingness_purchase",
                 "portrait", "open_questions"],
    "properties": {
        "basic": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "country", "city", "founded_year", "industry"],
            "properties": {
                "name": {"type": "string"},
                "country": {"type": "string"},
                "city": {"type": ["string", "null"]},
                "founded_year": {"type": ["integer", "null"]},
                "industry": {"type": "string"},
            },
        },
        "description": {"type": "string"},
        "problem_solved": _FIELD,
        "solution": _FIELD,
        "target_customer": _FIELD,
        "references": {"type": "array", "items": {"type": "string"}},
        "traction": {"type": ["string", "null"]},
        "sell_value_props": _VALUE_PROPS,
        "purchase_value_props": _VALUE_PROPS,
        "willingness_sell": _WILLINGNESS,
        "willingness_purchase": _WILLINGNESS,
        "portrait": _PORTRAIT,
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = EXTRACT_SYSTEM   # 범용 프롬프트 (prompts.py) 사용


def _prov_field(data: dict) -> ProvField:
    prov = Provenance(data["provenance"])
    conf = data.get("confidence")
    if prov == Provenance.inferred and conf is None:
        conf = 0.5   # 스키마 계약 보정 (REP-03: inferred엔 confidence 필수)
    return ProvField(value=data.get("value", ""), provenance=prov, confidence=conf)


def _willingness(value) -> Willingness | None:
    return Willingness(value) if value else None


def extract_profile(chunks: list[Chunk], extractor: Extractor
                    ) -> tuple[Profile, list[str], dict[str, list[str]]]:
    """청크들 → (Profile, open_questions, evidence). evidence = 필드 → 근거 청크 ID."""
    user = "\n\n".join(f"[{c.chunk_id}]\n{c.text}" for c in chunks)
    # deep=True — 상(像)은 다층 독해(추론)에서 나온다. 속도보다 상의 품질 우선.
    data = extractor.extract_json(SYSTEM_PROMPT, user, EXTRACTION_SCHEMA, deep=True)

    profile = Profile(
        basic=BasicInfo(**data["basic"]),
        description=data["description"],
        problem_solved=_prov_field(data["problem_solved"]),
        solution=_prov_field(data["solution"]),
        target_customer=_prov_field(data["target_customer"]),
        references=data["references"],
        traction=data.get("traction"),
        sell_value_props=[ValueProp(v) for v in data["sell_value_props"]],
        purchase_value_props=[ValueProp(v) for v in data["purchase_value_props"]],
        willingness_sell=_willingness(data.get("willingness_sell")),
        willingness_purchase=_willingness(data.get("willingness_purchase")),
        portrait=CompanyPortrait(**data["portrait"]) if data.get("portrait") else None,
    )
    # 그라운딩 검사 (이슈 #2) — 회사명이 자료에 실재하는지 확인.
    # 없으면 레퍼런스·고객사를 주체로 오추출했을 가능성 → 경고 로그(사후 방어).
    source = _norm(user)
    if profile.basic.name and profile.basic.name != "미상" \
            and _norm(profile.basic.name) not in source:
        progress.log("검증", f"⚠ 회사명 '{profile.basic.name}'이 자료에서 확인되지 "
                             f"않음 — 레퍼런스·고객사 오추출 가능성. 사람 확인 권장.")

    evidence = {
        name: data[name]["evidence_chunk_ids"]
        for name in ("problem_solved", "solution", "target_customer")
        if data[name]["evidence_chunk_ids"]
    }
    return profile, data["open_questions"], evidence
