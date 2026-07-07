"""데이터 계약 — 데이터스키마_명세.md · API_계약서.md 를 Pydantic으로 옮긴 것.

타입·enum의 단일 진실원천. 엔진 로직은 이 스키마 위에서만 동작한다.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class Dimension(str, Enum):           # 온톨로지 판단 차원 (공통 5 + buy 2)
    industry_fit = "industry_fit"
    purpose_alignment = "purpose_alignment"
    resource_complementarity = "resource_complementarity"
    stage_compatibility = "stage_compatibility"
    demonstrability = "demonstrability"
    substitute_comparison = "substitute_comparison"   # buy 전용
    opportunity_cost = "opportunity_cost"             # buy 전용


COMMON_DIMENSIONS = [
    Dimension.industry_fit,
    Dimension.purpose_alignment,
    Dimension.resource_complementarity,
    Dimension.stage_compatibility,
    Dimension.demonstrability,
]
BUY_ONLY_DIMENSIONS = [Dimension.substitute_comparison, Dimension.opportunity_cost]


class VerdictType(str, Enum):
    fit = "fit"
    caution = "caution"
    unfit = "unfit"


class RiskType(str, Enum):            # 리스크 3분류 (가이드 §4)
    precondition = "precondition"
    profitability = "profitability"
    dismissed = "dismissed"


class DecisionType(str, Enum):
    recommend = "recommend"
    conditional = "conditional"
    hold = "hold"
    terminate = "terminate"


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
    portrait: Optional[CompanyPortrait] = None   # 회사의 상 (LLM 경로에서 생성)


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


class CandidateOut(BaseModel):
    company_id: str
    profile_ref: str
    pool: PoolKind
    match_points: list[str]          # 보완성 근거 (RET-03)
    retrieval_score: float


class RetrieveResponse(BaseModel):
    candidates: list[CandidateOut]
    synthesized_counterpart: str     # 1단 합성 결과 (감사·디버그용)


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
    VLM은 이 질문을 페이지 어디에 붙일지 위치(box)만 찾는다 (역할 분리)."""
    evidence_id: str
    question: str                    # 엑사원이 던진 질문 원문 (VLM이 만든 게 아님)
    asset_index: int                 # 몇 번째 자산(IR덱)인지
    page: int                        # 1-base 페이지 번호
    box: BBox
    quote: str                       # 페이지에서 이 박스가 감싸는 텍스트 (위치 근거)


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
