# -*- coding: utf-8 -*-
"""독립 실행형 Strategy-first 글로벌 B2B 인터뷰 에이전트.

이 파일은 기존 interview_agent.py 계열을 import하지 않는다. Google Grounding으로
사전 리서치 문서를 만들고, Friendli의 EXAONE으로 답변을 구조화하며, 실제 대표와
최대 40개 질문을 적응형으로 진행한다. 핵심 결과는 평면 B1~B9가 아니라 최대 두 개의 실행
트랙으로 저장한다.

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
TARGET_COMPANY = "키뮤"
COMPANY_HINTS = ""

# human은 콘솔 입력, ai는 EXAONE이 가상 대표를 연기한다.
CEO_MODE = "human"

# 이미 대표에게 직접 확인한 값을 점 경로로 넣을 수 있다.
# 예: {"offer.chosen_solution": "ESG 캠페인"}
USER_SEED: Dict[str, Any] = {}

# 메인 질문 뒤에 같은 항목의 후속 질문을 바로 이어서 진행한다.
# 30문항에서는 피로도 안내만 하고, 비정상적인 무한 반복을 막는 안전 상한은 40문항이다.
SOFT_QUESTION_WARNING = 30
MAX_TOTAL_QUESTIONS = 40
MAX_CONTENT_QUESTIONS = 38
MAX_FINAL_REVIEW_ATTEMPTS = 2
MAX_SEMANTIC_ATTEMPTS = 3
MAX_TECHNICAL_RETRIES = 1

# API 및 출력 설정이다.
ENABLE_THINKING = False
PARSE_REASONING = True
CEO_TEMP = 0.7
AGENT_VERSION = "strategy_first_v3"

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
    "meeting_15_30min": "15~30분 소개 미팅",
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
    r"(?:프로젝트|고객|협업|계약|매출|수출|인증|특허|수상|테스트|검증|PoC|"
    r"전후|before|after|샘플|데모|성적서|보고서|레퍼런스|\d)",
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
        "schema_version": "11_strategy_interview_v3",
        "company": company,
        "offer": {
            "chosen_solution": None,
            "transaction_unit": None,
            "customer_use_context": None,
            "primary_customer_change": None,
            "core_feature": None,
            "maturity_stage": None,
            "status": "unknown",
        },
        "transaction_strategy": {
            "primary_goal": None,
            "goal_sequence": [],
            "current_stage": None,
            "current_bottleneck": None,
            "realistic_first_transaction": None,
            "success_criteria": None,
            "status": "unknown",
        },
        "strategy_tracks": [empty_track("A", 1, active=True)],
        "future_candidates": [],
        "shared_proofs": [],
        "field_meta": {},
        "answer_records": [],
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
    raw = value if isinstance(value, list) else re.split(r"\s*(?:→|->|,|;|\n)\s*", str(value or ""))
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
        result = ["transaction_strategy.primary_goal", "transaction_strategy.success_criteria"]
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
        confidence = "high" if origin == "user_stated" else "medium"
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
    """사용자 직접 확인값 또는 명시적 미정이며 stale이 아닌지 판정한다."""
    if path in state["interview_state"]["stale_paths"]:
        return False
    if path in state["interview_state"].get("partial_paths", []):
        return False
    if path in state["interview_state"]["explicit_unknown_paths"]:
        return True
    meta = state["field_meta"].get(path, {})
    return meta.get("origin") == "user_stated" and _has_value(get_path(state, path))


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
            _track_path(track_id, "purchase_logic.purchase_reason"),
            _track_path(track_id, "proof_strategy.primary_proof"),
            _track_path(track_id, "entry_strategy.primary_channel"),
            _track_path(track_id, "cta_strategy.primary_cta"),
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
    """회사 전체에 공통인 솔루션·거래 목표 질문 후보를 만든다."""
    return [
        QuestionCandidate(
            "offer", None,
            ("offer.chosen_solution",),
            ("offer.transaction_unit", "offer.customer_use_context", "offer.primary_customer_change", "offer.core_feature"),
            "이번 진출에서 전면에 세울 제안 대상을 분명히 하기 위해",
            100, 8, 8, 2,
        ),
        QuestionCandidate(
            "goal", None,
            ("transaction_strategy.primary_goal",),
            ("transaction_strategy.current_stage", "transaction_strategy.current_bottleneck", "transaction_strategy.success_criteria", "transaction_strategy.goal_sequence"),
            "가장 먼저 성사시킬 거래와 현재 단계를 구분하기 위해",
            96, 6, 8, 2,
        ),
    ]


def _track_candidates(track_id: str, *, secondary: bool) -> List[QuestionCandidate]:
    """한 전략 트랙의 시장부터 전환까지 질문 후보를 만든다."""
    penalty = 12 if secondary else 0
    p = lambda suffix: _track_path(track_id, suffix)
    return [
        QuestionCandidate(
            "market", track_id,
            (p("market.country_or_region"),),
            (p("market.rationale"), p("market.market_readiness"), p("market.regulation_or_localization")),
            f"트랙 {track_id}의 실제 우선 시장을 기존 활동 국가와 구분하기 위해",
            92 - penalty, 6, 8, 1,
        ),
        QuestionCandidate(
            "target", track_id,
            (p("target.organization_type"),),
            (p("target.buying_situation"), p("target.purchase_trigger"), p("target.urgency_signal"), p("target.exclusion_criteria")),
            f"트랙 {track_id}의 고객을 조직명뿐 아니라 구매 상황으로 좁히기 위해",
            90 - penalty, 6, 9, 2,
        ),
        QuestionCandidate(
            "purchase", track_id,
            (p("purchase_logic.purchase_reason"),),
            (p("purchase_logic.pain_point"), p("purchase_logic.current_alternative"), p("purchase_logic.loss_or_risk"), p("purchase_logic.urgency_trigger"), p("purchase_logic.key_benefit_priority"), p("purchase_logic.why_budget")),
            f"트랙 {track_id} 고객이 실제 예산을 쓰는 이유를 확인하기 위해",
            88 - penalty, 5, 10, 2,
        ),
        QuestionCandidate(
            "recipient", track_id,
            (p("recipient.first_reviewer"),),
            (p("recipient.primary_beneficiary"), p("recipient.technical_reviewer"), p("recipient.budget_owner"), p("recipient.internal_forward_to")),
            f"트랙 {track_id}에서 제안을 처음 검토할 사람을 확인하기 위해",
            84 - penalty, 3, 7, 1,
        ),
        QuestionCandidate(
            "proof", track_id,
            (p("proof_strategy.primary_proof"),),
            (p("proof_strategy.proof_type"), p("proof_strategy.source"), p("proof_strategy.verification_status"), p("proof_strategy.measurement_condition"), p("proof_strategy.sample_or_demo_availability"), p("proof_strategy.disclosable_before_nda"), p("proof_strategy.missing_proof_plan")),
            f"트랙 {track_id} 제안의 신뢰를 만드는 실제 근거와 제공 상태를 확인하기 위해",
            82 - penalty, 3, 9, 2,
        ),
        QuestionCandidate(
            "entry", track_id,
            (p("entry_strategy.primary_channel"),),
            (p("entry_strategy.alternative_channel"), p("entry_strategy.partner_role"), p("entry_strategy.partner_incentive"), p("entry_strategy.responsibility_split")),
            f"트랙 {track_id} 고객에게 도달할 첫 진입 경로를 확인하기 위해",
            78 - penalty, 2, 8, 2,
        ),
        QuestionCandidate(
            "cta", track_id,
            (p("cta_strategy.primary_cta"),),
            (p("cta_strategy.conversion_flow"), p("cta_strategy.core_message"), p("cta_strategy.acceptance_criteria"), p("cta_strategy.next_step_requirements")),
            f"트랙 {track_id}의 첫 요청과 후속 전환을 연결하기 위해",
            76 - penalty, 1, 8, 2,
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
    """메인 질문 직후 이어서 확인할 밀접한 세부 필드 묶음을 만든다."""
    if parent.variant != "main":
        return []
    track_id = parent.track_id or "A"
    p = lambda suffix: _track_path(track_id, suffix)
    specs: Dict[str, List[Tuple[str, Tuple[str, ...], str]]] = {
        "offer": [
            ("transaction_unit", ("offer.transaction_unit",), "제안의 실제 거래 단위를 확인하기 위해"),
            (
                "use_and_change",
                ("offer.customer_use_context", "offer.primary_customer_change"),
                "고객의 사용 상황과 사용 후 변화를 연결하기 위해",
            ),
            (
                "feature_and_maturity",
                ("offer.core_feature", "offer.maturity_stage"),
                "핵심 실행 역량과 현재 제공 가능 수준을 확인하기 위해",
            ),
        ],
        "goal": [
            (
                "stage_and_bottleneck",
                ("transaction_strategy.current_stage", "transaction_strategy.current_bottleneck"),
                "희망 거래와 현재 진행 단계를 구분하기 위해",
            ),
            (
                "success_and_sequence",
                ("transaction_strategy.success_criteria", "transaction_strategy.goal_sequence"),
                "첫 거래의 성공 기준과 다음 거래 순서를 확인하기 위해",
            ),
        ],
        "market": [
            (
                "reason_and_readiness",
                (p("market.rationale"), p("market.market_readiness")),
                "우선 시장을 선택한 근거와 준비 수준을 확인하기 위해",
            ),
            (
                "localization",
                (p("market.regulation_or_localization"),),
                "현지 규제·언어·운영 조정 필요성을 확인하기 위해",
            ),
        ],
        "target": [
            (
                "situation_and_trigger",
                (p("target.buying_situation"), p("target.purchase_trigger")),
                "고객이 실제 검토를 시작하는 상황과 계기를 확인하기 위해",
            ),
            (
                "urgency_and_exclusion",
                (p("target.urgency_signal"), p("target.exclusion_criteria")),
                "우선순위를 높이는 신호와 제외할 고객을 확인하기 위해",
            ),
        ],
        "purchase": [
            (
                "pain_and_alternative",
                (p("purchase_logic.pain_point"), p("purchase_logic.current_alternative")),
                "고객 문제와 현재 대안을 확인하기 위해",
            ),
            (
                "loss_and_urgency",
                (p("purchase_logic.loss_or_risk"), p("purchase_logic.urgency_trigger")),
                "도입하지 않을 때의 손실과 시급성을 확인하기 위해",
            ),
            (
                "benefit_and_budget",
                (p("purchase_logic.key_benefit_priority"), p("purchase_logic.why_budget")),
                "가장 중요한 혜택과 예산이 생기는 이유를 구분하기 위해",
            ),
        ],
        "recipient": [
            (
                "beneficiary_and_owner",
                (p("recipient.primary_beneficiary"), p("recipient.budget_owner")),
                "실제 수혜 부서와 예산 승인 주체를 확인하기 위해",
            ),
            (
                "review_flow",
                (p("recipient.technical_reviewer"), p("recipient.internal_forward_to")),
                "기술 검토와 내부 전달 흐름을 확인하기 위해",
            ),
        ],
        "proof": [
            (
                "type_source_status",
                (
                    p("proof_strategy.proof_type"),
                    p("proof_strategy.source"),
                    p("proof_strategy.verification_status"),
                ),
                "대표 실적의 유형·출처·검증 상태를 확인하기 위해",
            ),
            (
                "measurement_and_delivery",
                (
                    p("proof_strategy.measurement_condition"),
                    p("proof_strategy.sample_or_demo_availability"),
                    p("proof_strategy.disclosable_before_nda"),
                ),
                "성과 측정 조건과 외부 제공 가능 범위를 확인하기 위해",
            ),
        ],
        "entry": [
            (
                "alternative_and_partner",
                (p("entry_strategy.alternative_channel"), p("entry_strategy.partner_role")),
                "대안 경로와 현지 파트너 역할을 확인하기 위해",
            ),
            (
                "incentive_and_split",
                (p("entry_strategy.partner_incentive"), p("entry_strategy.responsibility_split")),
                "파트너 참여 이유와 업무 분담을 확인하기 위해",
            ),
        ],
        "cta": [
            (
                "conversion_flow",
                (p("cta_strategy.conversion_flow"),),
                "첫 요청 이후 계약까지의 전환 순서를 확인하기 위해",
            ),
            (
                "message_and_acceptance",
                (p("cta_strategy.core_message"), p("cta_strategy.acceptance_criteria")),
                "첫 제안의 핵심 메시지와 상대의 수락 기준을 확인하기 위해",
            ),
            (
                "next_requirements",
                (p("cta_strategy.next_step_requirements"),),
                "다음 단계로 넘어가기 위한 준비물을 확인하기 위해",
            ),
        ],
        "execution": [
            (
                "compliance_and_cost",
                (p("execution_constraints.regulation_certification"), p("execution_constraints.cost_impact")),
                "규제·인증과 비용 영향을 확인하기 위해",
            ),
            (
                "scale_and_localization",
                (p("execution_constraints.supply_scale_up"), p("execution_constraints.localization_support")),
                "공급 확대와 현지 지원 조건을 확인하기 위해",
            ),
            (
                "disclosure",
                (p("execution_constraints.disclosure_nda_policy"),),
                "자료 공개와 NDA 조건을 확인하기 위해",
            ),
        ],
    }
    attempts = state["interview_state"]["candidate_attempts"]
    result: List[QuestionCandidate] = []
    for index, (variant, paths, purpose) in enumerate(specs.get(parent.kind, []), 1):
        item = QuestionCandidate(
            parent.kind,
            parent.track_id,
            paths,
            tuple(),
            purpose,
            max(parent.criticality - index, 1),
            parent.dependency_count,
            parent.execution_risk,
            1,
            variant,
        )
        if all(is_user_resolved(state, path) for path in paths):
            continue
        if attempts.get(item.key, 0) >= MAX_SEMANTIC_ATTEMPTS:
            continue
        result.append(item)
    return result


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
    # 핵심 앵커가 해결된 경우에만 세부 실행 질문을 후보로 연다. 이 조건 덕분에
    # 실행 위험 질문이 아직 필요한 핵심 질문을 밀어내지 않는다.
    if all(is_user_resolved(state, path) for path in ANCHOR_PATHS_A):
        candidates.extend(_execution_candidate(track_id) for track_id in _active_track_ids(state))
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
    """현재 상태에서 가장 큰 전략적 공백 하나를 선택한다."""
    candidates = build_question_candidates(state)
    if not candidates:
        return None
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
    """후보의 목적을 유지하면서 최대 두 개의 밀접한 개념만 질문한다."""
    kind, track_id = candidate.kind, candidate.track_id or "A"
    if candidate.variant != "main":
        return _build_followup_question(state, candidate)
    attempt = semantic_attempt or state["interview_state"]["candidate_attempts"].get(candidate.key, 0) + 1
    solution = _confirmed_anchor(state, "offer.chosen_solution")
    if kind == "offer":
        if attempt > 1:
            known = display_path(state, "offer.chosen_solution")
            if known != "미해결":
                return f"‘{known}’은 실제 계약에서 프로젝트, 서비스, 제품 또는 라이선스 중 어느 형태에 가까운가요?"
            return "이번 해외 진출 제안의 이름을 하나만 말씀해 주세요. 제품명, 서비스명 또는 프로젝트명 모두 가능합니다."
        return (
            "이번 해외 진출에서 가장 먼저 제안할 제품, 서비스 또는 프로젝트는 무엇인가요?"
        )
    if kind == "goal":
        if attempt > 1:
            return "이번 진출에서 가장 먼저 성사시키려는 거래 유형 하나만 말씀해 주세요. (예: PoC, 유료 판매, 라이선싱, 공동 개발)"
        return (
            "이번 진출에서 가장 먼저 성사시키려는 거래는 무엇인가요? "
            "(예: 샘플 검토, PoC, 유료 판매, 라이선싱, 공동 개발)"
        )
    if kind == "market":
        if attempt > 1:
            return "이번 진출에서 다른 지역보다 먼저 공략할 국가 또는 지역 하나만 말씀해 주세요."
        prefix = "비교 트랙에서" if track_id == "B" else "기존 활동 국가와 별개로, 이번에"
        return f"{prefix} 가장 먼저 공략할 국가나 지역은 어디인가요?"
    if kind == "target":
        existing = get_path(state, _track_path(track_id, "target.organization_type"))
        if attempt > 1 and _has_value(existing):
            return f"{display_value(existing)}까지는 확인됐습니다. 그중 이번에 실제 영업을 가장 먼저 시작할 조직 유형 하나를 고른다면 어디인가요?"
        examples = "·".join(generate_segment_examples(client, state, track_id))
        subject = f"‘{solution}’의 " if solution else "이번 제안의 "
        return (
            f"{subject}가장 첫 제안 대상은 어떤 유형의 조직인가요? "
            f"사용 목적이 아니라 실제 조직 유형으로 말씀해 주세요. (예: {examples})"
        )
    if kind == "recipient":
        if attempt > 1:
            return "이 제안을 실제로 가장 먼저 검토할 직무나 부서 하나만 말씀해 주세요."
        return "실제로 제안을 받았을 때 처음 검토하고 내부 논의를 시작할 직무나 부서는 어디인가요?"
    if kind == "purchase":
        organization = _confirmed_anchor(state, _track_path(track_id, "target.organization_type"))
        subject = f"{organization.replace(', ', '이나 ')}이" if organization else "고객이"
        solution_text = f" ‘{solution}’에" if solution else " 이 제안에"
        if attempt > 1:
            budget = get_path(state, _track_path(track_id, "purchase_logic.why_budget"))
            prefix = f"{display_value(budget)} 때문에 관련 예산이 있다는 점은 확인했습니다. " if _has_value(budget) else ""
            return f"{prefix}다른 CSR·ESG 실행 대안이 아니라{solution_text} 예산을 쓰게 만드는 결정적인 선택 이유는 무엇인가요?"
        return (
            f"{subject}{solution_text} 실제 예산을 쓰게 만드는 결정적인 선택 이유는 무엇인가요? "
            "현재 사용 중인 대안이 있다면 함께 말씀해 주세요."
        )
    if kind == "proof":
        if attempt > 1:
            source = get_path(state, _track_path(track_id, "proof_strategy.source"))
            prefix = f"{display_value(source)}에 자료가 있다는 점은 확인했습니다. " if _has_value(source) else ""
            return f"{prefix}해외 제안에서 가장 먼저 보여줄 고객명, 프로젝트명, 성과 수치, 인증 또는 테스트 결과 하나를 말씀해 주세요."
        subject = f"‘{solution}’을 실제로 수행할 수 있다는" if solution else "이번 제안을 수행할 수 있다는"
        return (
            f"{subject} 근거로 가장 먼저 보여줄 고객 사례, 프로젝트 결과, 수치, 인증 또는 "
            "테스트 자료는 무엇인가요? 바로 제공 가능한지도 말씀해 주세요. 아직 없다면 없다고 답해 주세요."
        )
    if kind == "entry":
        if attempt > 1:
            return "첫 고객에게 직접 접근할지, 현지 파트너를 통할지 우선 경로 하나만 말씀해 주세요."
        return (
            "이 고객에게 가장 먼저 접근할 방식은 무엇인가요? "
            "(예: 최종 고객 직접 접근, 현지 유통 파트너, 에이전시, 컨소시엄, 대학·연구기관) "
            "비교할 두 번째 경로가 있다면 함께 말씀해 주세요."
        )
    if kind == "cta":
        if attempt > 1:
            return "첫 제안에서 상대방에게 요청할 다음 행동 하나만 말씀해 주세요. (예: 20분 소개 미팅, 제안서 검토, 데모 확인, 샘플 테스트)"
        return (
            "첫 제안에서 상대방에게 요청할 가장 부담 없고 구체적인 다음 행동은 무엇인가요? "
            "그 행동이 받아들여진 직후 이어질 한 단계도 말씀해 주세요. "
            "(예: 15~30분 소개 미팅, 제안서 검토, 데모 확인, 샘플 테스트)"
        )
    if kind == "execution":
        return (
            "첫 요청 이후 다음 단계로 넘어갈 때 가장 큰 장애물은 무엇인가요? "
            "(예: 규제·인증, 가격, 생산·공급 규모, 현지화, NDA와 자료 공개)"
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


# ============================================================================
# 답변 구조화: API 우선, 기술 실패 시에만 로컬 fallback
# ============================================================================

ANSWER_EXTRACT_SYS = """\
너는 글로벌 B2B 대표 인터뷰의 '원문 근거 추출기'다.

규칙:
- allowed_paths에 포함된 경로만 출력한다.
- 답변에 직접 존재하는 사실만 사용한다.
- 필드마다 supported, partial, ambiguous, not_mentioned 중 하나로 판정한다.
- evidence는 답변에서 글자 그대로 복사한 가장 짧은 근거 구절이어야 한다.
- 한 필드가 누락되어도 답변된 다른 필드를 버리지 않는다.
- normalized_candidate는 enum 필드에만 허용 토큰을 쓰고, 자유 텍스트는 evidence와 같은 언어·문자를 유지한다.
- 여러 시장·고객·채널이 명확히 비교되면 A를 우선 트랙, B를 비교 트랙으로 분리할 수 있다.
- 첫 행동과 후속 행동을 구분한다.
- 솔루션명이나 비전은 proof로 출력하지 않는다.
- PoC를 목표라고 답했을 뿐이면 current_stage로 복사하지 않는다.
- CSR 의무나 예산 배정은 why_budget 또는 purchase_trigger이며, 해당 제안을 선택하는 purchase_reason과 구분한다.
- 웹사이트·뉴스·IR은 proof의 source다. 구체적인 고객·프로젝트·수치·인증이 없으면 primary_proof를 supported로 만들지 않는다.
- '기업과 공공기관 모두', '가능한 많은 기업'처럼 우선순위가 없는 고객 답변은 organization_type을 partial로 판정한다.
- 내부 enum 필드는 field_rules의 allowed_tokens를 사용한다.

반드시 JSON 하나만 출력한다.
{
  "activate_track_b": false,
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

TECHNICAL_FAILURE_STATUSES = {
    "model_failed", "empty_response", "json_not_found", "parse_failed", "not_object",
    "invalid_contract", "invalid_source_grounding",
}

BUDGET_REASON_ONLY_RE = re.compile(
    r"(?:예산(?:이|을|에)?\s*(?:있|배정)|의무|컴플라이언스|법적\s*요구|정책상)", re.IGNORECASE
)
CHOICE_REASON_RE = re.compile(
    r"(?:대안|대비|선택|차별|효과|성과|전문성|품질|비용|빠르|고도화|개선)", re.IGNORECASE
)
PROOF_SOURCE_RE = re.compile(r"(?:웹사이트|홈페이지|뉴스|기사|IR|브로슈어|소개서)", re.IGNORECASE)
SPECIFIC_PROOF_RE = re.compile(
    r"(?:\d|프로젝트명|고객명|인증명|특허|수상|매출|수출|계약|PoC|테스트\s*결과|성과\s*수치)",
    re.IGNORECASE,
)


def extraction_allowed_paths(candidate: QuestionCandidate) -> List[str]:
    """현재 질문의 필드와 같은 의미의 비교 트랙 필드만 추가 허용한다."""
    allowed = list(candidate.all_paths)
    if candidate.track_id == "A" and candidate.kind in {
        "market", "target", "recipient", "purchase", "proof", "entry", "cta"
    }:
        for path in candidate.all_paths:
            if path.startswith("strategy_tracks.A."):
                allowed.append(path.replace("strategy_tracks.A.", "strategy_tracks.B.", 1))
    return sorted(set(allowed))


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
    return normalize_path_value(path, raw)


def validate_extraction_response(
    parsed: Dict[str, Any], answer: str, allowed_paths: Sequence[str]
) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]], bool, str]:
    """필드별 계약, 원문 근거, 정규화 타입과 한글 무결성을 검증한다."""
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
        return {}, [], [], False, "invalid_contract"
    allowed = set(allowed_paths)
    haystack = _compare_text(answer)
    updates: Dict[str, Any] = {}
    partial_paths: List[str] = []
    field_results: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            return {}, [], [], False, "invalid_contract"
        path = record.get("path")
        status = record.get("status")
        evidence = record.get("evidence")
        if path not in allowed or status not in {"supported", "partial", "ambiguous", "not_mentioned"}:
            return {}, [], [], False, "invalid_contract"
        result = {
            "path": path,
            "status": status,
            "evidence": evidence if isinstance(evidence, str) else None,
            "missing_detail": record.get("missing_detail"),
        }
        field_results.append(result)
        if status not in {"supported", "partial"}:
            continue
        if not isinstance(evidence, str):
            return {}, [], [], False, "invalid_contract"
        source_key = _compare_text(evidence)
        if len(source_key) < 2 or source_key not in haystack:
            return {}, [], [], False, "invalid_source_grounding"
        effective_path = path
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
        updates[effective_path] = value
        if result["status"] == "partial":
            partial_paths.append(effective_path)
    if not updates:
        return {}, partial_paths, field_results, False, "parsed_no_value"
    activate_b = bool(parsed.get("activate_track_b")) or any(
        path.startswith("strategy_tracks.B.") for path in updates
    )
    return updates, partial_paths, field_results, activate_b, "parsed"


def extract_answer_with_api(
    client: Any,
    state: Dict[str, Any],
    candidate: QuestionCandidate,
    question: str,
    answer: str,
) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]], bool, str, int, str]:
    """답변을 API로 우선 추출하고 기술적 실패 상태를 구분한다."""
    allowed_paths = extraction_allowed_paths(candidate)
    payload = {
        "question_kind": candidate.kind,
        "track_id": candidate.track_id,
        "allowed_paths": allowed_paths,
        "field_rules": {path: _rule_for_path(path) for path in allowed_paths if _rule_for_path(path)},
        "confirmed_context": {
            "solution": _confirmed_anchor(state, "offer.chosen_solution"),
            "goal": _confirmed_anchor(state, "transaction_strategy.primary_goal"),
            "market_A": _confirmed_anchor(state, "strategy_tracks.A.market.country_or_region"),
            "target_A": _confirmed_anchor(state, "strategy_tracks.A.target.organization_type"),
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
        updates, partial_paths, field_results, activate_b, validation_status = validate_extraction_response(
            parsed, answer, allowed_paths
        )
        if validation_status in {"parsed", "parsed_no_value"}:
            return updates, partial_paths, field_results, activate_b, validation_status, attempt, raw
        last_status = validation_status
        payload["invalid_previous_output"] = raw[:2500]
    return {}, [], [], False, last_status, MAX_TECHNICAL_RETRIES, last_raw


ORGANIZATION_RE = re.compile(
    r"(?:대기업|중견기업|중소기업|기업|스타트업|공공기관|정부기관|관공서|지자체|공기업|"
    r"병원|의료기관|학교|대학|연구기관|연구소|복지기관|비영리기관|제조사|공급업체|"
    r"유통사|도매상|소매상|브랜드|에이전시|농장|농가|OEM|Tier\s*1)",
    re.IGNORECASE,
)


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
    if candidate.kind == "proof":
        value = normalize_path_value(path, text)
        return {path: value} if value else {}
    if candidate.kind == "entry":
        token = _match_token(text, CHANNEL_TOKENS, CHANNEL_PAIRS)
        return {path: token} if token else {}
    if candidate.kind == "cta":
        token = _match_token(text, CTA_TOKENS, CTA_PAIRS)
        updates = {path: token} if token else {}
        flow = _as_string_list(text, max_items=4)
        if len(flow) > 1:
            updates[_track_path(candidate.track_id or "A", "cta_strategy.conversion_flow")] = flow
        return updates
    if candidate.kind == "execution":
        value = _as_short_text(text, 140)
        return {path: value} if value and len(text) <= 140 else {}
    return {}


BROAD_TARGET_RE = re.compile(
    r"(?:가능한\s*많은|모든|전체|전반적|다양한|가리지\s*않|기업과\s*공공기관|기업,\s*공공기관)",
    re.IGNORECASE,
)


def semantic_rescue_updates(
    candidate: QuestionCandidate, answer: str
) -> Tuple[Dict[str, Any], List[str]]:
    """AI의 값 없음 판정과 충돌하는 명시적 단답만 보수적으로 구조한다."""
    if candidate.kind not in {"offer", "goal", "market", "target", "recipient", "entry", "cta"}:
        return {}, []
    updates = local_fallback_updates(candidate, answer)
    partial_paths: List[str] = []
    if candidate.kind == "target" and updates and BROAD_TARGET_RE.search(answer):
        partial_paths.extend(updates)
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
    ) = extract_answer_with_api(
        client, state, candidate, question, answer
    )
    local_used = False
    semantic_rescue_used = False
    if extraction_status in TECHNICAL_FAILURE_STATUSES:
        updates = local_fallback_updates(candidate, answer)
        local_used = True
    elif extraction_status == "parsed_no_value" or not any(
        path in updates for path in candidate.required_paths
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
    # API가 넓은 고객군을 supported로 보더라도 우선순위가 없으면 부분 확인으로 보존한다.
    target_path = _track_path(candidate.track_id or "A", "target.organization_type")
    if candidate.kind == "target" and target_path in updates and BROAD_TARGET_RE.search(answer):
        partial_paths = sorted(set(partial_paths + [target_path]))
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
        "raw_response": raw_response,
    }


# ============================================================================
# 완료 판정, 후속 확인, 최종 요약
# ============================================================================

ANCHOR_PATHS_A = [
    "offer.chosen_solution",
    "transaction_strategy.primary_goal",
    "strategy_tracks.A.market.country_or_region",
    "strategy_tracks.A.target.organization_type",
    "strategy_tracks.A.recipient.first_reviewer",
    "strategy_tracks.A.purchase_logic.purchase_reason",
    "strategy_tracks.A.proof_strategy.primary_proof",
    "strategy_tracks.A.entry_strategy.primary_channel",
    "strategy_tracks.A.cta_strategy.primary_cta",
]

PATH_LABELS = {
    "offer.chosen_solution": "전면 솔루션",
    "transaction_strategy.primary_goal": "첫 거래 목표",
    "strategy_tracks.A.market.country_or_region": "주력 시장",
    "strategy_tracks.A.target.organization_type": "우선 고객",
    "strategy_tracks.A.target.buying_situation": "고객의 구매 상황",
    "strategy_tracks.A.recipient.first_reviewer": "첫 검토 부서",
    "strategy_tracks.A.purchase_logic.purchase_reason": "구매 이유",
    "strategy_tracks.A.proof_strategy.primary_proof": "대표 실적·증거",
    "strategy_tracks.A.proof_strategy.sample_or_demo_availability": "증거·샘플 제공 시점",
    "strategy_tracks.A.entry_strategy.primary_channel": "진입 채널",
    "strategy_tracks.A.cta_strategy.primary_cta": "처음 요청할 다음 행동",
    "strategy_tracks.A.cta_strategy.conversion_flow": "후속 전환 흐름",
    "strategy_tracks.A.execution_constraints.highest_risk": "가장 큰 실행 위험",
}


def _execution_paths(track_id: str) -> List[str]:
    """전략 실행 준비도를 위해 확인할 최소 세부 경로다."""
    return [
        _track_path(track_id, "target.buying_situation"),
        _track_path(track_id, "proof_strategy.sample_or_demo_availability"),
        _track_path(track_id, "cta_strategy.conversion_flow"),
        _track_path(track_id, "execution_constraints.highest_risk"),
    ]


def _followup_text(path: str) -> str:
    """미해결 경로를 사람이 바로 사용할 후속 질문으로 바꾼다."""
    track_match = re.match(r"strategy_tracks\.([AB])\.", path)
    track = "비교 진출안에서 " if track_match and track_match.group(1) == "B" else (
        "이번 진출안에서 " if track_match else ""
    )
    suffix = re.sub(r"^strategy_tracks\.[AB]\.", "", path)
    prompts = {
        "target.buying_situation": "고객이 실제 검토를 시작하는 상황은 무엇인가요?",
        "proof_strategy.sample_or_demo_availability": "현재 바로 제공할 수 있는 샘플·데모·자료는 어디까지인가요?",
        "cta_strategy.conversion_flow": "첫 요청 이후 샘플·PoC·계약은 어떤 순서로 이어지나요?",
        "execution_constraints.highest_risk": "다음 단계 전환을 막을 가장 큰 규제·원가·양산·NDA 위험은 무엇인가요?",
        "market.country_or_region": "가장 먼저 공략할 시장은 어디인가요?",
        "target.organization_type": "가장 먼저 제안할 고객 조직은 누구인가요?",
        "recipient.first_reviewer": "제안을 처음 검토할 부서는 어디인가요?",
        "purchase_logic.purchase_reason": "고객이 실제 예산을 쓰는 이유는 무엇인가요?",
        "proof_strategy.primary_proof": "가장 먼저 보여줄 실제 근거는 무엇인가요?",
        "entry_strategy.primary_channel": "고객에게 처음 접근할 경로는 무엇인가요?",
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
        b_core = [path.replace("strategy_tracks.A.", "strategy_tracks.B.", 1) for path in ANCHOR_PATHS_A[2:]]
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
    """최종 확인과 Markdown 산출물에 공통으로 쓸 사람용 전략 요약을 만든다."""
    lines = [
        "[공통 제안]",
        f"- 전면 솔루션: {display_path(state, 'offer.chosen_solution')}",
        f"- 첫 거래 목표: {display_path(state, 'transaction_strategy.primary_goal')}",
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
            f"- 구매 상황: {display_path(state, prefix + 'target.buying_situation')}",
            f"- 첫 검토 부서: {display_path(state, prefix + 'recipient.first_reviewer')}",
            f"- 구매 이유: {display_path(state, prefix + 'purchase_logic.purchase_reason')}",
            f"- 가장 중요한 고객 혜택: {display_path(state, prefix + 'purchase_logic.key_benefit_priority')}",
            f"- 대표 실적·증거: {display_path(state, prefix + 'proof_strategy.primary_proof')}",
            f"- 진입 채널: {display_path(state, prefix + 'entry_strategy.primary_channel')}",
            f"- 처음 요청할 다음 행동: {display_path(state, prefix + 'cta_strategy.primary_cta')}",
            f"- 후속 전환: {display_path(state, prefix + 'cta_strategy.conversion_flow')}",
        ])
    compute_completion(state)
    lines.extend(["", "[후속 확인 필요]"])
    followups = state["completion"]["followup_questions"]
    lines.extend([f"- {item}" for item in followups] or ["- 없음"])
    return lines


# ============================================================================
# 최종 확인 답변 처리
# ============================================================================

FINAL_LABEL_PATHS = {
    "솔루션": "offer.chosen_solution",
    "전면 솔루션": "offer.chosen_solution",
    "거래 목표": "transaction_strategy.primary_goal",
    "목표": "transaction_strategy.primary_goal",
    "시장": "strategy_tracks.A.market.country_or_region",
    "고객": "strategy_tracks.A.target.organization_type",
    "핵심 고객": "strategy_tracks.A.target.organization_type",
    "검토 부서": "strategy_tracks.A.recipient.first_reviewer",
    "구매 이유": "strategy_tracks.A.purchase_logic.purchase_reason",
    "대표 proof": "strategy_tracks.A.proof_strategy.primary_proof",
    "대표 근거": "strategy_tracks.A.proof_strategy.primary_proof",
    "진입 채널": "strategy_tracks.A.entry_strategy.primary_channel",
    "채널": "strategy_tracks.A.entry_strategy.primary_channel",
    "첫 cta": "strategy_tracks.A.cta_strategy.primary_cta",
    "다음 행동": "strategy_tracks.A.cta_strategy.primary_cta",
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

RESEARCH_PREFILL_SYS = """\
너는 공개 리서치 문서를 전략 인터뷰의 비확정 초안으로 구조화한다.
allowed_paths만 사용하고 문서에 직접 존재하는 정보만 출력한다.
전략 선택이 필요한 고객, 수신자, CTA는 추측하지 않는다.
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
    "transaction_strategy.primary_goal",
    "transaction_strategy.current_stage",
    "transaction_strategy.current_bottleneck",
    "strategy_tracks.A.market.country_or_region",
    "strategy_tracks.A.market.rationale",
    "strategy_tracks.A.purchase_logic.pain_point",
    "strategy_tracks.A.purchase_logic.current_alternative",
    "strategy_tracks.A.proof_strategy.primary_proof",
    "strategy_tracks.A.proof_strategy.source",
    "strategy_tracks.A.proof_strategy.verification_status",
    "strategy_tracks.A.entry_strategy.primary_channel",
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


def phase_research(client: Any) -> Dict[str, Any]:
    """사전 리서치 문서와 비확정 전략 초안을 만든다."""
    print("\n=== [1] 리서치 단계 ===")
    if SEARCH_PROVIDER.lower() == "google":
        print("  Google Grounding 통합 리서치 중...")
        research_doc = google_research(TARGET_COMPANY, COMPANY_HINTS)
    else:
        print("  [경고] 검색 비활성화 — 빈 리서치 문서로 인터뷰를 진행합니다.")
        research_doc = "(검색 비활성화)"
    state = init_state(TARGET_COMPANY)
    prefill = extract_research_prefill(client, research_doc)
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
    compute_completion(state)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    research_path = OUT_DIR / f"{TARGET_COMPANY}_research.md"
    research_path.write_text(
        f"# {TARGET_COMPANY} — 사전 리서치\n\n{research_doc}\n",
        encoding="utf-8",
    )
    print(f"  리서치 문서 저장: {research_path}")
    return {"state": state, "research_doc": research_doc}


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
    "goal": "첫 거래 목표",
    "market": "우선 시장",
    "target": "우선 고객",
    "purchase": "구매 이유",
    "recipient": "첫 검토 부서",
    "proof": "대표 실적·증거",
    "entry": "진입 경로",
    "cta": "처음 요청할 행동",
    "execution": "실행 위험",
}


def phase_interview(client: Any, state: Dict[str, Any], research_doc: str) -> List[Dict[str, str]]:
    """각 메인 질문 뒤 세부 후속 질문을 이어서 진행하고 마지막에 확인한다."""
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
        fingerprint = _compare_text(question)
        if fingerprint in state["interview_state"]["asked_question_fingerprints"]:
            required_label = PATH_LABELS.get(
                candidate.required_paths[0], KIND_DISPLAY.get(candidate.kind, "해당 항목")
            )
            question = (
                f"{semantic_attempt}번째 확인입니다. 앞선 답변에서 {required_label}이 아직 확정되지 않았습니다. "
                "이번에는 가장 중요한 내용 한 가지만 짧게 말씀해 주세요."
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
            if result["resolution_status"] == "confirmed_summary":
                state["completion"]["final_confirmed"] = True
            elif result.get("clarification_requested"):
                print(
                    "  [설명] '주력 진출안'은 이번 인터뷰에서 가장 먼저 실행할 시장·고객·접근 방식을 "
                    "한 묶음으로 정리한 것입니다. 비교안이 생길 때만 별도로 구분합니다."
                )
                question_state["rejection_reason"] = "사용자 설명 요청이 있어 수정 요약을 다시 확인해야 함"
            elif result["resolution_status"] == "resolved":
                print("  [반영] 말씀하신 수정 내용을 반영했습니다. 변경된 요약을 다시 확인합니다.")
                question_state["rejection_reason"] = "수정 반영 후 재확인 필요"
            else:
                question_state["rejection_reason"] = "최종 확인 또는 수정 내용을 해석하지 못함"
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
    transcript = phase_interview(client, research["state"], research["research_doc"])
    phase_output(research["state"], transcript)
    if research["state"]["completion"].get("final_confirmed"):
        print("\n인터뷰와 최종 확인이 완료되었습니다.")
    else:
        print("\n실행이 종료되었으며 최종 확인 또는 후속 확인이 남아 있습니다.")


if __name__ == "__main__":
    main()
