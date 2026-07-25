# -*- coding: utf-8 -*-
"""독립 실행형 Strategy-first 글로벌 B2B 인터뷰 에이전트.

이 파일은 기존 interview_agent.py 계열을 import하지 않는다. Google Grounding으로
사전 리서치 문서를 만들고, Friendli의 EXAONE으로 답변을 구조화하며, 실제 대표에게
10개 핵심 의사결정 질문과 최종 확인을 진행한다. 불충분한 답변은 재확인할 수 있고
40문항은 비정상적인 반복만 막는 안전 상한이다. 핵심 결과는 최대 두 개의 실행 트랙으로 저장한다.

필요 패키지:
    pip install python-dotenv openai google-genai jsonschema

공유 폴더에 함께 둘 파일:
    .env
    schema_strategy/11_strategy_interview_ontology.schema.json
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
from openai import OpenAI


# ============================================================================
# 사용자 설정
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

# Friendli Dedicated Endpoint 설정은 .env에서만 읽는다.
FRIENDLI_TOKEN = os.getenv("FRIENDLI_TOKEN", "")
FRIENDLI_BASE_URL = os.getenv("FRIENDLI_BASE_URL", "")
EXAONE_ENDPOINT_ID = os.getenv("EXAONE_ENDPOINT_ID", "")

# Gemini Google Grounding 설정이다. 검색 실패를 EXAONE 내부지식으로 대체하지 않는다.
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
SEARCH_PROVIDER = "google"  # "google" 또는 "none"
GEMINI_RESEARCH_MODEL = "gemini-3.1-flash-lite"

# 실제 사용 시 여기에서 회사명과 선택적 검색 힌트를 바꾼다.
TARGET_COMPANY = "다이브인"
COMPANY_HINTS = ""

# human은 콘솔 입력, ai는 EXAONE이 가상 대표를 연기한다.
CEO_MODE = "human"

# 이미 대표에게 직접 확인한 값을 점 경로로 넣을 수 있다.
# 예: {"offer.chosen_solution": "ESG 캠페인"}
USER_SEED: Dict[str, Any] = {}

# 보통 10~12문항으로 끝내되 복잡한 사례는 더 물을 수 있다.
# 15문항부터 피로도를 안내하고, 비정상적인 무한 반복만 40문항에서 차단한다.
SOFT_QUESTION_WARNING = 15
MAX_TOTAL_QUESTIONS = 40
MAX_CONTENT_QUESTIONS = 38
MAX_FINAL_REVIEW_ATTEMPTS = 2
MAX_SEMANTIC_ATTEMPTS = 2
MAX_TECHNICAL_RETRIES = 1

# API 및 출력 설정이다.
ENABLE_THINKING = False
PARSE_REASONING = True
CEO_TEMP = 0.7
AGENT_VERSION = "strategy_first_v5"

SCHEMA_PATH = BASE_DIR / "schema_strategy" / "11_strategy_interview_ontology.schema.json"
OUT_DIR = BASE_DIR / "runs" / "strategy_first"


# ============================================================================
# 내부 enum과 사람용 표시값
# ============================================================================

GOAL_TOKENS = {
    "poc", "paid_sales", "paid_project", "licensing", "strategic_partnership",
    "joint_development", "cmo_mass_production", "odm_development",
    "distribution_operation_partner", "public_oda_demonstration", "broad_exploration",
}
CHANNEL_TOKENS = {
    "direct_end_customer", "partner_distributor", "agency", "consortium_cluster",
    "university_research_institute", "government_public_oda",
}
CTA_TOKENS = {
    "meeting_15_30min", "sample_test_offer", "small_poc_offer",
    "proposal_review", "demo_video_review",
}
TRANSACTION_UNIT_TOKENS = {
    "software", "hardware", "data", "service", "licensing", "project",
}
KEY_BENEFIT_TOKENS = {
    "problem_solving", "revenue_growth", "cost_reduction", "risk_reduction",
    "regulatory_compliance", "social_impact",
}
SAMPLE_TOKENS = {"unknown", "immediate", "after_meeting", "after_nda", "none"}

DISPLAY_LABELS = {
    "poc": "PoC",
    "paid_sales": "유료 판매",
    "paid_project": "유료 프로젝트",
    "licensing": "라이선싱",
    "strategic_partnership": "전략적 제휴",
    "joint_development": "공동 개발",
    "cmo_mass_production": "CMO 대량생산",
    "odm_development": "ODM 개발",
    "distribution_operation_partner": "유통·운영 파트너",
    "public_oda_demonstration": "공공·ODA 실증",
    "broad_exploration": "폭넓은 기회 탐색",
    "direct_end_customer": "최종 고객 직접 접근",
    "partner_distributor": "현지 유통·사업 파트너",
    "agency": "현지 에이전시",
    "consortium_cluster": "컨소시엄·클러스터",
    "university_research_institute": "대학·연구기관",
    "government_public_oda": "정부·공공·ODA 경로",
    "meeting_15_30min": "소개 미팅",
    "sample_test_offer": "샘플 테스트",
    "small_poc_offer": "소규모 PoC",
    "proposal_review": "제안서 검토",
    "demo_video_review": "데모·영상 검토",
    "software": "소프트웨어",
    "hardware": "제품·장비",
    "data": "데이터",
    "service": "서비스",
    "project": "프로젝트",
    "problem_solving": "핵심 문제 해결",
    "revenue_growth": "매출·제품 차별화",
    "cost_reduction": "비용 절감",
    "risk_reduction": "위험 감소",
    "regulatory_compliance": "규제·인증 대응",
    "social_impact": "사회적 가치",
    "immediate": "즉시 제공 가능",
    "after_meeting": "미팅 후 제공 가능",
    "after_nda": "NDA 후 제공 가능",
    "none": "현재 제공 자료 없음",
    "unknown": "미정",
}

GOAL_PAIRS = [
    (("cmo", "대량생산"), "cmo_mass_production"),
    (("odm",), "odm_development"),
    (("라이선", "licens", "로열티"), "licensing"),
    (("공동개발", "공동 개발", "joint"), "joint_development"),
    (("유통", "운영 파트너", "distribut", "리셀"), "distribution_operation_partner"),
    (("공공", "oda", "정부"), "public_oda_demonstration"),
    (("전략적", "strategic"), "strategic_partnership"),
    (("poc", "실증", "파일럿", "pilot"), "poc"),
    (("유료 프로젝트",), "paid_project"),
    (("판매", "공급", "계약", "sales"), "paid_sales"),
]
CHANNEL_PAIRS = [
    (("에이전시", "agency", "대행사"), "agency"),
    (("컨소시엄", "클러스터", "consortium", "cluster"), "consortium_cluster"),
    (("대학", "연구기관", "연구소"), "university_research_institute"),
    (("정부", "공공", "oda"), "government_public_oda"),
    (("유통", "총판", "파트너", "distributor"), "partner_distributor"),
    (("직접", "최종 고객", "end customer"), "direct_end_customer"),
]
CTA_PAIRS = [
    (("영상", "데모"), "demo_video_review"),
    (("샘플", "테스트"), "sample_test_offer"),
    (("poc", "실증", "파일럿"), "small_poc_offer"),
    (("제안서", "제안 검토"), "proposal_review"),
    (("미팅", "회의", "소개", "통화", "콜"), "meeting_15_30min"),
]
UNIT_PAIRS = [
    (("소프트웨어", "saas"), "software"),
    (("하드웨어", "장비", "제품"), "hardware"),
    (("데이터",), "data"),
    (("라이선",), "licensing"),
    (("프로젝트", "캠페인"), "project"),
    (("서비스", "컨설팅"), "service"),
]
BENEFIT_PAIRS = [
    (("매출", "제품 차별", "브랜드 가치", "수익"), "revenue_growth"),
    (("비용", "원가", "절감"), "cost_reduction"),
    (("위험", "리스크", "안전", "손실"), "risk_reduction"),
    (("규제", "인증", "컴플라이언스"), "regulatory_compliance"),
    (("사회적", "임팩트", "esg", "복지", "환경"), "social_impact"),
    (("문제", "해결", "고도화", "개선", "효율"), "problem_solving"),
]

EXPLICIT_UNKNOWN_RE = re.compile(
    r"^(?:모르겠(?:습니다|어요)?|모름|아직\s*모름|미정|정하지\s*않았(?:습니다|어요)?|"
    r"없(?:습니다|어요|음)|현재는\s*없(?:습니다|어요)?)$",
    re.IGNORECASE,
)
CONFIRM_RE = re.compile(r"^(?:맞습니다|맞아요|네|예|확인|동의합니다|그대로입니다)[.!\s]*$", re.IGNORECASE)
END_RE = re.compile(r"^/end$", re.IGNORECASE)
SENTENCE_END_RE = re.compile(r"(?:합니다|입니다|됩니다|하려고\s*합니다|하고\s*싶습니다)[.!?]?$")
PROOF_RE = re.compile(
    r"(?:프로젝트|고객|협업|사례|계약|매출|수출|인증|특허|수상|테스트|검증|PoC|"
    r"전후|before|after|샘플|데모|성적서|보고서|레퍼런스|\d)",
    re.IGNORECASE,
)
NON_ANSWER_FEEDBACK_RE = re.compile(
    r"(?:왜\s*(?:또|다시|차이점|확정|이렇게)|있다니까|말했잖|"
    r"정신\s*없|한꺼번에|우르르|같은\s*질문|답했는데|무슨\s*말)",
    re.IGNORECASE,
)


# ============================================================================
# EXAONE 호출 및 JSON 파싱
# ============================================================================

def make_client() -> OpenAI:
    """환경변수로 Friendli OpenAI 호환 클라이언트를 만든다."""
    if not FRIENDLI_TOKEN or not FRIENDLI_BASE_URL or not EXAONE_ENDPOINT_ID:
        raise SystemExit(
            "[설정 필요] .env에 FRIENDLI_TOKEN, FRIENDLI_BASE_URL, "
            "EXAONE_ENDPOINT_ID를 설정하세요."
        )
    return OpenAI(api_key=FRIENDLI_TOKEN, base_url=FRIENDLI_BASE_URL)


def chat(
    client: OpenAI,
    system: str,
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 1800,
    enable_thinking: Optional[bool] = None,
) -> str:
    """Friendli chat/completions를 호출하고 기술 오류만 최대 세 번 재시도한다."""
    thinking = ENABLE_THINKING if enable_thinking is None else enable_thinking
    extra_body = {
        "chat_template_kwargs": {"enable_thinking": thinking},
        "parse_reasoning": PARSE_REASONING,
    }
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=EXAONE_ENDPOINT_ID,
                messages=[{"role": "system", "content": system}] + messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:  # API 네트워크·서버 오류만 재시도한다.
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"K-EXAONE 호출 실패: {last_error}")


def parse_json_object(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """코드펜스 또는 설명이 섞인 응답에서 JSON 객체 하나를 안전하게 읽는다."""
    if not text or not text.strip():
        return None, "empty_response"
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end < start:
            return None, "json_not_found"
        candidate = stripped[start : end + 1]
    for raw in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
        try:
            parsed = json.loads(raw)
            return (parsed, "parsed") if isinstance(parsed, dict) else (None, "not_object")
        except json.JSONDecodeError:
            continue
    return None, "parse_failed"


# ============================================================================
# 전략 상태와 점 경로 유틸
# ============================================================================

def empty_track(track_id: str, priority: int, *, active: bool) -> Dict[str, Any]:
    """스키마의 모든 트랙 필드를 명시적으로 초기화한다."""
    return {
        "track_id": track_id,
        "priority": priority,
        "label": None,
        "active": active,
        "market": {
            "country_or_region": None,
            "rationale": None,
            "market_readiness": None,
            "regulation_or_localization": None,
        },
        "target": {
            "organization_type": None,
            "buying_situation": None,
            "purchase_trigger": None,
            "urgency_signal": None,
            "exclusion_criteria": None,
        },
        "recipient": {
            "first_reviewer": None,
            "primary_beneficiary": None,
            "technical_reviewer": None,
            "budget_owner": None,
            "internal_forward_to": None,
        },
        "purchase_logic": {
            "pain_point": None,
            "current_alternative": None,
            "loss_or_risk": None,
            "urgency_trigger": None,
            "purchase_reason": None,
            "key_benefit_priority": [],
            "why_budget": None,
        },
        "proof_strategy": {
            "primary_proof": None,
            "proof_type": None,
            "source": None,
            "verification_status": "unknown",
            "measurement_condition": None,
            "sample_or_demo_availability": "unknown",
            "disclosable_before_nda": None,
            "missing_proof_plan": None,
        },
        "entry_strategy": {
            "primary_channel": None,
            "alternative_channel": None,
            "partner_role": None,
            "partner_incentive": None,
            "responsibility_split": None,
        },
        "cta_strategy": {
            "primary_cta": None,
            "conversion_flow": [],
            "core_message": None,
            "acceptance_criteria": None,
            "next_step_requirements": None,
        },
        "execution_constraints": {
            "regulation_certification": None,
            "cost_impact": None,
            "supply_scale_up": None,
            "localization_support": None,
            "disclosure_nda_policy": None,
            "highest_risk": None,
        },
        "status": "unknown",
    }


def init_state(company: str = TARGET_COMPANY) -> Dict[str, Any]:
    """새 인터뷰 상태를 만들며 주력 트랙 A만 활성화한다."""
    return {
        "schema_version": "11_strategy_interview_v4",
        "company": company,
        "offer": {
            "chosen_solution": None,
            "differentiator": None,
            "transaction_unit": None,
            "customer_use_context": None,
            "primary_customer_change": None,
            "core_feature": None,
            "maturity_stage": None,
            "status": "unknown",
        },
        "transaction_strategy": {
            "primary_goal": "poc",
            "goal_sequence": ["poc"],
            "current_stage": None,
            "current_bottleneck": None,
            "realistic_first_transaction": None,
            "success_criteria": None,
            "status": "unknown",
        },
        "strategy_tracks": [empty_track("A", 1, active=True)],
        "future_candidates": [],
        "shared_proofs": [],
        "field_meta": {
            "transaction_strategy.primary_goal": {
                "origin": "program_context",
                "confidence": "high",
                "source_answer_id": "A-0001",
                "verification_status": "not_required",
                "stale_reason": None,
            },
        },
        "answer_records": [{
            "answer_id": "A-0001",
            "question_id": None,
            "target_paths": ["transaction_strategy.primary_goal"],
            "value": "poc",
            "origin": "program_context",
            "source_type": "program_default",
            "verification_status": "not_required",
            "is_canonical": True,
        }],
        "question_states": {},
        "interview_state": {
            "question_count": 0,
            "content_question_count": 0,
            "manually_ended": False,
            "blocking_conflicts": [],
            "stale_paths": [],
            "explicit_unknown_paths": [],
            "partial_paths": [],
            "candidate_attempts": {},
            "asked_question_fingerprints": [],
            "extraction_events": [],
            "soft_limit_warning_shown": False,
        },
        "completion": {
            "interview_finished": False,
            "session_closed": False,
            "final_confirmed": False,
            "question_limit_reached": False,
            "clarification_pending": False,
            "anchor_complete": False,
            "strategy_ready": False,
            "missing_anchor_paths": [],
            "unresolved_execution_paths": [],
            "stale_paths": [],
            "blocking_conflicts": [],
            "followup_questions": [],
        },
    }


def ensure_track(state: Dict[str, Any], track_id: str) -> Dict[str, Any]:
    """요청한 트랙을 반환하고 B가 필요하면 최대 두 개 제약 안에서 생성한다."""
    for track in state["strategy_tracks"]:
        if track["track_id"] == track_id:
            if track_id == "B":
                track["active"] = True
            return track
    if track_id != "B" or len(state["strategy_tracks"]) >= 2:
        raise ValueError(f"지원하지 않는 전략 트랙: {track_id}")
    track = empty_track("B", 2, active=True)
    state["strategy_tracks"].append(track)
    return track


def get_track(state: Dict[str, Any], track_id: str) -> Optional[Dict[str, Any]]:
    """트랙을 생성하지 않고 조회한다."""
    return next((t for t in state["strategy_tracks"] if t["track_id"] == track_id), None)


def get_path(state: Dict[str, Any], path: str) -> Any:
    """`strategy_tracks.A.market...` 형태를 포함한 점 경로 값을 읽는다."""
    parts = path.split(".")
    if parts[:1] == ["strategy_tracks"]:
        if len(parts) < 3:
            return None
        current: Any = get_track(state, parts[1])
        parts = parts[2:]
    else:
        current = state
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_path_raw(state: Dict[str, Any], path: str, value: Any) -> None:
    """검증된 점 경로에 값을 쓴다."""
    parts = path.split(".")
    if parts[:1] == ["strategy_tracks"]:
        if len(parts) < 4 or parts[1] not in {"A", "B"}:
            raise KeyError(path)
        current: Any = ensure_track(state, parts[1])
        parts = parts[2:]
    else:
        current = state
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        raise KeyError(path)
    current[parts[-1]] = value


GLOBAL_VALUE_PATHS = {
    "offer.chosen_solution",
    "offer.differentiator",
    "offer.transaction_unit",
    "offer.customer_use_context",
    "offer.primary_customer_change",
    "offer.core_feature",
    "offer.maturity_stage",
    "transaction_strategy.primary_goal",
    "transaction_strategy.goal_sequence",
    "transaction_strategy.current_stage",
    "transaction_strategy.current_bottleneck",
    "transaction_strategy.realistic_first_transaction",
    "transaction_strategy.success_criteria",
}

TRACK_VALUE_SUFFIXES = {
    "label",
    "market.country_or_region",
    "market.rationale",
    "market.market_readiness",
    "market.regulation_or_localization",
    "target.organization_type",
    "target.buying_situation",
    "target.purchase_trigger",
    "target.urgency_signal",
    "target.exclusion_criteria",
    "recipient.first_reviewer",
    "recipient.primary_beneficiary",
    "recipient.technical_reviewer",
    "recipient.budget_owner",
    "recipient.internal_forward_to",
    "purchase_logic.pain_point",
    "purchase_logic.current_alternative",
    "purchase_logic.loss_or_risk",
    "purchase_logic.urgency_trigger",
    "purchase_logic.purchase_reason",
    "purchase_logic.key_benefit_priority",
    "purchase_logic.why_budget",
    "proof_strategy.primary_proof",
    "proof_strategy.proof_type",
    "proof_strategy.source",
    "proof_strategy.verification_status",
    "proof_strategy.measurement_condition",
    "proof_strategy.sample_or_demo_availability",
    "proof_strategy.disclosable_before_nda",
    "proof_strategy.missing_proof_plan",
    "entry_strategy.primary_channel",
    "entry_strategy.alternative_channel",
    "entry_strategy.partner_role",
    "entry_strategy.partner_incentive",
    "entry_strategy.responsibility_split",
    "cta_strategy.primary_cta",
    "cta_strategy.conversion_flow",
    "cta_strategy.core_message",
    "cta_strategy.acceptance_criteria",
    "cta_strategy.next_step_requirements",
    "execution_constraints.regulation_certification",
    "execution_constraints.cost_impact",
    "execution_constraints.supply_scale_up",
    "execution_constraints.localization_support",
    "execution_constraints.disclosure_nda_policy",
    "execution_constraints.highest_risk",
}


def is_allowed_value_path(path: str) -> bool:
    """모델이 임의 키를 만들지 못하도록 허용된 전략값 경로만 통과시킨다."""
    if path in GLOBAL_VALUE_PATHS:
        return True
    match = re.fullmatch(r"strategy_tracks\.([AB])\.(.+)", path)
    return bool(match and match.group(2) in TRACK_VALUE_SUFFIXES)


def _has_value(value: Any) -> bool:
    """None, 빈 문자열, 빈 배열을 미확인 값으로 판정한다."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _compare_text(value: Any) -> str:
    """원문 근거 비교를 위해 공백과 문장부호를 제거한다."""
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).lower()


def _as_short_text(value: Any, max_length: int = 100) -> Optional[str]:
    """표시값을 한 줄의 제한된 길이로 정리한다."""
    if isinstance(value, list):
        value = " / ".join(str(item) for item in value if str(item).strip())
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n'\"‘’“”")
    return text if 1 < len(text) <= max_length else None


def _unicode_is_sane(value: Any) -> bool:
    """CP949/Latin-1 오해석으로 생기는 대표적인 깨진 한글을 차단한다."""
    text = str(value or "")
    suspicious = sum(1 for char in text if 0x00A1 <= ord(char) <= 0x00FF)
    return suspicious == 0 and not re.search(r"(?:¹Ì|±â|°ø|¸¶|¿¡|ÀÇ|ºÎ¼­)", text)


def _match_token(value: Any, tokens: set[str], pairs: Sequence[Tuple[Tuple[str, ...], str]]) -> Optional[str]:
    """내부 enum 또는 한국어 자연어를 허용된 토큰 하나로 바꾼다."""
    text = str(value or "").strip().lower()
    if text in tokens:
        return text
    for keywords, token in pairs:
        if any(keyword.lower() in text for keyword in keywords):
            return token
    return None


def _as_string_list(value: Any, *, max_items: int = 6, max_length: int = 120) -> List[str]:
    """문자열 또는 배열을 중복 없는 짧은 문자열 배열로 정규화한다."""
    raw = value if isinstance(value, list) else re.split(
        r"\s*(?:→|->|,|;|\n)\s*|\s+및\s+|\s*(?:하고|한\s*뒤|후에)\s+|\s+(?:그다음|이후)\s+",
        str(value or ""),
    )
    result: List[str] = []
    for item in raw:
        text = _as_short_text(item, max_length)
        if text and text not in result:
            result.append(text)
        if len(result) >= max_items:
            break
    return result


def normalize_path_value(path: str, value: Any) -> Any:
    """경로별 타입과 의미에 맞게 API 또는 로컬 추출값을 정규화한다."""
    if path == "offer.transaction_unit":
        return _match_token(value, TRANSACTION_UNIT_TOKENS, UNIT_PAIRS)
    if path == "offer.maturity_stage":
        text = str(value or "").strip().lower()
        allowed = {"sample_stage", "poc_stage", "sellable", "scalable", "unknown"}
        if text in allowed:
            return text
        if any(word in text for word in ("대량", "확장 가능", "스케일", "양산")):
            return "scalable"
        if any(word in text for word in ("판매", "상용", "납품", "운영", "매출", "파트너십")):
            return "sellable"
        if any(word in text for word in ("poc", "실증", "파일럿")):
            return "poc_stage"
        if any(word in text for word in ("샘플", "시제품", "프로토타입", "초기")):
            return "sample_stage"
        return "unknown"
    if path == "transaction_strategy.primary_goal":
        return _match_token(value, GOAL_TOKENS, GOAL_PAIRS)
    if path == "transaction_strategy.goal_sequence":
        items = value if isinstance(value, list) else [value]
        result = [_match_token(item, GOAL_TOKENS, GOAL_PAIRS) for item in items]
        return list(dict.fromkeys(item for item in result if item))
    if path.endswith("entry_strategy.primary_channel") or path.endswith("entry_strategy.alternative_channel"):
        return _match_token(value, CHANNEL_TOKENS, CHANNEL_PAIRS)
    if path.endswith("cta_strategy.primary_cta"):
        return _match_token(value, CTA_TOKENS, CTA_PAIRS)
    if path.endswith("purchase_logic.key_benefit_priority"):
        items = value if isinstance(value, list) else [value]
        result = [_match_token(item, KEY_BENEFIT_TOKENS, BENEFIT_PAIRS) for item in items]
        return list(dict.fromkeys(item for item in result if item))
    if path.endswith("cta_strategy.conversion_flow"):
        return _as_string_list(value, max_items=6)
    if path.endswith("proof_strategy.sample_or_demo_availability"):
        text = str(value or "").lower()
        if text in SAMPLE_TOKENS:
            return text
        if "nda" in text:
            return "after_nda"
        if "미팅" in text or "회의" in text:
            return "after_meeting"
        if "없" in text:
            return "none"
        if any(word in text for word in ("즉시", "바로", "가능", "보유")):
            return "immediate"
        return None
    if path.endswith("proof_strategy.verification_status"):
        allowed = {"unknown", "unverified", "partially_verified", "verified", "contradicted"}
        text = str(value or "").strip().lower()
        return text if text in allowed else None
    if path == "offer.chosen_solution":
        text = _as_short_text(value, 80)
        if not text:
            return None
        text = re.sub(r"\s*(?:을|를)?\s*(?:진행|판매|제안|알리)(?:하려고|하고)?\s*(?:합니다|싶습니다)?\.?$", "", text)
        text = re.sub(r"\s*(?:입니다|이에요|예요)\.?$", "", text)
        text = re.sub(r"^전면\s*솔루션(?:은|으로)?\s*", "", text, flags=re.IGNORECASE)
        return text.strip() or None
    if path.endswith("market.country_or_region"):
        text = _as_short_text(value, 40)
        if text:
            text = re.sub(r"\s*(?:입니다|이에요|예요)\.?$", "", text)
        return text if text and not SENTENCE_END_RE.search(text) else None
    if path.endswith("target.organization_type"):
        return _as_short_text(value, 70)
    if path.endswith("recipient.first_reviewer") or ".recipient." in path:
        return _as_short_text(value, 70)
    if path.endswith("proof_strategy.primary_proof"):
        text = _as_short_text(value, 140)
        return text if text and PROOF_RE.search(text) else None
    return _as_short_text(value, 180)


def _active_track_ids(state: Dict[str, Any]) -> List[str]:
    """현재 사용 중인 트랙 ID를 우선순위 순으로 반환한다."""
    return [t["track_id"] for t in sorted(state["strategy_tracks"], key=lambda item: item["priority"]) if t["active"]]


def _track_path(track_id: str, suffix: str) -> str:
    return f"strategy_tracks.{track_id}.{suffix}"


def _dependent_paths(state: Dict[str, Any], changed_path: str) -> List[str]:
    """상위 전략값 변경이 의미를 무효화할 수 있는 하위 경로를 계산한다."""
    track_ids = _active_track_ids(state)
    if changed_path == "offer.chosen_solution":
        result = ["offer.differentiator", "transaction_strategy.success_criteria"]
        for track_id in track_ids:
            result.extend([
                _track_path(track_id, "target.organization_type"),
                _track_path(track_id, "target.buying_situation"),
                _track_path(track_id, "purchase_logic.purchase_reason"),
                _track_path(track_id, "proof_strategy.primary_proof"),
            ])
        return result
    match = re.fullmatch(r"strategy_tracks\.([AB])\.(.+)", changed_path)
    if not match:
        return []
    track_id, suffix = match.groups()
    if suffix == "market.country_or_region":
        return [
            _track_path(track_id, "recipient.first_reviewer"),
            _track_path(track_id, "entry_strategy.primary_channel"),
            _track_path(track_id, "execution_constraints.regulation_certification"),
            _track_path(track_id, "execution_constraints.localization_support"),
        ]
    if suffix in {"target.organization_type", "target.buying_situation", "target.purchase_trigger"}:
        return [
            _track_path(track_id, "recipient.first_reviewer"),
            _track_path(track_id, "purchase_logic.purchase_reason"),
            _track_path(track_id, "proof_strategy.primary_proof"),
            _track_path(track_id, "cta_strategy.primary_cta"),
        ]
    if suffix == "purchase_logic.purchase_reason":
        return [
            _track_path(track_id, "proof_strategy.primary_proof"),
            _track_path(track_id, "cta_strategy.core_message"),
            _track_path(track_id, "cta_strategy.acceptance_criteria"),
        ]
    if suffix == "entry_strategy.primary_channel":
        return [_track_path(track_id, "cta_strategy.primary_cta")]
    return []


def _mark_stale(state: Dict[str, Any], path: str, reason: str) -> None:
    """값을 지우지 않고 stale 메타데이터와 전역 목록에 표시한다."""
    if not _has_value(get_path(state, path)):
        return
    stale = set(state["interview_state"]["stale_paths"])
    stale.add(path)
    state["interview_state"]["stale_paths"] = sorted(stale)
    meta = state["field_meta"].setdefault(path, _new_meta("model_inferred", "low", None))
    meta["stale_reason"] = reason


def _new_meta(origin: str, confidence: str, answer_id: Optional[str]) -> Dict[str, Any]:
    """스키마와 일치하는 필드 메타데이터를 만든다."""
    return {
        "origin": origin,
        "confidence": confidence,
        "source_answer_id": answer_id,
        "verification_status": "not_required" if origin == "user_stated" else "unverified",
        "stale_reason": None,
    }


def add_answer_record(
    state: Dict[str, Any],
    *,
    question_id: Optional[str],
    target_paths: List[str],
    value: Any,
    origin: str,
    source_type: str,
    is_canonical: bool,
) -> str:
    """대표 답변과 외부 초안을 덮어쓰지 않는 이력으로 저장한다."""
    answer_id = f"A-{len(state['answer_records']) + 1:04d}"
    state["answer_records"].append({
        "answer_id": answer_id,
        "question_id": question_id,
        "target_paths": sorted(set(target_paths)),
        "value": value,
        "origin": origin,
        "source_type": source_type,
        "verification_status": "not_required" if origin == "user_stated" else "unverified",
        "is_canonical": is_canonical,
    })
    return answer_id


def apply_updates(
    state: Dict[str, Any],
    updates: Dict[str, Any],
    *,
    origin: str,
    answer_id: Optional[str],
    invalidate: bool = True,
) -> List[str]:
    """정규화된 전략값을 반영하고 상위 값 변경 시 하위 값을 stale 처리한다."""
    normalized: Dict[str, Any] = {}
    for path, raw_value in updates.items():
        if not is_allowed_value_path(path):
            continue
        value = normalize_path_value(path, raw_value)
        if _has_value(value):
            normalized[path] = value

    changed = [
        path for path, value in normalized.items()
        if _has_value(get_path(state, path)) and get_path(state, path) != value
    ]
    if invalidate:
        updated_paths = set(normalized)
        for path in changed:
            for dependent in _dependent_paths(state, path):
                if dependent not in updated_paths:
                    _mark_stale(state, dependent, f"{path} 값 변경으로 재확인 필요")

    touched: List[str] = []
    stale = set(state["interview_state"]["stale_paths"])
    unknown = set(state["interview_state"]["explicit_unknown_paths"])
    for path, value in normalized.items():
        _set_path_raw(state, path, value)
        confidence = "high" if origin in {"user_stated", "program_context"} else "medium"
        state["field_meta"][path] = _new_meta(origin, confidence, answer_id)
        stale.discard(path)
        unknown.discard(path)
        touched.append(path)
    state["interview_state"]["stale_paths"] = sorted(stale)
    state["interview_state"]["explicit_unknown_paths"] = sorted(unknown)
    refresh_statuses(state)
    return touched


def mark_explicit_unknown(
    state: Dict[str, Any], paths: Sequence[str], *, answer_id: Optional[str]
) -> None:
    """사용자가 모름·미정·없음을 명시한 필드를 별도 상태로 기록한다."""
    unknown = set(state["interview_state"]["explicit_unknown_paths"])
    stale = set(state["interview_state"]["stale_paths"])
    partial = set(state["interview_state"].get("partial_paths", []))
    for path in paths:
        if not is_allowed_value_path(path):
            continue
        # 외부 리서치 초안이 남아 있더라도 대표의 "없음/미정"보다 앞세우지 않는다.
        # 이전 초안은 answer_records에 보존되고 현재 전략값에서는 제거된다.
        current = get_path(state, path)
        _set_path_raw(state, path, [] if isinstance(current, list) else None)
        unknown.add(path)
        stale.discard(path)
        partial.discard(path)
        state["field_meta"][path] = _new_meta("user_stated", "high", answer_id)
    state["interview_state"]["explicit_unknown_paths"] = sorted(unknown)
    state["interview_state"]["stale_paths"] = sorted(stale)
    state["interview_state"]["partial_paths"] = sorted(partial)
    refresh_statuses(state)


def is_user_resolved(state: Dict[str, Any], path: str) -> bool:
    """사용자 확인값·프로그램 전제·명시적 미정이며 stale이 아닌지 판정한다."""
    if path in state["interview_state"]["stale_paths"]:
        return False
    if path in state["interview_state"].get("partial_paths", []):
        return False
    if path in state["interview_state"]["explicit_unknown_paths"]:
        return True
    meta = state["field_meta"].get(path, {})
    return meta.get("origin") in {"user_stated", "program_context"} and _has_value(get_path(state, path))


def refresh_statuses(state: Dict[str, Any]) -> None:
    """공통 제안과 트랙의 현재 상태를 메타데이터 기반으로 갱신한다."""
    offer_required = ["offer.chosen_solution"]
    state["offer"]["status"] = "confirmed" if all(is_user_resolved(state, p) for p in offer_required) else (
        "assumed" if _has_value(state["offer"]["chosen_solution"]) else "unknown"
    )
    tx_required = ["transaction_strategy.primary_goal"]
    state["transaction_strategy"]["status"] = "confirmed" if all(is_user_resolved(state, p) for p in tx_required) else (
        "assumed" if _has_value(state["transaction_strategy"]["primary_goal"]) else "unknown"
    )
    stale = set(state["interview_state"]["stale_paths"])
    for track in state["strategy_tracks"]:
        track_id = track["track_id"]
        if not track["active"]:
            track["status"] = "deferred"
            continue
        anchors = [
            _track_path(track_id, "market.country_or_region"),
            _track_path(track_id, "target.organization_type"),
            _track_path(track_id, "recipient.first_reviewer"),
            _track_path(track_id, "purchase_logic.why_budget"),
            _track_path(track_id, "proof_strategy.primary_proof"),
            _track_path(track_id, "entry_strategy.primary_channel"),
            _track_path(track_id, "cta_strategy.primary_cta"),
            _track_path(track_id, "cta_strategy.conversion_flow"),
        ]
        if any(path in stale for path in anchors):
            track["status"] = "stale"
        elif all(is_user_resolved(state, path) for path in anchors):
            track["status"] = "confirmed"
        elif any(_has_value(get_path(state, path)) for path in anchors):
            track["status"] = "assumed"
        else:
            track["status"] = "unknown"


# ============================================================================
# 질문 후보와 적응형 라우터
# ============================================================================

@dataclass(frozen=True)
class QuestionCandidate:
    """라우터가 점수화할 한 개의 사용자 질문 후보다."""

    kind: str
    track_id: Optional[str]
    required_paths: Tuple[str, ...]
    optional_paths: Tuple[str, ...]
    purpose: str
    criticality: int
    dependency_count: int
    execution_risk: int
    question_cost: int = 1
    variant: str = "main"

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.track_id or 'global'}:{self.variant}"

    @property
    def all_paths(self) -> Tuple[str, ...]:
        return self.required_paths + self.optional_paths


def _global_candidates() -> List[QuestionCandidate]:
    """회사 전체에 공통인 전면 제안과 차별점 질문 후보를 만든다."""
    return [
        QuestionCandidate(
            "offer", None,
            ("offer.chosen_solution",),
            ("offer.transaction_unit",),
            "이번 진출에서 전면에 세울 제안 대상을 분명히 하기 위해",
            100, 0, 0, 1,
        ),
        QuestionCandidate(
            "differentiator", None,
            ("offer.differentiator",),
            tuple(),
            "다른 대안과 비교했을 때 이 제안을 선택할 이유를 확인하기 위해",
            90, 0, 0, 1,
        ),
    ]


def _track_candidates(track_id: str, *, secondary: bool) -> List[QuestionCandidate]:
    """한 진출안의 핵심 의사결정 질문만 만든다."""
    penalty = 12 if secondary else 0
    p = lambda suffix: _track_path(track_id, suffix)
    return [
        QuestionCandidate(
            "market", track_id,
            (p("market.country_or_region"),),
            tuple(),
            "기존 활동 국가와 이번에 실제로 공략할 시장을 구분하기 위해",
            98 - penalty, 0, 0, 1,
        ),
        QuestionCandidate(
            "target", track_id,
            (p("target.organization_type"),),
            tuple(),
            "이번 제안을 가장 먼저 검토할 고객 조직 유형을 확인하기 위해",
            96 - penalty, 0, 0, 1,
        ),
        QuestionCandidate(
            "purchase", track_id,
            (p("purchase_logic.why_budget"),),
            tuple(),
            "고객이 이 제안을 검토할 수 있는 예산 배경을 확인하기 위해",
            94 - penalty, 0, 0, 1,
        ),
        QuestionCandidate(
            "recipient", track_id,
            (p("recipient.first_reviewer"),),
            tuple(),
            "제안을 처음 검토하고 내부 논의를 시작할 부서를 확인하기 위해",
            88 - penalty, 0, 0, 1,
        ),
        QuestionCandidate(
            "proof", track_id,
            (p("proof_strategy.primary_proof"),),
            (p("proof_strategy.source"),),
            "제안의 신뢰를 만드는 대표적인 협업 실적이나 증거를 확인하기 위해",
            86 - penalty, 0, 0, 1,
        ),
        QuestionCandidate(
            "entry", track_id,
            (p("entry_strategy.primary_channel"),),
            (p("entry_strategy.alternative_channel"),),
            "첫 고객에게 접근할 경로를 확인하기 위해",
            84 - penalty, 0, 0, 1,
        ),
        QuestionCandidate(
            "cta", track_id,
            (p("cta_strategy.primary_cta"),),
            tuple(),
            "첫 제안에서 상대에게 요청할 부담 없는 다음 행동을 확인하기 위해",
            82 - penalty, 0, 0, 1,
        ),
        QuestionCandidate(
            "conversion", track_id,
            (p("cta_strategy.conversion_flow"),),
            tuple(),
            "첫 요청 이후 다음 단계에서 전달하거나 확인할 내용을 확인하기 위해",
            80 - penalty, 0, 0, 1,
        ),
    ]


def _execution_candidate(track_id: str) -> QuestionCandidate:
    """핵심 앵커가 모두 해결된 뒤 남은 예산으로 가장 큰 실행 위험을 확인한다."""
    p = lambda suffix: _track_path(track_id, suffix)
    return QuestionCandidate(
        "execution", track_id,
        (p("execution_constraints.highest_risk"),),
        (
            "transaction_strategy.success_criteria",
            p("execution_constraints.regulation_certification"),
            p("execution_constraints.cost_impact"),
            p("execution_constraints.supply_scale_up"),
            p("execution_constraints.localization_support"),
            p("execution_constraints.disclosure_nda_policy"),
            p("proof_strategy.sample_or_demo_availability"),
            p("cta_strategy.conversion_flow"),
        ),
        f"트랙 {track_id}의 다음 단계 전환을 실제로 막을 위험을 확인하기 위해",
        72 if track_id == "A" else 58, 1, 10, 2,
    )


def _followup_candidates_for(
    state: Dict[str, Any], parent: QuestionCandidate
) -> List[QuestionCandidate]:
    """v4는 온톨로지 필드를 채우기 위한 고정 후속 질문을 만들지 않는다."""
    return []


def _candidate_still_needs_question(state: Dict[str, Any], candidate: QuestionCandidate) -> bool:
    """큐에 들어간 후보가 현재도 미해결이고 시도 상한 전인지 확인한다."""
    return (
        not all(is_user_resolved(state, path) for path in candidate.required_paths)
        and state["interview_state"]["candidate_attempts"].get(candidate.key, 0)
        < MAX_SEMANTIC_ATTEMPTS
    )


def build_question_candidates(state: Dict[str, Any]) -> List[QuestionCandidate]:
    """아직 대표가 해결하지 않은 질문만 만들고 시도 상한을 적용한다."""
    candidates = _global_candidates()
    for track_id in _active_track_ids(state):
        candidates.extend(_track_candidates(track_id, secondary=(track_id == "B")))
    attempts = state["interview_state"]["candidate_attempts"]
    result: List[QuestionCandidate] = []
    for candidate in candidates:
        if all(is_user_resolved(state, path) for path in candidate.required_paths):
            continue
        if attempts.get(candidate.key, 0) >= MAX_SEMANTIC_ATTEMPTS:
            continue
        result.append(candidate)
    return result


def score_question_candidate(state: Dict[str, Any], candidate: QuestionCandidate) -> float:
    """중요도·의존성·불확실성·실행 위험에서 질문 부담을 뺀 점수를 계산한다."""
    attempts = state["interview_state"]["candidate_attempts"].get(candidate.key, 0)
    missing = sum(not _has_value(get_path(state, path)) for path in candidate.required_paths)
    stale = sum(path in state["interview_state"]["stale_paths"] for path in candidate.required_paths)
    # 아직 한 번도 묻지 않은 핵심 항목을 먼저 다룬다. 같은 질문의 즉시 반복이
    # 진입 채널과 CTA를 밀어내지 않도록 재시도에는 감점을 준다.
    retry_penalty = 35 * attempts
    return (
        candidate.criticality
        + candidate.dependency_count * 2
        + candidate.execution_risk * 2
        + missing * 8
        + stale * 12
        - retry_penalty
        - candidate.question_cost * 3
    )


def select_next_question(state: Dict[str, Any]) -> Optional[QuestionCandidate]:
    """핵심 의존 순서를 지키면서 현재 가장 중요한 공백 하나를 선택한다."""
    candidates = build_question_candidates(state)
    if not candidates:
        return None
    order = {
        "offer": 0,
        "market": 1,
        "target": 2,
        "purchase": 3,
        "differentiator": 4,
        "recipient": 5,
        "proof": 6,
        "entry": 7,
        "cta": 8,
        "conversion": 9,
        "goal": 10,
        "execution": 11,
    }
    unasked = [
        item for item in candidates
        if state["interview_state"]["candidate_attempts"].get(item.key, 0) == 0
    ]
    if unasked:
        return min(
            unasked,
            key=lambda item: (
                order.get(item.kind, 99),
                0 if item.track_id in {None, "A"} else 1,
            ),
        )
    return max(candidates, key=lambda item: (score_question_candidate(state, item), -len(item.all_paths)))


# ============================================================================
# 사람용 질문 생성
# ============================================================================

SEGMENT_EXAMPLE_SYS = """\
너는 B2B 고객 세그먼트 질문의 예시 생성기다.
확정된 솔루션과 시장에 맞는 '고객 조직 유형' 예시를 3~4개만 만든다.
사용 목적이나 구매 이유가 아니라 실제 회사·기관·사업자 유형이어야 한다.
입력에 없는 특정 회사명은 만들지 않는다. JSON 하나만 출력한다.
{"examples":["조직 유형 1","조직 유형 2","조직 유형 3"]}
"""


def _confirmed_anchor(state: Dict[str, Any], path: str) -> Optional[str]:
    """질문에 노출해도 되는 사용자 확정 앵커만 반환한다."""
    if not is_user_resolved(state, path) or path in state["interview_state"]["explicit_unknown_paths"]:
        return None
    value = get_path(state, path)
    return str(value) if _has_value(value) else None


def generate_segment_examples(client: Any, state: Dict[str, Any], track_id: str) -> List[str]:
    """API로 업종 맞춤 조직 예시를 만들고 기술 실패일 때만 범용 예시로 대체한다."""
    payload = {
        "solution": _confirmed_anchor(state, "offer.chosen_solution"),
        "market": _confirmed_anchor(state, _track_path(track_id, "market.country_or_region")),
    }
    try:
        raw = chat(
            client,
            SEGMENT_EXAMPLE_SYS,
            [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.0,
            max_tokens=400,
            enable_thinking=False,
        )
        parsed, status = parse_json_object(raw)
        if status == "parsed" and isinstance(parsed.get("examples"), list):
            examples = [_as_short_text(item, 40) for item in parsed["examples"]]
            examples = [item for item in examples if item]
            if 2 <= len(examples) <= 4:
                return examples
        raise ValueError("invalid example contract")
    except Exception:
        return ["지자체·공공기관", "대기업", "중견기업", "전문 서비스 기관"]


def display_value(value: Any) -> str:
    """내부 enum과 배열을 사람이 이해할 수 있는 표현으로 바꾼다."""
    if isinstance(value, list):
        return " → ".join(display_value(item) for item in value) if value else "미해결"
    if value is None:
        return "미해결"
    return DISPLAY_LABELS.get(str(value), str(value))


def display_path(state: Dict[str, Any], path: str) -> str:
    """명시적 미정과 일반 미해결을 구분해 점 경로 값을 표시한다."""
    if path in state["interview_state"]["explicit_unknown_paths"]:
        return "사용자 확인: 현재 미정·없음"
    return display_value(get_path(state, path))


def _build_followup_question(
    state: Dict[str, Any], candidate: QuestionCandidate
) -> str:
    """메인 답변의 맥락을 이어받아 세부 공백만 자연어로 질문한다."""
    track_id = candidate.track_id or "A"
    solution = _confirmed_anchor(state, "offer.chosen_solution") or "이 제안"
    market = _confirmed_anchor(state, _track_path(track_id, "market.country_or_region")) or "해당 시장"
    target = _confirmed_anchor(state, _track_path(track_id, "target.organization_type")) or "우선 고객"
    prompts = {
        ("offer", "transaction_unit"):
            f"앞서 말씀하신 ‘{solution}’은 실제 계약에서 제품·장비, 소프트웨어, 서비스, 라이선스, 프로젝트 중 어느 형태에 가까운가요?",
        ("offer", "use_and_change"):
            f"고객은 어떤 상황에서 ‘{solution}’을 사용하며, 사용한 뒤 무엇이 달라지나요?",
        ("offer", "feature_and_maturity"):
            f"‘{solution}’의 결과를 만드는 핵심 기능이나 역량은 무엇이며, 현재 바로 판매하거나 실행할 수 있는 수준인가요?",
        ("goal", "stage_and_bottleneck"):
            "그 목표와 별개로 현재 실제 진행 단계는 어디이며, 다음 단계로 가는 가장 큰 걸림돌은 무엇인가요?",
        ("goal", "success_and_sequence"):
            "첫 거래가 성공했다고 판단할 기준은 무엇이며, 그다음에는 어떤 거래로 이어가려 하나요?",
        ("market", "reason_and_readiness"):
            f"왜 ‘{market}’을 먼저 선택하셨고, 현재 현지 고객·파트너·자료 준비는 어느 정도인가요?",
        ("market", "localization"):
            f"‘{market}’ 진출을 위해 별도로 맞춰야 할 규제, 인증, 언어 또는 운영 방식이 있나요? 없다면 없다고 말씀해 주세요.",
        ("target", "situation_and_trigger"):
            f"‘{target}’이 실제로 검토를 시작하는 상황은 언제이며, 도입 논의를 촉발하는 사건은 무엇인가요?",
        ("target", "urgency_and_exclusion"):
            f"‘{target}’ 중에서도 지금 우선해야 한다는 신호는 무엇이며, 반대로 첫 영업 대상에서 제외할 조직은 어디인가요?",
        ("purchase", "pain_and_alternative"):
            f"‘{target}’이 현재 겪는 구체적인 문제는 무엇이며, 지금은 어떤 방법이나 업체로 대신 해결하고 있나요?",
        ("purchase", "loss_and_urgency"):
            f"‘{target}’이 지금 해결하지 않으면 생기는 손실이나 위험은 무엇이며, 구매를 서두르게 만드는 계기는 무엇인가요?",
        ("purchase", "benefit_and_budget"):
            f"‘{solution}’에서 고객이 가장 중요하게 보는 혜택은 무엇이며, 관련 예산은 어떤 이유로 편성되나요?",
        ("recipient", "beneficiary_and_owner"):
            "이 제안으로 가장 직접적인 혜택을 받는 부서와 실제 예산을 승인하는 직무나 부서는 각각 어디인가요?",
        ("recipient", "review_flow"):
            "별도의 기술 검토가 필요하다면 누가 담당하며, 첫 검토자는 다음으로 어느 부서에 제안을 전달하나요?",
        ("proof", "type_source_status"):
            "앞서 말씀하신 대표 실적은 어떤 유형의 자료이며, 어디에서 확인할 수 있고 현재 검증된 상태인가요?",
        ("proof", "measurement_and_delivery"):
            "그 성과는 어떤 조건에서 측정됐으며, 샘플·데모·자료를 언제 제공할 수 있고 NDA 전에도 공개 가능한가요?",
        ("entry", "alternative_and_partner"):
            "첫 접근 경로가 막힐 경우 사용할 두 번째 경로는 무엇이며, 현지 파트너가 맡아야 할 역할은 무엇인가요?",
        ("entry", "incentive_and_split"):
            "현지 파트너가 이 제안에 참여할 이유는 무엇이며, 우리 회사와 파트너의 업무는 어떻게 나누나요?",
        ("cta", "conversion_flow"):
            "첫 요청이 받아들여진 뒤 미팅, 자료 검토, 샘플·데모, PoC, 계약은 어떤 순서로 이어지나요?",
        ("cta", "message_and_acceptance"):
            "첫 제안에서 상대에게 남길 핵심 메시지는 무엇이며, 상대가 다음 단계로 넘어가겠다고 판단하는 기준은 무엇인가요?",
        ("cta", "next_requirements"):
            "첫 요청 이후 다음 단계로 넘어가기 전에 우리 쪽과 상대방이 각각 준비해야 할 자료나 조건은 무엇인가요?",
        ("execution", "compliance_and_cost"):
            "실행에 필요한 규제·인증은 무엇이며, 그것이 가격이나 추가 비용에 어떤 영향을 주나요?",
        ("execution", "scale_and_localization"):
            "수요가 늘어날 때 공급을 확대할 수 있는 범위와 현지에서 필요한 운영·언어 지원은 무엇인가요?",
        ("execution", "disclosure"):
            "제안 단계에서 공개 가능한 자료와 NDA 체결 후에만 공개할 자료를 어떻게 구분하나요?",
    }
    return prompts.get(
        (candidate.kind, candidate.variant),
        "앞선 답변에서 아직 확인되지 않은 세부 내용을 구체적으로 말씀해 주세요.",
    )


def build_human_question(
    client: Any,
    state: Dict[str, Any],
    candidate: QuestionCandidate,
    semantic_attempt: Optional[int] = None,
) -> str:
    """v4의 핵심 의사결정 하나를 일상적인 표현으로 질문한다."""
    kind, track_id = candidate.kind, candidate.track_id or "A"
    attempt = semantic_attempt or state["interview_state"]["candidate_attempts"].get(candidate.key, 0) + 1
    solution = _confirmed_anchor(state, "offer.chosen_solution")
    if kind == "offer":
        if attempt > 1:
            return "이번 해외 진출에서 가장 먼저 제안할 제품이나 서비스의 이름을 하나만 말씀해 주세요."
        return "이번 해외 진출에서 가장 먼저 제안할 제품이나 서비스는 무엇인가요?"
    if kind == "market":
        if attempt > 1:
            return "이번 진출에서 다른 지역보다 먼저 공략할 국가 또는 지역 하나만 말씀해 주세요."
        return "기존 활동 국가와 별개로, 이번에 가장 먼저 공략할 국가나 지역은 어디인가요?"
    if kind == "target":
        reviewer = _confirmed_anchor(state, _track_path(track_id, "recipient.first_reviewer"))
        if attempt > 1 and reviewer:
            return (
                f"‘{reviewer}’은 제안을 검토할 부서로 기록했습니다. "
                "이번 제안을 받게 될 실제 고객 조직은 어떤 유형인가요? "
                "(예: 대기업, 공공기관, 제조업체, 병원, 학교)"
            )
        return (
            "이번 제안을 가장 먼저 검토할 고객은 어떤 유형의 조직인가요? "
            "복수의 고객군을 함께 공략한다면 모두 말씀해 주세요. "
            "(예: 대기업, 공공기관, 제조업체, 병원, 학교)"
        )
    if kind == "differentiator":
        company = _as_short_text(state.get("company"), 60) or "해당 기업"
        if solution:
            return (
                f"‘{company}’의 전면 제안은 ‘{solution}’입니다. 다른 경쟁 솔루션이나 "
                "기존 대안과 비교했을 때 가장 큰 차이는 무엇인가요?"
            )
        return (
            f"‘{company}’의 이번 제안은 다른 경쟁 솔루션이나 기존 대안과 비교했을 때 "
            "어떤 점이 가장 다른가요?"
        )
    if kind == "recipient":
        return "실제로 제안을 받았을 때 처음 검토하고 내부 논의를 시작할 직무나 부서는 어디인가요?"
    if kind == "purchase":
        organization = _confirmed_anchor(state, _track_path(track_id, "target.organization_type"))
        subject = f"‘{organization}’ 유형의 고객" if organization else "이 고객"
        return (
            f"{subject}이 이번 제안을 검토할 때 사용할 수 있는 예산 항목이나 "
            "사업상 배경은 무엇인가요?"
        )
    if kind == "proof":
        return (
            "이번 제안을 실제로 수행할 수 있다는 근거로 제시할 고객 협업 사례, 프로젝트 결과, "
            "성과 수치, 인증 또는 테스트 자료는 무엇인가요? 아직 없다면 없다고 말씀해 주세요."
        )
    if kind == "entry":
        return (
            "첫 고객에게 어떤 경로로 접근할 계획인가요? 직접 연락과 에이전시·현지 파트너 등 "
            "여러 경로를 함께 활용한다면 모두 말씀해 주세요."
        )
    if kind == "cta":
        return (
            "첫 제안에서 상대방에게 요청할 가장 부담 없고 구체적인 다음 행동은 무엇인가요? "
            "(예: 15~30분 소개 미팅, 제안서 검토, 데모 확인, 샘플 테스트)"
        )
    if kind == "conversion":
        first_action = display_path(state, _track_path(track_id, "cta_strategy.primary_cta"))
        return (
            f"‘{first_action}’ 다음에는 무엇을 전달하거나 확인할 계획인가요?"
        )
    return "현재 전략에서 가장 불확실한 부분을 구체적으로 말씀해 주세요."


def question_is_safe(question: str) -> bool:
    """내부 키, 지나친 길이, 세 개 이상의 물음표가 있는 질문을 차단한다."""
    if not question or len(question) > 300 or question.count("?") > 2:
        return False
    if re.search(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+", question):
        return False
    if any(mark in question for mark in ("{", "}", "[", "]")):
        return False
    return True


RESEARCH_CONTEXT_CATEGORIES = {
    "market": ("market_activity",),
    "target": ("customer_collaboration",),
    "differentiator": ("solution_mechanism",),
    "proof": ("proof", "customer_collaboration"),
}

GENERIC_SOLUTION_WORDS = {
    "제품", "서비스", "솔루션", "프로젝트", "사업", "캠페인", "플랫폼",
}


def _solution_keywords(solution: Optional[str]) -> List[str]:
    """검색 사실이 현재 전면 제안과 연결되는지 확인할 핵심 단어를 만든다."""
    if not solution:
        return []
    return [
        token.lower()
        for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", solution)
        if token.lower() not in GENERIC_SOLUTION_WORDS
    ]


def select_research_context(
    state: Dict[str, Any],
    candidate: QuestionCandidate,
    research_facts: Sequence[Dict[str, str]],
) -> Optional[str]:
    """현재 질문에 허용된 검증 사실 한 개만 선택한다."""
    allowed = RESEARCH_CONTEXT_CATEGORIES.get(candidate.kind, tuple())
    if not allowed:
        return None
    solution = _confirmed_anchor(state, "offer.chosen_solution")
    solution_keywords = _solution_keywords(solution)
    track_id = candidate.track_id or "A"
    target_keywords = _solution_keywords(
        _confirmed_anchor(state, _track_path(track_id, "target.organization_type"))
    )
    candidates: List[Tuple[int, str]] = []
    for fact in research_facts:
        if fact.get("category") not in allowed:
            continue
        text = _as_short_text(fact.get("fact"), 140)
        if not text:
            continue
        normalized = text.lower()
        # 사용자가 확정한 전면 제안과 관련 없는 다른 사업·정체성 사실은
        # 어떤 질문에도 노출하지 않는다. 연결이 약하면 중립 질문으로 되돌아간다.
        related = [keyword for keyword in solution_keywords if keyword in normalized]
        target_related = [keyword for keyword in target_keywords if keyword in normalized]
        if solution_keywords and not related and not target_related:
            continue
        score = len(related) + len(target_related) * 2
        if candidate.kind == "proof" and fact.get("category") == "customer_collaboration" \
                and target_related:
            score += 3
        if score <= 0 and (solution_keywords or target_keywords):
            continue
        candidates.append((score, text))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def add_research_context_to_question(
    state: Dict[str, Any],
    candidate: QuestionCandidate,
    question: str,
    research_facts: Sequence[Dict[str, str]],
) -> str:
    """검색 사실을 답이나 가설이 아닌 질문의 배경 문장으로만 붙인다."""
    fact = select_research_context(state, candidate, research_facts)
    if not fact:
        return question
    prefix = {
        "market": f"공개 자료에서는 과거 시장 활동으로 ‘{fact}’가 확인됩니다. ",
        "target": f"공개 자료에서는 기존 고객·협업 사례로 ‘{fact}’가 확인됩니다. ",
        "differentiator": f"공개 자료에서는 제안의 작동 방식으로 ‘{fact}’가 확인됩니다. ",
        "proof": f"공개 자료에서는 실적 근거로 ‘{fact}’가 확인됩니다. ",
    }.get(candidate.kind, "")
    contextualized = prefix + question
    return contextualized if question_is_safe(contextualized) else question


def build_semantic_retry_question(
    state: Dict[str, Any],
    candidate: QuestionCandidate,
    default_question: str,
) -> str:
    """직전 의미 판정의 부족 사유에 맞춰 같은 문장을 반복하지 않고 재질문한다."""
    last_assessment: Dict[str, Any] = {}
    for event in reversed(state["interview_state"].get("extraction_events", [])):
        if event.get("kind") != candidate.kind:
            continue
        last_assessment = event.get("semantic_assessment") or {}
        break
    fit = last_assessment.get("field_fit")
    role = last_assessment.get("answer_role")
    track_id = candidate.track_id or "A"
    if fit not in {"partial", "mismatch", "redirect"}:
        return default_question
    if candidate.kind == "purchase" and role == "end_user_benefit":
        return (
            "말씀하신 내용은 최종 사용자가 얻는 효과로 이해했습니다. "
            "고객 조직이 실제 예산을 편성하거나 구매 검토를 시작하게 만드는 "
            "내부 사업상 이유는 무엇인가요?"
        )
    if candidate.kind == "differentiator" and (
        role in {"generic_feature", "comparative_advantage"} or fit == "partial"
    ):
        return (
            "말씀하신 차이가 실제로 만들어지는 방식을 한 가지만 더 설명해 주세요. "
            "예를 들어 비용 부담을 어떤 구조로 낮추는지, 또는 일반적인 대안보다 "
            "고객 결과가 어떻게 달라지는지를 말씀해 주세요."
        )
    if candidate.kind == "target" and role == "reviewer_department":
        reviewer = display_path(state, _track_path(track_id, "recipient.first_reviewer"))
        return (
            f"제안 검토 부서로는 ‘{reviewer}’를 기록했습니다. "
            "이번 제안을 구매하거나 도입할 실제 고객 조직은 어떤 유형인가요? "
            "(예: 대기업, 공공기관, 제조업체, 병원, 학교)"
        )
    if candidate.kind == "recipient" and role == "customer_organization":
        return (
            "말씀하신 내용은 고객 조직 유형으로 기록했습니다. 그 조직 안에서 "
            "제안을 처음 검토하고 내부 논의를 시작할 직무나 부서는 어디인가요?"
        )
    if candidate.kind == "proof" and role == "proof_source":
        return (
            "자료가 있는 위치는 확인했습니다. 그 자료에서 가장 먼저 제시할 "
            "구체적인 고객 사례, 프로젝트명, 성과 수치, 인증·수상 또는 테스트 결과는 무엇인가요?"
        )
    if candidate.kind == "proof" and fit == "partial":
        return (
            "말씀하신 사례들 가운데 첫 제안에서 가장 먼저 보여줄 호텔이나 프로젝트 "
            "사례 하나를 말씀해 주세요. 공개하기 어렵다면 사례 유형만 말씀해도 됩니다."
        )
    if candidate.kind == "conversion":
        first_action = display_path(state, _track_path(track_id, "cta_strategy.primary_cta"))
        return (
            f"‘{first_action}’ 자체를 다시 설명하기보다, 그것이 끝난 직후 "
            "상대방과 다음으로 전달하거나 확인할 행동은 무엇인가요?"
        )
    return default_question


# ============================================================================
# 답변 구조화: API 우선, 기술 실패 시에만 로컬 fallback
# ============================================================================

ANSWER_EXTRACT_SYS = """\
너는 글로벌 B2B 대표 인터뷰의 '의미 판정기 겸 원문 근거 추출기'다.

규칙:
- 먼저 question_contract를 기준으로 답변의 주된 의미 역할과 질문 적합성을 판정한다.
- answer_role은 question_contract의 expected_roles 배열 값, redirect_roles의 개별 키,
  common_mismatch_roles 배열 값 중 정확히 하나를 사용한다.
- expected_roles, redirect_roles, common_mismatch_roles 같은 계약의 키 이름 자체를
  answer_role 값으로 출력하지 않는다.
- field_fit은 exact, partial, redirect, mismatch, explicit_unknown 중 하나다.
- exact는 질문이 요구한 의사결정 정보를 충분히 답한 경우에만 사용한다.
- partial은 관련 답변이지만 질문이 요구한 핵심 구분이나 구체성이 부족한 경우다.
- redirect는 유효한 답변이지만 현재 질문이 아니라 redirect_path의 정보인 경우다.
- redirect_roles가 비어 있으면 field_fit=redirect를 사용하지 않는다.
- mismatch는 질문과 다른 종류의 답변이거나 전략값으로 저장하기 어려운 경우다.
- confidence는 의미 판정의 확신도를 0과 1 사이 숫자로 출력한다.
- assessment.evidence도 답변에서 글자 그대로 복사한 가장 짧은 근거 구절이어야 한다.
- allowed_paths에 포함된 경로만 출력한다.
- 답변에 직접 존재하는 사실만 사용한다.
- 필드마다 supported, partial, ambiguous, not_mentioned 중 하나로 판정한다.
- evidence는 답변에서 글자 그대로 복사한 가장 짧은 근거 구절이어야 한다.
- 한 필드가 누락되어도 답변된 다른 필드를 버리지 않는다.
- normalized_candidate는 enum 필드에만 허용 토큰을 쓰고, 자유 텍스트는 evidence와 같은 언어·문자를 유지한다.
- 서로 다른 시장별 전략이 명확히 비교될 때만 A와 B 트랙으로 분리한다.
- 한 시장에서 여러 고객군이나 접근 채널을 함께 쓴다는 답변만으로 B 트랙을 만들지 않는다.
- 첫 행동과 후속 행동을 구분한다.
- 솔루션명이나 비전은 proof로 출력하지 않는다.
- PoC를 목표라고 답했을 뿐이면 current_stage로 복사하지 않는다.
- CSR 의무나 예산 배정은 why_budget 또는 purchase_trigger이며, 해당 제안을 선택하는 purchase_reason과 구분한다.
- 웹사이트·뉴스·IR은 proof의 source다. 구체적인 고객·프로젝트·수치·인증이 없으면 primary_proof를 supported로 만들지 않는다.
- '기업과 공공기관 모두'처럼 복수 고객군을 명시한 답변은 organization_type에 함께 보존한다.
- 내부 enum 필드는 field_rules의 allowed_tokens를 사용한다.
- 최종 사용자의 효익과 구매 조직의 예산 편성 이유를 구분한다.
- 기능을 단순히 나열한 답변과 경쟁 대안 대비 차이를 구분한다.
- 첫 요청을 다시 수행하는 답변과 첫 요청 이후의 후속 행동을 구분한다.
- question_contract의 sufficiency_rule을 충족하지 못하면 supported가 아니라 partial로 판정한다.

반드시 JSON 하나만 출력한다.
{
  "activate_track_b": false,
  "assessment": {
    "answer_role": "question_contract에 정의된 의미 역할",
    "field_fit": "exact",
    "redirect_path": null,
    "missing_reason": null,
    "confidence": 0.95,
    "evidence": "답변 원문 구절"
  },
  "fields": [
    {
      "path": "허용된 경로",
      "status": "supported",
      "evidence": "답변 원문 구절",
      "normalized_candidate": "enum 토큰 또는 원문 기반 값",
      "missing_detail": null
    }
  ]
}
"""

FIELD_RULES = {
    "offer.transaction_unit": {"allowed_tokens": sorted(TRANSACTION_UNIT_TOKENS)},
    "transaction_strategy.primary_goal": {"allowed_tokens": sorted(GOAL_TOKENS)},
    "entry_strategy.primary_channel": {"allowed_tokens": sorted(CHANNEL_TOKENS)},
    "entry_strategy.alternative_channel": {"allowed_tokens": sorted(CHANNEL_TOKENS)},
    "cta_strategy.primary_cta": {"allowed_tokens": sorted(CTA_TOKENS)},
    "purchase_logic.key_benefit_priority": {"allowed_tokens": sorted(KEY_BENEFIT_TOKENS)},
    "proof_strategy.sample_or_demo_availability": {"allowed_tokens": sorted(SAMPLE_TOKENS)},
}

# 질문마다 기대하는 답변의 의미 유형과 최소 충족 조건을 모델에 명시한다.
# 키워드는 명백한 단답의 안전망일 뿐이며, 아래 계약의 판정은 API가 담당한다.
QUESTION_SEMANTIC_CONTRACTS = {
    "offer": {
        "expected_roles": ["solution_offer"],
        "redirect_roles": {},
        "sufficiency_rule": "이번 해외 진출에서 실제로 제안할 제품·서비스·프로젝트의 식별 가능한 이름이나 설명",
    },
    "goal": {
        "expected_roles": ["transaction_goal"],
        "redirect_roles": {},
        "sufficiency_rule": "PoC, 판매, 라이선싱, 제휴처럼 가장 먼저 성사시키려는 거래 유형",
    },
    "market": {
        "expected_roles": ["geographic_market"],
        "redirect_roles": {},
        "sufficiency_rule": "이번에 우선 공략할 국가 또는 구체적인 지역",
    },
    "target": {
        "expected_roles": ["customer_organization"],
        "redirect_roles": {"reviewer_department": "recipient.first_reviewer"},
        "sufficiency_rule": "부서나 사용자가 아니라 제안을 구매·도입할 회사·기관·시설·사업자 유형. 호텔 등급처럼 세부 조건이 붙은 조직 유형도 충분함",
    },
    "purchase": {
        "expected_roles": ["buyer_budget_driver", "buyer_budget_category", "buyer_business_trigger"],
        "redirect_roles": {},
        "sufficiency_rule": "고객 조직의 예산 항목, 리모델링·교체·신규 사업 같은 지출 배경 또는 구매 검토를 시작하는 사업상 계기 중 하나",
    },
    "differentiator": {
        "expected_roles": ["comparative_advantage"],
        "redirect_roles": {},
        "sufficiency_rule": "기능명만 나열하지 않고 경쟁 솔루션·기존 대안과 달라지는 방식 또는 고객 결과",
    },
    "recipient": {
        "expected_roles": ["reviewer_department"],
        "redirect_roles": {"customer_organization": "target.organization_type"},
        "sufficiency_rule": "제안을 처음 검토하고 내부 논의를 시작하는 구체적인 직무 또는 부서",
    },
    "proof": {
        "expected_roles": ["performance_evidence", "proof_source"],
        "redirect_roles": {},
        "sufficiency_rule": "고객 협업 사례·프로젝트·성과 수치·인증·수상·테스트 결과. 여러 실제 사례나 레퍼런스가 있다고 명시한 답도 근거 존재로 수용하며 자료 위치만 말하면 partial",
    },
    "entry": {
        "expected_roles": ["access_channel"],
        "redirect_roles": {},
        "sufficiency_rule": "첫 고객에게 도달하는 직접 연락·광고·에이전시·유통 파트너 등의 주 접근 경로 하나. 대안 경로는 선택 사항",
    },
    "cta": {
        "expected_roles": ["recipient_next_action"],
        "redirect_roles": {},
        "sufficiency_rule": "첫 제안에서 상대방에게 요청할 작고 구체적인 행동",
    },
    "conversion": {
        "expected_roles": ["followup_action_sequence"],
        "redirect_roles": {},
        "sufficiency_rule": "확정된 첫 요청 이후 우리 측이 전달하거나 양측이 확인할 다음 행동 하나 이상. 한 단계 답변도 충분하며 첫 요청의 단순 반복만 partial",
    },
    "final_review": {
        "expected_roles": ["strategy_correction"],
        "redirect_roles": {},
        "sufficiency_rule": "요약에서 수정할 항목과 수정값",
    },
}

COMMON_MISMATCH_ROLES = [
    "end_user_benefit",
    "generic_feature",
    "company_vision",
    "unrelated_answer",
    "explicit_unknown",
]


def _semantic_contract_for_candidate(candidate: QuestionCandidate) -> Dict[str, Any]:
    """트랙에 독립적인 의미 계약을 실제 저장 경로가 포함된 계약으로 바꾼다."""
    base = QUESTION_SEMANTIC_CONTRACTS.get(candidate.kind, {
        "expected_roles": ["direct_answer"],
        "redirect_roles": {},
        "sufficiency_rule": "현재 질문에 직접 답하는 구체적인 정보",
    })
    track_id = candidate.track_id or "A"
    redirects: Dict[str, str] = {}
    for role, suffix in base.get("redirect_roles", {}).items():
        if suffix.startswith(("offer.", "transaction_strategy.", "strategy_tracks.")):
            redirects[role] = suffix
        else:
            redirects[role] = _track_path(track_id, suffix)
    return {
        "expected_roles": list(base.get("expected_roles", [])),
        "redirect_roles": redirects,
        "common_mismatch_roles": COMMON_MISMATCH_ROLES,
        "sufficiency_rule": base.get("sufficiency_rule"),
    }

TECHNICAL_FAILURE_STATUSES = {
    "model_failed", "empty_response", "json_not_found", "parse_failed", "not_object",
    "invalid_contract", "invalid_source_grounding",
}
LOCAL_FALLBACK_FAILURE_STATUSES = {
    "model_failed", "empty_response", "json_not_found", "parse_failed", "not_object",
}

BUDGET_REASON_ONLY_RE = re.compile(
    r"(?:예산(?:이|을|에)?\s*(?:있|배정)|의무|컴플라이언스|법적\s*요구|정책상)", re.IGNORECASE
)
BUDGET_CATEGORY_RE = re.compile(
    r"(?:예산|리모델링|시설\s*개선|교체|신규\s*사업|마케팅|교육|조달|구매|운영비|사업비)",
    re.IGNORECASE,
)
CHOICE_REASON_RE = re.compile(
    r"(?:대안|대비|선택|차별|효과|성과|전문성|품질|비용|빠르|고도화|개선)", re.IGNORECASE
)
PROOF_SOURCE_RE = re.compile(r"(?:웹사이트|홈페이지|뉴스|기사|IR|브로슈어|소개서)", re.IGNORECASE)
SPECIFIC_PROOF_RE = re.compile(
    r"(?:\d|프로젝트명|고객명|인증명|특허|수상|매출|수출|계약|PoC|테스트\s*결과|"
    r"성과\s*수치|협업\s*레퍼런스|레퍼런스)",
    re.IGNORECASE,
)


def extraction_allowed_paths(candidate: QuestionCandidate) -> List[str]:
    """현재 질문 경로와 의미상 안전하게 이동 가능한 경로만 허용한다."""
    allowed = list(candidate.all_paths)
    allowed.extend(
        _semantic_contract_for_candidate(candidate).get("redirect_roles", {}).values()
    )
    if candidate.track_id == "A" and candidate.kind == "market":
        for path in candidate.all_paths:
            if path.startswith("strategy_tracks.A."):
                allowed.append(path.replace("strategy_tracks.A.", "strategy_tracks.B.", 1))
    return sorted(set(allowed))


def direct_extraction_paths(candidate: QuestionCandidate) -> set[str]:
    """현재 질문의 직접 답변 경로와 의미 이동 전용 경로를 분리한다."""
    direct = set(candidate.all_paths)
    if candidate.track_id == "A" and candidate.kind == "market":
        direct.update(
            path.replace("strategy_tracks.A.", "strategy_tracks.B.", 1)
            for path in candidate.all_paths
            if path.startswith("strategy_tracks.A.")
        )
    return direct


def _rule_for_path(path: str) -> Dict[str, Any]:
    """트랙 ID를 제거한 suffix 기준으로 enum 출력 규칙을 찾는다."""
    suffix = re.sub(r"^strategy_tracks\.[AB]\.", "", path)
    return FIELD_RULES.get(path, FIELD_RULES.get(suffix, {}))


def _normalized_from_evidence(path: str, evidence: str, candidate: Any) -> Any:
    """자유 텍스트는 원문을, 제한 토큰·배열은 검증된 후보를 우선 정규화한다."""
    suffix = re.sub(r"^strategy_tracks\.[AB]\.", "", path)
    use_candidate = bool(_rule_for_path(path)) or suffix == "cta_strategy.conversion_flow"
    raw = candidate if use_candidate and candidate is not None else evidence
    if not _unicode_is_sane(raw):
        raw = evidence
    value = normalize_path_value(path, raw)
    # 모델이 허용 enum이 아닌 표현을 내보내도 사용자 원문이 명확하면 원문에서
    # 한 번 더 결정적으로 정규화한다. 예: "짧은 미팅" → meeting_15_30min.
    if not _has_value(value) and raw != evidence:
        value = normalize_path_value(path, evidence)
    return value


def _closed_choice_is_decisive(path: str, evidence: str) -> bool:
    """짧아도 의미가 명확한 선택값이나 후속 행동은 추가 설명 없이 확정한다."""
    suffix = re.sub(r"^strategy_tracks\.[AB]\.", "", path)
    if suffix in {"cta_strategy.primary_cta", "entry_strategy.primary_channel",
                  "entry_strategy.alternative_channel"}:
        return _has_value(normalize_path_value(path, evidence))
    if path in {"transaction_strategy.primary_goal", "offer.transaction_unit"}:
        return _has_value(normalize_path_value(path, evidence))
    if suffix == "cta_strategy.conversion_flow":
        return bool(_as_string_list(evidence, max_items=6))
    return False


def _partial_answer_is_sufficient(
    candidate: QuestionCandidate,
    path: str,
    evidence: str,
    assessment: Dict[str, Any],
) -> bool:
    """질문 목적을 충족한 단답을 모델의 과도한 partial 판정에서 복구한다."""
    role = assessment.get("answer_role")
    confidence = assessment.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < 0.55:
        return False
    suffix = re.sub(r"^strategy_tracks\.[AB]\.", "", path)
    if candidate.kind == "target" and suffix == "target.organization_type":
        return bool(ORGANIZATION_RE.search(evidence) and not _department_only_target_answer(evidence))
    if candidate.kind == "purchase" and suffix == "purchase_logic.why_budget":
        return role in {"buyer_budget_driver", "buyer_budget_category", "buyer_business_trigger"}
    if candidate.kind == "proof" and suffix == "proof_strategy.primary_proof":
        return role == "performance_evidence" and bool(PROOF_RE.search(evidence))
    if candidate.kind in {"cta", "entry"}:
        return _closed_choice_is_decisive(path, evidence)
    if candidate.kind == "conversion" and suffix == "cta_strategy.conversion_flow":
        return role == "followup_action_sequence" and bool(_as_string_list(evidence, max_items=6))
    return False


def _validate_semantic_assessment(
    parsed: Dict[str, Any],
    answer: str,
    candidate: QuestionCandidate,
    allowed_paths: Sequence[str],
) -> Tuple[Optional[Dict[str, Any]], str]:
    """모델의 의미 판정을 질문 계약과 원문 근거에 대조한다."""
    assessment = parsed.get("assessment")
    # v4 초기에 저장한 모의 응답과 테스트 응답은 계속 읽을 수 있게 한다.
    if assessment is None:
        return {
            "answer_role": "legacy_unspecified",
            "field_fit": "legacy",
            "redirect_path": None,
            "missing_reason": None,
            "confidence": None,
            "evidence": None,
        }, "legacy"
    if not isinstance(assessment, dict):
        return None, "invalid_contract"
    role = assessment.get("answer_role")
    field_fit = assessment.get("field_fit")
    redirect_path = assessment.get("redirect_path")
    missing_reason = assessment.get("missing_reason")
    confidence = assessment.get("confidence")
    evidence = assessment.get("evidence")
    if not isinstance(role, str) or field_fit not in {
        "exact", "partial", "redirect", "mismatch", "explicit_unknown",
    }:
        return None, "invalid_contract"
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) \
            or not 0 <= float(confidence) <= 1:
        return None, "invalid_contract"
    if missing_reason is not None and not isinstance(missing_reason, str):
        return None, "invalid_contract"
    if not isinstance(evidence, str):
        return None, "invalid_contract"
    source_key = _compare_text(evidence)
    if len(source_key) < 1 or source_key not in _compare_text(answer):
        return None, "invalid_source_grounding"

    contract = _semantic_contract_for_candidate(candidate)
    expected = set(contract["expected_roles"])
    redirects = contract["redirect_roles"]
    if role == "common_mismatch_roles":
        role = "unrelated_answer"
    if role == "redirect" and redirect_path in direct_extraction_paths(candidate):
        if candidate.kind == "purchase" and BUDGET_CATEGORY_RE.search(evidence):
            role = "buyer_budget_category"
            field_fit = "partial"
            redirect_path = None
        elif candidate.kind == "proof" and PROOF_RE.search(evidence):
            role = "performance_evidence"
            field_fit = "partial"
            redirect_path = None
    known_roles = expected | set(redirects) | set(contract["common_mismatch_roles"])
    if role not in known_roles:
        return None, "invalid_contract"
    if role in redirects:
        if field_fit != "redirect":
            field_fit = "redirect"
        redirect_path = redirects[role]
    elif field_fit == "redirect":
        return None, "invalid_contract"
    elif role not in expected and field_fit in {"exact", "partial"}:
        field_fit = "mismatch"
    if redirect_path is not None and redirect_path not in set(allowed_paths):
        return None, "invalid_contract"
    if field_fit == "redirect" and not redirect_path:
        return None, "invalid_contract"
    # 낮은 확신의 exact 판정은 값을 버리지 않고 부분 답변으로 낮춰 재확인한다.
    if field_fit == "exact" and float(confidence) < 0.55:
        field_fit = "partial"
        missing_reason = missing_reason or "의미 판정 확신도가 낮아 추가 확인 필요"
    return {
        "answer_role": role,
        "field_fit": field_fit,
        "redirect_path": redirect_path,
        "missing_reason": missing_reason,
        "confidence": float(confidence),
        "evidence": evidence,
    }, "validated"


def validate_extraction_response(
    parsed: Dict[str, Any],
    answer: str,
    candidate: QuestionCandidate,
    allowed_paths: Sequence[str],
) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]], bool, str, Dict[str, Any]]:
    """필드별 계약, 원문 근거, 정규화 타입과 한글 무결성을 검증한다."""
    assessment, assessment_status = _validate_semantic_assessment(
        parsed, answer, candidate, allowed_paths
    )
    if assessment is None:
        return {}, [], [], False, assessment_status, {}
    records = parsed.get("fields")
    # 이전 테스트와 저장된 모의 응답도 읽되 내부적으로 새 필드 계약으로 변환한다.
    if records is None and isinstance(parsed.get("updates"), list):
        if parsed.get("has_valid_value") is False and not parsed["updates"]:
            records = []
        else:
            records = [
                {
                    "path": item.get("path"),
                    "status": "supported",
                    "evidence": item.get("source_text"),
                    "normalized_candidate": item.get("normalized"),
                    "missing_detail": None,
                }
                for item in parsed["updates"] if isinstance(item, dict)
            ]
    if not isinstance(records, list):
        return {}, [], [], False, "invalid_contract", assessment
    allowed = set(allowed_paths)
    haystack = _compare_text(answer)
    updates: Dict[str, Any] = {}
    partial_paths: List[str] = []
    field_results: List[Dict[str, Any]] = []
    field_fit = assessment["field_fit"]
    redirect_path = assessment.get("redirect_path")
    direct_paths = direct_extraction_paths(candidate)
    # 질문 부적합 답변은 값이 그럴듯해 보여도 현재 필드에 저장하지 않는다.
    if field_fit in {"mismatch", "explicit_unknown"}:
        return {}, [], [], False, "parsed_no_value", assessment
    for record in records:
        if not isinstance(record, dict):
            return {}, [], [], False, "invalid_contract", assessment
        path = record.get("path")
        status = record.get("status")
        evidence = record.get("evidence")
        if path not in allowed or status not in {"supported", "partial", "ambiguous", "not_mentioned"}:
            return {}, [], [], False, "invalid_contract", assessment
        if field_fit == "redirect":
            if path != redirect_path:
                continue
        elif path not in direct_paths:
            # redirect용으로만 허용된 경로는 직접 답변에서 저장하지 않는다.
            continue
        if (
            status == "not_mentioned"
            and field_fit == "partial"
            and path in candidate.required_paths
            and candidate.kind == "purchase"
            and isinstance(evidence, str)
            and BUDGET_CATEGORY_RE.search(evidence)
        ):
            status = "partial"
        result = {
            "path": path,
            "status": status,
            "evidence": evidence if isinstance(evidence, str) else None,
            "missing_detail": record.get("missing_detail"),
            "answer_role": assessment.get("answer_role"),
            "field_fit": field_fit,
            "confidence": assessment.get("confidence"),
        }
        field_results.append(result)
        if status not in {"supported", "partial"}:
            continue
        if not isinstance(evidence, str):
            return {}, [], [], False, "invalid_contract", assessment
        source_key = _compare_text(evidence)
        if len(source_key) < 2 or source_key not in haystack:
            return {}, [], [], False, "invalid_source_grounding", assessment
        effective_path = path
        if (
            candidate.kind == "proof"
            and path.endswith("proof_strategy.source")
            and PROOF_RE.search(evidence)
            and not PROOF_SOURCE_RE.search(evidence)
        ):
            effective_path = candidate.required_paths[0]
            result["path"] = effective_path
        # 거래 목표로 PoC를 말한 답변을 현재 진행 단계로 확대 해석하지 않는다.
        if path == "transaction_strategy.current_stage" and re.search(r"(?:우선|목표)", answer) \
                and not re.search(r"(?:현재|지금|단계|진행\s*중)", answer):
            result["status"] = "ambiguous"
            result["missing_detail"] = "거래 목표와 현재 단계가 구분되지 않음"
            continue
        # 예산 존재 이유는 특정 제안을 선택하는 이유와 다른 필드로 보존한다.
        if path.endswith("purchase_logic.purchase_reason") and BUDGET_REASON_ONLY_RE.search(evidence) \
                and not CHOICE_REASON_RE.search(evidence):
            replacement = path.replace("purchase_reason", "why_budget")
            if replacement in allowed:
                effective_path = replacement
                result["path"] = replacement
                result["status"] = "partial"
                result["missing_detail"] = "예산 배정 이유는 확인됐지만 제안 선택 이유는 추가 확인 필요"
        # 자료가 있는 위치만 말한 답변은 대표 실적이 아니라 출처로 분류한다.
        if path.endswith("proof_strategy.primary_proof") and PROOF_SOURCE_RE.search(evidence) \
                and not SPECIFIC_PROOF_RE.search(evidence):
            replacement = path.replace("primary_proof", "source")
            if replacement in allowed:
                effective_path = replacement
                result["path"] = replacement
                result["status"] = "partial"
                result["missing_detail"] = "자료 출처는 확인됐지만 구체적인 대표 사례는 추가 확인 필요"
        value = _normalized_from_evidence(effective_path, evidence, record.get("normalized_candidate"))
        if not _has_value(value) or not _unicode_is_sane(value):
            result["status"] = "ambiguous"
            result["missing_detail"] = "정규화 값이 비었거나 문자 무결성 검사에 실패함"
            continue
        if field_fit == "partial":
            if _partial_answer_is_sufficient(candidate, effective_path, evidence, assessment):
                result["status"] = "supported"
                result["missing_detail"] = None
            else:
                result["status"] = "partial"
                result["missing_detail"] = (
                    assessment.get("missing_reason")
                    or result.get("missing_detail")
                    or "질문이 요구한 핵심 구분 또는 구체성 확인 필요"
                )
        if result["status"] == "partial" and _closed_choice_is_decisive(effective_path, evidence):
            # 계약 자체가 partial이면 단답 정규화가 가능해도 의미 부족 판정을 뒤집지 않는다.
            if field_fit != "partial":
                result["status"] = "supported"
                result["missing_detail"] = None
        updates[effective_path] = value
        if result["status"] == "partial":
            partial_paths.append(effective_path)
    if candidate.kind == "entry":
        primary = candidate.required_paths[0]
        alternative = next(
            (path for path in candidate.optional_paths if path.endswith("alternative_channel")),
            None,
        )
        # 주 경로 질문에 하나의 채널만 답했다면 모델이 alternative에 썼어도 주 경로로 보정한다.
        if alternative and alternative in updates and primary not in updates:
            updates[primary] = updates.pop(alternative)
            partial_paths = [primary if path == alternative else path for path in partial_paths]
            for result in field_results:
                if result.get("path") == alternative:
                    result["path"] = primary
        # 같은 채널을 주·보조 경로에 중복 저장하지 않는다.
        if alternative and primary in updates and updates.get(alternative) == updates[primary]:
            updates.pop(alternative, None)
            partial_paths = [path for path in partial_paths if path != alternative]
    if not updates:
        return {}, partial_paths, field_results, False, "parsed_no_value", assessment
    activate_b = any(
        path.startswith("strategy_tracks.B.") for path in updates
    )
    return updates, partial_paths, field_results, activate_b, "parsed", assessment


def extract_answer_with_api(
    client: Any,
    state: Dict[str, Any],
    candidate: QuestionCandidate,
    question: str,
    answer: str,
) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]], bool, str, int, str, Dict[str, Any]]:
    """답변을 API로 우선 추출하고 기술적 실패 상태를 구분한다."""
    allowed_paths = extraction_allowed_paths(candidate)
    payload = {
        "question_kind": candidate.kind,
        "track_id": candidate.track_id,
        "allowed_paths": allowed_paths,
        "field_rules": {path: _rule_for_path(path) for path in allowed_paths if _rule_for_path(path)},
        "question_contract": _semantic_contract_for_candidate(candidate),
        "confirmed_context": {
            "solution": _confirmed_anchor(state, "offer.chosen_solution"),
            "goal": _confirmed_anchor(state, "transaction_strategy.primary_goal"),
            "market_A": _confirmed_anchor(state, "strategy_tracks.A.market.country_or_region"),
            "target_A": _confirmed_anchor(state, "strategy_tracks.A.target.organization_type"),
            "reviewer_A": _confirmed_anchor(state, "strategy_tracks.A.recipient.first_reviewer"),
            "primary_cta_A": _confirmed_anchor(state, "strategy_tracks.A.cta_strategy.primary_cta"),
        },
        "question": question,
        "answer": answer,
    }
    last_status = "model_failed"
    last_raw = ""
    for attempt in range(MAX_TECHNICAL_RETRIES + 1):
        if attempt:
            payload["repair_instruction"] = (
                "이전 응답은 JSON 계약 또는 원문 근거 검증에 실패했습니다. 같은 답변만 근거로 다시 출력하세요."
            )
        try:
            raw = chat(
                client,
                ANSWER_EXTRACT_SYS,
                [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                temperature=0.0,
                max_tokens=1300,
                enable_thinking=False,
            )
        except Exception:
            last_status = "model_failed"
            continue
        last_raw = raw
        parsed, parse_status = parse_json_object(raw)
        if parsed is None:
            last_status = parse_status
            payload["invalid_previous_output"] = raw[:2500]
            continue
        updates, partial_paths, field_results, activate_b, validation_status, assessment = validate_extraction_response(
            parsed, answer, candidate, allowed_paths
        )
        if validation_status in {"parsed", "parsed_no_value"}:
            return (
                updates, partial_paths, field_results, activate_b,
                validation_status, attempt, raw, assessment,
            )
        last_status = validation_status
        payload["invalid_previous_output"] = raw[:2500]
    return {}, [], [], False, last_status, MAX_TECHNICAL_RETRIES, last_raw, {}


ORGANIZATION_RE = re.compile(
    r"(?:대기업|중견기업|중소기업|기업|스타트업|공공기관|정부기관|관공서|지자체|공기업|"
    r"호텔|리조트|숙박업체|병원|의료기관|학교|대학|연구기관|연구소|복지기관|비영리기관|제조사|공급업체|"
    r"유통사|도매상|소매상|브랜드|에이전시|농장|농가|OEM|Tier\s*1)",
    re.IGNORECASE,
)

DEPARTMENT_RE = re.compile(
    r"(?:부서|부문|본부|팀|담당자?|직무|구매부|조달부|마케팅부|영업부|"
    r"ESG\s*팀|CSR\s*팀|R\s*&\s*D|연구개발팀)",
    re.IGNORECASE,
)


def _department_only_target_answer(answer: str) -> Optional[str]:
    """고객 조직 질문에 부서만 답한 경우 검토 부서 값으로 돌려보낸다."""
    text = _as_short_text(answer, 70)
    if not text or not DEPARTMENT_RE.search(text):
        return None
    if ORGANIZATION_RE.search(text):
        return None
    return text


def local_fallback_updates(candidate: QuestionCandidate, answer: str) -> Dict[str, Any]:
    """API 기술 실패 때만 사용하는 보수적인 로컬 추출 규칙이다."""
    text = re.sub(r"\s+", " ", answer).strip()
    if not text:
        return {}
    path = candidate.required_paths[0]
    if candidate.variant != "main":
        if len(candidate.required_paths) != 1:
            return {}
        value = normalize_path_value(path, text)
        return {path: value} if _has_value(value) else {}
    if candidate.kind == "offer":
        solution = normalize_path_value(path, text)
        updates = {path: solution} if solution and len(text) <= 100 else {}
        unit = _match_token(text, TRANSACTION_UNIT_TOKENS, UNIT_PAIRS)
        if unit:
            updates["offer.transaction_unit"] = unit
        return updates
    if candidate.kind == "goal":
        token = _match_token(text, GOAL_TOKENS, GOAL_PAIRS)
        return {path: token} if token else {}
    if candidate.kind == "market":
        value = normalize_path_value(path, text)
        return {path: value} if value else {}
    if candidate.kind == "target":
        matches = list(dict.fromkeys(match.group(0) for match in ORGANIZATION_RE.finditer(text)))
        value = "·".join(matches[:3]) if matches else None
        return {path: value} if value else {}
    if candidate.kind == "recipient":
        value = _as_short_text(text, 70)
        return {path: value} if value and len(text) <= 70 else {}
    if candidate.kind == "purchase":
        value = _as_short_text(text, 100)
        return {path: value} if value and len(text) <= 100 else {}
    if candidate.kind == "differentiator":
        value = _as_short_text(text, 180)
        return {path: value} if value else {}
    if candidate.kind == "proof":
        value = normalize_path_value(path, text)
        return {path: value} if value else {}
    if candidate.kind == "entry":
        found: List[Tuple[int, str]] = []
        lowered = text.lower()
        for keywords, token in CHANNEL_PAIRS:
            positions = [lowered.find(keyword.lower()) for keyword in keywords]
            positions = [position for position in positions if position >= 0]
            if positions:
                found.append((min(positions), token))
        tokens = [token for _, token in sorted(found)]
        tokens = list(dict.fromkeys(tokens))
        updates = {path: tokens[0]} if tokens else {}
        if len(tokens) > 1:
            updates[_track_path(candidate.track_id or "A", "entry_strategy.alternative_channel")] = tokens[1]
        return updates
    if candidate.kind == "cta":
        token = _match_token(text, CTA_TOKENS, CTA_PAIRS)
        return {path: token} if token else {}
    if candidate.kind == "conversion":
        flow = _as_string_list(text, max_items=6)
        return {path: flow} if flow else {}
    if candidate.kind == "execution":
        value = _as_short_text(text, 140)
        return {path: value} if value and len(text) <= 140 else {}
    return {}


def safe_local_fallback_updates(
    candidate: QuestionCandidate, answer: str
) -> Dict[str, Any]:
    """API 기술 실패 시 오분류 가능성이 낮은 닫힌 단답만 복구한다."""
    if NON_ANSWER_FEEDBACK_RE.search(answer):
        return {}
    if candidate.kind in {"offer", "goal", "market", "target", "entry", "cta"}:
        return local_fallback_updates(candidate, answer)
    if candidate.kind == "recipient" and DEPARTMENT_RE.search(answer) \
            and not ORGANIZATION_RE.search(answer):
        return local_fallback_updates(candidate, answer)
    return {}


BROAD_TARGET_RE = re.compile(
    r"(?:가능한\s*많은|모든|전체|전반적|다양한|가리지\s*않|기업과\s*공공기관|기업,\s*공공기관)",
    re.IGNORECASE,
)


def semantic_rescue_updates(
    candidate: QuestionCandidate, answer: str
) -> Tuple[Dict[str, Any], List[str]]:
    """구형 응답에서 의미가 결정적인 단답만 보수적으로 구조한다."""
    if candidate.kind not in {
        "offer", "goal", "market", "target", "recipient", "entry", "cta",
    }:
        return {}, []
    updates = safe_local_fallback_updates(candidate, answer)
    partial_paths: List[str] = []
    return updates, partial_paths


def _update_partial_paths(
    state: Dict[str, Any], touched: Sequence[str], partial_paths: Sequence[str]
) -> None:
    """부분 답변은 값은 보존하되 해결된 필드로 계산하지 않는다."""
    current = set(state["interview_state"].get("partial_paths", []))
    current.difference_update(touched)
    current.update(path for path in partial_paths if path in touched)
    state["interview_state"]["partial_paths"] = sorted(current)


def apply_answer(
    state: Dict[str, Any],
    client: Any,
    *,
    question_id: str,
    candidate: QuestionCandidate,
    question: str,
    answer: str,
) -> Dict[str, Any]:
    """대표 답변 한 건을 이력으로 저장하고 전략 상태에 반영한다."""
    answer_id = add_answer_record(
        state,
        question_id=question_id,
        target_paths=list(candidate.all_paths),
        value=answer,
        origin="user_stated",
        source_type="current_user_interview",
        is_canonical=False,
    )
    if NON_ANSWER_FEEDBACK_RE.search(answer):
        semantic_assessment = {
            "answer_role": "unrelated_answer",
            "field_fit": "mismatch",
            "redirect_path": None,
            "missing_reason": "전략 답변이 아니라 질문 방식에 대한 반응으로 분류",
            "confidence": 1.0,
            "evidence": answer,
        }
        event = {
            "question_id": question_id,
            "kind": candidate.kind,
            "extraction_status": "user_feedback",
            "technical_retries": 0,
            "technical_fallback_used": False,
            "semantic_rescue_used": False,
            "field_results": [],
            "semantic_assessment": semantic_assessment,
            "touched_paths": [],
            "raw_response": "",
        }
        state["interview_state"]["extraction_events"].append(event)
        return {
            "resolution_status": "semantically_insufficient",
            "answer_id": answer_id,
            "touched_paths": [],
            "technical_retries": 0,
            "extraction_status": "user_feedback",
            "local_fallback_used": False,
            "semantic_rescue_used": False,
            "field_results": [],
            "semantic_assessment": semantic_assessment,
            "raw_response": "",
        }
    if candidate.kind == "target":
        reviewer = _department_only_target_answer(answer)
        if reviewer:
            reviewer_path = _track_path(candidate.track_id or "A", "recipient.first_reviewer")
            touched = apply_updates(
                state,
                {reviewer_path: reviewer},
                origin="user_stated",
                answer_id=answer_id,
                invalidate=True,
            )
            state["answer_records"][-1]["target_paths"] = touched
            state["answer_records"][-1]["is_canonical"] = bool(touched)
            field_results = [{
                "path": reviewer_path,
                "status": "supported",
                "evidence": answer,
                "missing_detail": "고객 조직이 아니라 검토 부서로 분류",
            }]
            semantic_assessment = {
                "answer_role": "reviewer_department",
                "field_fit": "redirect",
                "redirect_path": reviewer_path,
                "missing_reason": "고객 조직 유형이 아니라 검토 부서가 답변됨",
                "confidence": 1.0,
                "evidence": answer,
            }
            state["interview_state"]["extraction_events"].append({
                "question_id": question_id,
                "kind": candidate.kind,
                "extraction_status": "semantic_redirect",
                "technical_retries": 0,
                "technical_fallback_used": False,
                "semantic_rescue_used": True,
                "field_results": field_results,
                "semantic_assessment": semantic_assessment,
                "touched_paths": touched,
                "raw_response": "",
            })
            return {
                "resolution_status": "partially_resolved",
                "answer_id": answer_id,
                "touched_paths": touched,
                "technical_retries": 0,
                "extraction_status": "semantic_redirect",
                "local_fallback_used": False,
                "semantic_rescue_used": True,
                "field_results": field_results,
                "semantic_assessment": semantic_assessment,
                "raw_response": "",
            }
    if EXPLICIT_UNKNOWN_RE.fullmatch(answer.strip()):
        mark_explicit_unknown(state, candidate.required_paths, answer_id=answer_id)
        state["answer_records"][-1]["target_paths"] = list(candidate.required_paths)
        state["answer_records"][-1]["is_canonical"] = True
        return {
            "resolution_status": "explicit_unknown",
            "answer_id": answer_id,
            "touched_paths": [],
            "technical_retries": 0,
            "extraction_status": "explicit_unknown",
        }

    (
        updates,
        partial_paths,
        field_results,
        activate_b,
        extraction_status,
        technical_retries,
        raw_response,
        semantic_assessment,
    ) = extract_answer_with_api(
        client, state, candidate, question, answer
    )
    if semantic_assessment.get("field_fit") == "explicit_unknown":
        mark_explicit_unknown(state, candidate.required_paths, answer_id=answer_id)
        state["answer_records"][-1]["target_paths"] = list(candidate.required_paths)
        state["answer_records"][-1]["is_canonical"] = True
        event = {
            "question_id": question_id,
            "kind": candidate.kind,
            "extraction_status": "explicit_unknown",
            "technical_retries": technical_retries,
            "technical_fallback_used": False,
            "semantic_rescue_used": False,
            "field_results": field_results,
            "semantic_assessment": semantic_assessment,
            "touched_paths": [],
            "raw_response": raw_response[:5000],
        }
        state["interview_state"]["extraction_events"].append(event)
        return {
            "resolution_status": "explicit_unknown",
            "answer_id": answer_id,
            "touched_paths": [],
            "technical_retries": technical_retries,
            "extraction_status": "explicit_unknown",
            "local_fallback_used": False,
            "semantic_rescue_used": False,
            "field_results": field_results,
            "semantic_assessment": semantic_assessment,
            "raw_response": raw_response,
        }
    local_used = False
    semantic_rescue_used = False
    if extraction_status in LOCAL_FALLBACK_FAILURE_STATUSES:
        updates = safe_local_fallback_updates(candidate, answer)
        local_used = True
    elif (
        semantic_assessment.get("field_fit") == "legacy"
        and (extraction_status == "parsed_no_value" or not any(
        path in updates for path in candidate.required_paths
        ))
    ):
        rescue, rescued_partial = semantic_rescue_updates(candidate, answer)
        for path, value in rescue.items():
            updates.setdefault(path, value)
        partial_paths = sorted(set(partial_paths + rescued_partial))
        semantic_rescue_used = bool(rescue)
        if rescue:
            field_results.extend({
                "path": path,
                "status": "partial" if path in rescued_partial else "supported",
                "evidence": answer,
                "missing_detail": "우선순위 확인 필요" if path in rescued_partial else None,
            } for path in rescue)
    if activate_b:
        ensure_track(state, "B")
    touched = apply_updates(
        state, updates, origin="user_stated", answer_id=answer_id, invalidate=True
    ) if updates else []
    _update_partial_paths(state, touched, partial_paths)
    state["answer_records"][-1]["target_paths"] = touched
    state["answer_records"][-1]["is_canonical"] = bool(touched)
    required_resolved = all(is_user_resolved(state, path) for path in candidate.required_paths)
    resolution = "resolved" if required_resolved else (
        "partially_resolved" if touched else
        "semantically_insufficient" if extraction_status == "parsed_no_value" else
        "failed" if extraction_status in TECHNICAL_FAILURE_STATUSES else
        "semantically_insufficient"
    )
    event = {
        "question_id": question_id,
        "kind": candidate.kind,
        "extraction_status": extraction_status,
        "technical_retries": technical_retries,
        "technical_fallback_used": local_used,
        "semantic_rescue_used": semantic_rescue_used,
        "field_results": field_results,
        "semantic_assessment": semantic_assessment,
        "touched_paths": touched,
        "raw_response": raw_response[:5000],
    }
    state["interview_state"]["extraction_events"].append(event)
    return {
        "resolution_status": resolution,
        "answer_id": answer_id,
        "touched_paths": touched,
        "technical_retries": technical_retries,
        "extraction_status": extraction_status,
        "local_fallback_used": local_used,
        "semantic_rescue_used": semantic_rescue_used,
        "field_results": field_results,
        "semantic_assessment": semantic_assessment,
        "raw_response": raw_response,
    }


# ============================================================================
# 완료 판정, 후속 확인, 최종 요약
# ============================================================================

ANCHOR_PATHS_A = [
    "offer.chosen_solution",
    "offer.differentiator",
    "transaction_strategy.primary_goal",
    "strategy_tracks.A.market.country_or_region",
    "strategy_tracks.A.target.organization_type",
    "strategy_tracks.A.purchase_logic.why_budget",
    "strategy_tracks.A.recipient.first_reviewer",
    "strategy_tracks.A.proof_strategy.primary_proof",
    "strategy_tracks.A.entry_strategy.primary_channel",
    "strategy_tracks.A.cta_strategy.primary_cta",
    "strategy_tracks.A.cta_strategy.conversion_flow",
]

PATH_LABELS = {
    "offer.chosen_solution": "전면 솔루션",
    "offer.differentiator": "다른 대안과의 차이점",
    "transaction_strategy.primary_goal": "첫 거래 목표",
    "strategy_tracks.A.market.country_or_region": "주력 시장",
    "strategy_tracks.A.target.organization_type": "우선 고객",
    "strategy_tracks.A.purchase_logic.why_budget": "예산 배경",
    "strategy_tracks.A.recipient.first_reviewer": "첫 검토 부서",
    "strategy_tracks.A.proof_strategy.primary_proof": "대표 실적·증거",
    "strategy_tracks.A.proof_strategy.missing_proof_plan": "대체 증거 마련 계획",
    "strategy_tracks.A.proof_strategy.sample_or_demo_availability": "증거·샘플 제공 시점",
    "strategy_tracks.A.entry_strategy.primary_channel": "진입 채널",
    "strategy_tracks.A.cta_strategy.primary_cta": "처음 요청할 다음 행동",
    "strategy_tracks.A.cta_strategy.conversion_flow": "후속 전환 흐름",
    "strategy_tracks.A.execution_constraints.highest_risk": "가장 큰 실행 위험",
}


def _execution_paths(track_id: str) -> List[str]:
    """v4에서는 핵심 질문 밖의 온톨로지 세부값을 완료 조건으로 요구하지 않는다."""
    return []


def _followup_text(path: str) -> str:
    """미해결 경로를 사람이 바로 사용할 후속 질문으로 바꾼다."""
    track_match = re.match(r"strategy_tracks\.([AB])\.", path)
    track = "비교 진출안에서 " if track_match and track_match.group(1) == "B" else (
        "이번 진출안에서 " if track_match else ""
    )
    suffix = re.sub(r"^strategy_tracks\.[AB]\.", "", path)
    prompts = {
        "transaction_strategy.success_criteria": "PoC가 성공했다고 판단할 기준은 무엇인가요?",
        "cta_strategy.conversion_flow": "첫 요청 이후 샘플·PoC·계약은 어떤 순서로 이어지나요?",
        "cta_strategy.core_message": "첫 제안에서 가장 앞에 내세울 메시지는 무엇인가요?",
        "cta_strategy.acceptance_criteria": "상대가 다음 단계로 넘어갈 수락 기준은 무엇인가요?",
        "market.country_or_region": "가장 먼저 공략할 시장은 어디인가요?",
        "market.regulation_or_localization": "해당 시장에 맞춰 바꿔야 할 규제·언어·운영 조건은 무엇인가요?",
        "target.organization_type": "가장 먼저 제안할 고객 조직은 누구인가요?",
        "recipient.first_reviewer": "제안을 처음 검토할 부서는 어디인가요?",
        "purchase_logic.purchase_reason": "다른 대안이 아니라 이 제안을 선택할 직접적인 이유는 무엇인가요?",
        "purchase_logic.why_budget": "고객이 사용할 예산 항목이나 사업상 배경은 무엇인가요?",
        "proof_strategy.primary_proof": "가장 먼저 보여줄 실제 근거는 무엇인가요?",
        "proof_strategy.missing_proof_plan": "현재 증거가 없다면 어떤 사례나 검증 자료를 먼저 만들 계획인가요?",
        "entry_strategy.primary_channel": "고객에게 처음 접근할 경로는 무엇인가요?",
        "execution_constraints.regulation_certification": "진출 전에 확인할 규제나 인증은 무엇인가요?",
        "execution_constraints.localization_support": "현지에서 필요한 언어·운영 지원은 무엇인가요?",
        "cta_strategy.primary_cta": "첫 제안에서 요청할 행동은 무엇인가요?",
    }
    return track + prompts.get(suffix, f"{PATH_LABELS.get(path, path)}을 확인해 주세요.")


def compute_completion(state: Dict[str, Any], *, interview_finished: Optional[bool] = None) -> Dict[str, Any]:
    """인터뷰 종료, 앵커 완료, 실행 준비 완료를 서로 다른 상태로 계산한다."""
    refresh_statuses(state)
    if interview_finished is not None:
        state["completion"]["session_closed"] = interview_finished
    # 명시적 미정은 같은 질문을 반복하지 않게 해 주지만, Proof를 제외한 핵심값이
    # 실제로 비어 있다면 실행 가능한 앵커로 계산하지 않는다.
    proof_anchor = "strategy_tracks.A.proof_strategy.primary_proof"
    missing_anchor = [
        path for path in ANCHOR_PATHS_A
        if not is_user_resolved(state, path)
        or (path != proof_anchor and not _has_value(get_path(state, path)))
    ]
    execution_paths: List[str] = []
    for track_id in _active_track_ids(state):
        execution_paths.extend(_execution_paths(track_id))
    unresolved_execution = [
        path for path in execution_paths
        if not is_user_resolved(state, path) or not _has_value(get_path(state, path))
    ]
    # 비교 트랙을 활성화했다면 시장·고객·구매 논리·채널·CTA의 관계가 비어 있는
    # 상태를 실행 준비 완료로 보지 않는다. 주력 앵커 완료 여부와는 별도로 관리한다.
    if "B" in _active_track_ids(state):
        b_core = [
            path.replace("strategy_tracks.A.", "strategy_tracks.B.", 1)
            for path in ANCHOR_PATHS_A
            if path.startswith("strategy_tracks.A.")
        ]
        unresolved_execution.extend(
            path for path in b_core
            if not is_user_resolved(state, path) or not _has_value(get_path(state, path))
        )
    # 대표 Proof가 현재 없다고 확정된 경우에는 향후 어떤 근거를 만들지 계획이 있어야 한다.
    for track_id in _active_track_ids(state):
        proof_path = _track_path(track_id, "proof_strategy.primary_proof")
        plan_path = _track_path(track_id, "proof_strategy.missing_proof_plan")
        if proof_path in state["interview_state"]["explicit_unknown_paths"] and not _has_value(get_path(state, plan_path)):
            unresolved_execution.append(plan_path)
    unresolved_execution = list(dict.fromkeys(unresolved_execution))
    stale = sorted(set(state["interview_state"]["stale_paths"]))
    conflicts = sorted(set(state["interview_state"]["blocking_conflicts"]))
    anchor_complete = not missing_anchor
    strategy_ready = anchor_complete and not unresolved_execution and not stale and not conflicts
    followup_paths = list(dict.fromkeys(missing_anchor + stale + unresolved_execution))[:5]
    completion = state["completion"]
    completion.update({
        # 호환 필드는 성공 여부가 아니라 실행 루프가 닫혔는지를 나타낸다.
        "interview_finished": completion.get("session_closed", False),
        "anchor_complete": anchor_complete,
        "strategy_ready": strategy_ready,
        "clarification_pending": not completion.get("final_confirmed", False),
        "missing_anchor_paths": missing_anchor,
        "unresolved_execution_paths": unresolved_execution,
        "stale_paths": stale,
        "blocking_conflicts": conflicts,
        "followup_questions": [_followup_text(path) for path in followup_paths],
    })
    return completion


def strategy_summary_lines(state: Dict[str, Any]) -> List[str]:
    """최종 확인과 Markdown 산출물에 공통으로 쓸 확정 전략 요약을 만든다."""
    lines = [
        "[공통 제안]",
        f"- 전면 솔루션: {display_path(state, 'offer.chosen_solution')}",
        f"- 다른 대안과의 차이점: {display_path(state, 'offer.differentiator')}",
        f"- 진행 목표: {display_path(state, 'transaction_strategy.primary_goal')} 고객 발굴",
    ]
    active_ids = _active_track_ids(state)
    for track_id in active_ids:
        prefix = f"strategy_tracks.{track_id}."
        label = get_path(state, prefix + "label") or ("주력 진출안" if track_id == "A" else "비교 진출안")
        heading = "[이번 해외 진출 전략]" if len(active_ids) == 1 else f"[{label}]"
        lines.extend([
            "",
            heading,
            f"- 시장: {display_path(state, prefix + 'market.country_or_region')}",
            f"- 우선 고객: {display_path(state, prefix + 'target.organization_type')}",
            f"- 예산 배경: {display_path(state, prefix + 'purchase_logic.why_budget')}",
            f"- 첫 검토 부서: {display_path(state, prefix + 'recipient.first_reviewer')}",
            f"- 대표 실적·증거: {display_path(state, prefix + 'proof_strategy.primary_proof')}",
            f"- 진입 채널: {display_path(state, prefix + 'entry_strategy.primary_channel')}",
            f"- 처음 요청할 다음 행동: {display_path(state, prefix + 'cta_strategy.primary_cta')}",
            f"- 후속 전환: {display_path(state, prefix + 'cta_strategy.conversion_flow')}",
        ])
    return lines


# ============================================================================
# 최종 확인 답변 처리
# ============================================================================

FINAL_LABEL_PATHS = {
    "솔루션": "offer.chosen_solution",
    "전면 솔루션": "offer.chosen_solution",
    "차별점": "offer.differentiator",
    "다른 대안과의 차이점": "offer.differentiator",
    "거래 목표": "transaction_strategy.primary_goal",
    "목표": "transaction_strategy.primary_goal",
    "시장": "strategy_tracks.A.market.country_or_region",
    "고객": "strategy_tracks.A.target.organization_type",
    "핵심 고객": "strategy_tracks.A.target.organization_type",
    "예산 배경": "strategy_tracks.A.purchase_logic.why_budget",
    "검토 부서": "strategy_tracks.A.recipient.first_reviewer",
    "대표 proof": "strategy_tracks.A.proof_strategy.primary_proof",
    "대표 근거": "strategy_tracks.A.proof_strategy.primary_proof",
    "진입 채널": "strategy_tracks.A.entry_strategy.primary_channel",
    "채널": "strategy_tracks.A.entry_strategy.primary_channel",
    "첫 cta": "strategy_tracks.A.cta_strategy.primary_cta",
    "다음 행동": "strategy_tracks.A.cta_strategy.primary_cta",
    "후속 전환": "strategy_tracks.A.cta_strategy.conversion_flow",
    "전환 흐름": "strategy_tracks.A.cta_strategy.conversion_flow",
}


def _local_final_review_updates(answer: str) -> Dict[str, Any]:
    """`항목=값`과 대표적인 한국어 수정 표현을 보수적으로 읽는다."""
    updates: Dict[str, Any] = {}
    for chunk in re.split(r"[\n,;]+", answer):
        if "=" not in chunk:
            continue
        label, value = (part.strip() for part in chunk.split("=", 1))
        path = FINAL_LABEL_PATHS.get(label.lower()) or FINAL_LABEL_PATHS.get(label)
        if path and value:
            updates[path] = value
    # "전면 솔루션은 ESG 캠페인", "시장은 미국으로" 형태
    labels = sorted(FINAL_LABEL_PATHS, key=len, reverse=True)
    for label in labels:
        pattern = rf"{re.escape(label)}\s*(?:은|는|을|를|이|가)?\s*(?:=|:|으로|로)?\s*([^,;\n?]+)"
        match = re.search(pattern, answer, re.IGNORECASE)
        if not match:
            continue
        value = re.sub(r"\s*(?:으로|로|바꿔줘|변경해줘|입니다|이에요|예요)\s*$", "", match.group(1)).strip()
        path = FINAL_LABEL_PATHS[label]
        if value and len(value) <= 100:
            updates[path] = value
    # "ESG 캠페인을 전면 솔루션으로"처럼 값이 항목보다 먼저 오는 표현
    solution_match = re.search(
        r"([^,;\n?]{2,80}?)(?:을|를)\s*전면\s*솔루션(?:으로|로)", answer, re.IGNORECASE
    )
    if solution_match:
        updates["offer.chosen_solution"] = solution_match.group(1).strip()
    return updates


def _final_review_requests_explanation(answer: str) -> bool:
    """최종 답변에 내부 용어 설명이나 일반 질문이 포함됐는지 확인한다."""
    return bool(
        "?" in answer
        or re.search(r"(?:뭐야|무엇(?:인가요|이지|이야)?|설명해|뜻이야|의미야)", answer, re.IGNORECASE)
    )


def apply_final_review(
    state: Dict[str, Any], client: Any, *, question_id: str, question: str, answer: str
) -> Dict[str, Any]:
    """최종 확인에서 명시적으로 수정된 전략값만 반영한다."""
    answer_id = add_answer_record(
        state,
        question_id=question_id,
        target_paths=[],
        value=answer,
        origin="user_stated",
        source_type="current_user_final_review",
        is_canonical=False,
    )
    if CONFIRM_RE.fullmatch(answer.strip()):
        return {"resolution_status": "confirmed_summary", "answer_id": answer_id, "touched_paths": []}

    allowed_paths = sorted(set(FINAL_LABEL_PATHS.values()))
    pseudo = QuestionCandidate(
        "final_review", None, tuple(), tuple(allowed_paths),
        "최종 전략 수정 반영", 0, 0, 0,
    )
    (
        updates,
        partial_paths,
        field_results,
        activate_b,
        status,
        retries,
        raw_response,
        semantic_assessment,
    ) = extract_answer_with_api(
        client, state, pseudo, question, answer
    )
    local_updates = _local_final_review_updates(answer)
    # 최종 수정은 사용자가 직접 표현한 값이므로 API가 놓친 경로만 자연어 규칙으로 보완한다.
    for path, value in local_updates.items():
        updates.setdefault(path, value)
    if activate_b:
        ensure_track(state, "B")
    touched = apply_updates(
        state, updates, origin="user_stated", answer_id=answer_id, invalidate=True
    ) if updates else []
    _update_partial_paths(state, touched, partial_paths)
    state["answer_records"][-1]["target_paths"] = touched
    state["answer_records"][-1]["is_canonical"] = bool(touched)
    clarification_requested = _final_review_requests_explanation(answer)
    event = {
        "question_id": question_id,
        "kind": "final_review",
        "extraction_status": status,
        "technical_retries": retries,
        "technical_fallback_used": status in TECHNICAL_FAILURE_STATUSES,
        "semantic_rescue_used": bool(local_updates),
        "field_results": field_results,
        "semantic_assessment": semantic_assessment,
        "touched_paths": touched,
        "raw_response": raw_response[:5000],
    }
    state["interview_state"]["extraction_events"].append(event)
    return {
        "resolution_status": "resolved" if touched else "semantically_insufficient",
        "answer_id": answer_id,
        "touched_paths": touched,
        "technical_retries": retries,
        "extraction_status": status,
        "local_fallback_used": status in TECHNICAL_FAILURE_STATUSES,
        "semantic_rescue_used": bool(local_updates),
        "field_results": field_results,
        "semantic_assessment": semantic_assessment,
        "clarification_requested": clarification_requested,
    }


# ============================================================================
# Google Grounding 리서치와 비확정 prefill
# ============================================================================

RESEARCH_DOC_GEMINI_SYS = """\
너는 글로벌 B2B 진출 사전 리서처다. Google 검색 근거가 있는 사실만 사용한다.
한 번의 통합 리서치로 회사의 제품·서비스, 실제 사업 단계, 해외 활동, 고객·협업,
검증 자료, 진입 경로 후보를 조사한다. 확인되지 않은 수치와 전략을 만들지 않는다.
출처 URL과 확인 여부를 포함한 한국어 Markdown 문서를 작성한다.
"""

RESEARCH_FACT_SYS = """\
너는 공개 리서치에서 인터뷰 질문의 배경으로 쓸 검증 가능한 사실만 분류한다.

허용 category:
- market_activity: 과거 또는 현재의 국가·지역 활동
- customer_collaboration: 실제 고객군이나 협업 사례
- solution_mechanism: 특정 제품·서비스가 작동하는 방식
- proof: 프로젝트·계약·성과·인증·테스트 등 실적 근거

금지:
- 회사의 미션·정체성을 고객, 구매 이유, 검토 부서 또는 목표 시장으로 확대 해석
- 전략 추천, 우선순위, 가능성, 추정, 가설
- 문서에 직접 없는 사실

fact와 source_text는 리서치 문서에서 글자 그대로 복사한 짧은 구절이어야 한다.
최대 8개만 JSON 하나로 출력한다.
{"facts":[{"category":"market_activity","fact":"문서의 짧은 원문","source_text":"fact를 포함한 문서 원문"}]}
"""

RESEARCH_FACT_CATEGORIES = {
    "market_activity", "customer_collaboration", "solution_mechanism", "proof",
}

RESEARCH_INFERENCE_RE = re.compile(
    r"(?:유력|적합|가능성|추정|추천|우선\s*시장|타깃으로|구매\s*이유는|검토\s*부서는)",
    re.IGNORECASE,
)


def extract_research_facts(client: Any, research_doc: str) -> List[Dict[str, str]]:
    """문서 원문으로 역검증되는 사실 카드만 질문용으로 남긴다."""
    if not research_doc or research_doc == "(검색 비활성화)":
        return []
    try:
        raw = chat(
            client,
            RESEARCH_FACT_SYS,
            [{"role": "user", "content": research_doc[:24000]}],
            temperature=0.0,
            max_tokens=1600,
            enable_thinking=False,
        )
    except Exception:
        return []
    parsed, status = parse_json_object(raw)
    if status != "parsed" or not isinstance(parsed.get("facts"), list):
        return []
    document_key = _compare_text(research_doc)
    result: List[Dict[str, str]] = []
    for record in parsed["facts"]:
        if not isinstance(record, dict) or record.get("category") not in RESEARCH_FACT_CATEGORIES:
            continue
        fact = _as_short_text(record.get("fact"), 140)
        source = _as_short_text(record.get("source_text"), 240)
        if not fact or not source or RESEARCH_INFERENCE_RE.search(fact):
            continue
        fact_key = _compare_text(fact)
        source_key = _compare_text(source)
        if len(fact_key) < 4 or fact_key not in source_key or source_key not in document_key:
            continue
        item = {
            "category": str(record["category"]),
            "fact": fact,
            "source_text": source,
        }
        if item not in result:
            result.append(item)
        if len(result) >= 8:
            break
    return result


RESEARCH_PREFILL_SYS = """\
너는 공개 리서치 문서를 전략 인터뷰의 비확정 초안으로 구조화한다.
allowed_paths만 사용하고 문서에 직접 존재하는 정보만 출력한다.
전략 선택이 필요한 고객, 수신자, CTA는 추측하지 않는다.
동명이인·검색 노출·회사 식별 문제를 고객의 pain point나 current alternative로 분류하지 않는다.
offer.maturity_stage는 sample_stage, poc_stage, sellable, scalable, unknown 중 하나만 사용한다.
각 값에는 문서에서 그대로 가져온 source_text를 붙인다.
JSON 하나만 출력한다.
{"updates":[{"path":"허용 경로","normalized":"값","source_text":"문서 원문"}]}
"""

RESEARCH_PREFILL_PATHS = [
    "offer.chosen_solution",
    "offer.transaction_unit",
    "offer.customer_use_context",
    "offer.primary_customer_change",
    "offer.core_feature",
    "offer.maturity_stage",
    "transaction_strategy.current_stage",
    "strategy_tracks.A.proof_strategy.primary_proof",
    "strategy_tracks.A.proof_strategy.source",
    "strategy_tracks.A.proof_strategy.verification_status",
]


def google_research(company: str, hints: str) -> str:
    """Gemini 내장 Google 검색 한 번으로 근거가 포함된 리서치 문서를 만든다."""
    from google import genai
    from google.genai import types

    if not GOOGLE_API_KEY:
        raise SystemExit("[설정 필요] SEARCH_PROVIDER='google'이면 GOOGLE_API_KEY가 필요합니다.")
    google_client = genai.Client(api_key=GOOGLE_API_KEY)
    config = types.GenerateContentConfig(
        system_instruction=RESEARCH_DOC_GEMINI_SYS,
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.3,
        max_output_tokens=8000,
    )
    contents = [types.Content(role="user", parts=[types.Part.from_text(
        text=(
            f"대상 기업: {company}\n검색 힌트: {hints}\n\n"
            "제품·서비스, 거래 단계, 시장 활동, 고객·파트너, 검증 자료와 실행 제약을 "
            "한 번의 통합 Google 검색으로 조사해 주세요."
        )
    )])]
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = google_client.models.generate_content(
                model=GEMINI_RESEARCH_MODEL,
                contents=contents,
                config=config,
            )
            text = (getattr(response, "text", None) or "").strip()
            if not text:
                parts: List[str] = []
                for candidate in getattr(response, "candidates", None) or []:
                    content = getattr(candidate, "content", None)
                    for part in getattr(content, "parts", None) or []:
                        if getattr(part, "text", None):
                            parts.append(part.text)
                text = "\n".join(parts).strip()
            queries: List[str] = []
            chunks: List[Any] = []
            for candidate in getattr(response, "candidates", None) or []:
                metadata = getattr(candidate, "grounding_metadata", None)
                if metadata:
                    queries.extend(getattr(metadata, "web_search_queries", None) or [])
                    chunks.extend(getattr(metadata, "grounding_chunks", None) or [])
            if not text:
                raise RuntimeError("Gemini 응답 본문이 비어 있습니다.")
            if not queries or not chunks:
                raise RuntimeError("응답에 Google 검색 근거가 없습니다.")
            print(f"  Google 검색 질의 {len(queries)}개, 출처 {len(chunks)}개 확인")
            return text
        except Exception as exc:
            last_error = exc
            print(f"  [Gemini 리서치 재시도 {attempt + 1}/3] {exc}")
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Gemini Google Grounding 리서치 실패: {last_error}")


def extract_research_prefill(client: Any, research_doc: str) -> Dict[str, Any]:
    """리서치 문서를 사용자 확정값이 아닌 외부 초안으로 한 번 구조화한다."""
    payload = {
        "allowed_paths": RESEARCH_PREFILL_PATHS,
        "research_doc": research_doc[:24000],
    }
    try:
        raw = chat(
            client,
            RESEARCH_PREFILL_SYS,
            [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.0,
            max_tokens=2200,
            enable_thinking=False,
        )
    except Exception:
        return {}
    parsed, status = parse_json_object(raw)
    if status != "parsed" or not isinstance(parsed.get("updates"), list):
        return {}
    updates: Dict[str, Any] = {}
    haystack = _compare_text(research_doc)
    for record in parsed["updates"]:
        if not isinstance(record, dict):
            continue
        path = record.get("path")
        source = record.get("source_text")
        if path not in RESEARCH_PREFILL_PATHS or not isinstance(source, str):
            continue
        if len(_compare_text(source)) < 2 or _compare_text(source) not in haystack:
            continue
        value = _normalized_from_evidence(path, source, record.get("normalized"))
        if _has_value(value):
            updates[path] = value
    return updates


def strategy_schema_error(state: Dict[str, Any]) -> Optional[str]:
    """현재 상태의 스키마 위반을 저장 단계 이전에 짧은 오류 문자열로 반환한다."""
    try:
        import jsonschema
    except ImportError:
        return None
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(normalize_instance(state), schema)
    except Exception as exc:
        return str(exc)
    return None


def phase_research(client: Any) -> Dict[str, Any]:
    """사전 리서치 문서, 비확정 초안, 질문용 검증 사실을 만든다."""
    print("\n=== [1] 리서치 단계 ===")
    if SEARCH_PROVIDER.lower() == "google":
        print("  Google Grounding 통합 리서치 중...")
        research_doc = google_research(TARGET_COMPANY, COMPANY_HINTS)
    else:
        print("  [경고] 검색 비활성화 — 빈 리서치 문서로 인터뷰를 진행합니다.")
        research_doc = "(검색 비활성화)"
    state = init_state(TARGET_COMPANY)
    prefill = extract_research_prefill(client, research_doc)
    research_facts = extract_research_facts(client, research_doc)
    if prefill:
        answer_id = add_answer_record(
            state,
            question_id=None,
            target_paths=list(prefill),
            value=prefill,
            origin="external_research",
            source_type="online_research",
            is_canonical=False,
        )
        apply_updates(
            state, prefill, origin="external_research", answer_id=answer_id, invalidate=False
        )
    for path, value in USER_SEED.items():
        if not is_allowed_value_path(path):
            continue
        answer_id = add_answer_record(
            state,
            question_id=None,
            target_paths=[path],
            value=value,
            origin="user_stated",
            source_type="user_seed",
            is_canonical=True,
        )
        apply_updates(
            state, {path: value}, origin="user_stated", answer_id=answer_id, invalidate=False
        )
    schema_error = strategy_schema_error(state)
    if schema_error:
        raise RuntimeError(f"리서치 초안 적용 후 스키마 위반: {schema_error}")
    compute_completion(state)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    research_path = OUT_DIR / f"{TARGET_COMPANY}_research.md"
    research_path.write_text(
        f"# {TARGET_COMPANY} — 사전 리서치\n\n{research_doc}\n",
        encoding="utf-8",
    )
    print(f"  리서치 문서 저장: {research_path}")
    facts_path = OUT_DIR / f"{TARGET_COMPANY}_research_facts.json"
    facts_path.write_text(
        json.dumps(research_facts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  질문용 검증 사실 저장: {facts_path} ({len(research_facts)}개)")
    return {
        "state": state,
        "research_doc": research_doc,
        "research_facts": research_facts,
    }


# ============================================================================
# 콘솔 입력과 인터뷰 루프
# ============================================================================

def read_human_answer() -> str:
    """빈 줄 제출 방식으로 여러 줄 대표 답변을 읽는다."""
    print("  (대표 역할로 답변 입력 — 여러 줄 가능. 빈 줄에서 Enter 제출 / '/end' 종료)")
    lines: List[str] = []
    while True:
        try:
            line = input("  대표> ")
        except EOFError:
            break
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line)
        if END_RE.fullmatch(line.strip()):
            break
    return "\n".join(lines).strip()


CEO_SYS_TEMPLATE = """\
너는 {company}의 대표다. 아래 공개 리서치 범위에서만 짧고 현실적으로 답한다.
정확한 정보를 모르면 모른다고 답한다. 컨설턴트처럼 질문하거나 새로운 사실을 만들지 않는다.

[공개 리서치]
{research_doc}
"""


def _candidate_status_label(result: Dict[str, Any]) -> str:
    """내부 추출 상태를 질문 상태 enum으로 변환한다."""
    resolution = result["resolution_status"]
    return {
        "resolved": "resolved",
        "partially_resolved": "partially_resolved",
        "explicit_unknown": "explicit_unknown",
        "failed": "failed",
    }.get(resolution, "deferred")


KIND_DISPLAY = {
    "offer": "전면 제안",
    "differentiator": "차별점",
    "market": "우선 시장",
    "target": "우선 고객",
    "purchase": "구매 이유",
    "recipient": "첫 검토 부서",
    "proof": "대표 실적·증거",
    "entry": "진입 경로",
    "cta": "처음 요청할 행동",
    "conversion": "후속 전환 흐름",
    "execution": "실행 위험",
}


def phase_interview(
    client: Any,
    state: Dict[str, Any],
    research_doc: str,
    research_facts: Optional[Sequence[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    """핵심 의사결정 질문을 진행하고 검증된 검색 사실만 배경으로 덧붙인다."""
    print("\n=== [2] Strategy-first 인터뷰 ===")
    print(
        f"  질문 운영: {SOFT_QUESTION_WARNING}개부터 피로도 안내 / "
        f"안전 상한 {MAX_TOTAL_QUESTIONS}개(최종 확인 포함)"
    )
    transcript: List[Dict[str, str]] = []
    ceo_history: List[Dict[str, str]] = []
    followup_queue: List[QuestionCandidate] = []
    deferred_followups: List[QuestionCandidate] = []
    ceo_system = CEO_SYS_TEMPLATE.format(
        company=TARGET_COMPANY,
        research_doc=research_doc[:12000],
    ) if CEO_MODE == "ai" else ""

    while state["interview_state"]["content_question_count"] < MAX_CONTENT_QUESTIONS:
        candidate: Optional[QuestionCandidate] = None
        while followup_queue and candidate is None:
            queued = followup_queue.pop(0)
            if _candidate_still_needs_question(state, queued):
                candidate = queued
        if candidate is None:
            candidate = select_next_question(state)
        if candidate is None:
            while deferred_followups and candidate is None:
                queued = deferred_followups.pop(0)
                if _candidate_still_needs_question(state, queued):
                    candidate = queued
        if candidate is None:
            break
        attempts = state["interview_state"]["candidate_attempts"]
        attempts[candidate.key] = attempts.get(candidate.key, 0) + 1
        semantic_attempt = attempts[candidate.key]
        question = build_human_question(client, state, candidate, semantic_attempt)
        if semantic_attempt > 1:
            question = build_semantic_retry_question(state, candidate, question)
        question = add_research_context_to_question(
            state, candidate, question, research_facts or [],
        )
        fingerprint = _compare_text(question)
        if fingerprint in state["interview_state"]["asked_question_fingerprints"]:
            required_label = PATH_LABELS.get(
                candidate.required_paths[0], KIND_DISPLAY.get(candidate.kind, "해당 항목")
            )
            question = (
                f"앞선 답변만으로는 ‘{required_label}’ 항목을 확정하기 어려웠습니다. "
                "부족한 내용 한 가지만 짧게 말씀해 주세요."
            )
            fingerprint = _compare_text(question)
        if not question_is_safe(question):
            raise RuntimeError(f"사용자 질문 안전성 검사 실패: {candidate.key}")

        number = state["interview_state"]["content_question_count"] + 1
        question_id = f"I{number:03d}"
        turn_label = "후속" if candidate.variant != "main" else "메인"
        print(
            f"\n[질문 AI → 대표] (항목: {KIND_DISPLAY.get(candidate.kind, candidate.kind)}, "
            f"{turn_label} 질문, {semantic_attempt}회차)"
        )
        print(f"  · 질문 목적: {candidate.purpose}")
        print(f"  · 질문: {question}")
        transcript.append({
            "role": "interviewer",
            "content": f"(목적: {candidate.purpose})\nQ. {question}",
        })
        state["question_states"][question_id] = {
            "question_id": question_id,
            "kind": candidate.kind,
            "question_variant": candidate.variant,
            "track_id": candidate.track_id,
            "target_paths": list(candidate.all_paths),
            "status": "asked",
            "resolution_status": "asked",
            "answer_record_ids": [],
            "semantic_attempt": semantic_attempt,
            "technical_retries": 0,
            "question_text": question,
            "extraction_status": None,
            "semantic_rescue_used": False,
            "technical_fallback_used": False,
            "field_results": [],
            "rejection_reason": None,
        }
        state["interview_state"]["asked_question_fingerprints"].append(fingerprint)

        if CEO_MODE == "human":
            answer = read_human_answer()
            speaker = "대표(사람)"
        else:
            ceo_history.append({"role": "user", "content": question})
            answer = chat(
                client,
                ceo_system,
                ceo_history,
                temperature=CEO_TEMP,
                max_tokens=600,
                enable_thinking=False,
            )
            ceo_history.append({"role": "assistant", "content": answer})
            speaker = "가상 대표"
        if END_RE.fullmatch(answer.strip()):
            state["interview_state"]["manually_ended"] = True
            print("\n[사용자] 인터뷰를 수동 종료합니다.")
            break
        answer = answer or "(무응답)"
        print(f"[{speaker}]\n  {answer}")
        transcript.append({"role": "ceo", "content": answer})
        state["interview_state"]["content_question_count"] += 1
        state["interview_state"]["question_count"] += 1
        if (
            state["interview_state"]["question_count"] >= SOFT_QUESTION_WARNING
            and not state["interview_state"]["soft_limit_warning_shown"]
        ):
            state["interview_state"]["soft_limit_warning_shown"] = True
            print(
                f"  [안내] 질문이 {SOFT_QUESTION_WARNING}개에 도달했습니다. "
                "남은 공백을 계속 확인하되 언제든 '/end'로 종료할 수 있습니다."
            )

        result = apply_answer(
            state,
            client,
            question_id=question_id,
            candidate=candidate,
            question=question,
            answer=answer,
        )
        question_state = state["question_states"][question_id]
        question_state["status"] = _candidate_status_label(result)
        question_state["resolution_status"] = result["resolution_status"]
        question_state["answer_record_ids"] = [result["answer_id"]]
        question_state["technical_retries"] = result.get("technical_retries", 0)
        question_state["extraction_status"] = result.get("extraction_status")
        question_state["semantic_rescue_used"] = result.get("semantic_rescue_used", False)
        question_state["technical_fallback_used"] = result.get("local_fallback_used", False)
        question_state["field_results"] = result.get("field_results", [])
        if result["resolution_status"] in {"semantically_insufficient", "partially_resolved"}:
            question_state["rejection_reason"] = "답변 일부가 없거나 우선순위·구체성 확인이 필요함"
        if result["resolution_status"] == "failed":
            print("  [내부 추출 실패] 답변은 보존했으며 후속 확인 항목으로 남깁니다.")
        schema_error = strategy_schema_error(state)
        if schema_error:
            raise RuntimeError(f"{question_id} 답변 적용 후 스키마 위반: {schema_error}")
        if not all(is_user_resolved(state, path) for path in candidate.required_paths):
            if _candidate_still_needs_question(state, candidate):
                if candidate.variant == "main":
                    followup_queue.insert(0, candidate)
                else:
                    # 후속 묶음은 메인 직후 한 번 묻고, 남은 공백은 전체 메인
                    # 질문을 모두 다룬 뒤 다시 순환해 앞쪽 항목의 독점을 막는다.
                    deferred_followups.append(candidate)
        elif candidate.variant == "main":
            followup_queue[0:0] = _followup_candidates_for(state, candidate)

    final_attempt = 0
    while (
        not state["interview_state"]["manually_ended"]
        and not state["completion"].get("final_confirmed", False)
        and compute_completion(state).get("anchor_complete", False)
        and state["interview_state"]["question_count"] < MAX_TOTAL_QUESTIONS
        and final_attempt < MAX_FINAL_REVIEW_ATTEMPTS
    ):
        final_attempt += 1
        summary = "\n".join(strategy_summary_lines(state))
        question = (
            "아래 전략을 확인해 주세요. 맞으면 '맞습니다'라고 답하고, 수정할 내용은 "
            "'시장=일본, 검토 부서=R&D팀'처럼 항목 이름과 함께 적어 주세요.\n\n" + summary
        )
        question_id = f"I{state['interview_state']['question_count'] + 1:03d}"
        print(
            f"\n[질문 AI → 대표] (최종 확인 {final_attempt}/{MAX_FINAL_REVIEW_ATTEMPTS}, "
            f"전체 {state['interview_state']['question_count'] + 1}/{MAX_TOTAL_QUESTIONS})"
        )
        print(question)
        transcript.append({"role": "interviewer", "content": f"(목적: 최종 전략 확인)\nQ. {question}"})
        state["question_states"][question_id] = {
            "question_id": question_id,
            "kind": "final_review",
            "question_variant": "final",
            "track_id": None,
            "target_paths": sorted(set(FINAL_LABEL_PATHS.values())),
            "status": "asked",
            "resolution_status": "asked",
            "answer_record_ids": [],
            "semantic_attempt": 1,
            "technical_retries": 0,
            "question_text": question,
            "extraction_status": None,
            "semantic_rescue_used": False,
            "technical_fallback_used": False,
            "field_results": [],
            "rejection_reason": None,
        }
        answer = read_human_answer() if CEO_MODE == "human" else chat(
            client,
            ceo_system,
            [{"role": "user", "content": question}],
            temperature=CEO_TEMP,
            max_tokens=600,
            enable_thinking=False,
        )
        if not END_RE.fullmatch(answer.strip()):
            print(f"[{'대표(사람)' if CEO_MODE == 'human' else '가상 대표'}]\n  {answer}")
            transcript.append({"role": "ceo", "content": answer})
            result = apply_final_review(
                state,
                client,
                question_id=question_id,
                question=question,
                answer=answer,
            )
            question_state = state["question_states"][question_id]
            question_state["status"] = (
                "resolved" if result["resolution_status"] in {"resolved", "confirmed_summary"} else "deferred"
            )
            question_state["resolution_status"] = result["resolution_status"]
            question_state["answer_record_ids"] = [result["answer_id"]]
            question_state["technical_retries"] = result.get("technical_retries", 0)
            question_state["extraction_status"] = result.get("extraction_status")
            question_state["semantic_rescue_used"] = result.get("semantic_rescue_used", False)
            question_state["technical_fallback_used"] = result.get("local_fallback_used", False)
            question_state["field_results"] = result.get("field_results", [])
            schema_error = strategy_schema_error(state)
            if schema_error:
                raise RuntimeError(f"{question_id} 최종 수정 적용 후 스키마 위반: {schema_error}")
            if result["resolution_status"] == "confirmed_summary":
                state["completion"]["final_confirmed"] = True
            elif result.get("clarification_requested"):
                print(
                    "  [설명] '주력 진출안'은 이번 인터뷰에서 가장 먼저 실행할 시장·고객·접근 방식을 "
                    "한 묶음으로 정리한 것입니다. 비교안이 생길 때만 별도로 구분합니다."
                )
                question_state["rejection_reason"] = "사용자 설명 요청이 있어 수정 요약을 다시 확인해야 함"
                state["interview_state"]["question_count"] += 1
                break
            elif result["resolution_status"] == "resolved":
                print("  [반영] 말씀하신 수정 내용을 반영했습니다. 변경된 요약을 다시 확인합니다.")
                question_state["rejection_reason"] = "수정 반영 후 재확인 필요"
            else:
                question_state["rejection_reason"] = "최종 확인 또는 수정 내용을 해석하지 못함"
                state["interview_state"]["question_count"] += 1
                break
        else:
            state["interview_state"]["manually_ended"] = True
        state["interview_state"]["question_count"] += 1

    state["completion"]["session_closed"] = True
    state["completion"]["question_limit_reached"] = (
        state["interview_state"]["question_count"] >= MAX_TOTAL_QUESTIONS
        and not state["completion"].get("final_confirmed", False)
    )
    compute_completion(state, interview_finished=True)
    print("\n[완료 상태]")
    status_text = "최종 확인 완료" if state["completion"]["final_confirmed"] else (
        "사용자 직접 종료" if state["interview_state"]["manually_ended"] else
        "후속 확인 필요" if state["completion"]["followup_questions"] else
        "최종 확인 대기"
    )
    print(f"- 인터뷰 진행 상태: {status_text}")
    print(f"- 핵심 전략 앵커: {state['completion']['anchor_complete']}")
    print(f"- 실행 준비도: {state['completion']['strategy_ready']}")
    if state["completion"]["followup_questions"]:
        print("- 후속 확인 필요:")
        for item in state["completion"]["followup_questions"]:
            print(f"  · {item}")
    return transcript


# ============================================================================
# 정규화, 호환 projection, 파일 출력
# ============================================================================

def normalize_instance(state: Dict[str, Any]) -> Dict[str, Any]:
    """상태를 깊은 복사하고 순서와 중복을 정리한 검증용 인스턴스로 만든다."""
    normalized = copy.deepcopy(state)
    normalized["strategy_tracks"] = sorted(
        normalized["strategy_tracks"], key=lambda track: track["priority"]
    )[:2]
    normalized["future_candidates"] = list(dict.fromkeys(normalized["future_candidates"]))
    normalized["shared_proofs"] = list(dict.fromkeys(normalized["shared_proofs"]))
    normalized["interview_state"]["stale_paths"] = sorted(set(normalized["interview_state"]["stale_paths"]))
    normalized["interview_state"]["explicit_unknown_paths"] = sorted(
        set(normalized["interview_state"]["explicit_unknown_paths"])
    )
    compute_completion(normalized)
    return normalized


def project_strategy_to_legacy_bundles(state: Dict[str, Any]) -> Dict[str, Any]:
    """외부 호환이 필요할 때 새 주력 트랙을 기존 B1~B9 유사 구조로 투영한다."""
    track = get_track(state, "A") or empty_track("A", 1, active=True)
    return {
        "company": state["company"],
        "B1_front_solution": {
            "chosen_solution": state["offer"]["chosen_solution"],
            "customer_use_context": state["offer"]["customer_use_context"],
            "primary_customer_change": state["offer"]["primary_customer_change"],
            "transaction_units": [state["offer"]["transaction_unit"]] if state["offer"]["transaction_unit"] else [],
            "core_feature": state["offer"]["core_feature"],
        },
        "B2_transaction_goal": {
            "goal": state["transaction_strategy"]["primary_goal"],
            "goal_sequence": state["transaction_strategy"]["goal_sequence"],
            "current_stage": state["transaction_strategy"]["current_stage"],
            "current_bottleneck": state["transaction_strategy"]["current_bottleneck"],
            "success_criteria": state["transaction_strategy"]["success_criteria"],
        },
        "B3_primary_market": {"markets": [track["market"]["country_or_region"]] if track["market"]["country_or_region"] else []},
        "B4_target_segment": {
            "segments": [track["target"]["organization_type"]] if track["target"]["organization_type"] else [],
            "purchase_trigger": track["target"]["purchase_trigger"] or track["target"]["buying_situation"],
            "exclusion_criteria": track["target"]["exclusion_criteria"],
        },
        "B5_recipient": {
            "roles": [track["recipient"]["first_reviewer"]] if track["recipient"]["first_reviewer"] else [],
            "primary_beneficiary": track["recipient"]["primary_beneficiary"],
            "budget_owner": track["recipient"]["budget_owner"],
        },
        "B6_purchase_reason": {
            "pain_point": track["purchase_logic"]["pain_point"],
            "current_alternatives": [track["purchase_logic"]["current_alternative"]] if track["purchase_logic"]["current_alternative"] else [],
            "purchase_reason": track["purchase_logic"]["purchase_reason"],
            "key_benefit_priority": track["purchase_logic"]["key_benefit_priority"],
        },
        "B7_proof_point": {
            "selected_primary_proof": track["proof_strategy"]["primary_proof"],
            "verified_proofs": [track["proof_strategy"]["primary_proof"]] if track["proof_strategy"]["primary_proof"] else [],
            "proof_sources": [track["proof_strategy"]["source"]] if track["proof_strategy"]["source"] else [],
            "verification_status": track["proof_strategy"]["verification_status"],
        },
        "B8_entry_channel": {
            "channel": track["entry_strategy"]["primary_channel"],
            "channels_ab": [item for item in [track["entry_strategy"]["primary_channel"], track["entry_strategy"]["alternative_channel"]] if item],
            "partner_role": track["entry_strategy"]["partner_role"],
            "partner_incentive": track["entry_strategy"]["partner_incentive"],
        },
        "B9_cta_flow": {
            "primary_cta": track["cta_strategy"]["primary_cta"],
            "conversion_flow": track["cta_strategy"]["conversion_flow"],
            "core_message": track["cta_strategy"]["core_message"],
            "acceptance_criteria": track["cta_strategy"]["acceptance_criteria"],
        },
    }


def strategy_summary_markdown(state: Dict[str, Any]) -> str:
    """사람이 읽을 수 있는 최종 전략 요약 Markdown을 만든다."""
    completion = compute_completion(state)
    lines = [
        f"# {state['company']} — Strategy-first 인터뷰 결과",
        "",
        f"> agent_version: {AGENT_VERSION}",
        f"> session_closed: {completion['session_closed']}",
        f"> final_confirmed: {completion['final_confirmed']}",
        f"> question_limit_reached: {completion['question_limit_reached']}",
        f"> anchor_complete: {completion['anchor_complete']}",
        f"> strategy_ready: {completion['strategy_ready']}",
        "",
    ]
    lines.extend(strategy_summary_lines(state))
    return "\n".join(lines) + "\n"


def phase_output(state: Dict[str, Any], transcript: List[Dict[str, str]]) -> None:
    """전략 JSON, 검증용 JSON, 로그, 요약과 호환 projection을 저장한다."""
    print("\n=== [3] 산출물 저장 ===")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    compute_completion(state)

    rich_path = OUT_DIR / f"{TARGET_COMPANY}_strategy_filled.json"
    rich_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  전략 상태: {rich_path}")

    normalized = normalize_instance(state)
    normalized_path = OUT_DIR / f"{TARGET_COMPANY}_strategy_normalized.json"
    normalized_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  정규화 전략: {normalized_path}")

    transcript_lines = [f"# {TARGET_COMPANY} — Strategy-first 인터뷰 로그\n"]
    for item in transcript:
        label = "질문 AI" if item["role"] == "interviewer" else "대표"
        transcript_lines.append(f"## {label}\n{item['content']}\n")
    transcript_path = OUT_DIR / f"{TARGET_COMPANY}_interview_transcript.md"
    transcript_path.write_text("\n".join(transcript_lines), encoding="utf-8")
    print(f"  인터뷰 로그: {transcript_path}")

    summary_path = OUT_DIR / f"{TARGET_COMPANY}_strategy_summary.md"
    summary_path.write_text(strategy_summary_markdown(state), encoding="utf-8")
    print(f"  전략 요약: {summary_path}")

    diagnostic_path = OUT_DIR / f"{TARGET_COMPANY}_extraction_events.json"
    diagnostic_path.write_text(
        json.dumps(state["interview_state"]["extraction_events"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  추출 진단 로그: {diagnostic_path}")

    legacy_path = OUT_DIR / f"{TARGET_COMPANY}_legacy_projection.json"
    legacy_path.write_text(
        json.dumps(project_strategy_to_legacy_bundles(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  기존 형식 projection: {legacy_path}")

    try:
        import jsonschema

        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(normalized, schema)
        print("  [검증] 새 전략 스키마 통과")
    except ImportError:
        print("  [검증] jsonschema 미설치 — 건너뜀 (pip install jsonschema)")
    except Exception as exc:
        print(f"  [검증] 새 전략 스키마 위반: {exc}")


# ============================================================================
# 실행 진입점
# ============================================================================

def main() -> None:
    """리서치, 적응형 전략 인터뷰, 산출물 저장을 순서대로 실행한다."""
    print("=" * 68)
    print("Strategy-first 글로벌 B2B 인터뷰 에이전트")
    print(f"대상 기업: {TARGET_COMPANY}")
    print(f"버전: {AGENT_VERSION} / 질문 상한: {MAX_TOTAL_QUESTIONS}")
    print("=" * 68)
    client = make_client()
    research = phase_research(client)
    transcript = phase_interview(
        client,
        research["state"],
        research["research_doc"],
        research["research_facts"],
    )
    phase_output(research["state"], transcript)
    if research["state"]["completion"].get("final_confirmed"):
        print("\n인터뷰와 최종 확인이 완료되었습니다.")
    else:
        print("\n실행이 종료되었으며 최종 확인 또는 후속 확인이 남아 있습니다.")


if __name__ == "__main__":
    main()
