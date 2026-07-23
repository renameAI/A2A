# -*- coding: utf-8 -*-
"""
negotiation_sim.py — Buyer AI ↔ Seller AI 온톨로지 기반 협상 시뮬레이터 (v2)
==================================================================
v2 보완 (README_judge_ontology_simulation.md 대조 결과 반영):
  ① 가설 카드 산출: 최종 판정이 exploit/explore 가설(explore 는 evidence_needed
     필수 = JDG-13)과 조건별 check_method 를 남긴다 — "판단이란 확인 리스크와
     검증 계획을 남기는 행위"(README §4.1)
  ③ JDG-03: 차원 간 불일치(fit vs caution/unfit)를 코드가 자동으로 확인
     리스크로 변환
  ④ provenance: 세션에 데이터 등급(simulated) 기계 판독 태그
  ⑤ 시나리오 매트릭스: --scenario 로 결말 커버리지 분산
     (baseline=conditional / structural=terminate① / values=철수② /
      hold_reverse=hold+Impact역신호 / recommend_inbound=recommend+인바운드)

구조 (interview_agent.py 검증 구조 재활용):
  각 측 = [EXTRACT: 상대 발화→내 온톨로지 상태] + [SPEAK: 갭/무브 발화] 분리 호출.
온톨로지: buyer_ontology.yaml(BB1~10) / seller_ontology.yaml(SB1~10)
필요 패키지: pip install google-genai jsonschema
==================================================================
"""

import re
import json
import time
import argparse
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

# ==================================================================
# CONFIG
# ==================================================================
# API 키는 저장소에 커밋하지 않는다 — secrets_local.py(.gitignore 대상) 또는 환경변수에서 로드
import os as _os
try:
    from secrets_local import GOOGLE_API_KEY
except ImportError:
    GOOGLE_API_KEY = _os.environ.get("GOOGLE_API_KEY", "")
JUDGE_MODEL = "gemini-3.1-pro-preview"
SPEAK_MODEL = "gemini-3.1-flash-lite"
MAX_ROUNDS = 7
BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "negotiation_ontology.schema.json"
OUT_DIR = BASE_DIR
ADJUSTMENTS_PATH = BASE_DIR / "ontology_adjustments.json"   # 피드백 반영 조정 계층(③)


def load_adjustments() -> Dict[str, Any]:
    """피드백 루프(feedback_loop.py)가 축적한 온톨로지 조정 계층을 로드.
    정규 YAML 온톨로지는 불변으로 두고, 이 계층이 프롬프트에 '우선 규칙'으로 얹힌다."""
    if ADJUSTMENTS_PATH.exists():
        try:
            return json.loads(ADJUSTMENTS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[경고] 조정 파일 파싱 실패({e}) — 무시하고 진행")
    return {}


def _rules_text(rules: List[Dict[str, Any]]) -> str:
    if not rules:
        return ""
    lines = "\n".join(f"- ({r.get('basis', '?')}) {r.get('rule', '')}" for r in rules)
    return (f"\n\n[피드백 반영 조정 규칙 — 실사용 피드백에서 학습된 것으로, 위 기본 규범과 "
            f"충돌 시 이 규칙을 우선한다]\n{lines}")


SCOPE_FILTER_SYS = """\
너는 '조정 규칙 적용 심사자'다. 아래 협상 맥락(판매자·구매자 프로필)과 규칙 목록을 보고,
각 규칙이 '이 협상의 도메인·거래 유형'에 적용되어야 하는지 판정한다.
- 규칙의 scope.domain 이 이 협상의 도메인·거래 유형과 부합하면 적용.
- 명백히 다른 도메인의 관행(예: 아트 라이선스 관행을 반도체 부품 거래에)이면 제외.
- 확실하지 않으면 제외하라 — 잘못 적용하는 것이 더 해롭다.
반드시 JSON 하나만: {"apply": ["<rule id>", ...]}"""


def filter_adjustments(client, adj: Dict[str, Any], scn: Dict[str, str]) -> Dict[str, Any]:
    """활성 규칙을 (전역=무조건 적용) + (범위형=경량 심사로 적용/제외) 로 나눠 선별."""
    out = {"buyer": [], "seller": [], "buyer_skipped": 0, "seller_skipped": 0}
    scoped_all: List[Dict[str, Any]] = []
    for side in ("buyer", "seller"):
        for r in adj.get(f"{side}_rules", []):
            if r.get("status", "active") != "active":
                continue
            if r.get("scope", {}).get("global"):
                out[side].append(r)                 # 전역 규칙: 심사 없이 적용
            else:
                scoped_all.append((side, r))
    if not scoped_all:
        return out
    ctx = (f"[판매자] {scn['seller_company']}: {scn['seller_public'][:350]}\n"
           f"[구매자] {scn['buyer_company']}: {scn['buyer_public'][:350]}\n\n[규칙 목록]\n"
           + "\n".join(f"- id={r.get('id','?')} side={s} scope.domain={r.get('scope',{}).get('domain','(미지정)')} "
                       f"| rule={r.get('rule','')[:100]}" for s, r in scoped_all))
    try:
        res = chat_json(client, SPEAK_MODEL, SCOPE_FILTER_SYS, ctx,
                        temperature=0.1, max_tokens=800)
        apply_ids = set(res.get("apply", []) or [])
    except Exception:
        apply_ids = set()                            # 심사 실패 시 보수적으로 전부 제외
    for side, r in scoped_all:
        if r.get("id") in apply_ids:
            out[side].append(r)
        else:
            out[f"{side}_skipped"] += 1
    return out

# ==================================================================
# 시나리오 매트릭스 (⑤ 결말 커버리지 분산 — README §0 과적합 방지 장치의 재현)
# ==================================================================
_COBOT_PUBLIC = """\
장애물 극복 휠 모듈 '미라클휠' 제조사. 기존 휠체어·카트 설계를 크게 바꾸지 않고
장착만으로 계단·턱 극복 기능을 추가. Proof: CES 2025 혁신상 2관왕, 일본 독점공급
계약(연 10억 수출 보장), 적용 Before/After 데모 영상, 특허 포트폴리오."""
_COBOT_PRIVATE = """\
[내부 사정 — 필요시에만 정직하게 공개]
- 대량 양산 캐파 제한적(대형 체인 전량 공급 무리). 중규모까지 대응 가능.
- FDA 는 기기별 영향 분석 자료 제작 가능, CE MDR 분석 진행 중.
- 유럽 AS 미구축 — 초기 직접 밀착 + 확산 시 현지 파트너 방침.
- 일본 독점계약은 일본 한정(법무 확인 완료).
- 전략: 유럽 첫 레퍼런스. 소량 PoC 기본. MG 요구 없음."""

_KIMU_PUBLIC = """\
발달장애 디자이너 아트웍 스튜디오. 원천 창작물을 내부 프로 디자이너가 브랜드 목적에
맞게 편집·상업화. Proof: 삼성전자·페레로로쉐·YG 와 유료·반복 협업(전부 공개 가능),
전시/팝업 적용 비주얼 다수."""
_KIMU_PRIVATE = """\
[내부 사정 — 원칙]
- 핵심 원칙: 디자이너가 창작 주체로 크레딧 + 지속 로열티. 일회성 매입·무단 재해석 거절.
- 과거 카피 피해 경험 → 정식 라이선스 합의 전 시안 선공유 금지.
- 소규모·고부가 협업이 강점. 대량 저마진 공급은 부적합.
- 좋은 파트너에겐 빠르고 유연. 안 맞으면 정중히 철수."""

SCENARIOS: Dict[str, Dict[str, str]] = {
    "baseline": {   # 기대 결말: conditional (규제·비교데이터 조건)
        "seller_company": "코봇시스템", "seller_public": _COBOT_PUBLIC, "seller_private": _COBOT_PRIVATE,
        "buyer_company": "메디무브 GmbH",
        "buyer_public": "독일 재활·이동 보조기기 제조사(전동휠체어·보행보조기). 유럽 자체 브랜드, 내년 신모델 기획 중.",
        "buyer_private": """\
- 구매이유: 신모델 차별화 60% > 원가 25% > ESG 스토리 15%(스토리 앞세우면 미온).
- 대체재: 자체 R&D 서스펜션 개선 검토(make-vs-buy) + 프랑스 경쟁 모듈사 접촉.
- 선결: CE MDR 영향(분류 변경시 채택 불가), 유럽 AS 체계.
- 타이밍: 신모델 기획 진행 중 — 3개월 내 부품 확정(좋으면 촉매).
- 결정구조: 나는 제품기획 담당 — 승인은 경영진+규제팀. 내부 설득 자료 필요.
- 성향: 워싱 경계, 정직에 신뢰 가산, 카피 의도 없음, 리콜 치명 업계라 소규모 검증 선호.""",
        "first_speaker": "seller", "buyer_opening_hint": "",
    },
    "structural": {   # 기대 결말: terminate_structural (캐파 deal-breaker) — 키뮤 1-E 구조
        "seller_company": "코봇시스템", "seller_public": _COBOT_PUBLIC, "seller_private": _COBOT_PRIVATE,
        "buyer_company": "유로마트 리테일 그룹",
        "buyer_public": "유럽 12개국 1,200개 매장 대형 생활용품 유통 체인. 모빌리티 보조용품 카테고리 확장 중.",
        "buyer_private": """\
- 구매이유: 매대 회전율·마진. 스토리·혁신상은 참고사항일 뿐.
- 정책(고정): 전 매장 일괄 입점만 가능 — 일부 매장 테스트·소량 라인은 물류 정책상 불가.
- 선결(서류 선행): CE 인증 전 SKU + 배상책임보험 + EDI 연동 + 1,200개 매장 초도·리오더 대량 캐파.
  이 중 하나라도 안 되면 시스템 등록 자체가 불가 — 협상 여지 없음(표준 벤더 계약).
- 성향: 미팅 전 서류 확인이 프로세스. 캐파를 정직하게 밝히는 상대에겐 '나중에 규모 되면
  다시 연락하라'고 관계는 남긴다.""",
        "first_speaker": "seller", "buyer_opening_hint": "",
    },
    "values": {   # 기대 결말: seller walk_away_values (카피·착취) — 키뮤 1-F 구조
        "seller_company": "키뮤스튜디오", "seller_public": _KIMU_PUBLIC, "seller_private": _KIMU_PRIVATE,
        "buyer_company": "트렌디코 어패럴",
        "buyer_public": "유럽 중저가 패스트패션 브랜드. 시즌마다 아트 콜라보 캡슐 컬렉션 출시.",
        "buyer_private": """\
- 속내: 트렌드를 싸고 빠르게. 아트웍이 마음에 들면 일회성으로 사거나, 자체 디자인팀이
  '참고해서 유사하게 재해석'해 라이선스 없이 쓰는 관행(카피). 로열티는 절대 안 함.
- 접근: 관심 있는 척 시안 몇 개를 먼저 보내달라고 요구한다.
- 진정성·창작자 보상에는 관심 없음. 다양성 스토리는 마케팅 소재로만.""",
        "first_speaker": "seller", "buyer_opening_hint": "",
    },
    "hold_reverse": {   # 기대 결말: hold + Impact 역신호 — 키뮤 1-C/1-I 결합 구조
        "seller_company": "키뮤스튜디오", "seller_public": _KIMU_PUBLIC, "seller_private": _KIMU_PRIVATE,
        "buyer_company": "갤러리 세이가",
        "buyer_public": "도쿄의 컨템포러리 아트 갤러리. 신진 작가 발굴 전시로 알려짐.",
        "buyer_private": """\
- 구매이유: 예술적 진정성·동시대 미술 담론 내 고유성 50% > 관객 반응 30% > 사회 서사 20%.
- 역신호: '장애·포용 스토리'를 앞세우면 작품이 사연으로 소비된다고 보아 강한 거부.
  작가 개인의 시각 언어로 접근해야만 검토.
- 현실: 월 수십 개 포트폴리오 수신, 전시는 6~12개월 전 확정, 굿즈는 전시 후 부수적.
- 문화: 의사결정 신중·다단계. 관심이 있어도 절제된 화법("검토해 보겠습니다" = 거절 아님).
  재촉은 무례로 받아들임. 서두르지 않는 상대에게만 천천히 마음을 연다.
- 이번 대화에서는 확답을 주지 않는다 — 이미지 검토 후 천천히 연락하겠다는 수준까지만.""",
        "first_speaker": "seller", "buyer_opening_hint": "",
    },
    "recommend_clean": {   # 기대 결말: recommend — 무규제 도메인·다운사이드 0 (키뮤 1/1-H '밑져야 본전' 구조)
        "seller_company": "키뮤스튜디오", "seller_public": _KIMU_PUBLIC, "seller_private": _KIMU_PRIVATE,
        "buyer_company": "노르디스카 홈",
        "buyer_public": "북유럽 리빙·홈데코 브랜드. 시즌마다 아티스트 콜라보 캡슐 라인 출시.",
        "buyer_private": """\
- 구매이유: 콜라보 디자인 신선도 55% > 가격 30% > 브랜드 스토리 15%. 규제·인증 부담 없는 소비재.
- 현황: 다음 시즌 캡슐의 아티스트 슬롯 1개가 비어 있고 기존 유럽 풀 후보들이 식상함 — 새 결이 절실.
- 대체재: 유럽 아티스트 풀은 많지만 '이런 결'은 없음을 스스로 인지.
- 다운사이드: MG(최소보장) 요구 없고 실물 샘플만 확인되면 부담 없음 — 소량 캡슐이라 실패해도 손실 미미.
- 결정구조: 나는 크리에이티브 디렉터 — 콜라보 선정은 내 전결.
- 성향: 카피 의도 없음, 정당한 로열티 구조 수용. 실물 퀄리티 확인(샘플 지참 약속)이면 충분.
- 판단 기준: 핵심(디자인 핏·실물 검증 경로·로열티 구조)이 채워지고 다운사이드가 낮으면
  주저 없이 recommend(미팅 확정)로 결정한다 — '밑져야 본전'.""",
        "first_speaker": "seller", "buyer_opening_hint": "",
    },
    "recommend_inbound": {   # 기대 결말: recommend + 인바운드(진위 게이트) — 키뮤 1-H/1-G 요소
        "seller_company": "코봇시스템", "seller_public": _COBOT_PUBLIC, "seller_private": _COBOT_PRIVATE,
        "buyer_company": "알펜모빌 AG",
        "buyer_public": "스위스 프리미엄 전동휠체어 제조사. 아웃도어·액티브 세그먼트 강자.",
        "buyer_private": """\
- 상황: CES 2025 에서 미라클휠 실물 데모를 직접 확인(실재·진위 이미 확인됨) — 내가 먼저 연락.
- 구매이유: 차기 프리미엄 라인 차별화가 절실(경쟁사 대비 기능 정체). 예산 확보됨.
- 대체재: 자체 개발 검토했으나 특허 회피 어렵고 2년 이상 소요 결론 — 외부 모듈 도입으로 방향 확정.
- 선호: 소량 PoC 로 시작(리스크 관리), MG·독점 요구 없음. CE 인증 영향 자료는 당연히 필요.
- 결정구조: 나는 CTO — 사실상 결정권자(경영진 신뢰 두터움).
- 타이밍: 다음 분기 부품 아키텍처 확정 — 지금이 적기(촉매).""",
        "first_speaker": "buyer",
        "buyer_opening_hint": "CES 2025 부스에서 미라클휠 데모를 직접 봤고, 차기 라인 적용 가능성을 타진하려고 내가 먼저 연락하는 인바운드 첫 문의.",
    },
}

# ==================================================================
# 사용량/비용 계측
# ==================================================================
PRICING = {"gemini-3.1-pro-preview": (2.00, 12.00), "gemini-3.1-flash-lite": (0.10, 0.40)}
PRICING_DEFAULT = (0.30, 2.50)
USD_TO_KRW = 1400
USAGE: Dict[str, Dict[str, int]] = {}


def _record_usage(model: str, inp: int, out: int) -> None:
    u = USAGE.setdefault(model, {"input": 0, "output": 0, "calls": 0})
    u["input"] += inp; u["output"] += out; u["calls"] += 1


def report_cost() -> None:
    print("\n=== [비용] API 사용량 / 추정 비용 ===")
    total = 0.0
    for model, u in USAGE.items():
        pin, pout = PRICING.get(model, PRICING_DEFAULT)
        c = u["input"] / 1e6 * pin + u["output"] / 1e6 * pout
        total += c
        print(f"  {model}: 호출 {u['calls']}회 | 입력 {u['input']:,} · 출력 {u['output']:,} tok | ~${c:.4f}")
    print(f"  추정 총비용: ~${total:.4f} (약 {total*USD_TO_KRW:,.0f}원)")


# ==================================================================
# Gemini 헬퍼
# ==================================================================
def make_client() -> genai.Client:
    if not GOOGLE_API_KEY:
        raise SystemExit("[설정 필요] GOOGLE_API_KEY 를 채우세요.")
    return genai.Client(api_key=GOOGLE_API_KEY)


def chat(client, model, system, user, temperature=0.4, max_tokens=3000,
         json_mode=False, use_search=False) -> str:
    cfg: Dict[str, Any] = {"system_instruction": system, "temperature": temperature,
                           "max_output_tokens": max_tokens}
    if use_search:
        cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    elif json_mode:
        cfg["response_mime_type"] = "application/json"
    config = types.GenerateContentConfig(**cfg)
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=user)])]
    last_err = None
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=model, contents=contents, config=config)
            um = getattr(resp, "usage_metadata", None)
            if um is not None:
                _record_usage(model, int(getattr(um, "prompt_token_count", 0) or 0),
                              int(getattr(um, "candidates_token_count", 0) or 0)
                              + int(getattr(um, "thoughts_token_count", 0) or 0))
            text = getattr(resp, "text", None)
            if not text:
                parts = []
                for cand in (getattr(resp, "candidates", None) or []):
                    for p in (getattr(cand.content, "parts", None) or []):
                        if getattr(p, "text", None):
                            parts.append(p.text)
                text = "\n".join(parts)
            return (text or "").strip()
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Gemini 호출 실패: {last_err}")


def extract_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        s, e = text.find("{"), text.rfind("}")
        candidate = text[s:e + 1] if s != -1 and e != -1 else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {}


def chat_json(client, model, system, user, temperature=0.2, max_tokens=3000) -> Dict[str, Any]:
    for _ in range(2):
        parsed = extract_json(chat(client, model, system, user,
                                   temperature=temperature, max_tokens=max_tokens, json_mode=True))
        if parsed:
            return parsed
    return {}


# ==================================================================
# 온톨로지 상태 (YAML 정규 소스 미러)
# ==================================================================
BB_IDS = ["BB1_purpose_fit", "BB2_value_hierarchy", "BB3_substitute",
          "BB4_opportunity_cost", "BB5_evidence", "BB6_execution_gate",
          "BB7_timing", "BB8_trust", "BB9_decision_structure", "BB10_contract_protection"]
BB_KO = {"BB1_purpose_fit": "목적 정합", "BB2_value_hierarchy": "가치 서열",
         "BB3_substitute": "대체재 우위", "BB4_opportunity_cost": "기회비용·다운사이드",
         "BB5_evidence": "실증 신호", "BB6_execution_gate": "실행·선결 게이트",
         "BB7_timing": "단계·타이밍", "BB8_trust": "신뢰·진정성",
         "BB9_decision_structure": "결정 구조", "BB10_contract_protection": "계약·정보 보호"}
BB_PRIORITY = ["BB6_execution_gate", "BB8_trust",
               "BB1_purpose_fit", "BB2_value_hierarchy", "BB3_substitute",
               "BB4_opportunity_cost", "BB5_evidence", "BB7_timing",
               "BB9_decision_structure", "BB10_contract_protection"]

SB_IDS = ["SB1_pitch_alignment", "SB2_value_positioning", "SB3_evidence_packaging",
          "SB4_substitute_defense", "SB5_threshold_design", "SB6_execution_honesty",
          "SB7_authenticity_structure", "SB8_champion_arming",
          "SB9_protection_screening", "SB10_strategic_relationship"]


def init_bb() -> Dict[str, Any]:
    st = {b: {"status": "unknown", "verdict": "na"} for b in BB_IDS}
    st["BB8_trust"]["authenticity_gate"] = "na"
    st["BB8_trust"]["exploitation_detected"] = False
    st["BB6_execution_gate"]["dealbreaker"] = False
    return st


def init_sb() -> Dict[str, Any]:
    st = {b: {"status": "unknown"} for b in SB_IDS}
    st["SB1_pitch_alignment"].update({"buyer_real_reason": "", "misread_type": "unknown"})
    st["SB2_value_positioning"]["position"] = "unknown"
    st["SB4_substitute_defense"]["substitute_type"] = "unknown"
    st["SB9_protection_screening"]["screening"] = "unknown"
    st["SB10_strategic_relationship"]["stance"] = "pursue"
    return st


def merge(state, updates, valid_ids) -> None:
    for k, patch in (updates or {}).items():
        if k in valid_ids and isinstance(patch, dict):
            state[k].update(patch)


def bb_gaps(state) -> List[str]:
    return [b for b in BB_PRIORITY if state[b].get("status") != "confirmed"]


def brief(state, ids) -> str:
    return "\n".join(f"- {k}: {json.dumps(state[k], ensure_ascii=False)}" for k in ids)


# ==================================================================
# 온톨로지 프롬프트 요약 (YAML 과 1:1)
# ==================================================================
BUYER_ONTOLOGY_SUMMARY = """\
[구매자 판단 온톨로지 — 10 basis]
BB1 목적정합: 제안이 '내 목적(과 내 고객의 목적)'에 직접 기여하는가
BB2 가치서열: 상대 강조점이 내 '1순위 구매이유'와 일치하는가 (자랑≠구매이유; 스펙트럼 reverse_signal~mission)
BB3 대체재: 기존 대안(자체개발 make-vs-buy·경쟁사·현상유지) 대비 전환비용을 넘는 우위인가
BB4 기회비용: 실패 손실이 작은가 — 낮으면 '밑져야 본전' 수락 가능
BB5 실증: 주장이 '내 도메인 신뢰 화폐'로 증명되나
BB6 실행게이트(선결): 인증·캐파·AS·기존계약 — 구조적 미달은 deal-breaker
BB7 타이밍: 내 사이클의 '지금'인가 (진행중 데드라인은 촉매)
BB8 신뢰: 실재(진위)·구조적 진정성·정직성 (한계 정직 공개=신뢰 가산 / 착취신호=즉시 차단)
BB9 결정구조: 내부 승인 통과 자료가 필요한가
BB10 계약보호: IP·NDA 순서·독점 범위·표준 절차
[원칙] 내가 사는 가치≠상대가 파는 가치 / 열의는 판단이 아니다 / 미온의 원인을 구분해 표현
[결정] recommend / conditional(조건+검증방법 명시) / hold / terminate_structural(관계보존) / terminate_values(관계차단)"""

SELLER_ONTOLOGY_SUMMARY = """\
[판매자 판단 온톨로지 — 10 basis]
SB1 소구정렬: 상대의 '진짜 구매이유' 판독 — 미온 원인 3분류(방향→소구교체 / 증거→증거제시 / 문화→hold)
SB2 가치위치: 내 간판자산(스토리·임팩트)의 노출 수위 조절 (역신호면 감춰라)
SB3 증거패키징: 상대 도메인 화폐로, 요구 전에 예고, CTA와 결합
SB4 대체재방어: 존중 프레임으로 인정 후 '대체불가' 논거 (make-vs-buy엔 특허·시간가치·검증실적)
SB5 문턱설계: 상대 다운사이드를 먼저 제거 — 15분·소량 PoC·MG 없음·성과 조건부
SB6 실행정직: 한계를 먼저 정확히 공개 + 해소 경로 — 정직이 전략
SB7 진정성구조: 가치 주장은 주체+보상+투명성 구조로 증명
SB8 챔피언무장: 상대가 결정자가 아니면 내부 설득 자료를 쥐여줘라
SB9 보호선별: 카피·착취 신호엔 정보 봉쇄·철수 / 큰 제안은 범위 축소 역제안 / NDA 순서
SB10 전략관계: 매출 밖 가치 계산·hold 인내·철수 2종 구분(구조적=재접촉 여지/가치충돌=관계 없음)
[발화 표준 조립] [상대 기준 인정/복창]+[답(증거 예고)]+[낮은 CTA 1개] — 3요소 초과 금지"""


# ==================================================================
# 프롬프트 템플릿 ({} 치환 — JSON 예시는 이중 중괄호)
# ==================================================================
SELLER_SPEAK_TMPL = """\
너는 '{seller}' 의 해외 BD 담당자다. 아래 판매자 온톨로지를 판단 근거로, 상대(구매사)
에게 다음 발화 1개를 만든다.

{ontology}

[회사 공개 정보]
{public}
[내부 사정]
{private}

규칙:
- 발화는 표준 조립([인정]+[답/증거]+[낮은 CTA])을 따르고 4문장 이내. 마크다운 금지.
- 상대가 기준·우려를 말했으면 반드시 그 언어로 복창·응답. 같은 소구 3회 반복 금지.
- 내 한계를 물으면 정직하게 공개하고 해소 경로를 붙인다.
- 착취 신호(카피·무단 재해석·권리 무시·시안 선요청+로열티 거부)가 확실하면
  stance=withdraw_values 로 정보 봉쇄·품격 철수. 구조적 불가면 withdraw_structural(재접촉 여지).
- 상대가 절제·신중 화법(문화)이면 재촉하지 말고 hold 존중 발화.
- 첫 발화(대화 없음)면 콜드 오프닝: 소구 1개+낮은 CTA.
반드시 JSON 하나만 출력:
{{"move":"<사용 basis, 예: SB3+SB5>","utterance":"<발화>","stance":"pursue|hold|withdraw_structural|withdraw_values"}}"""

SELLER_EXTRACT_TMPL = """\
너는 판매자 '{seller}' 측의 판단 추출기다. 상대(구매자)의 직전 발화를 온톨로지에
대조해 SB 상태를 갱신한다.

{ontology}

갱신 대상(근거 있는 것만):
- SB1: buyer_real_reason(상대 발화 근거로만), misread_type(direction|evidence|culture|none|unknown), status
- SB2: position(reverse_signal|premise|bonus|weapon|core|mission|unknown), status
- SB4: substitute_type(existing_pool|in_house|make_vs_buy|status_quo|competitor|none|unknown), status
- SB8: champion_mode(bool), status
- SB9: screening(clean|caution|exploitative|unknown) — 시안 선요청+로열티 거부+재해석 언급은 exploitative, status
- 기타 SB 도 근거 있으면 status/note. 근거 없는 다발 금지.
반드시 JSON 하나만: {{"state_updates": {{"<SB id>": {{...}}}}}}"""

BUYER_SPEAK_TMPL = """\
너는 '{buyer}' 의 구매 검토 담당자다. 아래 구매자 온톨로지를 판단 근거로, 판매자에게
다음 발화 1개를 만들거나 결정을 내린다.

{ontology}

[회사 공개 정보]
{public}
[🔒 봉인 — 나만 아는 속내]
{private}

규칙:
- 갭 목록 최우선 basis 1개를 골라 검증질문을 내 맥락(봉인)에 맞춰 던진다. 4문장 이내. 마크다운 금지.
- 봉인을 한꺼번에 노출하지 않는다 — 상대의 좋은 질문/답에만 해당 부분을 드러낸다.
- 상대가 내 1순위와 다른 것을 앞세우면 내 기준을 밝히고 되받는다(봉인의 성향대로 —
  절제 화법 지시가 있으면 그 화법으로).
- 핵심 basis(BB1·BB2·BB3·BB5·BB6)가 채워졌고 게이트 통과면 action="decide".
- 게이트 위반 확증(BB6 구조적 미달 / BB8 착취)이면 즉시 action="decide" 종결.
- 봉인에 '확답 주지 않음' 지시가 있으면 결정은 hold 로.
반드시 JSON 하나만 출력:
{{"action":"continue|decide","target_basis":"<BB id or ''>","utterance":"<발화>",
 "decision":"recommend|conditional|hold|terminate_structural|terminate_values|"}}
(continue 면 decision 은 빈 문자열. decide 면 utterance 는 마무리 발화.)"""

BUYER_EXTRACT_TMPL = """\
너는 구매자 '{buyer}' 측의 판단 추출기다. 판매자의 직전 발화를 온톨로지에 대조해
BB 상태를 갱신한다.

{ontology}

[구매자 봉인(판정 기준)]
{private}

갱신 규칙:
- 근거 생긴 basis 만: {{"status":"assumed|confirmed","verdict":"fit|caution|unfit|na","evidence":"<한 문장>"}}
- BB2 는 seller_asset_position 포함 가능(reverse_signal|premise|bonus|weapon|core|mission|unknown).
- BB3 은 substitute_types 배열(existing_pool|in_house_capability|make_vs_buy|competitor|status_quo_installed|none).
- BB6 dealbreaker(bool)는 구조적 미달 '확증'시만 true.
- BB8: 판매자의 실재·검증 가능 신호(공개 수상 실물 확인, 실계약 증빙)가 있으면
  authenticity_gate="passed". 착취·기만이 명확하면 exploitation_detected=true.
- 판매자 '주장'만으로 confirmed 금지 — 검증 경로(자료·데모·분석 약속)까지 있으면
  confirmed, 주장뿐이면 assumed.
반드시 JSON 하나만: {{"state_updates": {{"<BB id>": {{...}}}}}}"""

BUYER_FINAL_TMPL = """\
너는 구매자 '{buyer}' 측 판단자다. 협상 전체 기록과 최종 BB 상태로 구조화 결정을 산출한다.
{ontology}
[봉인]
{private}
반드시 JSON 하나만 출력:
{{"state_updates": {{"<BB id>": {{"status":..,"verdict":..,"evidence":..}}}},
 "decision":"recommend|conditional|hold|terminate_structural|terminate_values",
 "rationale":"<근거 2~3문장>",
 "conditions":[{{"condition":"<선결조건>","check_method":"<무엇으로 채점/검증하는가>"}}, ...],
 "risks":[{{"type":"precondition|profitability|dismissed","desc":"<리스크>"}}, ...],
 "hypotheses":[{{"frame":"exploit|explore","lens":"buy","statement":"<이번 협상에서 검증된 처방 또는 시험할 베팅>","dimension":"<관련 BB id>","evidence_needed":"<explore 필수: 채점 방법>"}}, ...]}}
규칙: 모든 BB 의 status/verdict 확정. conditional 이면 conditions 1개 이상 + 각 조건에
check_method 필수. 리스크 최소 1개. 가설 카드 1~3개(explore 는 evidence_needed 없으면 무효=JDG-13)."""

SELLER_FINAL_TMPL = """\
너는 판매자 '{seller}' 측 판단자다. 협상 전체 기록과 최종 SB 상태로 최종 정리를 산출한다.
{ontology}
반드시 JSON 하나만 출력:
{{"state_updates": {{"<SB id>": {{완성 필드}}}},
 "outcome": "meeting_agreed|poc_agreed|deal_structured|hold|walk_away_structural|walk_away_values|no_agreement",
 "rationale": "<근거 2~3문장>",
 "hypotheses":[{{"frame":"exploit|explore","lens":"sell","statement":"<검증된 무브 처방 또는 시험할 베팅>","dimension":"<관련 SB id>","evidence_needed":"<explore 필수>"}}, ...]}}
모든 SB status 확정(근거=confirmed/추정=assumed/미확인=unknown).
SB3.evidence_offered / SB5.downside_removers / SB6.limits_disclosed 배열을 대화에서 실제
나온 것으로 채워라. 가설 카드 1~3개(explore 는 evidence_needed 필수).
[철수 2종 구분 — 엄수] SB9.screening=exploitative(카피·착취 감지)로 철수했다면 outcome 은
반드시 walk_away_values 다(관계를 맺지 않는 철수). walk_away_structural 은 '내 요건 미달'
(캐파·인증 등 구조적 불가, 관계 보존·재접촉 여지)일 때만 쓴다. 혼동 금지."""


# ==================================================================
# 결론 설명 문서 생성 (①) — 감사 추적(audit trail) 기반, 창작 금지
# ==================================================================
EXPLAIN_SYS = """\
너는 협상 판정의 '설명자'다. 아래 사실 자료(대화 기록, 상태변화 감사추적, 최종 상태,
결정)만을 근거로, 인간 사용자가 "왜 이 결론에 이르렀는가"를 이해할 수 있는 설명 문서를
마크다운으로 작성하라.

구성(이 순서·헤딩 유지):
# 협상 결론 설명서
## 1. 결론 요약  — 구매자 결정·판매자 outcome 을 각 1~2문장으로
## 2. 판단 경로 (라운드별)  — 각 라운드에서 '어떤 발화'가 '어떤 basis 판정'을 어떻게
   바꿨는지, 감사추적 항목을 인용해 인과로 서술 (발화 인용은 한 구절만)
## 3. 게이트 점검  — BB6(실행 게이트)·BB8(신뢰/착취)·SB9(선별) 이 어떻게 판정됐고
   결론에 어떤 영향을 줬는지
## 4. 왜 이 결정인가 / 왜 다른 결정이 아닌가  — 채택 결정의 결정적 근거와, 인접한
   대안 결정(예: conditional 대신 recommend/hold)이 기각된 이유
## 5. 조건과 검증 계획  — 각 조건과 check_method, 무엇이 확인되면 다음 단계로 가는지
## 6. 남은 가설  — exploit/explore 가설과 각 explore 의 채점 방법(evidence_needed)

규칙: 자료에 없는 내용을 창작하지 마라. 근거가 없는 항목은 "(기록 없음)" 으로 적어라.
마크다운 강조(**) 사용 가능, 표 사용 가능. 문서 전문만 출력."""


# ==================================================================
# 임의 회사 자동 프로필 생성 (① 사용자 지정 buyer/seller 지원)
#   실존 회사: Google 검색 그라운딩으로 공개 프로필 생성
#   가상 회사(자료 없음): 이름+힌트로 개연적 프로필 생성 ([구성] 표기)
#   봉인(private)은 각 온톨로지 축에 맞춰 개연적으로 생성 — provenance=constructed
# ==================================================================
def research_company(client, name: str, hint: str) -> str:
    doc = chat(client, SPEAK_MODEL,
               "회사를 Google 검색으로 조사해, B2B 협상 상대로서의 공개 프로필을 "
               "5~8줄 평문으로 작성하라(사업 내용·제품·시장·최근 동향·검증된 성과). "
               "검색 결과가 없으면 '자료 없음'만 출력.",
               f"회사명: {name}\n힌트: {hint or '(없음)'}",
               temperature=0.3, max_tokens=2000, use_search=True)
    if not doc or "자료 없음" in doc[:20]:
        doc = chat(client, SPEAK_MODEL,
                   "가상의 회사 프로필 설계자다. 이름과 힌트로 개연적인 B2B 공개 프로필을 "
                   "5~7줄 평문으로 작성하라. 첫 줄에 '[구성 — 가정 프로필]' 표기.",
                   f"회사명: {name}\n힌트: {hint or '(없음)'}",
                   temperature=0.6, max_tokens=1200)
    return doc.strip()


def gen_private(client, role: str, name: str, public: str, counterpart_public: str) -> str:
    if role == "buyer":
        spec = ("구매자 봉인을 buyer 온톨로지 축에 맞춰 6~9줄 평문으로: "
                "①구매이유 서열(% 포함, 1순위 명확) ②대체재(자체개발/경쟁사/현상유지 중 현실적인 것) "
                "③선결 게이트(해당 도메인의 규제·인증·캐파·AS 등) ④타이밍(사이클·데드라인) "
                "⑤결정구조(담당자=결정권자인지) ⑥성향(워싱 경계, 정직에 신뢰 가산, 카피 의도 없음=클린) "
                "⑦다운사이드 성향(소규모 검증 선호 등). 상대 회사 제품과 개연적으로 맞물리게.")
    else:
        spec = ("판매자 내부사정을 seller 온톨로지 축에 맞춰 5~8줄 평문으로: "
                "①정직하게 공개할 한계(캐파·인증·지원체계 중 현실적인 것) ②해소 경로 "
                "③전략 목표(레퍼런스 등) ④협상 원칙(MG 여부, 소량 PoC 등) ⑤보호 원칙(IP·NDA).")
    return chat(client, JUDGE_MODEL,
                f"협상 시뮬레이션 설계자다. {spec} 머리에 '[구성]' 표기. 평문만.",
                f"[{role} 회사] {name}\n[공개 프로필]\n{public}\n\n[상대 회사 프로필]\n{counterpart_public}",
                temperature=0.7, max_tokens=2500).strip()


# ==================================================================
# 회사 풀 모드 — company_research.py 가 저장한 리서치를 입력으로 사용
# ==================================================================
POOL_DIR = BASE_DIR / "company_pool"


def load_pool() -> List[Dict[str, Any]]:
    entries = []
    for f in sorted(POOL_DIR.glob("*.json")):
        try:
            entries.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return entries


def find_in_pool(pool: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    name = name.strip()
    for e in pool:                      # 정확 일치 우선
        if e["company"] == name:
            return e
    hits = [e for e in pool if name in e["company"]]
    return hits[0] if len(hits) >= 1 else None


def pool_scenario(client, s_entry: Dict[str, Any], b_entry: Dict[str, Any]) -> Dict[str, str]:
    """풀의 리서치 2건 → 협상 시나리오. 공개 프로필은 저장된 리서치를 그대로 쓰고,
    봉인(private)은 역할·상대에 맞춰 이 시점에 생성한다([구성])."""
    s_name, b_name = s_entry["company"], b_entry["company"]
    print(f"\n=== [풀 모드] 판매자: {s_name}  |  구매자: {b_name} ===")
    for e, role in ((s_entry, "판매자"), (b_entry, "구매자")):
        if e.get("provenance") != "grounded":
            print(f"  [주의] {role} {e['company']} 리서치 provenance={e.get('provenance')} — 자료 빈약")
    sp = f"({s_entry.get('sector','')}) {s_entry['public_profile']}"
    bp = f"({b_entry.get('sector','')}) {b_entry['public_profile']}"
    spriv = gen_private(client, "seller", s_name, sp, bp)
    bpriv = gen_private(client, "buyer", b_name, bp, sp)
    print("  봉인(private) 생성 완료 — provenance=[구성]")
    return {"seller_company": s_name, "seller_public": sp, "seller_private": spriv,
            "buyer_company": b_name, "buyer_public": bp, "buyer_private": bpriv,
            "first_speaker": "seller", "buyer_opening_hint": ""}


def choose_from_pool(pool: List[Dict[str, Any]],
                     seller_name: Optional[str], buyer_name: Optional[str],
                     interactive: bool) -> tuple:
    import random as _rnd
    if interactive:
        print(f"\n[회사 풀] {len(pool)}개사:")
        for i, e in enumerate(pool, 1):
            print(f"  {i:>3}. {e['company']:20s} {e.get('sector','')[:22]}")
        si = int(input("판매자 번호 > ").strip())
        bi = int(input("구매자 번호 > ").strip())
        return pool[si - 1], pool[bi - 1]
    s = find_in_pool(pool, seller_name) if seller_name else None
    b = find_in_pool(pool, buyer_name) if buyer_name else None
    if seller_name and not s:
        raise SystemExit(f"[오류] 풀에서 '{seller_name}' 을 찾지 못함 (--list-pool 로 확인)")
    if buyer_name and not b:
        raise SystemExit(f"[오류] 풀에서 '{buyer_name}' 을 찾지 못함 (--list-pool 로 확인)")
    remaining = [e for e in pool if e is not s and e is not b]
    if s is None:
        s = _rnd.choice(remaining); remaining = [e for e in remaining if e is not s]
    if b is None:
        b = _rnd.choice(remaining)
    return s, b


def build_custom_scenario(client, seller_name, seller_hint, buyer_name, buyer_hint) -> Dict[str, str]:
    print(f"\n=== [프로필 자동 생성] 판매자: {seller_name} / 구매자: {buyer_name} ===")
    sp = research_company(client, seller_name, seller_hint)
    print(f"  판매자 공개 프로필 생성 완료 ({len(sp)}자)")
    bp = research_company(client, buyer_name, buyer_hint)
    print(f"  구매자 공개 프로필 생성 완료 ({len(bp)}자)")
    spriv = gen_private(client, "seller", seller_name, sp, bp)
    bpriv = gen_private(client, "buyer", buyer_name, bp, sp)
    print("  봉인(private) 생성 완료 — provenance=[구성]")
    return {"seller_company": seller_name, "seller_public": sp, "seller_private": spriv,
            "buyer_company": buyer_name, "buyer_public": bp, "buyer_private": bpriv,
            "first_speaker": "seller", "buyer_opening_hint": ""}


# ==================================================================
# 협력 제안 메일 생성 (② recommend/conditional 결론 시)
#   판매자 메일: 확인된 구매이유 언어 + 조건별 대응(check_method 겨냥) + 낮은 CTA
#   구매자 메일: 결정 통보 + 요청 자료(조건·채점방법 기반) + 다음 단계
# ==================================================================
SELLER_EMAIL_SYS = """\
너는 판매자 회사의 BD 담당자다. 방금 끝난 협상 결과를 바탕으로 구매자에게 보낼
'협력 제안 후속 이메일'을 작성한다.
원칙(판매자 온톨로지):
- 상대의 '확인된 구매 이유'의 언어로 시작한다(내 자랑 언어 금지).
- 구매자가 남긴 조건 각각에 1:1로 대응한다 — 무엇을, 언제, 어떤 형태(check_method 를
  겨냥한 자료·데이터·샘플)로 제공할지 명시.
- 정직: 아직 안 되는 것은 안 된다고 쓰고 해소 일정과 함께.
- 마지막은 낮은 CTA 1개(15~30분 미팅/자료 검토 등)와 구체 일정 제안.
- 형식: 제목 1줄 + 본문 4~6문단, 존칭 비즈니스 한국어, 마크다운 강조 금지.
출력은 이메일 전문만."""

BUYER_EMAIL_SYS = """\
너는 구매자 회사의 검토 담당자다. 방금 끝난 협상 결과를 바탕으로 판매자에게 보낼
'검토 결과 회신 및 협력 진행 이메일'을 작성한다.
원칙(구매자 온톨로지):
- 결정(진행/조건부 진행)을 명확히 통보하고, 긍정 평가한 지점(우리 기준 충족)을 짚는다.
- 조건이 있으면 각 조건과 '무엇으로 검증할지(check_method)'를 요청 자료 목록으로 변환.
- 내부 절차(결정 구조)가 있으면 일정과 함께 안내.
- 마지막은 다음 단계 1개와 회신 기한 제안.
- 형식: 제목 1줄 + 본문 3~5문단, 존칭 비즈니스 한국어, 마크다운 강조 금지.
출력은 이메일 전문만."""


def gen_emails(client, session: Dict[str, Any], hist_text: str) -> Dict[str, str]:
    ctx = (f"[판매자] {session['seller_company']}\n[구매자] {session['buyer_company']}\n"
           f"[구매자 결정] {json.dumps(session['buyer_decision'], ensure_ascii=False)}\n"
           f"[판매자 최종상태 요약] SB1={json.dumps(session['seller_state']['SB1_pitch_alignment'], ensure_ascii=False)}\n"
           f"[대화 기록]\n{hist_text}")
    s_mail = chat(client, SPEAK_MODEL, SELLER_EMAIL_SYS, ctx, temperature=0.5, max_tokens=2500)
    b_mail = chat(client, SPEAK_MODEL, BUYER_EMAIL_SYS, ctx, temperature=0.5, max_tokens=2500)
    return {"seller": s_mail.strip(), "buyer": b_mail.strip()}


# ==================================================================
# 정규화 안전망 (열거형 이탈·형식 보정 — 스키마 적합 보장)
# ==================================================================
_ENUM_FIELDS = {
    ("bb", "BB2_value_hierarchy", "seller_asset_position"):
        ({"reverse_signal", "premise", "bonus", "weapon", "core", "mission", "unknown"}, "unknown"),
    ("bb", "BB8_trust", "authenticity_gate"): ({"passed", "failed", "na"}, "na"),
    ("sb", "SB1_pitch_alignment", "misread_type"):
        ({"direction", "evidence", "culture", "none", "unknown"}, "unknown"),
    ("sb", "SB2_value_positioning", "position"):
        ({"reverse_signal", "premise", "bonus", "weapon", "core", "mission", "unknown"}, "unknown"),
    ("sb", "SB4_substitute_defense", "substitute_type"):
        ({"existing_pool", "in_house", "make_vs_buy", "status_quo", "competitor", "none", "unknown"}, "unknown"),
    ("sb", "SB9_protection_screening", "screening"):
        ({"clean", "caution", "exploitative", "unknown"}, "unknown"),
    ("sb", "SB10_strategic_relationship", "stance"):
        ({"pursue", "hold", "withdraw_structural", "withdraw_values", "unknown"}, "unknown"),
}
_BB3_TYPES = {"existing_pool", "in_house_capability", "make_vs_buy", "competitor",
              "status_quo_installed", "none"}


def sanitize_session(session: Dict[str, Any]) -> None:
    states = {"bb": session["buyer_state"], "sb": session["seller_state"]}
    for (side, bid, field), (allowed, default) in _ENUM_FIELDS.items():
        node = states[side].get(bid, {})
        v = node.get(field)
        if v is not None and v not in allowed:
            node["note"] = (node.get("note", "") + f" [정규화: {field}='{v}'→'{default}']").strip()
            node[field] = default
    b3 = session["buyer_state"].get("BB3_substitute", {})
    if isinstance(b3.get("substitute_types"), list):
        kept = [x for x in b3["substitute_types"] if x in _BB3_TYPES]
        dropped = [x for x in b3["substitute_types"] if x not in _BB3_TYPES]
        if dropped:
            b3["note"] = (b3.get("note", "") + f" [정규화 제외: {dropped}]").strip()
        b3["substitute_types"] = kept
    for bid, f in (("BB6_execution_gate", "dealbreaker"), ("BB8_trust", "exploitation_detected")):
        node = session["buyer_state"].get(bid, {})
        if f in node and not isinstance(node[f], bool):
            node[f] = str(node[f]).lower() in ("true", "1", "yes")
    # 조건: 문자열 → {condition, check_method} 객체, check_method 누락 보정
    conds = session["buyer_decision"].get("conditions", [])
    fixed = []
    for c in conds:
        if isinstance(c, str):
            fixed.append({"condition": c, "check_method": "(미기재 — 후속 지정 필요)"})
        elif isinstance(c, dict):
            fixed.append({"condition": str(c.get("condition") or c.get("desc") or "(미기재)"),
                          "check_method": str(c.get("check_method") or "(미기재 — 후속 지정 필요)")})
    session["buyer_decision"]["conditions"] = fixed
    # 가설 카드: frame/lens 보정, explore 는 evidence_needed 보장(JDG-13)
    cards = []
    for h in session.get("hypotheses", []):
        if not isinstance(h, dict) or not h.get("statement"):
            continue
        frame = h.get("frame") if h.get("frame") in ("exploit", "explore") else "explore"
        lens = h.get("lens") if h.get("lens") in ("buy", "sell") else "buy"
        card = {"frame": frame, "lens": lens, "statement": str(h["statement"]),
                "dimension": str(h.get("dimension", ""))}
        ev = h.get("evidence_needed")
        if frame == "explore":
            card["evidence_needed"] = str(ev) if ev else "(미기재 — 채점 방법 지정 필요)"
        elif ev:
            card["evidence_needed"] = str(ev)
        cards.append(card)
    session["hypotheses"] = cards


def jdg03_dimension_conflict_risks(bb: Dict[str, Any], risks: List[Dict[str, str]]) -> None:
    """JDG-03: 차원 간 불일치(fit 존재 + caution/unfit 존재)를 자동으로 확인 리스크로 변환."""
    if not any(bb[k].get("verdict") == "fit" for k in BB_IDS):
        return
    existing = " ".join(str(r.get("desc", "")) for r in risks)
    for k in BB_IDS:
        v = bb[k].get("verdict")
        if v in ("caution", "unfit") and k not in existing and BB_KO[k] not in existing:
            risks.append({"type": "precondition",
                          "desc": f"[JDG-03 차원불일치] {BB_KO[k]}({k})={v} — 타 차원 fit 과 불일치, "
                                  f"확인 필요. 근거: {bb[k].get('evidence', '(미기재)')}"})


# ==================================================================
# 시뮬레이션 루프
# ==================================================================
def run(scn_name: str, scn: Optional[Dict[str, str]] = None) -> None:
    scn = scn or SCENARIOS[scn_name]
    seller, buyer = scn["seller_company"], scn["buyer_company"]
    print(textwrap.dedent(f"""
    ==================================================
     협상 시뮬레이터 v2 | 시나리오: {scn_name}
     판매자: {seller}  |  구매자: {buyer}
    =================================================="""))
    client = make_client()
    # ③ 피드백 조정 계층 로드 → 범위(scope) 심사 후 적용분만 온톨로지에 주입
    adj = load_adjustments()
    sel = filter_adjustments(client, adj, scn)
    buyer_onto = BUYER_ONTOLOGY_SUMMARY + _rules_text(sel["buyer"])
    seller_onto = SELLER_ONTOLOGY_SUMMARY + _rules_text(sel["seller"])
    if sel["buyer"] or sel["seller"] or sel["buyer_skipped"] or sel["seller_skipped"]:
        print(f"[조정 규칙] buyer 적용 {len(sel['buyer'])}·범위외 제외 {sel['buyer_skipped']} / "
              f"seller 적용 {len(sel['seller'])}·범위외 제외 {sel['seller_skipped']}")

    fmt_s = dict(seller=seller, ontology=seller_onto,
                 public=scn["seller_public"], private=scn["seller_private"])
    fmt_b = dict(buyer=buyer, ontology=buyer_onto,
                 public=scn["buyer_public"], private=scn["buyer_private"])
    P_SS = SELLER_SPEAK_TMPL.format(**fmt_s)
    P_SE = SELLER_EXTRACT_TMPL.format(seller=seller, ontology=seller_onto)
    P_BS = BUYER_SPEAK_TMPL.format(**fmt_b)
    P_BE = BUYER_EXTRACT_TMPL.format(buyer=buyer, ontology=buyer_onto,
                                     private=scn["buyer_private"])
    P_BF = BUYER_FINAL_TMPL.format(buyer=buyer, ontology=buyer_onto,
                                   private=scn["buyer_private"])
    P_SF = SELLER_FINAL_TMPL.format(seller=seller, ontology=seller_onto)

    bb, sb = init_bb(), init_sb()
    transcript: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []       # ① 상태변화 감사 추적 (설명 문서의 근거)
    buyer_decided: Optional[str] = None
    rounds = 0

    def note_audit(rnd: int, side: str, updates: Dict[str, Any]) -> None:
        for k, p in (updates or {}).items():
            if isinstance(p, dict) and p:
                compact = {kk: (vv[:120] if isinstance(vv, str) else vv) for kk, vv in p.items()}
                audit.append({"round": rnd, "side": side, "basis": k, "update": compact})

    def hist() -> str:
        return "\n".join(f"[{t['who']}] {t['text']}" for t in transcript) or "(아직 대화 없음)"

    # 인바운드: 구매자 첫 문의
    if scn["first_speaker"] == "buyer":
        bp = chat_json(client, SPEAK_MODEL, P_BS,
                       f"[라운드 0 — 인바운드 첫 문의]\n{scn['buyer_opening_hint']}\n"
                       f"위 상황으로 판매자에게 보내는 첫 문의 발화를 만들어라(action=continue).",
                       temperature=0.6)
        b_utt = (bp.get("utterance") or "").strip() or "(발화 생성 실패)"
        print(f"\n[R0 구매자(인바운드)]\n  {b_utt}")
        transcript.append({"who": "buyer", "text": b_utt, "round": 0})

    for rnd in range(1, MAX_ROUNDS + 1):
        rounds = rnd
        # ---------- SELLER ----------
        if any(t["who"] == "buyer" for t in transcript):
            upd = chat_json(client, JUDGE_MODEL, P_SE,
                            f"[대화 기록]\n{hist()}\n\n[현재 SB]\n{brief(sb, SB_IDS)}\n\n"
                            f"[구매자 직전 발화]\n{transcript[-1]['text']}")
            merge(sb, upd.get("state_updates", {}), SB_IDS)
            note_audit(rnd, "seller", upd.get("state_updates", {}))
        sp = chat_json(client, SPEAK_MODEL, P_SS,
                       f"[라운드 {rnd}]\n[대화 기록]\n{hist()}\n\n[현재 SB]\n{brief(sb, SB_IDS)}\n"
                       f"다음 발화를 만들어라.", temperature=0.6)
        s_utt = (sp.get("utterance") or "").strip() or "(발화 생성 실패)"
        stance = sp.get("stance", "pursue")
        print(f"\n[R{rnd} 판매자 | move={sp.get('move','?')} | stance={stance}]\n  {s_utt}")
        transcript.append({"who": "seller", "text": s_utt, "round": rnd})
        sb["SB10_strategic_relationship"]["stance"] = stance if stance in (
            "pursue", "hold", "withdraw_structural", "withdraw_values") else "pursue"
        if stance in ("withdraw_structural", "withdraw_values"):
            print("\n[판매자] 철수 — 협상 종료.")
            break

        # ---------- BUYER ----------
        upd = chat_json(client, JUDGE_MODEL, P_BE,
                        f"[대화 기록]\n{hist()}\n\n[현재 BB]\n{brief(bb, BB_IDS)}\n\n"
                        f"[판매자 직전 발화]\n{s_utt}")
        merge(bb, upd.get("state_updates", {}), BB_IDS)
        note_audit(rnd, "buyer", upd.get("state_updates", {}))
        bp = chat_json(client, SPEAK_MODEL, P_BS,
                       f"[라운드 {rnd}]\n[대화 기록]\n{hist()}\n\n[현재 BB]\n{brief(bb, BB_IDS)}\n\n"
                       f"[미확정 갭(우선순위순)]\n{bb_gaps(bb)}\n다음 발화 또는 결정을 만들어라.",
                       temperature=0.6)
        b_utt = (bp.get("utterance") or "").strip() or "(발화 생성 실패)"
        action = bp.get("action", "continue")
        print(f"\n[R{rnd} 구매자 | {'결정' if action=='decide' else '질문→'+str(bp.get('target_basis',''))}]\n  {b_utt}")
        transcript.append({"who": "buyer", "text": b_utt, "round": rnd})
        if action == "decide":
            buyer_decided = bp.get("decision") or ""
            print(f"\n[구매자] 결정 신호: {buyer_decided}")
            break

    # ---------- 최종 구조화 판정 ----------
    print("\n=== 최종 판정 ===")
    bf = chat_json(client, JUDGE_MODEL, P_BF,
                   f"[대화 전체]\n{hist()}\n\n[최종 BB]\n{brief(bb, BB_IDS)}\n"
                   f"[대화 중 결정 신호] {buyer_decided or '(없음 — 라운드 소진/판매자 철수)'}",
                   max_tokens=4500)
    merge(bb, bf.get("state_updates", {}), BB_IDS)
    note_audit(rounds, "buyer_final", bf.get("state_updates", {}))
    buyer_decision = {"decision": bf.get("decision", buyer_decided or "hold"),
                      "rationale": bf.get("rationale", ""),
                      "conditions": bf.get("conditions", []),
                      "risks": bf.get("risks", [])}
    buyer_hyps = bf.get("hypotheses", [])

    sf = chat_json(client, JUDGE_MODEL, P_SF,
                   f"[대화 전체]\n{hist()}\n\n[최종 SB]\n{brief(sb, SB_IDS)}\n"
                   f"[구매자 최종 결정] {buyer_decision['decision']}", max_tokens=4500)
    merge(sb, sf.get("state_updates", {}), SB_IDS)
    note_audit(rounds, "seller_final", sf.get("state_updates", {}))
    seller_outcome = {"outcome": sf.get("outcome", "no_agreement"),
                      "rationale": sf.get("rationale", "")}
    seller_hyps = sf.get("hypotheses", [])
    for h in buyer_hyps:
        if isinstance(h, dict):
            h["lens"] = "buy"
    for h in seller_hyps:
        if isinstance(h, dict):
            h["lens"] = "sell"

    # ---------- 보정·안전망 ----------
    for k in BB_IDS:
        bb[k].setdefault("status", "unknown"); bb[k].setdefault("verdict", "na")
        if bb[k]["status"] not in ("unknown", "assumed", "confirmed"): bb[k]["status"] = "assumed"
        if bb[k]["verdict"] not in ("fit", "caution", "unfit", "na"): bb[k]["verdict"] = "na"
    for k in SB_IDS:
        sb[k].setdefault("status", "unknown")
        if sb[k]["status"] not in ("unknown", "assumed", "confirmed"): sb[k]["status"] = "assumed"
    if not buyer_decision["rationale"]:
        buyer_decision["rationale"] = "(근거 미산출)"
    if buyer_decision["decision"] == "terminate_structural":
        bb["BB6_execution_gate"]["dealbreaker"] = True
    if not seller_outcome["rationale"]:
        seller_outcome["rationale"] = "(근거 미산출)"
    # 철수 2종 구분 안전망: 착취 감지 상태의 철수는 반드시 values 로 (결정론적 강제)
    if (sb["SB9_protection_screening"].get("screening") == "exploitative"
            and str(seller_outcome["outcome"]).startswith("walk_away")):
        if seller_outcome["outcome"] != "walk_away_values":
            seller_outcome["rationale"] += " [보정: SB9=exploitative → walk_away_values 로 재분류]"
            seller_outcome["outcome"] = "walk_away_values"
    if sb["SB10_strategic_relationship"].get("stance") == "withdraw_values" \
            and str(seller_outcome["outcome"]).startswith("walk_away"):
        seller_outcome["outcome"] = "walk_away_values"
    jdg03_dimension_conflict_risks(bb, buyer_decision["risks"])   # ③ JDG-03

    session = {"seller_company": seller, "buyer_company": buyer, "rounds": rounds,
               "buyer_state": bb, "buyer_decision": buyer_decision,
               "seller_state": sb, "seller_outcome": seller_outcome,
               "hypotheses": (buyer_hyps or []) + (seller_hyps or []),          # ① 가설 카드
               "audit": audit,                                                  # 판정 감사 추적
               "provenance": {"buyer_side": "simulated", "seller_side": "simulated",
                              "outcome_anchor": False}}                          # ④ provenance
    sanitize_session(session)
    if buyer_decision["decision"] == "conditional" and not session["buyer_decision"]["conditions"]:
        session["buyer_decision"]["conditions"] = [
            {"condition": "(조건 미상세)", "check_method": "(미기재 — 후속 지정 필요)"}]

    # ---------- 출력·저장·검증 ----------
    print(f"\n[구매자 결정] {buyer_decision['decision']} — {buyer_decision['rationale']}")
    for c in session["buyer_decision"]["conditions"]:
        print(f"  조건: {c['condition']}  | 채점: {c['check_method']}")
    print(f"[판매자 outcome] {seller_outcome['outcome']} — {seller_outcome['rationale']}")
    print(f"[가설 카드] {len(session['hypotheses'])}건")

    tag = f"{scn_name}_{seller}_vs_{buyer}".replace(" ", "")
    (OUT_DIR / f"negotiation_{tag}_session.json").write_text(
        json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    log = [f"# 협상 로그 [{scn_name}] — {seller}(판매) ↔ {buyer}(구매)\n"]
    for t in transcript:
        log.append(f"## R{t['round']} {'판매자' if t['who']=='seller' else '구매자'}\n{t['text']}\n")
    log.append(f"## 최종\n- 구매자: **{buyer_decision['decision']}** — {buyer_decision['rationale']}\n"
               f"- 조건: {json.dumps(session['buyer_decision']['conditions'], ensure_ascii=False)}\n"
               f"- 리스크: {json.dumps(buyer_decision['risks'], ensure_ascii=False)}\n"
               f"- 판매자: **{seller_outcome['outcome']}** — {seller_outcome['rationale']}\n"
               f"- 가설 카드: {json.dumps(session['hypotheses'], ensure_ascii=False, indent=1)}\n")
    (OUT_DIR / f"negotiation_{tag}_transcript.md").write_text("\n".join(log), encoding="utf-8")
    print(f"산출물: negotiation_{tag}_session.json / _transcript.md")

    # ---------- ① 결론 설명 문서 (감사 추적 기반, 모든 결말에서 생성) ----------
    expl = chat(client, JUDGE_MODEL, EXPLAIN_SYS,
                f"[대화 기록]\n{hist()}\n\n[상태변화 감사추적(라운드·측·basis·갱신)]\n"
                f"{json.dumps(audit, ensure_ascii=False, indent=1)}\n\n"
                f"[최종 구매자 상태]\n{brief(bb, BB_IDS)}\n\n[최종 판매자 상태]\n{brief(sb, SB_IDS)}\n\n"
                f"[구매자 결정]\n{json.dumps(session['buyer_decision'], ensure_ascii=False)}\n"
                f"[판매자 outcome]\n{json.dumps(seller_outcome, ensure_ascii=False)}\n"
                f"[가설 카드]\n{json.dumps(session['hypotheses'], ensure_ascii=False)}",
                temperature=0.3, max_tokens=5000)
    exp_path = OUT_DIR / f"negotiation_{tag}_explanation.md"
    exp_path.write_text(expl + f"\n\n---\n*본 설명서는 세션 감사추적(audit) {len(audit)}건을 "
                               f"근거로 생성됨. 피드백은 feedback_loop.py 로 입력.*\n",
                        encoding="utf-8")
    print(f"결론 설명서: {exp_path.name}")

    try:
        import jsonschema
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(session, schema)
        print("[검증] 세션 스키마 통과 ✅")
    except ImportError:
        print("[검증] jsonschema 미설치 — 건너뜀")
    except Exception as e:
        print(f"[검증] 스키마 위반: {e}")

    # ---------- ② 협력 제안 메일 (recommend/conditional 시 양측 관점 생성) ----------
    if buyer_decision["decision"] in ("recommend", "conditional"):
        print("\n=== 협력 제안 메일 생성 (결론:", buyer_decision["decision"], ") ===")
        mails = gen_emails(client, session, hist())
        sm = OUT_DIR / f"negotiation_{tag}_email_seller.md"
        bm = OUT_DIR / f"negotiation_{tag}_email_buyer.md"
        sm.write_text(f"# 판매자({seller}) → 구매자({buyer}) 협력 제안 메일\n\n{mails['seller']}\n",
                      encoding="utf-8")
        bm.write_text(f"# 구매자({buyer}) → 판매자({seller}) 검토 회신 메일\n\n{mails['buyer']}\n",
                      encoding="utf-8")
        print(f"  저장: {sm.name} / {bm.name}")
        print("\n--- [판매자 제안 메일 미리보기] ---")
        print(textwrap.indent(mails["seller"][:600], "  "))
    else:
        print(f"\n(결론이 {buyer_decision['decision']} 이므로 제안 메일은 생성하지 않음)")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="온톨로지 기반 협상 시뮬레이터 v3 — 기본 실행 = 회사 풀에서 두 회사 선택",
        epilog="기본(인자 없음): company_pool/ 에서 임의 두 회사로 협상. "
               "선택: --pool-seller '회사명' --pool-buyer '회사명' 또는 --choose(번호 선택). "
               "풀 채우기: company_research.py 사용.")
    ap.add_argument("--pool-seller", default=None, help="풀에서 판매자 회사명 지정(부분일치)")
    ap.add_argument("--pool-buyer", default=None, help="풀에서 구매자 회사명 지정(부분일치)")
    ap.add_argument("--choose", action="store_true", help="풀 목록을 보고 번호로 직접 선택")
    ap.add_argument("--list-pool", action="store_true", help="회사 풀 목록 출력 후 종료")
    ap.add_argument("--scenario", default=None, choices=list(SCENARIOS.keys()) + ["all"],
                    help="(구모드) 프리셋 시나리오")
    ap.add_argument("--seller", default=None, help="(구모드) 임의 판매자 — 즉석 리서치·봉인 생성")
    ap.add_argument("--buyer", default=None, help="(구모드) 임의 구매자 — 즉석 리서치·봉인 생성")
    ap.add_argument("--seller-hint", default="", help="판매자 검색/설정 힌트")
    ap.add_argument("--buyer-hint", default="", help="구매자 검색/설정 힌트")
    ap.add_argument("--profiles", default=None, help="(구모드) 수동 프로필 JSON 경로")
    args = ap.parse_args()

    if args.list_pool:
        pool = load_pool()
        print(f"[회사 풀] {POOL_DIR} — {len(pool)}개사")
        for e in pool:
            print(f"  · {e['company']:20s} [{e.get('provenance','?'):12s}] {e.get('sector','')[:22]}")
        return

    if args.profiles:                      # (구모드) 수동 프로필 파일
        scn = json.loads(Path(args.profiles).read_text(encoding="utf-8"))
        scn.setdefault("first_speaker", "seller"); scn.setdefault("buyer_opening_hint", "")
        run("custom", scn)
    elif args.seller and args.buyer:       # (구모드) 즉석 리서치
        client = make_client()
        scn = build_custom_scenario(client, args.seller, args.seller_hint,
                                    args.buyer, args.buyer_hint)
        (OUT_DIR / f"custom_profiles_{args.seller}_{args.buyer}.md".replace(" ", "")).write_text(
            f"# 자동 생성 프로필 [구성]\n\n## 판매자: {scn['seller_company']}\n{scn['seller_public']}\n\n"
            f"### 내부사정\n{scn['seller_private']}\n\n## 구매자: {scn['buyer_company']}\n"
            f"{scn['buyer_public']}\n\n### 봉인\n{scn['buyer_private']}\n", encoding="utf-8")
        run("custom", scn)
    elif args.scenario:                    # (구모드) 프리셋
        for n in (list(SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]):
            run(n)
    else:                                  # ★ 기본 = 풀 모드 (③)
        pool = load_pool()
        if len(pool) < 2:
            raise SystemExit(
                f"[오류] 회사 풀({POOL_DIR})에 회사가 {len(pool)}개뿐입니다.\n"
                f"  먼저 리서치 프로그램으로 풀을 채우세요:\n"
                f"    python company_research.py --kosdaq 200\n"
                f"    python company_research.py --company '회사명' --hint '설명'")
        s_entry, b_entry = choose_from_pool(pool, args.pool_seller, args.pool_buyer,
                                            interactive=args.choose)
        client = make_client()
        scn = pool_scenario(client, s_entry, b_entry)
        run("pool", scn)
    report_cost()
    print("\n완료.")


if __name__ == "__main__":
    main()
