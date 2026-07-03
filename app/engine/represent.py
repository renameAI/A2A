"""Represent — 자료 → 3형 출력 (REP-01~07, 기획서 5장).

두 경로:
- LLM 경로 (ANTHROPIC_API_KEY 설정 시): 수집(fetch) → 청킹 → LLM 추출 (ING-01~04)
- Mock 경로 (키 없음): 구조화 텍스트("키: 값" 라인) 파서 — CI·로컬 개발용 (ING-05)
출력 계약(3형 출력 + 최소 프로필 게이트)은 두 경로 동일.
"""
from ..config import Settings, get_settings
from ..errors import ProfileBelowMinimum
from ..ingest.chunking import Chunk, chunk_text, pdf_to_text
from ..ingest.extractor import extract_profile
from ..ingest.fetchers import fetch_instagram, fetch_pdf_bytes, fetch_url
from ..schemas import (AssetType, OntologyAnchor, Profile, BasicInfo, ProvField,
                       Provenance, RepresentRequest, RepresentResponse,
                       ValueProp, Willingness)
from .common import infer_stage, pseudo_embedding
from .llm import get_extractor

_VALUE_PROP_MAP = {
    "매출": ValueProp.revenue_growth,
    "비용": ValueProp.cost_reduction,
    "임팩트": ValueProp.impact,
    "문제해결": ValueProp.problem_solving,
}
_WILLINGNESS_MAP = {
    "매우 적극적": Willingness.very_high,
    "적극적": Willingness.high,
    "중간": Willingness.medium,
    "소극적": Willingness.low,
    "매우 소극적": Willingness.very_low,
}

# 사람에게 되물을 질문 문구 (REP-02: 비워두지 않고 '질문필요'로 마킹)
_ASK_QUESTIONS = {
    "problem": "귀사가 해결하는 문제는 무엇인가요? (표면 키워드가 아닌, 상대가 겪는 문제 관점으로)",
    "solution": "그 문제를 어떤 방식으로 해결하나요?",
    "target": "누구에게 팔고 싶으신가요? (타겟 고객)",
    "value_prop": "핵심 가치 제안은 무엇인가요? (매출/비용/임팩트/문제해결 중)",
}


# ── 자산 수집 → 청크 (ING-01, ING-02) ───────────────────────────────

def _asset_text(asset, settings: Settings) -> str:
    if asset.content:
        return asset.content
    if asset.type == AssetType.ir_deck:
        return pdf_to_text(fetch_pdf_bytes(asset.url or "", settings))
    if asset.type == AssetType.instagram:
        return fetch_instagram(asset.url or "", settings)
    return fetch_url(asset.url or "", settings)


def _ingest_assets(req: RepresentRequest, settings: Settings
                   ) -> tuple[list[Chunk], str]:
    """자산들 → 출처 라벨 달린 청크 + (Mock 경로용) 전체 텍스트."""
    from .. import progress
    chunks: list[Chunk] = []
    full_text_parts: list[str] = []
    for i, asset in enumerate(req.assets):
        label = f"a{i + 1}:{asset.type.value}"
        progress.log("수집", f"{label} 수집 시작"
                     + (f" — {asset.url}" if asset.url else " (직접 입력)"))
        text = _asset_text(asset, settings)
        progress.log("수집", f"{label} 완료 — {len(text):,}자")
        chunks.extend(chunk_text(text, label))
        full_text_parts.append(text)
    if req.dialogue:
        qa = "\n".join(f"{t.q}: {t.a}" for t in req.dialogue)
        chunks.extend(chunk_text(f"[보강 대화 답변 — 최우선 신뢰]\n{qa}", "dialogue"))
        full_text_parts.append(qa)
    return chunks, "\n".join(full_text_parts)


# ── Mock 경로 — 구조화 텍스트 파서 ──────────────────────────────────

def _parse_lines(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key and val:
            fields[key] = val
    return fields


def _prov_field(fields: dict, key: str) -> ProvField:
    if key in fields:
        return ProvField(value=fields[key], provenance=Provenance.stated)
    return ProvField(value="", provenance=Provenance.ask)


def _mock_extract(full_text: str) -> tuple[Profile, list[str]]:
    fields = _parse_lines(full_text)
    profile = Profile(
        basic=BasicInfo(
            name=fields.get("이름", "미상"),
            country=fields.get("국가", "미상"),
            city=fields.get("도시"),
            founded_year=int(fields["설립"]) if fields.get("설립", "").isdigit() else None,
            industry=fields.get("산업", "unknown"),
        ),
        description=fields.get("설명", ""),
        problem_solved=_prov_field(fields, "문제"),
        solution=_prov_field(fields, "솔루션"),
        target_customer=_prov_field(fields, "타겟"),
        references=[r.strip() for r in fields.get("레퍼런스", "").split(",") if r.strip()],
        traction=fields.get("트랙션"),
        sell_value_props=[_VALUE_PROP_MAP[t.strip()]
                          for t in fields.get("판매가치", "").split(",")
                          if t.strip() in _VALUE_PROP_MAP],
        purchase_value_props=[_VALUE_PROP_MAP[t.strip()]
                              for t in fields.get("구매가치", "").split(",")
                              if t.strip() in _VALUE_PROP_MAP],
        willingness_sell=_WILLINGNESS_MAP.get(fields.get("판매의향", "")),
        willingness_purchase=_WILLINGNESS_MAP.get(fields.get("구매의향", "")),
    )

    open_questions: list[str] = []
    if profile.problem_solved.provenance == Provenance.ask:
        open_questions.append(_ASK_QUESTIONS["problem"])
    if profile.solution.provenance == Provenance.ask:
        open_questions.append(_ASK_QUESTIONS["solution"])
    if profile.target_customer.provenance == Provenance.ask:
        open_questions.append(_ASK_QUESTIONS["target"])
    if not profile.sell_value_props and not profile.purchase_value_props:
        open_questions.append(_ASK_QUESTIONS["value_prop"])
    if profile.willingness_sell is None and profile.willingness_purchase is None:
        open_questions.append("협력 의향(판매/구매)은 어느 정도인가요?")
    return profile, open_questions


# ── 공통 게이트·출력 ────────────────────────────────────────────────

def _check_minimum(profile: Profile, open_questions: list[str]) -> None:
    """최소 프로필 기준 (REP-06): 문제·솔루션·VP·타겟 각 1개 이상."""
    minimum_met = bool(
        profile.problem_solved.value
        and profile.solution.value
        and profile.target_customer.value
        and (profile.sell_value_props or profile.purchase_value_props)
    )
    if not minimum_met:
        raise ProfileBelowMinimum(open_questions)


def represent(req: RepresentRequest, settings: Settings | None = None
              ) -> RepresentResponse:
    from .. import progress
    settings = settings or get_settings()
    chunks, full_text = _ingest_assets(req, settings)
    progress.log("청킹", f"청킹 완료 — {len(chunks)}개 청크 (출처 라벨 유지)")

    extractor = get_extractor(settings)
    if extractor is not None:
        progress.log("추출", "K-EXAONE 다층 독해 시작 — 회사의 상(像) 구축")
        profile, open_questions, evidence = extract_profile(chunks, extractor)
        progress.log("추출", f"프로필 완성 — {profile.basic.name} / "
                             f"보강 질문 {len(open_questions)}건")
        engine_mode = "llm"
    else:
        progress.log("추출", "Mock 모드 — 구조화 텍스트 파서 사용 (LLM 키 없음)")
        profile, open_questions = _mock_extract(full_text)
        evidence = None
        engine_mode = "mock"

    _check_minimum(profile, open_questions)
    progress.log("게이트", "최소 프로필 기준(REP-06) 통과")

    anchors = [
        OntologyAnchor(category="industry", value=profile.basic.industry),
        OntologyAnchor(category="region", value=profile.basic.country),
        OntologyAnchor(category="stage", value=infer_stage(profile)),
    ]
    embedding = pseudo_embedding(
        f"{profile.problem_solved.value} {profile.solution.value} "
        f"{profile.target_customer.value}"
    )
    return RepresentResponse(
        profile=profile,
        embedding=embedding,
        ontology_anchors=anchors,
        minimum_met=True,
        open_questions=open_questions,
        engine_mode=engine_mode,
        evidence=evidence,
    )
