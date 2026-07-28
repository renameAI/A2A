"""LLM 추출 — 청크들 → 프로필 필드 + provenance + 근거 청크 (ING-03, ING-04).

구조화 출력 스키마로 형식을 강제하므로 파싱 실패가 없다. 모든 자연어 필드는
한국어 표준으로 정규화한다 (ING-06, REP-08).
"""
import re

from .. import progress
from ..engine.llm import Extractor
from ..engine.prompts import EXTRACT_SCHEMA, EXTRACT_SYSTEM, extract_user
from ..schemas import (CompanyPortrait, Profile, BasicInfo, ProvField,
                       Provenance, ValueProp, Willingness)
from .chunking import Chunk


def _norm(s: str) -> str:
    return re.sub(r"[\s·().,\-]", "", s).lower()


def _prov_field(data: dict) -> ProvField:
    prov = Provenance(data["provenance"])
    conf = data.get("confidence")
    if prov == Provenance.inferred and conf is None:
        conf = 0.5   # 스키마 계약 보정 (REP-03: inferred엔 confidence 필수)
    if conf is not None:
        # 시스템 경계 — LLM이 스키마(0~1)를 벗어난 값을 낼 수 있다(실측: 7.2,
        # 10점 스케일 착각으로 추정). JSON Schema의 min/max는 강제 보장이 아니라
        # 코드가 최종 방어선이어야 한다 — 클램프로 원인 대신 증상만 억누르지 않게
        # 원본값은 버리지 않고 [0,1]로 정직하게 눌러 담는다.
        conf = max(0.0, min(1.0, conf))
    return ProvField(value=data.get("value", ""), provenance=prov, confidence=conf)


def _willingness(value) -> Willingness | None:
    return Willingness(value) if value else None


def extract_profile(chunks: list[Chunk], extractor: Extractor
                    ) -> tuple[Profile, list[str], dict[str, list[str]]]:
    """청크들 → (Profile, open_questions, evidence). evidence = 필드 → 근거 청크 ID."""
    user = extract_user(chunks)
    progress.log("추론", f"다층 독해 입력 — 청크 {len(chunks)}개 · {len(user):,}자")
    # deep=True — 상(像)은 다층 독해(추론)에서 나온다. 프롬프트를 줄이지 않는다.
    data = extractor.extract_json(EXTRACT_SYSTEM, user, EXTRACT_SCHEMA, deep=True)

    profile = Profile(
        basic=BasicInfo(**data["basic"]),
        description=data["description"],
        problem_solved=_prov_field(data["problem_solved"]),
        solution=_prov_field(data["solution"]),
        target_customer=_prov_field(data["target_customer"]),
        references=data["references"],
        traction=data.get("traction"),
        # 중복 제거 — 실측(할리케이 PDF, 2026-07-27): sell_value_props에 동일값
        # ("revenue_growth")이 7회 반복되고 나머지 3종(impact 등, 원문에 지역상생·
        # 시니어고용 신호가 뚜렷한데도)은 전혀 안 나온 사례. 스키마(_VALUE_PROPS)에
        # uniqueItems를 걸어도 API가 강제 보장하지 않을 수 있다(구조화 출력은 JSON
        # Schema 부분집합만 지원하는 프로바이더가 흔함) — 순서 보존 dedup으로 코드가
        # 최종 방어선. UI가 이 필드를 그대로 렌더링해 중복이 데모 화면에 스팸처럼
        # 노출된다(app.js:1318).
        sell_value_props=list(dict.fromkeys(
            ValueProp(v) for v in data["sell_value_props"])),
        purchase_value_props=list(dict.fromkeys(
            ValueProp(v) for v in data["purchase_value_props"])),
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

    # 인용 계약 (R2) — evidence_chunk_ids가 실존 청크를 가리키는지 검증.
    # bbox의 "배치 밖 페이지 폐기"와 동일 원리: 존재하지 않는 인용은 폐기 + 집계.
    valid_ids = {c.chunk_id for c in chunks}
    n_invalid = 0
    evidence: dict[str, list[str]] = {}
    for name in ("problem_solved", "solution", "target_customer"):
        ids = data[name]["evidence_chunk_ids"]
        kept = [i for i in ids if i in valid_ids]
        n_invalid += len(ids) - len(kept)
        if kept:
            evidence[name] = kept
    if n_invalid:
        progress.log("검증", f"⚠ 실존하지 않는 근거 청크 인용 {n_invalid}건 폐기 "
                             f"(환각 인용 — 유효 청크 {len(valid_ids)}개와 대조)")
    return profile, data["open_questions"], evidence
