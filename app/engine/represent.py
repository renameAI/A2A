"""Represent — 자료 → 3형 출력 (REP-01~07, 기획서 5장).

두 경로:
- LLM 경로 (ANTHROPIC_API_KEY 설정 시): 수집(fetch) → 청킹 → LLM 추출 (ING-01~04)
- Mock 경로 (키 없음): 구조화 텍스트("키: 값" 라인) 파서 — CI·로컬 개발용 (ING-05)
출력 계약(3형 출력 + 최소 프로필 게이트)은 두 경로 동일.
"""
from .. import progress
from ..config import Settings, get_settings
from ..errors import ProfileBelowMinimum
from ..ingest.chunking import Chunk, chunk_text, pdf_to_text
from ..ingest.extractor import extract_profile
from ..ingest.fetchers import fetch_instagram, fetch_pdf_bytes, fetch_url
from ..schemas import (AssetType, CompanyPortrait, OntologyAnchor, Profile,
                       BasicInfo, ProvField, Provenance, RepresentRequest,
                       RepresentResponse, ValueProp, Willingness)
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
_WILLINGNESS_QUESTION = "협력 의향(판매/구매)은 어느 정도인가요?"

# 보강 질문(원문) → Mock 파서가 읽는 정규 필드명 (단일 진실 소스)
_QUESTION_TO_FIELD = {
    _ASK_QUESTIONS["problem"]: "문제",
    _ASK_QUESTIONS["solution"]: "솔루션",
    _ASK_QUESTIONS["target"]: "타겟",
}


def _dialogue_to_mock_lines(dialogue) -> list[str]:
    """보강 답변을 Mock 파서용 정규 라인('필드: 값')으로 변환.

    프론트는 질문 원문을 그대로 되돌려준다 — 필드 매핑은 여기서만 한다.
    """
    lines: list[str] = []
    for t in dialogue:
        q, a = (t.q or "").strip(), t.a
        field = _QUESTION_TO_FIELD.get(q)
        if field:
            lines.append(f"{field}: {a}")
        elif q == _ASK_QUESTIONS["value_prop"]:
            props = [kw for kw in _VALUE_PROP_MAP if kw in a]
            if "문제 해결" in a and "문제해결" not in props:
                props.append("문제해결")
            if props:
                lines.append("판매가치: " + ",".join(props))
        elif q == _WILLINGNESS_QUESTION:
            lines.append(f"판매의향: {a}")
        else:                               # 이미 정규 키(판매의향 등)면 그대로
            lines.append(f"{q}: {a}")
    return lines


# ── 자산 수집 → 청크 (ING-01, ING-02) ───────────────────────────────

def _asset_text(asset, settings: Settings) -> str:
    if asset.content:
        return asset.content
    if asset.type == AssetType.ir_deck:
        return pdf_to_text(fetch_pdf_bytes(asset.url or "", settings))
    if asset.type == AssetType.instagram:
        return fetch_instagram(asset.url or "", settings)
    if asset.type == AssetType.website:
        # 웹사이트는 멀티페이지 크롤 (소개·제품 등 우선순위 링크, robots 준수)
        from ..ingest.crawler import crawl_website
        return crawl_website(asset.url or "", settings)
    return fetch_url(asset.url or "", settings)   # 기사 등 단일 페이지


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
        qa = "\n".join(f"{t.q}: {t.a}" for t in req.dialogue)      # LLM 경로: 질문 원문 유지
        chunks.extend(chunk_text(f"[보강 대화 답변 — 최우선 신뢰]\n{qa}", "dialogue"))
        full_text_parts.append("\n".join(_dialogue_to_mock_lines(req.dialogue)))
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
    profile.portrait = _mock_portrait(profile)
    return profile, open_questions


# 상 합성용 한국어 라벨 — enum 토큰(revenue_growth, sme...)이 산문에 새지 않게
_VP_KO = {ValueProp.revenue_growth: "매출 증대", ValueProp.cost_reduction: "비용 절감",
          ValueProp.impact: "임팩트", ValueProp.problem_solving: "문제 해결"}
_STAGE_KO = {"enterprise": "대기업", "chain": "체인·프랜차이즈", "seed": "시드 단계",
             "startup": "스타트업", "sme": "중소기업"}
_W_KO = {Willingness.very_high: "매우 적극적", Willingness.high: "적극적",
         Willingness.medium: "중간", Willingness.low: "소극적",
         Willingness.very_low: "매우 소극적"}


def _mock_portrait(profile: Profile) -> CompanyPortrait:
    """Mock 경로 간이 상 합성 — 파싱된 필드로 7필드를 결정적으로 조립.

    정직성 원칙: 자료에 없는 사실을 지어내지 않는다. 부재는 부재로 서술하고,
    규칙 기반 추정에는 '추정:'을 붙인다 (LLM 경로 portrait 지침과 동일 계약).
    수혜자: Scout mock 가설(portrait.gaps 소비 — explore 가설이 폴백에서 진짜
    결핍 기반으로 승격), 감사 로그, UI 상(像) 카드. mock Judge·상대 합성은
    규칙 기반이라 portrait를 읽지 않는다 — 효과를 과장하지 않는다.
    """
    p = profile
    prob, sol, tgt = (p.problem_solved.value, p.solution.value,
                      p.target_customer.value)
    has_refs, has_traction = bool(p.references), bool(p.traction)

    # identity — 있는 조각만으로 조립, 빈 조각은 미상 명시
    if prob and sol:
        identity = (f"{tgt or '(타겟 미상)'}의 '{prob}' 문제를 "
                    f"'{sol}' 방식으로 푸는 회사.")
    else:
        identity = ("자료만으로 정체성을 세우기에 부족 — "
                    + "·".join(k for k, v in
                               [("문제", prob), ("솔루션", sol), ("타겟", tgt)]
                               if not v) + " 미확인.")

    # business_model — mock 자료엔 돈 구조 필드가 없다: 가치 축만 정직하게
    # (판매·구매 양쪽에 같은 축이 있으면 중복 — 순서 유지 dedup)
    vp = list(dict.fromkeys(
        _VP_KO[v] for v in p.sell_value_props + p.purchase_value_props))
    business_model = ((f"가치 축: {', '.join(vp)}. " if vp else "")
                      + "수익 구조(과금 주체·단가·계약 형태)는 자료에 없음 — 미상.")

    # edge — 간이 파서는 해자를 판별할 수 없다: 레퍼런스만 잠재 신호로.
    # '등'은 다 나열하지 못했을 때만 — 3건 이하 전량 나열에 붙이면 없는 건수를 암시한다.
    if has_refs:
        listed = ", ".join(p.references[:3]) \
            + (" 등" if len(p.references) > 3 else "")
        edge = (f"자료상 뚜렷한 해자 신호 없음. 추정: 레퍼런스 "
                f"{len(p.references)}건({listed})이 진입장벽의 간접 신호일 수 있음.")
    else:
        edge = "자료상 뚜렷한 해자 신호 없음."

    # stage_narrative — 기존 결정적 단계 추정기 재사용 + 부재 신호 해석
    stage = _STAGE_KO.get(infer_stage(p), "중소기업")
    urgent = ("첫 레퍼런스 확보" if not has_refs
              else "트랙션 신호 축적" if not has_traction
              else "확장·재계약")
    stage_narrative = (f"추정 단계: {stage} (규칙 기반 키워드 판정). "
                       f"레퍼런스 {'있음' if has_refs else '없음'}·"
                       f"트랙션 {'있음' if has_traction else '없음'} — "
                       f"추정: 지금 절실한 것은 {urgent}.")

    # assets — 자료에서 확인된 것만 나열
    asset_parts = ([f"솔루션: {sol}"] if sol else []) \
        + ([f"레퍼런스: {', '.join(p.references)}"] if has_refs else []) \
        + ([f"트랙션: {p.traction}"] if has_traction else [])
    assets = ("; ".join(asset_parts) + " — 보완성 판단의 재료."
              if asset_parts else "자료에서 확인된 자산 없음.")

    # gaps — 실질 결핍(단계 추정)을 앞세운다: Scout mock 가설·검색어가 이 문장의
    # 앞부분을 그대로 소비하므로, 메타 서술("자료가 답하지 않은 것")로 시작하면
    # 검색어가 무의미해진다. 부재 필드 목록은 뒤에 부기.
    missing = [k for k, v in [("문제 정의", prob), ("솔루션", sol),
                              ("타겟", tgt), ("레퍼런스", has_refs),
                              ("트랙션", has_traction)] if not v]
    wp = p.willingness_purchase
    if wp is None:
        buy_face = "사는 쪽 얼굴(구매 니즈)은 자료에 없음 — 미상."
    elif wp in (Willingness.low, Willingness.very_low):
        # 소극적 표명을 '표명 있음'으로 뭉개면 자료 왜곡 — 수준을 그대로 서술
        buy_face = (f"구매 의향은 {_W_KO[wp]}으로 표명됨 — "
                    "사는 쪽 니즈는 크지 않다고 읽힌다.")
    else:
        buy_face = (f"구매 의향 표명 있음({_W_KO[wp]}) — "
                    "사는 쪽 니즈 구체화 필요.")
    gaps = (f"{urgent} (추정 — 단계 신호 기반). {buy_face}"
            + (f" 자료가 답하지 않은 것: {', '.join(missing)}." if missing else ""))

    # risk_signals — 부재도 신호다 (EXTRACT_SYSTEM 독해 규칙과 동일)
    signals = ([f"{m} 부재 — 초기 단계 또는 자료 미비 신호" for m in missing]
               + (["협력 의향 미표명"] if p.willingness_sell is None
                  and p.willingness_purchase is None else []))
    risk_signals = "; ".join(signals) if signals else "특이 신호 없음."

    return CompanyPortrait(
        identity=identity, business_model=business_model, edge=edge,
        stage_narrative=stage_narrative, assets=assets, gaps=gaps,
        risk_signals=risk_signals)


# ── 보강 질문 4지선다화 (자료 단서 기반 가설 선지) ──────────────────

_MOCK_CLARIFY_OPTIONS = {
    "problem": [
        {"label": "특정 고객군의 비용 부담", "hint": "비용 절감형 파트너와 매칭"},
        {"label": "기존 방식의 매출 정체", "hint": "매출 증대형 파트너와 매칭"},
        {"label": "수작업·비효율 프로세스", "hint": "자동화·효율화 수요처와 매칭"},
        {"label": "규제·품질 기준 대응 부담", "hint": "인증·컴플라이언스 축으로 매칭"},
    ],
    "solution": [
        {"label": "제품(HW/SW) 직접 판매", "hint": "구매 담당자 대상 아웃리치로 매칭"},
        {"label": "서비스·운영 대행", "hint": "위탁·파트너십 구조로 매칭"},
        {"label": "플랫폼·중개", "hint": "양면 시장의 공급/수요 양쪽과 매칭"},
        {"label": "데이터·구독형 SaaS", "hint": "반복 매출 구조 — PoC 후 구독 전환 매칭"},
    ],
    "target": [
        {"label": "중소기업 (오너 직접 결정)", "hint": "의사결정 빠른 소규모 딜로 매칭"},
        {"label": "대기업·체인 본사", "hint": "조달 절차가 긴 대형 계약형 매칭"},
        {"label": "공공·기관", "hint": "입찰·레퍼런스 중심 매칭"},
        {"label": "동종 업계 파트너사", "hint": "재판매·번들 제휴형 매칭"},
    ],
    "value_prop": [
        {"label": "매출 증대", "hint": "상대의 탑라인을 올려주는 제안으로 매칭"},
        {"label": "비용 절감", "hint": "상대의 원가·운영비를 줄이는 제안으로 매칭"},
        {"label": "임팩트", "hint": "ESG·사회적 가치 축의 상대와 매칭"},
        {"label": "문제 해결", "hint": "상대의 구체적 결핍을 직접 해소하는 매칭"},
    ],
    "willingness": [
        {"label": "매우 적극적", "hint": "바로 아웃리치 가능한 딜로 취급"},
        {"label": "적극적", "hint": "우선순위 높은 매칭 풀에 포함"},
        {"label": "중간", "hint": "조건이 맞을 때만 제안"},
        {"label": "소극적", "hint": "판단이 보수화됨 (JDG-08)"},
    ],
}


def _question_field(q: str) -> "str | None":
    """질문 원문 → 선지 유형. 키워드 미적중이면 None(분류 불가).

    적대적 검토 확정(H1): 예전엔 미적중을 'problem'으로 폴백해, LLM 컨설턴트형
    자유 질문(EXTRACT_SYSTEM이 명시적으로 지시하는 형식)이 전부 problem으로
    오분류됐다 — problem이 결정돼 있으면 전량 redundant 폐기, 아니면 필드중복으로
    1개만 생존(질문 학살). 분류 불가는 None으로 정직하게 반환한다."""
    if "가치" in q:
        return "value_prop"
    if "의향" in q:
        return "willingness"
    if "방식" in q or "해결하나요" in q:
        return "solution"
    if "팔고" in q or "타겟" in q:
        return "target"
    if "문제" in q or "해결하는" in q:
        return "problem"
    return None


def _mock_clarify(open_questions: list[str]) -> list[dict]:
    # 분류 불가 질문은 problem 선지를 폴백으로 (Mock 경로 질문은 전부 정준형이라 실사용 무영향)
    return [{"question": q,
             "why": "자료에서 이 항목을 확인하지 못했습니다.",
             "options": _MOCK_CLARIFY_OPTIONS[_question_field(q) or "problem"]}
            for q in open_questions]


def _clarify_questions(extractor, profile: Profile,
                       open_questions: list[str], full_text: str) -> list[dict]:
    """보강 질문마다 자료 단서 기반 4지선다 생성 — 실패 시 규칙 선지로 폴백."""
    from .. import progress
    if extractor is None:
        return _mock_clarify(open_questions)
    from .llm import sanitize
    from .prompts import CLARIFY_SCHEMA, CLARIFY_SYSTEM, clarify_user
    try:
        data = sanitize(extractor.extract_json(
            CLARIFY_SYSTEM, clarify_user(open_questions, full_text, profile),
            CLARIFY_SCHEMA))
        progress.log("보강", f"비즈니스 모델 파악 — {data['model_summary']}")
        by_q = {item["question"]: item for item in data["items"]}
        items = []
        for i, q in enumerate(open_questions):     # 질문 원문 계약 방어 (순서 폴백)
            item = by_q.get(q) or (data["items"][i] if i < len(data["items"]) else None)
            if item and len(item.get("options", [])) == 4:
                items.append({"question": q, "why": item["why"],
                              "options": item["options"]})
            else:
                items.append(_mock_clarify([q])[0])
        return items
    except Exception as exc:                        # 선지 실패가 온보딩을 막으면 안 됨
        progress.log("보강", f"선지 생성 실패 — 규칙 선지로 폴백 ({exc})")
        return _mock_clarify(open_questions)


# ── 프로필 계약 집행 (FORMALIZATION.md R1·R3) ──────────────────────────
# R1: stated 그라운딩 강등 — provenance=stated인데 원문에 근거가 없으면(환각 신호)
#     inferred(conf 0.5)로 강등한다. 값은 표준 한국어로 패러프레이즈되므로 임계는
#     보수적으로 잡는다(명백한 환각만): 3-gram 포함도 < 0.15.
# R3: 정규화 사영 — 국가·산업 표기를 결정적으로 정규화. industry_adjacent가
#     문자열 일치 기반이라 "SaaS" vs "saas" 표기 요동이 인접성 판정을 조용히 깨뜨린다.

_GROUND_DEMOTE_THRESHOLD = 0.15   # R1 — 이 미만이면 원문 근거 없음으로 판정

_COUNTRY_CANON = {
    "대한민국": "한국", "korea": "한국", "south korea": "한국", "republic of korea": "한국",
    "일본": "일본", "japan": "일본", "베트남": "베트남", "vietnam": "베트남",
    "미국": "미국", "usa": "미국", "united states": "미국", "us": "미국",
}


def _canon_country(value: str) -> str:
    return _COUNTRY_CANON.get((value or "").strip().lower(), (value or "").strip())


def _canon_industry(value: str) -> str:
    return (value or "").strip().lower().replace(" ", "_")


import re as _re

_HANGUL = _re.compile(r"[가-힣]")


def _hangul_ratio(s: str) -> float:
    chars = [c for c in s if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _HANGUL.match(c)) / len(chars)


def _script_verifiable(value: str, full_text: str) -> bool:
    """교차언어 방어 (적대적 검토 확정 H2) — 값과 원문의 문자 체계가 다르면
    3-gram 포함도는 정당한 stated도 0이 된다 (EXTRACT_SYSTEM이 '모든 값을
    한국어로 정규화'를 지시하므로 영어 원문 → 한국어 값이 정상 경로다).
    한글 위주 값 + 한글이 거의 없는 원문(또는 그 반대)이면 검증 불가로 판정."""
    v, s = _hangul_ratio(value), _hangul_ratio(full_text)
    if v >= 0.5 and s < 0.05:    # 한국어 값 ↔ 비한국어 원문
        return False
    if v < 0.05 and s >= 0.5:    # 반대 방향
        return False
    return True


def ground_profile(profile: Profile, full_text: str) -> dict:
    """프로필 계약 집행 (in-place). 반환: 정직 집계 {demoted, canonicalized, unverifiable}.

    R1은 stated 필드에만 적용한다 — inferred/ask는 이미 불확실 선언이 있다.
    강등은 폐기가 아니다: 값은 남기되 라벨을 정직하게 만든다(자료 근거 없음 = 추론).
    검증 불가(교차언어)와 검증 실패(환각)는 다르다 — 전자는 강등하지 않는다.
    """
    from .vision import grounding_score   # bbox와 동일한 3-gram 포함도 재사용
    tally = {"demoted": 0, "canonicalized": 0, "unverifiable": 0}

    for name in ("problem_solved", "solution", "target_customer"):
        f = getattr(profile, name)
        if f.provenance != Provenance.stated or not f.value:
            continue
        if not _script_verifiable(f.value, full_text):
            tally["unverifiable"] += 1
            continue   # 검증 불가 ≠ 검증 실패 — 라벨 유지
        g = grounding_score(f.value, full_text)
        if g is not None and g < _GROUND_DEMOTE_THRESHOLD:
            f.provenance = Provenance.inferred
            f.confidence = 0.5
            tally["demoted"] += 1
            progress.log("검증", f"⚠ R1 강등 — {name}='{f.value[:30]}…'가 stated로 "
                                 f"보고됐지만 원문 근거 없음(g={g:.2f}) → inferred(0.5)")

    canon_c = _canon_country(profile.basic.country)
    if canon_c != profile.basic.country:
        profile.basic.country = canon_c
        tally["canonicalized"] += 1
    canon_i = _canon_industry(profile.basic.industry)
    if canon_i != profile.basic.industry:
        profile.basic.industry = canon_i
        tally["canonicalized"] += 1
    return tally


# ── open_questions 5공리 코드 집행 (FORMALIZATION.md L1) ───────────────
# 5공리는 EXTRACT_SYSTEM 프롬프트에만 있고 집행기가 없었다(= 미집행 제약집합).
# 여기서 provenance를 근거로 결정적으로 집행한다 — 양 경로(mock/LLM)에 균일 적용.

_QUESTION_PRIORITY = {"problem": 0, "solution": 1, "target": 2,
                      "value_prop": 3, "willingness": 4}
_MAX_QUESTIONS = 5   # 공리 ⑤ 예산


def _field_underdetermined(field_type: str, profile: Profile) -> bool:
    """공리 ②판정가능성·③비중복성 — 이 질문의 대상 필드가 아직 미결정인가.
    미결정 = provenance ask, 또는 inferred이며 confidence<0.6, 또는 (VP·의향) 미충족."""
    def _prov(f) -> bool:
        if f.provenance == Provenance.stated:
            return False
        if f.provenance == Provenance.inferred and (f.confidence or 0) >= 0.6:
            return False
        return True   # ask, 또는 저확신 inferred
    if field_type == "problem":
        return _prov(profile.problem_solved)
    if field_type == "solution":
        return _prov(profile.solution)
    if field_type == "target":
        return _prov(profile.target_customer)
    if field_type == "value_prop":
        return not (profile.sell_value_props or profile.purchase_value_props)
    if field_type == "willingness":
        return profile.willingness_sell is None and profile.willingness_purchase is None
    return True   # 분류 불가 질문은 보존 (판단 유보)


def enforce_question_axioms(open_questions: list[str], profile: Profile
                            ) -> tuple[list[str], dict]:
    """open_questions에 5공리를 결정적으로 집행. (정제된 질문, 폐기 집계) 반환.

    ①원자성: 대상 필드별 1개(중복 필드 질문 제거)  ②판정가능성·③비중복성: 이미 결정된
    필드 질문 폐기  ④정보가치 정렬: 최소프로필 필드 우선  ⑤예산: ≤5.
    """
    rejected = {"redundant": 0, "duplicate_field": 0, "over_budget": 0}
    seen_fields: set[str] = set()
    kept: list[tuple[int, str]] = []   # (우선순위, 질문)
    for q in open_questions:
        ftype = _question_field(q)
        if ftype is None:
            # 분류 불가(컨설턴트형 자유 질문 등) — ②③·① 판정 없이 보존 (H1 수정).
            # 어느 필드를 겨냥하는지 코드가 모르면 폐기 근거도 없다. 예산(⑤)만 적용.
            kept.append((9, q))
            continue
        if not _field_underdetermined(ftype, profile):   # ②③ 이미 결정됨
            rejected["redundant"] += 1
            continue
        if ftype in seen_fields:                          # ① 같은 필드 중복
            rejected["duplicate_field"] += 1
            continue
        seen_fields.add(ftype)
        kept.append((_QUESTION_PRIORITY.get(ftype, 9), q))
    kept.sort(key=lambda x: x[0])                         # ④ 정보가치(최소프로필 우선)
    result = [q for _, q in kept]
    if len(result) > _MAX_QUESTIONS:                      # ⑤ 예산
        rejected["over_budget"] = len(result) - _MAX_QUESTIONS
        result = result[:_MAX_QUESTIONS]
    return result, rejected


# ── 공통 게이트·출력 ────────────────────────────────────────────────

def _check_minimum(profile: Profile, open_questions: list[str],
                   extractor=None, full_text: str = "") -> None:
    """최소 프로필 기준 (REP-06): 문제·솔루션·VP·타겟 각 1개 이상."""
    minimum_met = bool(
        profile.problem_solved.value
        and profile.solution.value
        and profile.target_customer.value
        and (profile.sell_value_props or profile.purchase_value_props)
    )
    if not minimum_met:
        clarify = _clarify_questions(extractor, profile, open_questions, full_text)
        raise ProfileBelowMinimum(open_questions, clarify)


def represent(req: RepresentRequest, settings: Settings | None = None
              ) -> RepresentResponse:
    from .. import progress
    settings = settings or get_settings()
    with progress.node("fetch", "자료 수집·청킹"):
        chunks, full_text = _ingest_assets(req, settings)
        progress.log("청킹", f"청킹 완료 — {len(chunks)}개 청크 (출처 라벨 유지)")

    extractor = get_extractor(settings)
    if extractor is not None:
        progress.log("추출", "다층 독해 시작 — 회사의 상(像) 구축")
        profile, open_questions, evidence = extract_profile(chunks, extractor)
        progress.log("추출", f"프로필 완성 — {profile.basic.name} / "
                             f"보강 질문 {len(open_questions)}건")
        engine_mode = "llm"
    else:
        with progress.node("mock.parse", "Mock 파서 (LLM 키 없음)"):
            progress.log("추출", "Mock 모드 — 구조화 텍스트 파서 사용")
            profile, open_questions = _mock_extract(full_text)
        evidence = None
        engine_mode = "mock"

    with progress.node("contract", "프로필 계약 집행 (R1·R3)"):
        tally = ground_profile(profile, full_text)
        progress.log("계약", f"stated 그라운딩 강등 {tally['demoted']}건 · "
                             f"교차언어 검증불가 {tally['unverifiable']}건(라벨 유지) · "
                             f"국가/산업 정규화 {tally['canonicalized']}건")

    with progress.node("axioms", "질문 공리 집행 (L1)"):
        n_before = len(open_questions)
        open_questions, rej = enforce_question_axioms(open_questions, profile)
        progress.log("공리", f"보강 질문 {n_before} → {len(open_questions)}건 "
                             f"(폐기: 이미결정 {rej['redundant']}·필드중복 "
                             f"{rej['duplicate_field']}·예산초과 {rej['over_budget']})")

    with progress.node("gate", "최소 프로필 게이트 (REP-06)"):
        _check_minimum(profile, open_questions, extractor, full_text)
        progress.log("게이트", "최소 프로필 기준(REP-06) 통과")

    from .. import audit
    with progress.node("audit", "감사 로그 (SYS-04)"):
        audit.record("represent", {
            "name": profile.basic.name, "engine_mode": engine_mode,
            "assets": [a.type.value for a in req.assets],
            "open_questions": open_questions,
            "portrait": profile.portrait.model_dump() if profile.portrait else None,
        })

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
