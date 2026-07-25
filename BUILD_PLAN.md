# 독립 전략 인터뷰 프로그램 구축 계획 (v1 기준 기록)

> 이 문서는 최초 설계의 기준선 기록이다. 현재 실행판은 10개 핵심 의사결정 질문과 최종 확인을 사용하는 v4다. 15문항에서 피로도를 안내하며 전체 40문항은 안전 상한으로만 사용한다. 최신 사용법은 `README.md`를 따른다.

## v4 확정 변경

1. 기존 v3는 `interview_test_v3_full_ontology.py`와 v3 스키마로 보존한다.
2. PoC 고객 발굴은 프로그램 전제로 기록하며 대표에게 거래 목표와 현재 단계를 반복해서 묻지 않는다.
3. 질문은 전면 제안, 시장, 고객, 예산 배경, 차별점, 검토 부서, 실적, 진입 경로, 첫 행동, 후속 전환에 집중한다.
4. 사용 상황·사용 후 변화·거래 단위·제공 준비도·특정 실적 하나·실행 위험 등은 고정 후속 질문으로 만들지 않는다.
5. 복수 고객군과 복수 접근 경로는 유효한 전략 답변으로 보존한다.
6. 검색 결과는 선택지나 가설을 만들지 않고 질문의 배경 사실로만 사용한다.
7. 검색 사실은 리서치 문서의 원문으로 역검증하며, 시장 활동·고객 협업·솔루션 작동 방식·실적의 네 범주만 허용한다.
8. 예산, 검토 부서, 진입 경로, 첫 행동, 후속 전환에는 검색 사실을 사용하지 않는다.
9. 검색 배경을 허용한 모든 질문에서도 현재 사용자가 확정한 전면 제안과 핵심 단어가 겹치는 사실만 허용한다.

## 0. 이 문서의 목적

이 문서는 새 Codex 대화에서 기존 대화의 긴 맥락 없이도 바로 개발을 시작할 수 있도록 만든 인수인계 문서다.

새 프로그램은 현재 Target-first 독립 실행판을 안전한 기준점으로 사용하되, 기존 파일을 수정하지 않고 별도의 독립 실행 프로그램으로 구축한다. 데이터 확보·웹 리서치 품질은 이번 개발의 중심 범위에서 제외하고, 인터뷰 질문·답변 해석·전략 트랙 구성·완료 판정·결과 저장을 개선한다.

---

## 1. 현재 기준 파일과 자료

### 1.1 실행 기준 파일

현재 최신 독립 실행판:

```text
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\interview_agent(v0)\interview_agent_target_first_standalone.py
```

특징:

- `interview_agent.py`를 import하지 않는 단일 Python 파일이다.
- `.env`에서 Friendli 및 Google API 설정을 읽는다.
- Google Grounding 기반 사전 리서치와 EXAONE 기반 추출·정규화를 포함한다.
- B1~B9 핵심 질문 9개와 최종 확인 1개, 총 10문항으로 제한되어 있다.
- 결과를 `runs/target_first`에 저장한다.
- 현재 질문 온톨로지 JSON Schema를 검증에 사용한다.

### 1.2 현재 스키마

```text
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\10_interview_ontology.schema.json
```

### 1.3 보편 인터뷰 규칙을 도출할 참고 자료

```text
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\anpoly_interview_process.md
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\cobotsystem_interview_process.md
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\kimustudio_interview_process.md
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\livecare_interview_process.md
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\mushn_interview_process.md
```

이 자료에서는 회사명, 시장명, 실제 답변, 수치 같은 개별 데이터를 규칙으로 고정하지 않는다. 여러 기업에 반복 적용되는 질문 순서와 판단 원칙만 사용한다.

---

## 2. 새 프로그램의 목표

새 프로그램은 최대 10번의 사용자 질문 안에서 다음 결과를 만드는 것을 목표로 한다.

1. 이번 진출에서 전면에 세울 솔루션과 거래 형태를 분명히 한다.
2. 하나의 주력 실행 트랙과 필요 시 하나의 비교 트랙을 구분한다.
3. 각 트랙에서 시장, 고객, 구매 촉발 상황, 검토 부서, 구매 이유가 서로 맞물리게 한다.
4. 구매 이유를 단순 문구가 아니라 고객 문제, 기존 대안, 손실·위험, 긴급성, Key Benefit으로 해석한다.
5. 솔루션과 Proof Point를 구분하고, 실제 제시 가능한 증거와 공개 조건을 기록한다.
6. 진입 채널과 파트너 역할을 구분한다.
7. 첫 CTA와 이후 샘플·PoC·파일럿·계약 전환 흐름을 연결한다.
8. 앞선 답변이 바뀌면 영향을 받는 후속 항목을 자동으로 재검토한다.
9. 10문항 안에 확인되지 않은 내용은 추정 완성하지 않고 명시적인 후속 확인 목록으로 남긴다.
10. 인터뷰가 끝났다는 사실과 실행 준비가 끝났다는 사실을 구분한다.

### 2.1 반드시 유지할 UX 원칙

- 사용자에게 보여주는 질문은 최대 10개다.
- 마지막 1개 질문은 전체 전략 확인·수정 질문으로 예약한다.
- 한 질문은 최대 두 개의 밀접한 개념까지만 묻는다.
- 한 질문 안에 서로 독립적인 세 가지 이상을 나열하지 않는다.
- 내부 스키마 키와 enum 토큰을 사용자에게 그대로 노출하지 않는다.
- 리서치 초안이 사용자 확정 솔루션과 맞지 않으면 질문에 노출하지 않는다.
- 사용자가 짧게 답해도 의미가 충분하면 재질문하지 않는다.
- `모름`, `미정`, `없음`은 실패가 아니라 유효한 상태로 처리한다.
- 정확한 수치가 없으면 수치를 만들어내거나 반복해서 강요하지 않는다.

### 2.2 이번 작업에서 하지 않을 것

- Google Grounding 검색 방식의 전면 재설계
- EXAONE 모델 또는 Friendli 엔드포인트 교체
- 배치 실행 기능 변경
- GUI, 웹 인터페이스 또는 데이터베이스 구축
- 콜드메일 자동 작성 기능 추가
- 기존 `interview_agent.py`, `interview_agent_target_first.py`, 현재 standalone 파일 수정

---

## 3. 현재 프로그램에서 반드시 해결해야 할 구조적 문제

### 3.1 9개 질문이 9개 대표 필드에 모두 소비됨

현재는 B1~B9마다 대표 필드가 하나씩 지정되어 있고 내용 질문도 9개다. 따라서 신규 인터뷰에서는 각 다발을 한 번씩 질문하면 질문 예산이 끝난다.

현재 `MAX_SEMANTIC_ATTEMPTS_PER_BUNDLE = 2`이지만, 재질문 큐는 아직 묻지 않은 다발 뒤에 처리된다. 일반적인 신규 실행에서는 의미상 재질문이 실행될 자리가 없다.

해결 방향:

- 질문 라우터가 다발 순회가 아니라 `전략적으로 가장 큰 미해결 위험`을 기준으로 다음 질문을 선택한다.
- 모든 B1~B9를 무조건 한 번씩 묻지 않는다.
- 답변 한 번으로 두 개의 강하게 연결된 필드가 명확해지면 둘 다 확정한다.
- 리서치나 이전 답변으로 충분한 항목은 질문 예산을 쓰지 않는다.
- 재질문은 단순히 큐 마지막에 넣지 않고, 현재 답변이 핵심 전략의 분기점인지 평가해 우선순위를 정한다.

### 3.2 풍부한 스키마와 실제 인터뷰가 분리되어 있음

현재 스키마에는 다음 필드가 있지만 기본 Target-first 인터뷰에서는 거의 질문하지 않는다.

- 현재 단계와 병목
- 성공 기준
- 구매 촉발 조건과 제외 조건
- 실제 사용자·수혜자·예산 승인자
- 기존 대안·손실·긴급성·Key Benefit
- Proof 출처·검증 상태·공개 범위·샘플 준비도
- 파트너 역할·인센티브
- CTA 이후 전환 흐름·수락 기준
- A/B 트랙
- 규제·원가·양산·NDA 제약

해결 방향:

- 스키마 필드 수를 그대로 질문 수로 변환하지 않는다.
- 필드를 `핵심 앵커`, `트랙 일관성`, `실행 가능성`, `후속 확인` 네 등급으로 분류한다.
- 질문 예산 안에서는 핵심 앵커와 가장 위험한 실행 조건을 우선 확인한다.

### 3.3 복수 시장과 복수 고객의 관계가 사라짐

현재 `markets`, `segments`, `roles`는 배열이지만 각 값 사이의 관계를 표현하지 않는다.

예를 들어 일본 상업 판매와 베트남 공공 PoC를 동시에 저장하면 다음 관계가 사라진다.

- 일본 → 와규 농장 → 농장 오너 → ROI → 직접 접근 → 미팅 후 PoC
- 베트남 → 대학·연구기관 → 연구 책임자 → 실증 리포트 → 대학 경유 → 공동 PoC 제안

해결 방향:

- 새 온톨로지의 중심 단위를 `strategy_track` 객체로 변경한다.
- 최대 2개 트랙을 지원한다.
- 각 트랙이 시장·고객·수신자·구매 논리·Proof·채널·CTA를 자체적으로 가진다.

### 3.4 대표의 수정이 후속 전략에 전파되지 않음

현재 최종 확인에서 시장이나 솔루션을 수정해도 기존 고객, 구매 이유, Proof, 채널, CTA를 다시 평가하지 않는다.

해결 방향:

- 필드 간 의존성 그래프를 코드에 명시한다.
- 상위 앵커가 바뀌면 하위 필드를 `stale` 상태로 변경한다.
- stale 값은 삭제하지 않고 이전 값과 변경 이유를 기록한다.
- 질문 예산이 남으면 가장 중요한 stale 항목을 다시 묻는다.
- 최종 확인에서 변경되어 재질문할 수 없으면 결과에 `requires_followup`으로 기록한다.

### 3.5 스키마 통과와 전략 완성을 혼동할 수 있음

현재는 미완성 다발이 `confirmed`가 아니면 조건부 필수 필드 검사가 적용되지 않아, 전략적으로 미완성인 JSON도 스키마 자체는 통과할 수 있다.

해결 방향:

- `schema_valid`와 `strategy_ready`를 분리한다.
- 완료 상태를 최소 세 단계로 관리한다.

```text
interview_finished   사용자가 10문항 인터뷰를 마침
anchor_complete      핵심 전략 앵커가 모두 사용자 확인됨
strategy_ready       실행에 필요한 필드와 트랙 일관성이 충족됨
```

- 결과 화면에서 `스키마 통과`만 완료 메시지로 사용하지 않는다.

---

## 4. 권장 파일 구성

기존 파일을 건드리지 않고 다음 구조를 새로 만든다.

```text
자료수집_QnA_AI/
├─ interview_agent(v0)/
│  └─ interview_agent_target_first_standalone.py       # 기존 기준 파일, 수정 금지
├─ interview_agent_strategy_first_standalone.py        # 새 독립 실행 파일
├─ schema_strategy/
│  └─ 11_strategy_interview_ontology.schema.json       # 새 스키마
├─ tests_strategy/
│  ├─ test_strategy_state.py
│  ├─ test_question_router.py
│  ├─ test_answer_extraction.py
│  ├─ test_dependency_invalidation.py
│  ├─ test_completion.py
│  ├─ test_schema_output.py
│  └─ fixtures/
│     ├─ generic_material_company.json
│     ├─ generic_hardware_company.json
│     ├─ generic_creative_company.json
│     ├─ generic_agritech_company.json
│     └─ generic_food_ingredient_company.json
└─ runs/
   └─ strategy_first/
```

### 4.1 독립성 조건

새 실행 파일은 다음을 만족해야 한다.

- `import interview_agent` 금지
- `import interview_agent_target_first` 금지
- 실행에 필요한 공통 함수가 새 파일 안에 포함되어야 함
- `.env`와 스키마를 제외하면 다른 프로젝트 Python 파일이 없어도 import 가능해야 함
- 스키마 경로는 새 영문 폴더를 우선 사용하고, 개발 중 기존 경로 fallback을 둘 수 있음
- Windows와 macOS에서 모두 `Path(__file__).resolve().parent` 기준 상대경로 사용

---

## 5. 새 전략 온톨로지 설계

### 5.1 최상위 구조 권장안

```json
{
  "company": "기업명",
  "offer": {},
  "transaction_strategy": {},
  "strategy_tracks": [],
  "shared_proofs": [],
  "interview_state": {},
  "completion": {},
  "answer_records": [],
  "question_states": {}
}
```

### 5.2 offer

```text
chosen_solution             이번에 전면에 세울 제품·서비스·캠페인
transaction_unit            제품, 장비, 소프트웨어, 서비스, 라이선스, 프로젝트 등
customer_use_context        고객이 사용하는 상황
primary_customer_change     사용 후 가장 중요한 변화
core_feature                이를 가능하게 하는 핵심 기능
maturity_stage              샘플, PoC, 판매 가능, 양산 가능 등
```

### 5.3 transaction_strategy

```text
primary_goal                가장 먼저 추진할 거래 목표
goal_sequence               첫 목표 이후 전환 순서
current_stage               현재 실제 사업화 단계
current_bottleneck          다음 단계의 가장 큰 병목
realistic_first_transaction 현실적인 첫 거래 단위
success_criteria            첫 단계의 성공 판정 기준
```

### 5.4 strategy_tracks

최대 두 개까지만 허용한다.

```json
{
  "track_id": "A",
  "priority": 1,
  "label": "사람이 이해할 수 있는 트랙 이름",
  "market": {},
  "target": {},
  "recipient": {},
  "purchase_logic": {},
  "proof_strategy": {},
  "entry_strategy": {},
  "cta_strategy": {},
  "execution_constraints": {},
  "status": "unknown|assumed|confirmed|stale|deferred"
}
```

#### market

```text
country_or_region
rationale
market_readiness
regulation_or_localization
```

#### target

```text
organization_type
buying_situation
purchase_trigger
urgency_signal
exclusion_criteria
```

조직 유형만으로 타겟을 확정하면 안 된다. 최소한 `organization_type + buying_situation 또는 purchase_trigger` 조합이 있어야 실행 가능한 타겟으로 본다.

#### recipient

```text
first_reviewer
primary_beneficiary
technical_reviewer
budget_owner
internal_forward_to
```

10문항 모드에서는 `first_reviewer`를 핵심으로 확인하고, 나머지는 답변에 직접 포함되면 함께 저장한다.

#### purchase_logic

```text
pain_point
current_alternative
loss_or_risk
urgency_trigger
purchase_reason
key_benefit_priority
why_budget
```

Key Benefit은 다음 토큰을 내부 저장에 사용하되 사용자 질문에는 자연어로 표시한다.

```text
problem_solving
revenue_growth
cost_reduction
risk_reduction
regulatory_compliance
social_impact
```

사회적 기업이라고 `social_impact`를 자동 1순위로 두지 않는다.

#### proof_strategy

```text
primary_proof
proof_type
source
verification_status
measurement_condition
sample_or_demo_availability
disclosable_before_nda
missing_proof_plan
```

솔루션명, 비전, 사업 목적은 Proof로 인정하지 않는다.

#### entry_strategy

```text
primary_channel
alternative_channel
partner_role
partner_incentive
responsibility_split
```

#### cta_strategy

```text
primary_cta
conversion_flow
core_message
acceptance_criteria
next_step_requirements
```

#### execution_constraints

```text
regulation_certification
cost_impact
supply_scale_up
localization_support
disclosure_nda_policy
highest_risk
```

### 5.5 상태와 출처

모든 전략값은 값만 저장하지 말고 다음 메타데이터를 추적한다.

```text
origin                 user_stated | external_research | model_inferred
confidence             high | medium | low
source_answer_id
verification_status
stale_reason
```

기존 값이 수정되어도 answer record에서 이전 답변을 삭제하지 않는다.

---

## 6. 질문 라우터 설계

### 6.1 질문 예산

```text
MAX_TOTAL_QUESTIONS   = 10
MAX_CONTENT_QUESTIONS = 9
FINAL_REVIEW_QUESTIONS = 1
```

다만 기존처럼 B1~B9에 한 질문씩 고정 배정하지 않는다.

### 6.2 질문 후보의 우선순위 점수

각 질문 후보에 다음 점수를 계산한다.

```text
criticality       전략 전체에 미치는 영향
dependency_count  이 답변에 의존하는 후속 필드 수
uncertainty       현재 값의 불확실성
user_required     대표 확인이 반드시 필요한지
execution_risk    잘못되면 실제 실행이 어려운 정도
question_cost     질문 복잡도와 사용자 부담
```

권장 계산 개념:

```text
priority_score =
    criticality
  + dependency_count
  + uncertainty
  + user_required
  + execution_risk
  - question_cost
```

정확한 수치보다 순서가 중요하다.

### 6.3 기본 질문 단계

고정 문구가 아니라 다음 단계의 목적을 유지한다.

1. 전면 솔루션과 거래 단위
2. 첫 거래 목표와 현재 단계
3. 주력 시장 또는 시장 트랙
4. 우선 고객과 구매 촉발 상황
5. 첫 검토 부서와 예산·기술 검토 구조
6. 실제 구매 이유와 기존 대안
7. 가장 강한 Proof와 제공 가능 상태
8. 진입 채널 또는 A/B 트랙 선택
9. 첫 CTA와 후속 전환 흐름
10. 전체 전략 확인·수정

답변이나 리서치에서 여러 항목이 이미 명확하면 해당 단계는 건너뛰고 다음으로 중요한 실행 위험을 질문한다.

### 6.4 질문 묶음 규칙

허용되는 묶음:

- 솔루션 + 거래 단위
- 목표 + 현재 단계
- 고객 유형 + 구매 촉발 상황
- Proof + 현재 제공 가능 여부
- CTA + 바로 다음 한 단계

피해야 하는 묶음:

- 시장 + 고객 + 부서 + 구매 이유
- Proof + NDA + 규제 + 가격 + 양산
- 첫 CTA + 전체 계약 흐름 + 성공 기준

### 6.5 선택지 사용 규칙

- 사용자가 답변하기 어려운 enum 성격의 질문에는 3~5개의 자연어 예시를 제시한다.
- 리서치에서 확인되지 않은 후보를 사실처럼 제시하지 않는다.
- 예시는 정답 제한이 아니라 답변 방향 안내라고 표시한다.
- B4 조직 유형 예시는 산업에 맞게 API가 생성하되, API 실패 시 범용 예시를 사용한다.

### 6.6 재질문 판단

재질문은 다음 경우에만 한다.

- 답변이 질문 대상 개념과 의미상 무관함
- 두 후보 중 어느 것을 선택했는지 구분할 수 없음
- 필수 분기점인데 답변이 지나치게 포괄적임
- 상위 답변 변경으로 기존 하위 전략이 stale 상태가 됨

재질문하지 않는 경우:

- `POC`, `프랑스`, `ESG팀`처럼 짧지만 명확한 답변
- 사용자가 `모름`, `미정`, `없음`을 명시함
- 세부 필드는 부족하지만 핵심 질문 대상은 해결됨

### 6.7 질문 예산 부족 처리

9개 내용 질문을 모두 사용했는데 중요한 필드가 남으면:

- 억지로 값을 생성하지 않는다.
- 최종 요약에서 `후속 확인 필요`로 표시한다.
- `completion.followup_questions`에 최대 5개를 저장한다.
- `strategy_ready`를 false로 둔다.

---

## 7. 답변 추출·정규화 정책

### 7.1 API 우선 원칙

현재 적용된 정책을 유지한다.

1. API가 답변에서 구조화 값을 추출한다.
2. API 응답이 정상이고 `유효한 값 없음`이라고 판단하면 로컬 규칙을 실행하지 않는다.
3. API 호출 실패, 빈 응답, JSON 파싱 실패, 계약 위반, 원문 근거 검증 실패일 때만 로컬 fallback을 사용한다.
4. 로컬 fallback 값도 의미 검증을 통과해야 한다.

### 7.2 원문 근거 검증

- 모든 정규화 값은 `source_text`를 포함해야 한다.
- `source_text`는 대표 답변에 실제로 존재해야 한다.
- 사용자가 질문의 후보를 명시적으로 확인한 경우에만 질문 문구를 근거 범위에 포함한다.
- 모델이 답변에 없는 국가, 회사, 성과, 부서, 수치를 생성하면 폐기한다.

### 7.3 장문 답변 정제

- 장문 전체를 표시값으로 저장하지 않는다.
- 솔루션, 시장, 부서, Proof, 채널, CTA는 짧은 명사구나 enum으로 정규화한다.
- 구매 이유와 Pain Point는 의미를 유지하되 각각 독립된 짧은 문장 또는 명사구로 분리한다.
- 한 답변에 여러 트랙이 나오면 시간 순서와 우선순위를 보존한다.

### 7.4 명시적 미정 처리

```text
unknown_confirmed = 사용자가 현재 모른다고 명시
missing            = 질문하지 않았거나 추출하지 못함
failed             = 내부 기술 오류
```

세 상태를 절대 하나의 `미해결`로 합치지 않는다.

---

## 8. 의존성 및 수정 전파 설계

### 8.1 권장 의존성 그래프

```text
offer.chosen_solution
 ├─ transaction_strategy
 ├─ strategy_tracks[*].target
 ├─ strategy_tracks[*].purchase_logic
 └─ strategy_tracks[*].proof_strategy

strategy_tracks[*].market
 ├─ target
 ├─ recipient
 ├─ entry_strategy
 └─ execution_constraints

strategy_tracks[*].target
 ├─ recipient
 ├─ purchase_logic
 ├─ proof_strategy
 └─ cta_strategy

strategy_tracks[*].purchase_logic
 ├─ proof_strategy
 ├─ core_message
 └─ acceptance_criteria

strategy_tracks[*].entry_strategy
 └─ cta_strategy
```

### 8.2 상위 값 변경 시 처리

예: 최종 확인에서 `시장=프랑스`를 `시장=일본`으로 수정한 경우

1. 시장 값은 즉시 사용자 확정값으로 저장한다.
2. 기존 시장 답변은 answer record에 보존한다.
3. 해당 트랙의 recipient, channel, regulation, purchase logic을 stale 처리한다.
4. 질문 예산이 남아 있으면 가장 중요한 stale 항목을 다시 묻는다.
5. 질문 예산이 없으면 최종 결과에 다음을 기록한다.

```text
시장 변경으로 인해 검토 부서, 진입 채널, 규제 조건의 재확인이 필요합니다.
```

### 8.3 트랙 병합과 분리

- 사용자가 시장 두 개를 말해도 구매 논리와 채널이 같으면 하나의 트랙에 복수 시장을 둘 수 있다.
- 시장별 구매자·예산·채널이 다르면 별도 트랙으로 분리한다.
- 트랙은 최대 두 개까지만 사용자에게 보여준다.
- 세 번째 후보는 `future_candidates`로 저장한다.

---

## 9. 완료 판정

### 9.1 anchor_complete

최소 조건:

- 전면 솔루션
- 첫 거래 목표
- 주력 트랙의 시장
- 주력 트랙의 고객 조직 및 구매 상황
- 주력 트랙의 첫 검토자
- 주력 트랙의 구매 이유
- 주력 트랙의 Proof 또는 `현재 없음` 확인
- 주력 트랙의 진입 채널
- 주력 트랙의 첫 CTA

### 9.2 strategy_ready

추가 조건:

- 고객과 구매 이유가 서로 일치함
- Proof가 솔루션 자체와 구분됨
- 채널이 고객과 시장에 맞음
- CTA 이후 최소 한 단계가 존재함
- blocking conflict가 없음
- 가장 중요한 실행 제약이 확인되었거나 명시적으로 후속 확인 대상으로 남음

### 9.3 종료 메시지

종료 시 단순히 `완료`라고 출력하지 않는다.

예시:

```text
인터뷰가 종료되었습니다.
- 핵심 전략 앵커: 완료
- 실행 준비도: 부분 완료
- 후속 확인 필요: 규제 조건, 샘플 제공 시점
```

---

## 10. 최종 확인 질문과 출력

### 10.1 최종 확인 화면

단순 9개 필드 목록이 아니라 트랙 구조를 보여준다.

```text
[공통 제안]
- 전면 솔루션:
- 첫 거래 목표:

[주력 트랙 A]
- 시장:
- 우선 고객:
- 구매 촉발 상황:
- 첫 검토 부서:
- 구매 이유:
- 대표 Proof:
- 진입 채널:
- 첫 CTA:
- 후속 전환:

[비교 트랙 B]
- 시장/고객:
- A와 다른 구매 논리:
- 진입 채널:

[후속 확인 필요]
- ...
```

최종 답변 형식 예시:

```text
맞습니다
시장=일본
검토 부서=R&D팀
비교 트랙=제외
```

### 10.2 산출물

```text
runs/strategy_first/{company}_research.md
runs/strategy_first/{company}_strategy_filled.json
runs/strategy_first/{company}_strategy_normalized.json
runs/strategy_first/{company}_interview_transcript.md
runs/strategy_first/{company}_strategy_summary.md
```

`strategy_summary.md`에는 다음을 포함한다.

- 공통 제안
- 트랙 A/B 표
- Key Benefit 우선순위
- Proof 및 제공 가능 자료
- CTA와 전환 흐름
- 실행 제약
- 후속 확인 목록

콜드메일 문안 자체는 생성하지 않는다.

### 10.3 기존 형식 호환

필요하다면 새 전략 객체에서 기존 B1~B9 정규화 JSON을 만드는 별도 projection 함수를 둔다.

```text
project_strategy_to_legacy_bundles(strategy) -> legacy_instance
```

이 함수는 호환용이며 새 프로그램의 내부 상태를 기존의 평면 B1~B9 구조로 제한하면 안 된다.

---

## 11. 구현 단계

### 단계 1. 기준선 고정과 안전 복사

작업:

1. 현재 standalone 파일의 크기, 해시, import 여부를 기록한다.
2. 현재 파일을 수정하지 않는다.
3. 새 파일을 `interview_agent_strategy_first_standalone.py`로 생성한다.
4. 기존 리서치·API 클라이언트·콘솔 입력·파일 저장 기능을 새 파일에 포함한다.
5. 새 파일에서 기존 프로젝트 Python 파일 import가 없는지 AST로 검사한다.

완료 조건:

- 새 파일이 독립 import됨
- 실제 API를 호출하지 않고 import 가능함
- 기존 세 Python 파일의 해시가 변하지 않음

### 단계 2. 새 스키마와 상태 모델

작업:

1. `11_strategy_interview_ontology.schema.json` 생성
2. `offer`, `transaction_strategy`, `strategy_tracks`, `completion` 구현
3. 최대 두 트랙 제약 구현
4. 값 출처와 stale 상태 구현
5. 명시적 미정과 기술 실패를 분리

완료 조건:

- 빈 초기 상태가 스키마에 맞음
- 주력 트랙 하나만 있는 상태가 검증됨
- A/B 두 트랙 상태가 검증됨
- 잘못된 세 번째 활성 트랙은 검증 실패

### 단계 3. 질문 후보와 우선순위 엔진

작업:

1. 다발 순회 로직 제거
2. 필드별 중요도·의존성·실행 위험 정의
3. 질문 후보 생성 함수 구현
4. 질문 예산을 고려한 다음 질문 선택 구현
5. final review 한 문항 예약

권장 함수:

```python
build_question_candidates(state) -> list[QuestionCandidate]
score_question_candidate(candidate, state) -> float
select_next_question(state, budget) -> QuestionCandidate | None
```

완료 조건:

- 해결된 항목은 다시 질문하지 않음
- 핵심 분기점은 세부 항목보다 먼저 질문됨
- 불충분 답변의 재질문이 9개 다발 순회 뒤로 무조건 밀리지 않음

### 단계 4. 사람용 질문 생성

작업:

1. 내부 용어 번역표 정리
2. 질문별 최대 두 개 개념 제한
3. 솔루션·타겟·Proof 혼동 방지
4. B4를 조직 유형과 구매 상황의 조합으로 개선
5. CTA 질문에 자연어 예시 제공
6. API 질문 생성 실패 시 안전한 로컬 질문 사용

완료 조건:

- `_`가 포함된 스키마 키가 질문에 노출되지 않음
- 긴 사용자 답변이 다음 질문에 그대로 삽입되지 않음
- 다른 필드의 답변이 잘못 앵커로 재사용되지 않음

### 단계 5. 답변 추출기

작업:

1. 트랙 식별 추출
2. 필드별 구조화 계약 구현
3. 원문 근거 검증
4. API 정상 무값과 기술 실패 분리
5. 로컬 fallback 범위 제한
6. 한 답변에서 복수 관련 필드 추출 지원

완료 조건:

- `POC`, `프랑스`, `ESG팀`이 한 번에 해결됨
- 긴 문장에서 짧은 전략값만 저장됨
- 답변에 없는 값은 저장되지 않음
- API 정상 무값일 때 로컬 규칙이 임의 생성하지 않음

### 단계 6. 수정 전파와 stale 관리

작업:

1. 의존성 그래프 구현
2. 상위 값 변경 탐지
3. 영향 필드 stale 처리
4. 질문 예산 내 재확인
5. 예산 소진 시 후속 확인 목록 생성

완료 조건:

- 시장 수정 시 시장 의존 필드가 그대로 confirmed로 남지 않음
- 솔루션 수정 시 이전 Proof와 구매 이유가 자동 확정 상태를 유지하지 않음
- 변경 전 값은 answer record에 보존됨

### 단계 7. 완료 판정과 최종 확인

작업:

1. `interview_finished`, `anchor_complete`, `strategy_ready` 분리
2. 트랙별 일관성 검사
3. blocking conflict 검사
4. 최종 확인 질문 구현
5. 최종 수정 후 stale 재계산

완료 조건:

- 10번째 질문을 넘지 않음
- 미완성인데 `strategy_ready=true`가 되지 않음
- 스키마 통과와 전략 완료 메시지가 구분됨

### 단계 8. 산출물과 호환 projection

작업:

1. 새 JSON 두 종류 저장
2. 사람이 읽는 전략 요약 Markdown 저장
3. 인터뷰 로그 저장
4. 필요 시 legacy B1~B9 projection 구현

완료 조건:

- 모든 경로가 실행 파일 기준 상대경로
- Windows/macOS 파일명에 안전함
- JSON Schema 검증 결과와 전략 준비도 결과를 모두 출력

### 단계 9. 패키징

최종 공유 폴더:

```text
strategy_first_test/
├─ interview_agent_strategy_first_standalone.py
├─ .env                              # 별도 전달
├─ requirements.txt
└─ schema_strategy/
   └─ 11_strategy_interview_ontology.schema.json
```

`requirements.txt` 권장 항목:

```text
python-dotenv
openai
requests
google-genai
jsonschema
```

venv는 전달하지 않는다.

---

## 12. 테스트 계획

### 12.1 테스트 원칙

- 자동 테스트에서는 실제 API를 호출하지 않는다.
- `chat`, Google 리서치, 콘솔 입력을 mock 처리한다.
- 실제 기업 데이터를 fixture에 그대로 복사하지 않고 일반화된 사례를 사용한다.
- 테스트가 만든 `runs`와 `__pycache__`는 정리한다.

### 12.2 필수 단위 테스트

#### 상태 모델

- 빈 상태 생성
- 트랙 하나 생성
- 트랙 두 개 생성
- 세 번째 트랙 거부
- origin·answer_id 보존

#### 질문 라우터

- 전면 솔루션이 없으면 솔루션 질문 우선
- 솔루션은 있지만 시장이 없으면 시장 질문
- 시장과 고객이 충돌하면 일관성 질문 우선
- 불충분한 핵심 답변의 재질문이 후순위로 무한 밀리지 않음
- 내용 질문 9개와 최종 확인 1개를 초과하지 않음

#### 답변 추출

- 짧은 확정 답변 처리
- 장문 답변 정제
- 복수 시장의 우선순위 처리
- 조직 유형과 사용 목적 분리
- Proof와 솔루션 분리
- 첫 CTA와 후속 CTA 분리
- 명시적 미정 처리
- API 정상 무값 처리
- API 기술 실패 시 로컬 fallback 처리

#### 수정 전파

- 솔루션 변경 → 구매 논리·Proof stale
- 시장 변경 → 수신자·채널·규제 stale
- 고객 변경 → 구매 이유·Proof·CTA stale
- final review 변경 → follow-up 필요 항목 생성

#### 완료 판정

- 9개 앵커만 있으면 `anchor_complete=true`
- 전환 흐름이나 핵심 위험이 부족하면 `strategy_ready=false`
- blocking conflict가 있으면 완료 금지
- 명시적 미정은 missing과 구분

#### 스키마 및 산출물

- 단일 트랙 JSON 검증 통과
- A/B 트랙 JSON 검증 통과
- summary Markdown 생성
- legacy projection이 필요 필드 타입을 만족

### 12.3 일반화된 통합 시나리오

#### 소재 기업

- 적용 산업을 좁혀야 함
- 컨소시엄과 소재기업 A/B
- 샘플 및 물성 검증 필요
- LCA·규제·가격 위험

#### 하드웨어 부품 기업

- 완제품과 부품 공급 구분
- 제조사 PoC
- 장착·인증·원가·양산 위험
- 영상·데모 Proof

#### 크리에이티브·임팩트 기업

- 사회적 가치가 실제 구매 이유인지 검증
- 브랜드 직접 접근과 에이전시 접근 분리
- 사용자 정정 시 Key Benefit 재정렬

#### 농축산 기술 기업

- 국가별 상업 트랙과 공공 실증 트랙 분리
- 시장별 구매자와 CTA가 달라야 함
- 정량 수치 미보유 처리

#### 식품 원료 기업

- 완제품과 원료 구분
- 샘플→파일럿→CMO 전환
- 안전성·규제·NDA 확인

### 12.4 회귀 테스트

기존 Target-first 파일에서 이미 해결한 다음 문제를 새 버전이 다시 만들지 않아야 한다.

- `POC`라고 답했는데 미해결 처리
- B1의 문자열 필드가 배열로 저장되어 스키마 위반
- 리서치의 다른 솔루션이 B7 Proof 질문에 노출
- 장문 답변이 B4 질문에 그대로 삽입
- 스키마 키가 사용자 질문에 노출
- 이메일 중심 표현이 제안 중심 인터뷰에 등장
- 첫 CTA와 후속 행동을 혼동
- API 성공 응답을 로컬 규칙이 덮어씀

---

## 13. 최종 승인 기준

다음을 모두 만족해야 새 프로그램을 완료로 본다.

### 독립성

- [ ] 기존 Python 파일을 import하지 않는다.
- [ ] 새 실행 파일, 새 스키마, `.env`만으로 실행 가능하다.
- [ ] macOS와 Windows 상대경로가 동작한다.

### UX

- [ ] 사용자 질문은 최대 10개다.
- [ ] 최종 확인 질문이 포함된다.
- [ ] 질문 하나에 독립 개념 세 개 이상을 요구하지 않는다.
- [ ] 내부 키와 enum이 노출되지 않는다.

### 의미 정확도

- [ ] 짧지만 확실한 답변을 해결로 처리한다.
- [ ] 솔루션·고객·구매 이유·Proof를 서로 혼동하지 않는다.
- [ ] 답변에 없는 사실을 생성하지 않는다.
- [ ] 명시적 미정과 기술 실패를 구분한다.

### 전략 구조

- [ ] 주력 트랙 한 개를 완성할 수 있다.
- [ ] 필요 시 비교 트랙 한 개를 별도로 저장할 수 있다.
- [ ] 트랙별 시장·고객·구매 이유·채널·CTA 관계가 유지된다.
- [ ] 상위 답변 수정이 하위 전략에 전파된다.

### 완료와 결과

- [ ] `interview_finished`, `anchor_complete`, `strategy_ready`가 분리된다.
- [ ] 미확인 항목을 후속 질문 목록으로 남긴다.
- [ ] 새 JSON Schema 검증을 통과한다.
- [ ] 사람이 읽는 전략 요약 파일이 생성된다.

### 테스트

- [ ] 실제 API 없이 모든 자동 테스트가 통과한다.
- [ ] 다섯 개 일반화 통합 시나리오가 통과한다.
- [ ] 기존 회귀 문제들이 재발하지 않는다.

---

## 14. 새 Codex 대화에 그대로 전달할 시작 프롬프트

아래 내용을 새 대화에 복사해 사용한다.

```text
다음 계획서를 기준으로 기존 파일을 수정하지 않고 새로운 독립 실행 프로그램을 구축해줘.

계획서:
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\독립_전략인터뷰_프로그램_구축_계획.md

기준 파일:
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\interview_agent(v0)\interview_agent_target_first_standalone.py

현재 스키마:
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\10_interview_ontology.schema.json

보편 인터뷰 규칙 참고 자료:
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\anpoly_interview_process.md
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\cobotsystem_interview_process.md
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\kimustudio_interview_process.md
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\livecare_interview_process.md
C:\Users\wisda\OneDrive\문서\자료수집_QnA_AI\질문온톨로지\mushn_interview_process.md

먼저 다음만 수행해:
1. 계획서와 기준 파일 및 스키마를 전부 읽는다.
2. .env는 읽거나 출력하지 않는다.
3. 기존 파일을 수정하지 않는다.
4. 현재 코드에서 재사용할 부분과 교체할 부분을 함수 단위로 표로 정리한다.
5. 새 파일명과 새 스키마 구조를 최종 점검한다.
6. 구현 전에 단계별 작업 계획과 예상 위험을 나에게 보여주고 승인을 기다린다.

중요 조건:
- 새 파일은 interview_agent.py 또는 기존 target_first 파일을 import하지 않는 독립 실행판이어야 한다.
- 사용자 질문은 최종 확인을 포함하여 최대 10개다.
- 실제 API를 사용하는 테스트는 하지 않는다.
- 기존 파일과 기존 스키마는 보존한다.
- 데이터 확보·Google Grounding 재설계는 이번 범위에서 제외한다.
```

---

## 15. 새 작업에서 가장 먼저 결정할 한 가지

구현 전 반드시 다음 원칙을 확정한다.

> 최대 10문항은 유지하되, 10문항 안에 모든 세부 실행 정보를 억지로 채우지 않고 `핵심 전략 완성`과 `후속 확인 필요`를 구분한다.

이 원칙을 지키지 않으면 두 가지 문제가 다시 발생한다.

1. 모든 세부 필드를 한 질문에 몰아넣어 질문이 길고 이해하기 어려워진다.
2. 질문 수를 지키기 위해 확인하지 않은 값을 모델이 추정해서 전략이 완성된 것처럼 보이게 된다.

따라서 새 프로그램의 성공 기준은 “10문항으로 모든 것을 확정”이 아니라 다음이어야 한다.

> 10문항 안에 가장 중요한 실행 전략을 정확하게 확정하고, 남은 위험은 숨기지 않고 구조화된 후속 확인 항목으로 남긴다.
