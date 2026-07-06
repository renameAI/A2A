# A2A B2B 매칭엔진

전달 패키지 v1.2(기획서·엔진 PRD·API 계약서·데이터스키마 명세)를 요구사항으로 구현한
**K-EXAONE 기반 전체 프로그램** — stateless 엔진(Represent/Retrieve/Judge/Compose/협상)
+ 제품 백엔드 + 웹 UI. 모든 LLM 작업은 비동기 job으로 돌며 **엔진의 사고 과정이
실시간 로그로 UI에 표시**된다.

## 로컬 실행 (3줄)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 의존성 (버전 고정)
cp .env.example .env   # FRIENDLI_TOKEN·FRIENDLI_ENDPOINT_ID 입력 (없으면 Mock 모드)
.venv/bin/uvicorn app.main:app --port 8423   # → http://localhost:8423 (웹 UI)
```

- 웹 UI: `http://localhost:8423/` · 엔진 API 문서: `/docs`
- 테스트: `.venv/bin/python -m pytest tests/ -q` (항상 Mock — API 비용 0)

### LLM 프로바이더 (`LLM_PROVIDER`) — 고정 선택, 모델 개입 없음

| 값 | 모델 | 용도 |
|---|---|---|
| `friendli` (기본) | K-EXAONE-236B (Friendli dedicated) | 소버린 트랙 |
| `local` | 로컬 OpenAI 호환 모델 (Ollama·llama.cpp) | **완전 오프라인** — 외부 API 없음 |
| `anthropic` | Claude | 대안 |
| `mock` | 규칙 파서 | LLM 없이 계약·흐름만 |

**오프라인/저사양 실행** — 인터넷 없이 로컬 모델로 돌리려면:
```bash
ollama run exaone3.5:7.8b        # 저사양 EXAONE (또는 qwen2.5, llama3.1 등)
# .env: LLM_PROVIDER=local  (LOCAL_LLM_MODEL/BASE_URL 기본값이 Ollama에 맞춰짐)
```
약한 모델도 견디도록 **프롬프트 강제 + 코드 정화 이중 방어**가 들어있다:
`deep` 경로의 추론↔구조화 2단계 분리, JSON 파싱 재시도, 그리고 아래 3가지 보정.

### 웹사이트 크롤링 (ING-01·08·09)

`website` 자산은 단일 페이지가 아니라 **멀티페이지 크롤**로 수집한다
([app/ingest/crawler.py](app/ingest/crawler.py)):

- **본문 추출**: trafilatura(2025 벤치마크 F1 0.945, 1위) 1차 → BeautifulSoup 휴리스틱 폴백
- **우선순위 링크 추적**: 같은 도메인의 회사소개·제품·서비스·팀·사례 페이지를 최대 5페이지
  (`CRAWL_MAX_PAGES`) — 회사의 상(像)에 기여하는 페이지만
- **robots.txt 준수** (ING-08): 차단 경로는 요청 자체를 보내지 않고 로그
- **24시간 디스크 캐시** (ING-09): `cache/` — 같은 URL 재수집 방지
- **JS SPA 감지**: CSR 빈 껍데기는 조용한 빈 프로필 대신 명확한 안내 에러
- 기사(`article`)는 단일 페이지 + trafilatura 추출

감사 로그(SYS-04): 모든 represent/judge/negotiate 출력이 `audit/YYYYMMDD.jsonl`에
저장된다 — HITL 검토·재학습용 (기획서 11장 데이터 자산).

### 출력 품질 보정 (약한 모델 대비)

| 이슈 | 방어 |
|---|---|
| 환각 (없는 지명·수치 생성) | `HARD_RULES` "사실 고정" + deep 2단계 + grounding 경고 |
| 회사명 오추출 (레퍼런스를 주체로) | `EXTRACT_SYSTEM` "주체 고정" 규칙 + 자료 대조 검증 로그 |
| 한자·깨진 글자 혼입 | `HARD_RULES` "순수 한국어" + `sanitize()` 코드 정화 |

## LLM 켜기 — K-EXAONE (소버린 트랙, 현재 연결됨)

```bash
cp .env.example .env    # FRIENDLI_TOKEN + FRIENDLI_ENDPOINT_ID 설정 (K-EXAONE, 최우선)
                        # 또는 ANTHROPIC_API_KEY (대안 어댑터)
                        # 인스타그램 수집은 APIFY_TOKEN 추가
```

- **K-EXAONE-236B** (Friendli dedicated, OpenAI 호환): controllable reasoning 특성에 맞춰
  **"깊게 추론(thinking ON) → 구조화(thinking OFF + json_schema)" 2단계** 패턴 사용.
  Represent·Judge는 deep 경로(품질 우선, 호출당 4~5분), Compose·합성은 단일 호출.
- 키가 없으면 Mock으로 동작 (`engine_mode: "mock"` 표기). 테스트는 항상 Mock(비용 0).
- 실측: 다이브인→리비 하노이 판단이 CoT #01 전문가 결론(조건부·PMS 선결·소규모 PoC)과 수렴 확인.

설계·요구사항: [docs/PHASE2_수집추출_설계.md](docs/PHASE2_수집추출_설계.md)
⚠ `.env`는 절대 커밋 금지 — 키 노출 시 즉시 재발급.

## 파이프라인 DAG 뷰 (DevOps 모니터링)

각 모듈(`/product/onboard`·`/match`·`/judge`·`/compose`·`/negotiate`)이 실행되는 동안
웹 UI가 **노드 그래프**로 진행 과정을 보여준다:

- **노드 상태**: 대기(회색 점선) → 실행 중(주황 펄스) → 완료(초록) / 실패(빨강, 예외 메시지 포함)
- **소요 시간**: 노드마다 `완료 · 0.3s` 식으로 표기, 실행 중이면 서버 경과시간 기준 실시간 갱신
- **노드 간 연결**: 완료된 경로는 실선, 진행 중인 경로는 애니메이션 점선, 이번 실행에서
  건너뛴 분기(예: LLM 키 없어 Mock 경로를 탄 경우)는 옅은 점선으로 표시
- **협상(negotiate)은 동적 DAG**: 라운드마다 노드가 새로 생기고, 그 라운드 내부의
  결격 게이트·판단·감사 단계가 자식 노드로 세로로 이어진다 (라운드 = 부모, 내부 단계 = 자식)
- **로그 필터**: 노드를 클릭하면 그 구간에서 찍힌 로그만 필터링해서 보여준다

구현: 백엔드 [progress.py](app/progress.py)의 `with progress.node(id, label):` 컨텍스트가
`node_start`/`node_end` 이벤트를 구조화 로그로 남기고, 프론트 [app.js](app/product/static/app.js)의
`renderPipeline()`이 이를 SVG DAG로 그린다. 로그 문자열 파싱이 아니라 정확한 수명주기 이벤트 기반이라
실패 지점이 항상 정확한 노드에 표시된다.

## 엔드포인트 (API_계약서 v1.0)

| 엔드포인트 | 방식 | 역할 |
|---|---|---|
| `POST /v1/represent` | 동기 | 자료 → 프로필+임베딩+온톨로지 앵커 (3형 출력) |
| `POST /v1/retrieve` | 동기 | 상대 합성 → 하이브리드 검색 (보완성, 유사도 아님) |
| `POST /v1/judge` | **비동기 202** | 후보 쌍 → 구조화 판단 (점수 아님) |
| `POST /v1/negotiate` | **비동기 202** | A2A 협상 왕복 (거절 분류→손잡이 묶음→3종 종료) |
| `GET /v1/jobs/{id}` | 폴링 | 비동기 결과 수신 |
| `POST /v1/compose` | 동기 | 아웃리치/추천요약 초안 (`send_blocked` 항상 true) |

에러 계약: `400 invalid_input` / `409 profile_below_minimum` / `422 no_strong_candidate` /
`423 deal_breaker` (비동기 job에서는 `status=error`로 수렴).

## 우리가 받아야 할 것 (사용자 입력 계약)

웹 UI(`/`) 좌측 체크리스트와 동일 — 엔진이 판단하기 위한 입력:

| 구분 | 항목 | 미충족 시 |
|---|---|---|
| **필수** | 기업 자료 ≥1 (IR덱 PDF·웹사이트·기사·인스타그램·텍스트) | 온보딩 불가 |
| **필수 (최소 프로필)** | 푸는 문제 · 솔루션 · 타겟 고객 · 가치 제안 ≥1 | `409` + 보강 질문 → 답변 후 재분석 (매칭 풀 제외, REP-06) |
| 권장 | 협력 의향 (판매/구매) | 판단이 "확인 필요"로 보수화 (JDG-08) |
| 권장 | 판매자 사전정보 (`키: 값` — 쉐어 최저선·전략 단계·통제 항목) | 협상 최저선 미보장(NEG-06), 전략적 역전 추론 불가 |
| 매칭 시 | 의도 — 타겟 지역·가치제안·제안 유형 | 합성 씨앗 부족으로 후보 품질 저하 |

## 개발 단계

- [x] **Phase 1 — 엔진 골격 + stateless API v0**
  - 스키마·4엔드포인트·비동기 job·에러 계약·규칙 기반(Mock) 추론·협상 루프·테스트 19건
- [x] **Phase 2a — 자료 수집·추출 (Represent 실화)**
  - IR덱 PDF 청킹·웹사이트/기사/인스타그램 수집·LLM 구조화 추출(provenance+근거 청크)
  - `.env`에 키만 넣으면 켜지고, 없으면 Mock degrade (ING-01~08)
- [x] **Phase 2b — 범용 프롬프트 + 전 함수 LLM 경로** (현재)
  - [engine/prompts.py](app/engine/prompts.py): 도메인 무관 범용 프롬프트 (판단 구조·리스크 3분류·
    추론 무브·두 렌즈 규칙 내장). Judge/Compose/Retrieve 합성 모두 키만 넣으면 실추론.
  - **회사의 상(像)**: Represent가 5층 다층 독해(표면→기능→경제→전략→양면)로
    `portrait` 7항목(정체성·수익구조·차별화·단계와 절실함·가진 것·결핍·리스크 신호)을
    역추론 → Judge가 "양측의 상 재구성"부터 판단 → Compose가 수신자 언어 번역에 사용.
    스타트업 독해 규칙(수사/사실 분리·부재 신호·트랙션의 언어·자료 유형 보정) 내장.
  - deal-breaker 하드 게이트는 LLM 경로에서도 항상 규칙으로 보장 (JDG-04)
- [x] **Phase 3(일부) — 프론트엔드 + 제품 백엔드** (현재)
  - `/` 웹 UI: "받아야 할 것" 체크리스트 + 자료입력→프로필→후보→판단→초안→협상 한 사이클
  - `/product/*` stateful 제품 레이어 (온보딩·매칭·판단·초안·협상 오케스트레이션, 인메모리)
  - 구매자 사전정보 시뮬레이션 가상 부여 + 정직 표기 (7-A.6)
- [x] **Consultant 모드 (CON-01~02)** — 진단 인터뷰 엔진
  - 실제 인터뷰 시뮬레이션 3건(식품소재·소재 딥테크·하드웨어 부품)에서 검증된
    방법론을 [prompts.py](app/engine/prompts.py) `CONSULT_SYSTEM`으로 형식화:
    한 번에 하나씩 좁히기 · 회사의 상에서 도출한 4~6지선다+힌트 · 업종별 질문 축 ·
    10슬롯(솔루션·pain point·세그먼트·시장·수신자·CTA·proof·제공물·리스크·후속) 확정 시
    종료 + 최종 아웃리치 가설 산출
  - `POST /product/consult` (비동기 job) · UI ②+ 섹션(선택지 칩·복수선택·자유입력·
    가설→의도 반영) · 인터뷰 전 과정 감사 로그 축적(대표 인터뷰 = CoT 데이터 자산)
- [ ] **Phase 4 — 데이터 파이프라인·학습·평가**: CoT JSONL 검증기·held-out 봉인·LoRA SFT (박사 협업)
- [ ] **Phase 5 — 운영화**: 상태 영속화(PostgreSQL/Redis)·감사 로그(SYS-04)·Next.js 데모 UX
- [ ] **Phase 3 — CoT 데이터 파이프라인**: JSONL 검증기·커버리지 매트릭스·held-out 봉인 (DAT-01~05)
- [ ] **Phase 4 — 학습·평가**: EXAONE LoRA SFT·베이스라인 비교 (EVL-01~05, 박사 협업)
- [ ] **Phase 5 — 통합·데모**: Next.js 프론트·감사 로그(SYS-04)·job 영속화(Redis)

## PRD P0 커버리지 (Phase 1 기준)

- **구현+테스트 완료**: REP-01/02/03/06, RET-01/02/03/04, JDG-01/02/03/04/06/07/08,
  NEG-01/02/03/04/05/06, CMP-01/02/04/05/06, SYS-01/02
- **구조만 준비 (Phase 2~4에서 실체화)**: REP-04(추상화 레벨 — LLM 필요),
  RET-05·JDG-05·EVL-*(held-out 데이터 필요), JDG-09(explore 비율), JDG-12(학습), DAT-*

## 정직한 한계 (v0)

- 추론은 **규칙 기반 Mock**이다 — bigram 유사도 + 키워드 온톨로지. 판단의 "지능"은
  Phase 2(LLM)·Phase 4(CoT 파인튜닝)에서 온다. v0가 증명하는 것은 **계약·구조·흐름**이다.
- 시드 풀 7개사는 CoT 샘플 #01~#04 케이스의 재현이며, 실제 외부 풀 충원은 범위 밖(별도 트랙).
- deal-breaker 리스트는 placeholder 2건 — **Jin(BD) 확정 필요** (`engine/dealbreakers.py`).
- job 스토어는 인메모리 — 서버 재시작 시 소실 (Phase 5에서 영속화).
