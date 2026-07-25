# Strategy-first 독립 인터뷰 에이전트

기존 `interview_agent.py` 계열을 import하지 않는 별도 테스트 프로그램이다. v5는 온톨로지의 모든 빈칸을 묻지 않고 실제 진출 전략을 결정하는 10개 핵심 질문과 최종 확인을 사용한다. 보통 11문항으로 끝나며, 불충분한 답변 재확인이 생기면 늘어날 수 있다. 15문항부터 피로도를 안내하고 비정상적인 무한 반복만 40문항에서 차단한다.

Google Grounding 결과는 답이나 선택지를 대신 만들지 않는다. 문서 원문으로 역검증된 사실을 시장·기존 고객/협업·제안 작동 방식·실적 질문의 배경으로만 한 개씩 사용한다. 목표, 예산, 검토 부서, 진입 경로, 첫 행동과 후속 전환에는 검색 사실을 붙이지 않는다.

답변 검증은 하이브리드 방식이다. EXAONE이 질문별 의미 계약에 따라 답변 역할,
질문 적합성, 충분성, 이동할 필드와 확신도를 JSON으로 판정한다. 로컬 규칙은
`구매부서`처럼 오분류 가능성이 매우 낮은 단답과 API 기술 실패의 안전망으로만
사용하며, 구매 이유·차별점처럼 맥락 판단이 필요한 답을 임의 확정하지 않는다.
계약 위반이나 파싱 실패 시 구매 이유·차별점·Proof·후속 흐름 같은 자유 텍스트를
fallback으로 확정하지 않는다.

후속 질문은 해당 메인 질문 바로 다음에 한 개씩 진행한다. 핵심 미해결 항목이
남아 있으면 최종 확인으로 넘어가지 않으며, 최종 확인에는 전략 요약과 수정 안내만
표시하고 미해결 질문을 한꺼번에 포함하지 않는다.

## 구성

```text
interview_agent/
├─ interview_test.py
├─ interview_test_v3_full_ontology.py
├─ schema_strategy/
│  ├─ 11_strategy_interview_ontology.schema.json
│  └─ 11_strategy_interview_ontology_v3.schema.json
├─ tests_strategy/
├─ runs/strategy_first/
├─ requirements.txt
├─ .env.example
├─ BUILD_PLAN.md
└─ BASELINE_AUDIT.md
```

실행에는 YAML 파일이나 기존 `interview_agent.py`가 필요하지 않다.

## 대상 기업 변경

`interview_test.py` 상단의 다음 두 값을 변경한다.

```python
TARGET_COMPANY = "테스트할 기업명"
COMPANY_HINTS = "동명이인 구분 등에 필요한 선택적 검색 힌트"
```

## 환경변수

`.env.example`을 `.env`라는 이름으로 복사하고 실제 키를 입력한다.

```dotenv
FRIENDLI_TOKEN=flp_...
FRIENDLI_BASE_URL=https://api.friendli.ai/dedicated/v1
EXAONE_ENDPOINT_ID=...
GOOGLE_API_KEY=...
```

`.env`는 테스트 패키지나 Git 저장소에 포함하지 않는다.

## macOS 설치와 실행

프로젝트 폴더 안에서 새 가상환경을 만든다. 다른 컴퓨터에서 만든 `.venv` 폴더는 복사하지 않는다.

```bash
cd interview_agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python interview_test.py
```

## Windows PowerShell 설치와 실행

```powershell
Set-Location interview_agent
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python .\interview_test.py
```

## 테스트

테스트는 실제 Friendli 또는 Google API를 호출하지 않는다.

```bash
python -m unittest discover -s tests_strategy -v
```

현재 테스트 범위:

- 독립 import와 스키마 유효성
- 최대 두 개 전략 트랙
- 10개 핵심 의사결정 질문 순서
- 짧은 확답 처리
- 필드별 원문 근거 추출과 결정적 정규화
- 질문별 의미 역할·적합성·충분성 계약
- 오답 필드 이동, 부분 답변, 의미 불일치, 명시적 미정 처리
- 의미 판정 결과를 반영한 조건부 재질문
- API 의미 누락에 대한 고정형 단답 구조장치
- 계약 위반 시 자유 텍스트 저장 차단과 redirect 경로 격리
- 고객·CTA·진입 채널·다음 행동의 과도한 partial 판정 보정
- 미해결 개별 처리 후 순수 최종 확인
- 리서치 prefill enum 정규화와 증분 스키마 검증
- 기술 실패 fallback과 의미 구조장치 분리
- 솔루션·시장 수정의 stale 전파
- 인터뷰 종료·핵심 앵커·실행 준비도 분리
- 고정 온톨로지 후속 질문 미생성
- 검색 사실 원문 역검증, 질문별 허용 범위, 솔루션 관련성 검사
- 15문항 피로도 안내와 전체 40문항 안전 상한
- 자연어 최종 수정과 설명 요청 분리
- 한글 깨짐 차단과 추출 진단 로그
- 소재·하드웨어·크리에이티브·농축산·식품 원료 일반화 사례

## 출력 파일

실행 결과는 `runs/strategy_first`에 저장된다.

```text
{company}_research.md
{company}_research_facts.json
{company}_strategy_filled.json
{company}_strategy_normalized.json
{company}_interview_transcript.md
{company}_strategy_summary.md
{company}_legacy_projection.json
{company}_extraction_events.json
```

완료 상태는 다음을 구분한다.

- `session_closed`: 실행 루프가 종료됨
- `final_confirmed`: 대표가 최종 요약을 승인함
- `question_limit_reached`: 최종 승인 전 질문 상한에 도달함
- `clarification_pending`: 설명 또는 최종 재확인이 필요함
- `anchor_complete`: 주력 전략의 핵심 앵커가 확인됨
- `strategy_ready`: 트랙 일관성과 최소 실행 조건까지 확인됨

`새 전략 스키마 통과`는 JSON 구조가 유효하다는 의미이며, `strategy_ready`와 동일하지 않다.
