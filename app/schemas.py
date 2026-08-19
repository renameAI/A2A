"""데이터 계약 — 데이터스키마_명세.md · API_계약서.md 를 Pydantic으로 옮긴 것.

타입·enum의 단일 진실원천. 엔진 로직은 이 스키마 위에서만 동작한다.
"""
from enum import Enum
from typing import Literal, Optional

from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator)


# ── 공통 enum (스키마 §2~4) ──────────────────────────────────────────

class Provenance(str, Enum):          # 프로필 필드 출처 (기획서 5.4)
    stated = "stated"
    inferred = "inferred"
    ask = "ask"


class SourceTag(str, Enum):           # 데이터 출처 [관찰]/[시뮬]/[구성] (가이드 §6)
    observed = "observed"
    simulated = "simulated"
    constructed = "constructed"


class ValueProp(str, Enum):           # 고정 4종 (기획서 3.2)
    revenue_growth = "revenue_growth"
    cost_reduction = "cost_reduction"
    impact = "impact"
    problem_solving = "problem_solving"


class Willingness(str, Enum):
    very_high = "very_high"
    high = "high"
    medium = "medium"
    low = "low"
    very_low = "very_low"


class Dimension(str, Enum):
    """판단 축 — judge_cases/{buyer,seller}_ontology.yaml이 정규 소스.

    이전엔 자체 7차원(공통 5 + buy 2)이었다. 교체 근거: 그 축 집합엔 **실행·선결
    게이트(BB6)와 신뢰·착취(BB8)가 없어서**, "인증을 못 넘어 죽는 딜"이나 "상대가
    우리 IP를 빼가려 한다"를 표현할 언어 자체가 없었다. 실 B2B에서 결정적인 두
    가지이고, decision_gate의 terminate 규칙이 딛는 축이기도 하다.

    렌즈마다 축이 다르다(부분집합이 아니라 분리) — 사는 쪽과 파는 쪽은 같은 거래를
    다른 질문으로 본다.
    """
    # buy 렌즈 — "나는 이것으로 무엇을 하려는가"
    BB1_purpose_fit = "BB1_purpose_fit"                    # 목적 정합
    BB2_value_hierarchy = "BB2_value_hierarchy"            # 가치 서열
    BB3_substitute = "BB3_substitute"                      # 대체재 우위
    BB4_opportunity_cost = "BB4_opportunity_cost"          # 기회비용·다운사이드
    BB5_evidence = "BB5_evidence"                          # 실증 신호
    BB6_execution_gate = "BB6_execution_gate"              # 실행·선결 게이트
    BB7_timing = "BB7_timing"                              # 단계·타이밍
    BB8_trust = "BB8_trust"                                # 신뢰·진정성
    BB9_decision_structure = "BB9_decision_structure"      # 결정 구조
    BB10_contract_protection = "BB10_contract_protection"  # 계약·정보 보호
    # sell 렌즈 — "나는 무엇을 어떻게 납득시키는가"
    SB1_pitch_alignment = "SB1_pitch_alignment"
    SB2_value_positioning = "SB2_value_positioning"
    SB3_evidence_packaging = "SB3_evidence_packaging"
    SB4_substitute_defense = "SB4_substitute_defense"
    SB5_threshold_design = "SB5_threshold_design"
    SB6_execution_honesty = "SB6_execution_honesty"
    SB7_authenticity_structure = "SB7_authenticity_structure"
    SB8_champion_arming = "SB8_champion_arming"
    SB9_protection_screening = "SB9_protection_screening"
    SB10_strategic_relationship = "SB10_strategic_relationship"


# 판단 축은 렌즈와 무관하게 BB다. seller_ontology.yaml을 실제로 열어보면 SB축은
# 판정 루브릭이 아니라 **실행 플레이북**이다 — 축마다 readout(신호 판독)→moves(대응
# 행동)이고 verdict_rule이 아예 없다. "내가 잘 팔고 있나"의 자기점검이지 "이 딜이
# 되나"의 판정이 아니다. 딜 성사 여부는 결국 사는 쪽 논리가 정하므로 judge는 양쪽
# 렌즈 모두 BB로 판정하고, vantage는 '누가 self인가'만 바꾼다.
# SB축은 협상(negotiate)·작문(compose) 단계에서 쓴다.
JUDGE_DIMENSIONS = [d for d in Dimension if d.value.startswith("BB")]
SELLER_PLAYBOOK_AXES = [d for d in Dimension if d.value.startswith("SB")]


class AxisStatus(str, Enum):
    """축의 **검증 상태** — verdict(판정)와 별개다.

    이전 스키마엔 verdict만 있어 "확인했는데 위험하다"와 "확인을 못 했다"가 둘 다
    caution으로 뭉갰다. judge.py의 _apply_evidence_gate는 주석에 "정보 부재 ≠ unfit"
    이라 써놓고도 그걸 표현할 자리가 없었다 — 그 불일치를 이 필드가 해소한다.
    decision_gate의 'unknown ≥ 3 → hold' 규칙도 이 축 위에서만 성립한다.
    """
    unknown = "unknown"        # 검증 못 함 — 판단 재료 부족
    assumed = "assumed"        # 정황상 추정 — 확인 안 됨
    confirmed = "confirmed"    # 근거로 확인됨


class VerdictType(str, Enum):
    fit = "fit"
    caution = "caution"
    unfit = "unfit"
    na = "na"                  # 이 거래엔 해당 없는 축 (미검증과 구분)


# ── 기업 온톨로지 (engine/company_ontology.py) ──────────────────────

class OntologyAxis(BaseModel):
    """축 하나의 판독 — judge와 같은 문법으로 value와 status를 분리한다.

    분리하는 이유: "확인해보니 그런 접점이 없다"와 "확인을 못 했다"는 후속 행동이
    다르다. 전자는 그 경로를 접고, 후자는 더 알아본다.
    """
    value: str
    status: AxisStatus


class SignalCategory(str, Enum):
    """타이밍 신호의 유형 — PredictLeads 뉴스이벤트 택소노미(11그룹 37카테고리)의
    그룹 층을 차용했다. 카테고리는 업종 어휘가 아니라 **기업에 일어나는 사건의
    유형**이라 어느 업종에나 성립한다(축 원칙과 같은 이유로 하드코딩이 아니다).

    왜 필요한가: "이 회사가 맞는가"만 판정하고 "지금이 맞는가"를 안 봤다.
    Unify·Bombora·11x가 전부 이 축 위에 서 있다 — 확장·투자·채용 같은 사건이
    '구매 창'이 열리는 순간이다."""
    expansion = "expansion"          # 신규 거점·시설 확장·인력 증원
    investment = "investment"        # 자금 조달·상장·실적 발표
    leadership = "leadership"        # 경영진 영입·교체
    new_offering = "new_offering"    # 신제품·신규 사업·통합 출시
    partnership = "partnership"      # 신규 계약·제휴 (수용성의 직접 증거)
    procurement = "procurement"      # 조달·입찰·파트너 모집 공고 (가장 직접적)
    cost_cutting = "cost_cutting"    # 감원·거점 축소 — 부정 신호도 신호다
    other = "other"


class TimingSignal(BaseModel):
    """관측된 사건 하나. evidence는 자료에 있는 문장 그대로 — 인용 계약과 같은
    규율이다. 사건이 없으면 이 목록이 비는 것이 정직한 상태다."""
    category: SignalCategory
    evidence: str
    observed_at: str = ""            # 자료에 날짜가 있으면 그대로, 없으면 빈 문자열
    # 이 문장을 읽은 페이지. 메일이 "여기서 봤습니다"라고 링크로 밝히려면
    # 사건마다 출처가 있어야 한다 — 사이트 단위로는 상대가 확인할 수 없다.
    source_url: str = ""


class ContactPath(BaseModel):
    """공개된 접촉 경로 하나 — 자료에 실제로 나온 것만.

    role_hint는 MEDDIC의 역할 구분을 스니펫 수준으로 낮춘 것이다. 검색 결과만으로
    Economic Buyer를 특정하는 것은 불가능하므로(그건 리서치 단계 일), 여기서는
    자료에 드러난 부서·직함 힌트(商品部·구매팀·마케팅 담당)만 적는다."""
    channel: str                     # 예: 문의 폼 / 대표 메일 / 전화 / 파트너 모집 페이지
    value: str                       # URL·주소·번호 — 자료에 있는 그대로
    role_hint: str = ""              # 자료에 드러난 담당 부서·직함. 없으면 빈 문자열


class CompanyOntology(BaseModel):
    """후보 기업마다 남는 구조화된 판독.

    자유 문장(what/signal)과 달리 기계가 다시 쓸 수 있다 — 유사 기업 비교, 검색어
    파생, 재검색 확장이 전부 이 축 위에서 일어난다. 그래서 업종 어휘를 코드가
    알 필요가 없다(하드코딩 제거의 실제 수단).
    """
    axes: dict[str, OntologyAxis] = {}
    search_keywords: list[str] = []
    source_url: str = ""
    # 구조화 확장 (2026-08): 축이 '상태'라면 signals는 '사건', contacts는 '문'이다.
    signals: list[TimingSignal] = []
    contacts: list[ContactPath] = []
    # 상대가 거래 문의를 받는 언어(BCP-47). 메일을 어느 말로 쓸지 정한다 —
    # 지금까지는 지정이 없어 상대가 누구든 한국어로 나갔다.
    business_language: str = ""


class RiskType(str, Enum):            # 리스크 3분류 (가이드 §4)
    precondition = "precondition"
    profitability = "profitability"
    dismissed = "dismissed"


class DecisionType(str, Enum):
    recommend = "recommend"
    conditional = "conditional"
    hold = "hold"
    # terminate를 둘로 나눈다 — 사업적으로 완전히 다른 결말이라 한 라벨로 묶으면
    # 후속 행동이 갈리지 않는다. structural은 조건이 바뀌면 다시 볼 상대,
    # values는 다시 접촉하지 않을 상대다.
    terminate_structural = "terminate_structural"   # 구조 미달 — 관계는 보존
    terminate_values = "terminate_values"           # 착취·가치 충돌 — 관계 차단


TERMINATE_DECISIONS = (DecisionType.terminate_structural,
                       DecisionType.terminate_values)


class ConfidenceBand(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


# ── 프로필 (스키마 §3.1~3.3) ────────────────────────────────────────

class ProvField(BaseModel):
    """provenance 래퍼 — 추론 가능 필드는 단순 문자열 금지 (REP-03)."""
    value: str
    provenance: Provenance
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _inferred_needs_confidence(self):
        if self.provenance == Provenance.inferred and self.confidence is None:
            raise ValueError("provenance=inferred 필드에는 confidence가 필수 (REP-03)")
        return self


class BasicInfo(BaseModel):
    name: str
    country: str
    city: Optional[str] = None
    founded_year: Optional[int] = None
    industry: str

    @field_validator("founded_year")
    @classmethod
    def _plausible_year(cls, v):
        """명백한 오파싱은 코드가 막는다 — 실측: "4년간 모금액 23배 성장"에서
        founded_year=4가 나왔다. LLM에게 맡길 수 없는 종류의 검증이라(스키마상
        정수면 통과) 범위를 코드로 못박는다. 범위 밖은 버리고 None — 틀린 값을
        그럴듯하게 남기느니 '미상'이 정직하다."""
        if v is None:
            return None
        import datetime
        if not (1800 <= v <= datetime.date.today().year + 1):
            return None
        return v


class CompanyPortrait(BaseModel):
    """회사의 상(像) — 필드 나열이 아니라 입체적 이해 (v1.1 확장, 기획서 1.3·5.3).

    자료가 보여주는 '결과'에서 역추론한 '전략·의도·처지'. Represent가 세우고
    Judge·Compose가 판단·설득의 재료로 이어받는다. 전체가 추론이므로 [추론된 상]으로 취급.
    """
    identity: str          # 한 문장 정체성 — 추상화 레벨의 문제-솔루션-가치 삼각형
    business_model: str    # 누가, 언제, 무엇에, 어떤 구조로 돈을 내나
    edge: str              # 차별화 — 남이 쉽게 못 따라하는 것 (없으면 그렇다고)
    stage_narrative: str   # 지금 어느 단계이고, 무엇이 전략적으로 절실한가
    assets: str            # 가진 것 — 보완성 추론의 재료 (역량·자원·레퍼런스·네트워크)
    gaps: str              # 결핍 — 필요로 하는 것. 이 회사의 '사는 쪽 얼굴' 포함
    risk_signals: str      # 자료에서 읽히는 리스크 신호 (과장 관습·부재 신호 포함)


class Profile(BaseModel):
    basic: BasicInfo
    description: str
    problem_solved: ProvField        # 추상화 레벨 (REP-04)
    solution: ProvField
    target_customer: ProvField
    references: list[str] = []
    traction: Optional[str] = None
    sell_value_props: list[ValueProp] = []
    purchase_value_props: list[ValueProp] = []
    willingness_sell: Optional[Willingness] = None
    willingness_purchase: Optional[Willingness] = None
    portrait: Optional[CompanyPortrait] = None   # 회사의 상 — LLM 다층 독해 또는 mock 간이 합성. None은 구버전 저장 데이터뿐


class PrivateStateItem(BaseModel):
    key: str
    value: str
    source: SourceTag


class PrivateState(BaseModel):
    """두 렌즈 분기의 핵심 — 각자만 아는 패 (기획서 7.11)."""
    items: list[PrivateStateItem] = []
    willingness_note: Optional[str] = None


class Intent(BaseModel):
    value_props: list[ValueProp] = Field(min_length=1)
    target_region: Optional[str] = None
    target_type: Optional[str] = None
    proposal_type: Optional[str] = None
    price_range: Optional[str] = None
    notes: Optional[str] = None
    # 협상 준비 인터뷰(interview_agent) 연결 — 있으면 judge_user()가 판단 텍스트에 포함한다.
    differentiator: Optional[str] = None
    key_proof: Optional[str] = None
    entry_channel: Optional[str] = None
    # SaaS Lead Request 확장 (이슈 #6-D, 기획서 §6.5) — 전부 optional·기본값이라
    # 기존 요청·테스트 계약 불변.
    target_industry: Optional[str] = None
    target_company_size: Optional[str] = None
    target_contact_role: Optional[str] = None
    must_have_conditions: list[str] = []
    excluded_conditions: list[str] = []
    lead_count: Optional[int] = None
    outreach_language: Optional[str] = None
    call_to_action: Optional[str] = None
    # 발굴 목적 — 매출 파이프라인(revenue)과 PoC 파트너(poc)는 다른 질문이다.
    # 매출은 "살 여력이 있나", PoC는 "같이 실험할 구조·의지가 있나"(ITONICS
    # readiness·IBM EEIMM의 governance 축). 목적이 다르면 업종 제안·검색어·
    # 온톨로지 판독의 강조점이 달라진다.
    purpose: Literal["revenue", "poc"] = "revenue"


class SearchBrief(BaseModel):
    """검색 전 단계 산출물 (§6.3) — 사용자 확인·웹 쿼리 생성·재검색 기록에 사용."""
    deterministic_anchor: str
    synthesized_counterpart: str
    query_hypotheses: list[str]
    must_have: list[str]
    exclusions: list[str]



# ── /v1/represent (API §1) ──────────────────────────────────────────

class AssetType(str, Enum):
    ir_deck = "ir_deck"
    website = "website"
    article = "article"
    portfolio = "portfolio"
    text = "text"
    instagram = "instagram"   # v1.1 확장 (ING-01)


class Asset(BaseModel):
    type: AssetType
    content: str
    url: Optional[str] = None
    lang: Optional[str] = None


class DialogueTurn(BaseModel):
    q: str
    a: str


class LensHint(str, Enum):
    sell = "sell"
    buy = "buy"
    both = "both"


class RepresentRequest(BaseModel):
    client_request_id: Optional[str] = None
    assets: list[Asset] = Field(min_length=1)
    dialogue: list[DialogueTurn] = []
    lens_hint: Optional[LensHint] = None
    # SaaS 후보 모드 (이슈 #6-C, 기획서 §5.4): prospect는 직접 질문할 사용자가
    # 없으므로 최소 프로필 게이트를 강제하지 않고 부분 Profile + minimum_met=False를
    # 반환한다. 기본값 requester — 기존 호출·테스트 계약 불변.
    profile_purpose: Literal["requester", "prospect"] = "requester"


class OntologyAnchor(BaseModel):
    category: str
    value: str


class RepresentResponse(BaseModel):
    profile: Profile
    embedding: list[float]
    ontology_anchors: list[OntologyAnchor]
    minimum_met: bool                # REP-06
    open_questions: list[str]        # ask 항목 + 저확신 추론 (REP-07)
    engine_mode: str = "mock"        # "llm" | "mock" — 조용한 degrade 금지 (ING-05)
    evidence: Optional[dict[str, list[str]]] = None   # 필드 → 근거 청크 ID (ING-04)
    # 수집·채굴 투명성 — 크롤·파싱한 자료가 실제로 얼마나 쓰였는지 사용자가 본다.
    # sources: 자산별 {label, type, url, chars, preview}. mined: 하드 팩트 마이너 결과.
    sources: list[dict] = []
    mined: Optional[dict] = None


# ── /v1/retrieve (API §2) ───────────────────────────────────────────

class RetrieveDirection(str, Enum):
    sell_outreach = "sell_outreach"
    purchase_sourcing = "purchase_sourcing"


class PoolChoice(str, Enum):
    members = "members"
    external = "external"
    both = "both"


class PoolKind(str, Enum):
    members = "members"
    external = "external"


class RetrieveRequest(BaseModel):
    client_request_id: Optional[str] = None
    requester_profile: Profile
    intent: Intent
    direction: RetrieveDirection     # 검색 면 결정 (RET-02)
    pool: PoolChoice = PoolChoice.both
    k: int = Field(default=30, ge=1, le=50)
    compare_api: bool = False        # True면 API(K-EXAONE-236B)도 같이 채점(비교용, 느림)
    # True면 강한 후보(τ 이상)가 없을 때 422 대신 최고점 약한 후보를 정직하게 반환
    # (CandidateOut.weak=True로 표시, 강한 후보와 섞어서 조용히 승격하지 않음).
    allow_weak: bool = False


class CandidateOut(BaseModel):
    company_id: str
    profile_ref: str
    pool: PoolKind
    match_points: list[str]          # 보완성 근거 (RET-03)
    retrieval_score: float
    # 학습 스코어러(EXAONE 특수토큰 파인튜닝) 관련도 0~10 — 서버 연결 시에만 채워짐.
    # 랭킹 순서만 담당하고 게이트는 retrieval_score(휴리스틱)가 유지 (정직 폴백).
    learned_relatedness: Optional[float] = None
    # API(K-EXAONE-236B) 관련도 0~10 — 비교 모드에서만 채워짐. 랭킹엔 안 씀.
    api_relatedness: Optional[float] = None
    # True면 강한 후보 기준(τ) 미달 — allow_weak=True로 억지로 채운 후보임을 명시.
    weak: bool = False


class RetrieveResponse(BaseModel):
    candidates: list[CandidateOut]
    synthesized_counterpart: str     # 1단 합성 결과 (감사·디버그용)
    scorer_latency_ms: Optional[int] = None   # E9(1.2B) 배치 채점 지연
    api_latency_ms: Optional[int] = None       # API(K-EXAONE-236B) 채점 지연 (비교 모드)
    weak_fallback: bool = False       # True면 강한 후보 0건 — candidates가 전부 약한 후보(τ 미달)


# ── /v1/judge (API §3) ──────────────────────────────────────────────

class Vantage(str, Enum):
    seller = "seller"
    buyer = "buyer"


class Objective(str, Enum):
    exploration_budget = "exploration_budget"
    willingness_gate = "willingness_gate"


class JudgeRequest(BaseModel):
    # 메시지 본문 입력 금지 (JDG-07) — 정의되지 않은 필드는 전부 거부
    model_config = ConfigDict(extra="forbid")

    client_request_id: Optional[str] = None
    callback_url: Optional[str] = None
    vantage: Vantage
    objective: Objective
    self_profile: Profile
    self_private_state: PrivateState = PrivateState()
    counterpart_profile: Profile
    counterpart_private_state: Optional[PrivateState] = None
    intent: Intent


class CategoryJudgment(BaseModel):
    dimension: Dimension
    verdict: VerdictType
    rationale: str                   # '왜' 필수 — 평평한 체크리스트 금지 (JDG-02)
    # 검증 상태는 판정과 별개 (AxisStatus 참고). 기본 confirmed — 기존 산출물이
    # status를 안 주던 시절과 호환되지 않게 unknown을 기본값으로 두면, 축을 채운
    # 판단까지 전부 '미검증 3개 이상 → hold'로 쓸려 내려간다.
    status: AxisStatus = AxisStatus.confirmed
    # 게이트 플래그 — 특정 축에서만 의미가 있고, 결정을 단독으로 뒤집는다.
    # BB6: 선결 조건 구조적 미달 → terminate_structural
    # BB8: 상대의 착취 의도 감지 → terminate_values
    dealbreaker: bool = False
    exploitation_detected: bool = False


class Risk(BaseModel):
    type: RiskType
    description: str
    check_method: Optional[str] = None


class MatchSummary(BaseModel):
    problem_solution: str
    value_proposition: str
    reference: str                   # 없으면 "first_case" (JDG-10)


class JudgeResult(BaseModel):
    category_judgments: list[CategoryJudgment]
    risks: list[Risk]
    reasoning_moves: list[str]
    trajectory: str
    decision: DecisionType
    decision_rationale: str
    fit_reasons: list[str] = Field(min_length=1)   # JDG-01
    gap_factors: list[str] = []
    match_summary: MatchSummary
    deal_structure: Optional[str] = None
    confidence_band: Optional[ConfidenceBand] = None
    # 자기일관성 투표 (L2/L3) — k-표본 다수결 일치율과, 저합의 시 사람 라우팅 플래그.
    # judge_samples=1이면 sample_agreement=1.0, needs_human=False (단일 표본, 기존 동작).
    sample_agreement: Optional[float] = None
    needs_human: bool = False


# ── /v1/compose (API §4) ────────────────────────────────────────────

class ComposeMode(str, Enum):
    outreach = "outreach"
    recommendation_summary = "recommendation_summary"


class Lens(str, Enum):
    sell = "sell"
    buy = "buy"


class ComposeRequest(BaseModel):
    client_request_id: Optional[str] = None
    mode: ComposeMode
    judge_result: JudgeResult        # 근거 추적원 (CMP-02)
    self_profile: Profile
    counterpart_profile: Profile
    lens: Lens
    variants: int = Field(default=1, ge=1, le=5)
    tone: Optional[str] = None       # sell만 유효 (CMP-05)


class ClaimTrace(BaseModel):
    claim: str
    fit_reason_ref: str


class ComposedMessage(BaseModel):
    variant_label: str
    title: str
    body: str
    claim_trace: list[ClaimTrace]    # 주장→근거 매핑 (CMP-02)
    reference_used: str


class ComposeResponse(BaseModel):
    messages: list[ComposedMessage]
    send_blocked: bool = True        # 항상 true — 사람 승인 게이트 (CMP-06)


class CandidateInsight(BaseModel):
    """Retrieve와 Compose V2 사이의 근거 계층 (§7) — 적합·부적합을 결정하지 않는다.

    생성 원칙(§7.2): 잠재 니즈를 확정 사실처럼 표현하지 않고, 관측과 추론을
    분리하며, 근거 없는 수치·고객명을 생성하지 않는다. uncertainties는 이메일에서
    단정하면 안 되는 목록이다."""
    candidate_id: str
    observed_needs: list[str] = []
    need_evidence: list[str] = []       # 근거 문장 요지 (원문 관측분만)
    value_bridge: list[str] = []        # 후보의 문제 ↔ 요청 기업 솔루션 연결점
    personalization_hooks: list[str] = []
    uncertainties: list[str] = []
    source_urls: list[str] = []
    # 아웃리치 킷 — 누구에게(역할)·어디로(채널)·왜 지금(신호)·첫 문장 훅.
    # 심층 판독(사이트 본문)에서 읽은 접점·신호로 채운다. 근거 없으면 빈 문자열.
    outreach: dict = {}


class ComposeLeadRequest(BaseModel):
    """Judge 없는 메일 생성 입력 (§8.3) — 가짜 JudgeResult를 만들지 않는다(§8.1)."""
    requester_profile: Profile
    intent: Intent
    candidate_profile: Profile
    candidate_insight: CandidateInsight
    variants: int = Field(default=2, ge=1, le=3)
    tone: Optional[str] = None
    language: str = "ko"


class LeadEmailDraft(BaseModel):
    variant_label: str
    subject: str
    body: str
    # 한국어 대역 — 읽을 수 없는 메일을 승인할 수는 없다. 지정 언어가
    # 한국어면 subject·body와 같다.
    subject_ko: str = ""
    body_ko: str = ""
    call_to_action: str
    claim_trace: list[ClaimTrace] = []
    sources_used: list[str] = []
    warnings: list[str] = []            # 확인 안 돼 본문에서 뺀 정보 (§8.6)


class ComposeLeadResponse(BaseModel):
    drafts: list[LeadEmailDraft]
    send_blocked: bool = True           # 항상 true — 자동 발송 경로 없음 (§8.7)


# ── /v1/negotiate (API §5, 스키마 §5) ───────────────────────────────

class NegotiateRequest(BaseModel):
    client_request_id: Optional[str] = None
    seller_profile: Profile
    seller_private_state: PrivateState = PrivateState()
    buyer_profile: Profile
    buyer_private_state: PrivateState = PrivateState()
    intent: Intent
    max_rounds: int = Field(default=3, ge=1, le=5)   # NEG-05


class RoundResponse(str, Enum):
    accept = "accept"
    reject = "reject"
    counter = "counter"


class RejectionInfo(BaseModel):
    dimension: Dimension             # 사유의 차원 매핑 (NEG-02)
    reason: str
    recoverable: bool                # 풀림/안풀림 (NEG-03)


class KnobAdjustment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    knob: str
    from_: str = Field(alias="from")
    to: str


class NegotiationRound(BaseModel):
    round: int
    proposal: str
    response: RoundResponse
    rejection: Optional[RejectionInfo] = None
    knobs_adjusted: list[KnobAdjustment] = []        # 묶음 조정 (NEG-04)


class TerminationType(str, Enum):
    agreement = "agreement"
    breakdown = "breakdown"
    round_limit = "round_limit"


class NegotiationResult(BaseModel):
    rounds: list[NegotiationRound]
    termination: TerminationType     # NEG-05 3종
    rounds_used: int


# ── 비동기 job (API §0.2) ───────────────────────────────────────────

class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    error = "error"


class JobOut(BaseModel):
    job_id: str
    status: JobStatus
    result: Optional[dict] = None
    error: Optional[dict] = None
    logs: list[dict] = []            # 진행 과정 로그 + 노드 이벤트 (v1.1 확장)
    elapsed: float = 0.0             # 서버 기준 총 경과 초 — 실행 중 노드 시간 계산용


# ── 근거 시각화 (bbox) — IR덱 원문 위 빨간 박스 + 댓글 강제 (v1.2 확장) ──
# Simsa(cts_screening) 검토 SaaS의 box_2d 패턴을 재사용. 비전 모델(Gemini)이
# 있을 때만 켜진다 — 텍스트 추출(LLM_PROVIDER)과는 독립된 기능.

class BBox(BaseModel):
    """Gemini box_2d 그대로 — [ymin, xmin, ymax, xmax], 0~1000 정규화."""
    ymin: float
    xmin: float
    ymax: float
    xmax: float


class QuestionPin(BaseModel):
    """페이지 이미지 위에 꽂힌 엑사원 질문 하나. 질문은 추론 모델이 만들고,
    VLM은 이 질문을 페이지 어디에 붙일지 위치(box)만 찾는다 (역할 분리).
    핀은 검증기(vision.validate_box/grounding_score)를 통과한 것만 저장된다."""
    evidence_id: str
    question: str                    # 엑사원이 던진 질문 원문 (VLM이 만든 게 아님)
    asset_index: int                 # 몇 번째 자산(IR덱)인지
    page: int                        # 1-base 페이지 번호
    box: BBox
    quote: str                       # 페이지에서 이 박스가 감싸는 텍스트 (위치 근거)
    relevance: float = 0.0           # VLM 자기채점 관련도 r∈[0,1] (임계 0.5 미만은 폐기됨)
    grounding: Optional[float] = None  # 인용↔텍스트레이어 포함도 g∈[0,1]; None=검증불가(스캔 PDF)


class ThreadComment(BaseModel):
    author: str                      # "ai" | "human"
    text: str
    ts: str                          # ISO 문자열 (컨텍스트 유틸이 Date.now 금지라 문자열로만 취급)


class CommentThread(BaseModel):
    """시트 댓글처럼 질문 핀 하나에 매달리는 스레드. 엑사원 질문이 자동으로 첫 댓글이
    되고, 사람이 답하기 전까지 open으로 남아 매칭 진행을 막는다 (강제 응답)."""
    thread_id: str
    evidence_id: str
    status: str = "open"             # "open" | "resolved"
    comments: list[ThreadComment] = []


class ThreadReplyRequest(BaseModel):
    text: str


# ── Scout — 지식 분리 → 가설 → 웹 검색 숏리스트 (기획서 §1 가설수립·JDG-09) ──
# Retrieve가 '이미 아는 풀' 안에서 찾는다면, Scout는 풀 밖(웹)에서 후보를 충원한다
# (기획서 6.4 '외부 풀 충원' 트랙의 v0). exploit=명백지 기반 정석 가설,
# explore=암묵지(회사의 상 — 역추론된 결핍·전략) 기반 모험 가설.

class KnowledgeKind(str, Enum):
    explicit = "explicit"    # 명백지 — 자료에 명시(stated)·자가신고
    tacit = "tacit"          # 암묵지 — 역추론(inferred·portrait)


class KnowledgeItem(BaseModel):
    kind: KnowledgeKind
    field: str               # 출처 필드 (예: "problem_solved", "portrait.gaps")
    content: str
    confidence: Optional[float] = None   # tacit만 (inferred 확신도)


class HypothesisTrack(str, Enum):
    exploit = "exploit"      # 정석 — 명백지가 가리키는 검증된 파트너 패턴
    explore = "explore"      # 모험 — 암묵지에서 도출한 비자명 파트너 가설


class PartnerHypothesis(BaseModel):
    track: HypothesisTrack
    hypothesis: str          # 어떤 파트너가 왜 맞는가 (한 문장)
    grounded_in: list[str]   # 근거 지식 field 목록 — 가설의 출처 추적
    search_query: str        # 웹 검색어
    partner_type: str        # 기대 파트너 유형 (예: "동남아 중가 호텔 운영사")


class ScoutCandidate(BaseModel):
    track: HypothesisTrack
    hypothesis: str          # 이 후보를 찾게 한 가설 원문
    title: str
    url: str
    snippet: str
    domain: str
    relevance: float         # 결정적 스코어 (가설·검색어 ↔ 제목+스니펫 bigram overlap)


class ScoutCompany(BaseModel):
    """웹 히트에서 LLM이 발굴한 기업 후보 — 기사도 '단서'로 소비하되, 기업명은
    히트 원문에 실재해야 한다(코드가 대조 검증). 다이브인그룹 데모의 후보 카드처럼
    '기업'이 보이게 하는 층."""
    name: str
    track: HypothesisTrack
    hypothesis: str          # 이 기업을 찾게 한 가설 원문
    summary: str             # 히트 원문 범위 안의 한 줄 소개
    country: Optional[str] = None
    source_url: str          # 근거 히트 (실제 검색 결과 URL)
    source_domain: str
    source_title: str
    # 이 기업을 발굴하게 한 검색 히트의 원문 조각. 예전엔 회사명만 남기고 버렸는데,
    # 그 결과 judge가 (이름·국가·한 줄 요약)만 보고 10축을 판정해야 해서 축 대부분이
    # unknown으로 떨어졌다. 이미 검색해서 가진 자료라 추가 비용 0이다.
    evidence: str = ""


class ScoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: Optional[str] = None
    profile: Profile
    intent: Intent
    k: int = Field(default=6, ge=1, le=20)
    # explore 비율 — JDG-09 '파라미터로 조정 가능'(초기값 TBD·박사 몫이라 기본 1/3)
    explore_ratio: float = Field(default=0.34, ge=0.0, le=1.0)


class ScoutResponse(BaseModel):
    knowledge: list[KnowledgeItem]
    hypotheses: list[PartnerHypothesis]
    shortlist: list[ScoutCandidate]
    companies: list[ScoutCompany] = []   # LLM 기업 발굴 (mock 경로에선 빈 리스트 — 정직)
    engine_mode: str                 # 가설 생성 경로 (llm | mock)
    web_search_used: bool            # false면 검색 실패/차단 — 정직 표기
