# A2A B2B 매칭엔진

전달 패키지 v1.2(기획서·엔진 PRD·API 계약서·데이터스키마 명세)를 요구사항으로 구현한
**K-EXAONE 기반 전체 프로그램** — stateless 엔진(Represent/Retrieve/Judge/Compose/협상)
+ 제품 백엔드 + 웹 UI. 모든 LLM 작업은 비동기 job으로 돌며 **엔진의 사고 과정이
실시간 로그로 UI에 표시**된다.

> 📐 **프롬프트 파이프라인의 수학적 형식화**: 출력 예측불가를 조건부 분산 이분산성으로 진단하고,
> 계약 검증기·게이트를 연산자로 형식화한 문서 → [docs/FORMALIZATION.md](docs/FORMALIZATION.md)

## 로컬 실행 (3줄)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 의존성 (버전 고정)
cp .env.example .env   # FRIENDLI_TOKEN·FRIENDLI_ENDPOINT_ID 입력 (필수 — 없으면 실패)
.venv/bin/uvicorn app.main:app --port 8423   # → http://localhost:8423 (웹 UI)
```

## 로컬 테스트 빠른 설정 (A2A/DAG + DB 저장소)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 필요 시만 키 입력
mkdir -p data uploads pages
.venv/bin/uvicorn app.main:app --port 8425 --reload   # .env에 실 키 필요
```

- UI 확인
  - 매칭: `http://localhost:8425/`
  - A2A 흐름: `http://localhost:8425/a2a.html`
  - SQLite 저장소: `http://localhost:8425/db.html`
- DB 저장소 점검
```bash
curl -s http://localhost:8425/product/db/inspect
```
- A2A 스트림 스모크 (`message/stream`, `/a2a.html` 실행 전/후 동일 응답)
```bash
cat >/tmp/a2a-smoke.json <<'JSON'
{
  "jsonrpc": "2.0",
  "id": "smoke",
  "method": "message/stream",
  "params": {
    "message": {
      "role": "user",
      "kind": "message",
      "messageId": "m",
      "parts": [
        {
          "kind": "data",
          "data": {
            "skill": "represent",
            "input": {
              "assets": [
                {
                  "type": "text",
                  "content": "이름: 다이브인그룹\n국가: 한국\n산업: hospitality\n설명: 노후 호텔 전환\n문제: 노후 객실 매출 정체\n솔루션: 저자본 예술 전환\n타겟: 중소 호텔 오너\n판매가치: 매출"
                }
              ]
            }
          }
        }
      ]
    }
  }
}
JSON

curl -N -X POST http://localhost:8425/a2a \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/a2a-smoke.json
```

- 웹 UI: `http://localhost:8423/` · 엔진 API 문서: `/docs`
- 테스트: `.venv/bin/python -m pytest tests/ -q` (mock 제거로 다수가 실 키를 요구한다 —
  아래 '테스트 상태' 참고)

### 외부 후보 pool 확장 (`.claude/launch.json`, 로컬 전용 — 커밋 안 됨)

기본 pool은 시드 7개뿐이라 대부분의 실제 매칭 요청에서 후보를 못 찾는다. 아래
환경변수를 켜면 외부 리서치 pool이 합쳐진다(둘 다 안 켜면 기존 시드만 — 테스트·
골든셋은 영향 없음). `.claude/launch.json`의 `runtimeArgs`에 이어 붙인다:

```bash
A2A_POOL_DIR=/절대/경로/judge_cases/company_pool \
A2A_E11_POOL_PATH=/절대/경로/dataset/represent_sft_raw.jsonl \
exec .venv/bin/uvicorn app.main:app --port ${PORT:-8423}
```

- `A2A_POOL_DIR` — 코스닥 201사 (`judge_cases/company_pool/*.json`, sector/product
  텍스트 기반 — problem_solved 등은 뭉뚱그려 추론)
- `A2A_E11_POOL_PATH` — E11 증류 코퍼스 932사 (`dataset/represent_sft_raw.jsonl`,
  실제 교사 추출 problem_solved/solution/target_customer — 더 구체적이라 같은
  회사명이 겹치면 이쪽이 우선한다)
- 이름이 겹치지 않으면 시드 7 + 201 + 932 = 총 1140개까지 합쳐진다
- **주의**: preview 도구로 서버를 이름 기반(`name: "a2a-engine"`)으로 띄우면 세션
  중 launch.json을 다시 안 읽고 첫 실행 커맨드를 캐시하는 경우가 있었다(실측 —
  env var를 나중에 추가했는데 pool이 계속 6개로 나왔던 원인). 값을 바꾼 뒤엔
  `lsof -t -i :8423 | xargs kill` 후 새 프로세스로 직접 띄워서 확인할 것.

### LLM 프로바이더 (`LLM_PROVIDER`) — 고정 선택, 모델 개입 없음

| 값 | 모델 | 용도 |
|---|---|---|
| `friendli` (기본) | K-EXAONE-236B (Friendli dedicated) | 소버린 트랙 |
| `local` | 로컬 OpenAI 호환 모델 (Ollama·llama.cpp) | **완전 오프라인** — 외부 API 없음 |
| `anthropic` | Claude | 대안 |

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
- 키가 없으면 **즉시 실패**한다 (config_error). 조용한 규칙 대체(mock)는 2026-07 제거 —
  가짜 결과가 진짜처럼 보이는 통로였다. 실 API로만 검증한다.
- 실측: 다이브인→리비 하노이 판단이 CoT #01 전문가 결론(조건부·PMS 선결·소규모 PoC)과 수렴 확인.

설계·요구사항: [docs/PHASE2_수집추출_설계.md](docs/PHASE2_수집추출_설계.md)
⚠ `.env`는 절대 커밋 금지 — 키 노출 시 즉시 재발급.

## 파이프라인 DAG 뷰 (DevOps 모니터링)

각 모듈(`/product/onboard`·`/match`·`/judge`·`/compose`·`/negotiate`)이 실행되는 동안
웹 UI가 **노드 그래프**로 진행 과정을 보여준다:

- **노드 상태**: 대기(회색 점선) → 실행 중(주황 펄스) → 완료(초록) / 실패(빨강, 예외 메시지 포함)
- **소요 시간**: 노드마다 `완료 · 0.3s` 식으로 표기, 실행 중이면 서버 경과시간 기준 실시간 갱신
- **노드 간 연결**: 완료된 경로는 실선, 진행 중인 경로는 애니메이션 점선, 이번 실행에서
  건너뛴 분기는 옅은 점선으로 표시
- **협상(negotiate)은 동적 DAG**: 라운드마다 노드가 새로 생기고, 그 라운드 내부의
  결격 게이트·판단·감사 단계가 자식 노드로 세로로 이어진다 (라운드 = 부모, 내부 단계 = 자식)
- **로그 필터**: 노드를 클릭하면 그 구간에서 찍힌 로그만 필터링해서 보여준다

구현: 백엔드 [progress.py](app/progress.py)의 `with progress.node(id, label):` 컨텍스트가
`node_start`/`node_end` 이벤트를 구조화 로그로 남기고, 프론트 [app.js](app/product/static/app.js)의
`renderPipeline()`이 이를 SVG DAG로 그린다. 로그 문자열 파싱이 아니라 정확한 수명주기 이벤트 기반이라
실패 지점이 항상 정확한 노드에 표시된다.

## AI 컨설턴트 인터뷰 (CON-01~02) 로컬에서 써보기

기존 `LLM_PROVIDER` 설정을 그대로 재사용한다 — 이 기능만을 위한 별도 키·환경변수는 없다.

```bash
# 스모크 테스트 (.env에 실 키 필요)
.venv/bin/uvicorn app.main:app --port 8425   # .env에 실 키 필요
# 웹 UI → ① 자료 입력 → 프로필 분석 → "②+ AI 컨설턴트 인터뷰" 섹션 → "인터뷰 시작"
```

- **실제 LLM 경로** (`friendli`/`local`/`anthropic`): 매 턴 회사의 상(像)에서 새로 도출한
  질문+4~6지선다(힌트 포함)를 생성. `.env`에 `FRIENDLI_TOKEN`+`FRIENDLI_ENDPOINT_ID`를 넣거나
  (상단 "오프라인/저사양 실행" 참고) 로컬 모델을 띄운 뒤 같은 UI 흐름으로 확인.
- **API 직접 호출**: `POST /product/consult {company_id, history:[{question,answer}]}` →
  비동기 job, `GET /product/jobs/{id}`로 폴링. 10슬롯이 다 차면 `done:true`+`hypothesis` 반환.
- **테스트**: `.venv/bin/python -m pytest tests/test_consultant.py -v`

## A2A 전송 계층 — JSON-RPC 2.0 + SSE 스트리밍

Google Agent2Agent 프로토콜을 정식 채택. 단일 엔드포인트 `POST /a2a`가 JSON-RPC 봉투를
받아 우리 엔진 스킬을 A2A Task로 감싼다 — 외부 에이전트가 표준 규약으로 이 엔진을 부른다.

- **Capability discovery**: `GET /.well-known/agent.json` — Agent Card(스킬·모달리티·
  streaming 능력). `preferredTransport: JSONRPC`, `additionalInterfaces`에 `/a2a` 광고.
- **메서드**: `message/send`(→Task), `message/stream`(→SSE), `tasks/get`(폴링),
  `tasks/cancel`(협조적 취소).
- **에러코드**: JSON-RPC 표준(-32700 파싱 / -32600 잘못된 요청 / -32601 메서드없음 /
  -32602 잘못된 파라미터) + A2A 확장(-32001 TaskNotFound / -32002 TaskNotCancelable).
- **Task lifecycle**: job 상태를 A2A TaskState로 매핑(`submitted/working/completed/failed`).
  **최소 프로필 미달·미응답 질문 핀 = A2A `input-required`** — "작업은 끝났지만 사람
  입력 전까지 다음 단계 불가"를 표준 상태로 표현(강제 응답과 동일 개념).
- **SSE 이벤트**(`message/stream`): 최초 `task` 스냅샷 → 진행 `status-update`들(엔진
  노드 실시간) → `artifact-update`(결과) → 최종 `status-update`(`final:true`). 각 SSE
  data는 완전한 JSON-RPC 응답이다.

```bash
# 스킬을 A2A Task로 호출 (message는 DataPart에 skill+input을 싣는다)
curl -X POST localhost:8423/a2a -H 'Content-Type: application/json' -d '{
  "jsonrpc":"2.0","id":"1","method":"message/send",
  "params":{"message":{"role":"user","kind":"message","messageId":"m","parts":[
    {"kind":"data","data":{"skill":"represent","input":{"assets":[
      {"type":"text","content":"이름: ...\n문제: ...\n솔루션: ..."}]}}}]}}}'
# → {"result":{"kind":"task","id":"...","status":{"state":"working"}}}
# 스트리밍: 같은 body에 "method":"message/stream" → text/event-stream
```

`app/a2a.py` 단일 모듈, `TestClient` 스트리밍으로 lifecycle 테스트(`tests/test_a2a.py`).
취소 한계: 엔진 스킬은 계산 중간 중단 지점이 없어 `tasks/cancel`은 협조적(완료돼도 결과 폐기).

## 웹 파트너 스카우트 — 명백지/암묵지 → explore/exploit 가설 → 웹 검색

Retrieve가 '이미 아는 풀' 안에서 찾는다면, Scout는 **풀 밖(웹)**에서 후보를 충원한다
(기획서 6.4 외부 풀 충원 트랙의 v0 · JDG-09 탐색 예산을 충원 단계에 적용).

1. **지식 분리** ([engine/scout.py](app/engine/scout.py) `split_knowledge`, 결정적):
   명백지 = provenance `stated`·레퍼런스·자가신고 / 암묵지 = `inferred`(확신도 동반)·회사의 상 7항목(정의상 역추론).
2. **가설 생성**: exploit(정석) 가설은 **명백지에만** 근거, explore(모험) 가설은 **암묵지 최소 1개** 근거 —
   프롬프트 계약([prompts.py](app/engine/prompts.py) `SCOUT_SYSTEM`)을 코드가 집행(위반 가설 폐기+집계).
3. **웹 검색** ([ingest/websearch.py](app/ingest/websearch.py)): 키 없는 DuckDuckGo HTML(크롤러와 같은
   공개 웹 범주, 24h 캐시). 상용 검색 API(Tavily·Serper)는 AXR 협의 후 이 모듈만 교체.
4. **숏리스트**: 도메인 dedup + 노이즈 도메인 필터 + explore 쿼터 배분(`explore_ratio`, JDG-09 파라미터) +
   결정적 정렬. 검색 전멸 시 `web_search_used:false`로 정직 표기(가설은 유효).

`POST /product/scout {company_id, intent, k, explore_ratio}` · 엔진 API `POST /v1/scout` · A2A 스킬 `scout`.
정직한 한계: mock 경로는 전부 명백지라 explore 가설이 원천 불가(계약의 정직한 동작) — 모험 가설은
실 LLM 경로(회사의 상)에서 나온다. v0 숏리스트엔 정보성 글이 섞일 수 있다(가설 검색어 품질은 LLM 경로가 우위).

## 근거 시각화 (bbox) — IR덱 원문 위 AI가 본 위치 + 댓글 강제

Simsa(cts_screening 검토 SaaS)의 box_2d 패턴을 재사용한 선택 기능. IR덱 PDF 페이지 이미지
위에 AI가 프로필을 뽑아낼 때 실제로 본 위치를 빨간 박스로 표시하고, AI가 스스로 확신하지
못한 부분은 점선 박스 + 사람에게 되묻는 댓글 스레드로 남는다 — **답하기 전엔 매칭(`/product/match`)
으로 못 넘어간다** (강제 응답, 409 `unclear_evidence_unresolved`).

```bash
cp .env.example .env    # GEMINI_API_KEY 입력 (텍스트 추출 LLM_PROVIDER와 완전히 독립)
.venv/bin/uvicorn app.main:app --port 8423
# 웹 UI → ① IR덱 PDF 자산으로 온보딩 → "②++ 근거 시각화" 섹션 자동 표시
```

- 키가 없으면 기능 자체가 조용히 꺼진다 (다른 온보딩 흐름엔 영향 없음) — `GEMINI_API_KEY`만
  넣으면 켜진다.
- 텍스트 추출(K-EXAONE 등)과 **완전히 독립된 비전(vision) 경로**다 — [engine/vision.py](app/engine/vision.py)
  `GeminiBBoxExtractor`가 PDF 페이지를 PNG로 렌더링([ingest/pdf_render.py](app/ingest/pdf_render.py),
  PyMuPDF)한 뒤 Gemini에 넘겨 `box_2d`([ymin,xmin,ymax,xmax], 0~1000 정규화) + 근거 인용문 +
  `unclear`(불확실) 플래그를 직접 받는다.
- 프롬프트는 [engine/prompts.py](app/engine/prompts.py) `BBOX_SYSTEM`에 중앙화 — 이 페이지에
  실제로 없는 내용은 절대 채우지 말라는 사실 고정 규칙 + "애매하면 무조건 unclear로 표시" 규칙.
- **댓글 스레드**: `unclear=true` 근거마다 AI가 첫 댓글로 질문을 남기고 스레드가 `open`으로
  생성된다. `POST /product/companies/{id}/threads/{id}/reply`로 사람이 답하면 `resolved`로
  닫히고, 그제서야 매칭이 풀린다 (시트 댓글처럼 스레드가 쌓이는 구조 — `app/schemas.py`
  `CommentThread`/`ThreadComment`).
- **테스트**: `.venv/bin/python -m pytest tests/test_vision.py -v` (5건, Fake 비전 추출기로
  오프라인 — 실제 Gemini 호출 없이 강제 응답 게이트·좌표 보존·페이지 이미지 서빙 전부 검증)

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
  - **프롬프트 중앙화**: 모든 LLM 시스템 프롬프트·스키마·user 빌더는 오직
    [engine/prompts.py](app/engine/prompts.py) 한 곳에만 존재한다 — 다른 모듈(represent·judge·
    compose·retrieve·consultant·2단계 구조화기)은 여기서 import만 하고 프롬프트를 직접
    작성하지 않는다. 프롬프트를 고칠 곳이 항상 한 파일이라는 뜻.
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
- [x] **근거 시각화 (bbox)** — Simsa 검토 SaaS 패턴 재사용, Gemini vision 선택 기능
  - IR덱 페이지 이미지 위 근거 위치를 빨간 박스로 표시([engine/vision.py](app/engine/vision.py),
    [ingest/pdf_render.py](app/ingest/pdf_render.py)) · 불확실 근거는 점선 + 댓글 스레드로
    사람에게 되묻고, 답하기 전엔 매칭 진행 불가(강제 응답, `app/schemas.py` `CommentThread`)
  - `GEMINI_API_KEY`만 있으면 켜짐 — 텍스트 추출(`LLM_PROVIDER`)과 완전히 독립
- [x] **Phase 4 — CoT 데이터 파이프라인** (DAT-01~05): [app/dataset.py](app/dataset.py)
  - 감사 로그(audit/*.jsonl) 위에 검증·커버리지·분할·봉인. 순수 데이터 엔지니어링(LLM·네트워크 없음)
  - `validate_records`(kind별 필수 필드, 불량 라인 격리) · `coverage_matrix`(kind×차원 분포로
    데이터 공백 진단) · `split_held_out`(주체명 sha256 해시로 **결정적** 분할 — 같은 회사 궤적은
    한쪽에만, 누수 방지) · `seal`/`verify_seal`(held-out 지문 + train 누수 검사)
  - `python -m app.dataset --audit-dir audit --out-dir dataset` → train/heldout/seal/report 생성
- [~] **Phase 5 — 학습·평가** (평가 절반 완료): [app/eval/benchmark.py](app/eval/benchmark.py)
  - **평가 벤치마크 하네스**: 골든셋([app/eval/golden_cases.json](app/eval/golden_cases.json))으로 엔진
    정확도·안정성 채점. judge는 극성(engage/defer/reject) 일치, retrieve는 top-1·distractor 제외.
    LLM 경로는 k회 실행해 정확도+재현성(L0 재사용) 동시 보고. Mock↔LLM 베이스라인 비교 = 규칙 대비 실추론 이득.
    L2 파인튜닝 모델이 붙으면 같은 하네스에 provider만 바꿔 base↔tuned 비교로 확장.
    `python scripts/run_benchmark.py [--only judge|retrieve] [--k N]`.
    정직성: 골든셋은 외부 검증 라벨이 아니라 시드 풀의 '의도된 역할'을 인코딩한 소량 v0(그 한계를 `_meta`에 명시).
  - 남음(학습 절반): EXAONE LoRA SFT — CoT 데이터셋 → SFT 포맷 내보내기 + GPU 학습(박사 협업/외부 환경 필요)
- [~] **Phase 6 — 운영화** (영속화 완료): 회사·job·A2A Task 영속화 · 감사 로그(SYS-04) · Next.js 미착수
  - **회사 상태 영속화**: `ProductStore`가 SQLite 백엔드([app/product/store.py](app/product/store.py)) —
    회사·질문 핀·댓글 스레드·소통 루프(answered_questions)가 **서버 재시작을 생존**한다.
    무인프라(파이썬 stdlib `sqlite3`, docker 불필요), 중첩 pydantic은 JSON 블롭으로 저장.
  - **job·A2A Task 영속화** ([app/jobs.py](app/jobs.py), [app/a2a.py](app/a2a.py)): 같은 패턴·같은 DB.
    A2A는 Task를 핵심 추상으로 세웠는데(`tasks/get`·`tasks/cancel`) 인메모리면 재시작 후 완료
    Task도 `-32001`이 되던 **프로토콜 구멍**을 막았다. 이제 완료 Task·산출물·A2A 메타(skill·
    contextId·history)·취소 마킹·`client_request_id` 멱등이 전부 재시작을 생존한다.
    **좀비 수확**(정직성): 재시작 시 `running`이던 job은 스레드가 죽었으므로 `error`로 수확 —
    '영원한 running'(A2A SSE 무한 루프의 원인)을 막는다. 실행 중 로그는 메모리(폴링), DB 쓰기는
    상태 전이 시점만(쓰기 폭주 방지).
    실증: 서버 프로세스 kill → 새 프로세스에서 같은 `task_id`로 `completed`·산출물·결과 복원 확인.
  - 리포지토리 경계가 깔끔해 나중에 PostgreSQL로 무손실 이관 가능(`_connect`만 교체).
    DB 경로는 `A2A_DB_PATH`(기본 `data/a2a.db`).
  - 남음: PostgreSQL/Redis 실이관(기획서 명시, 트래픽 생길 때), Next.js 데모 UX

## PRD P0 커버리지 (Phase 1 기준)

- **구현+테스트 완료**: REP-01/02/03/06, RET-01/02/03/04, JDG-01/02/03/04/06/07/08,
  NEG-01/02/03/04/05/06, CMP-01/02/04/05/06, SYS-01/02
- **구조만 준비 (Phase 2~4에서 실체화)**: REP-04(추상화 레벨 — LLM 필요),
  RET-05·JDG-05·EVL-*(held-out 데이터 필요), JDG-09(explore 비율), JDG-12(학습), DAT-*

## 실측 성능 — 실 LLM 캘리브레이션 (K-EXAONE)

> 분산축소 레버(L0~L3·R1~R4)를 실 K-EXAONE 출력으로 계측·검증한 값. 형식화 문서
> [docs/FORMALIZATION.md](docs/FORMALIZATION.md)의 실행 로드맵을 데이터로 뒷받침한다.
> 재현: [`scripts/calibrate_retrieve.py`](scripts/calibrate_retrieve.py) (synth 덤프 → 오프라인 재분석).

**Retrieve — 앵커 혼합(R4)의 분산 감쇠** (실 synth 8회, 다이브인 프로필)

| 후보 | synth 단독 점수 범위 | 앵커 혼합 점수 범위 | 분산 감쇠 | τ=0.12 통과 |
|---|---|---|---|---|
| ext-livi-hanoi | 0.350–0.407 | 0.400–0.429 | 0.50× | 8/8 |
| ext-bangkok-mid | 0.047–0.263 | 0.176–0.234 | **0.27×** | 7/8 → **8/8** |
| ext-casablanca | 0.050–0.283 | 0.208–0.275 | **0.29×** | 7/8 → **8/8** |

- **환각 방어 실증**: run 8에서 K-EXAONE가 무관 synth("군사 예산 삭감 호텔")를 냈을 때
  synth 단독은 bangkok 0.047·casa 0.050으로 **τ 아래 유실** 위기 → 앵커 혼합이 0.176·0.208로 **구제**.
- **τ=0.12 검증**: 매칭(≥0.176) vs 노이즈(≤0.070) 청정갭 정중앙. QC 4인 교차검증 96관측에서 하향 τ교차 **0/96**.
- **희소 프로필**은 코인플립(livi τ통과 1/8) — τ로 못 고치는 입력 품질 문제라 **저신뢰 기권 플래그**로 정직 표기.

**분산축소 레버 (결정적 효과, 측정값)**

| 레버 | 측정 지표 | 예전 → 지금 |
|---|---|---|
| L1 질문 공리 | 보강 질문 수 | 6 → 3 (이미 결정·중복 필드 폐기) |
| L2+L3 판단 | 저합의 자동추천 | recommend(미계측) → hold + 사람 라우팅 (일치율 0.40) |
| R1 그라운딩 | 환각 stated | stated 통과 → inferred 강등 + 질문 재개방 |
| R4 tie-break | 동점 순위 | 풀 순서 의존 → 항상 동일 |

**평가 벤치마크** (골든셋 v0 · 7 케이스 · [app/eval/benchmark.py](app/eval/benchmark.py))

| 조건 | retrieve top-1 | 비고 |
|---|---|---|
| 재랭킹 없음 (휴리스틱) | **1.000** | 의도(지역·경쟁사)가 순서에 새겨져 있음 |
| listwise 무조건 발동 | 0.400 | 의도를 유사도가 덮어씀 → **조건부 발동으로 수정** |
| E9 학습 스코어러 | 0.400 → **0.800** | 의도 티어 도입 후 |

- retrieve 케이스는 5건(지역 steering 2·방향 전환·순수 보완성·CoT anchor)으로 확충.
- **Mock 베이스라인 행은 삭제했다** — mock 경로를 제거했으므로 더 이상 측정 대상이 아니다.
- judge 케이스는 5건이나 **정답 `decision`이 붙은 것은 1건**뿐 — 확충이 병목(위 '정직한 한계').

**엔진 안정성**: 결정적 테스트(스코어러·계약·분산·적대검증 4파일) **85건 통과**,
judge 자기일관성·적대검증 **43건 통과**. 전체 스위트는 실 LLM 의존으로 39분·73실패 —
게이트로 쓸 수 없는 상태라 분리·결정화가 필요하다(위 '정직한 한계').

## 기획서와 달라진 것 — 실측이 설계를 바꾼 지점들

전달 패키지 v1.2를 요구사항으로 시작했지만, **실제로 돌려보며 계측한 결과가 설계를
바꾼 곳**이 여럿이다. 각 항목은 "왜 바꿨나(실측 근거) → 무엇으로 바꿨나" 순으로 적는다.
바뀐 이유가 취향이 아니라 데이터라는 게 이 절의 요점이다.

### 1. Mock 경로 제거 — "키 없으면 degrade"를 버렸다

**기획**: 키가 없으면 규칙 기반 Mock으로 degrade해 항상 동작.
**실측**: 그 경로가 *가짜 결과가 진짜처럼 보이는 통로*였다. 두 번 물렸다 —
`LLM_PROVIDER=mock`인데 `.env` 자동 로드로 스코어러는 실 API를 호출하고 있었고(비용·비결정),
골든셋을 Mock으로 재다가 결정적 앵커 경로만 검증하고 실제 경로를 놓칠 뻔했다.
**지금**: 키가 없으면 **즉시 `config_error`로 실패**한다. `get_extractor`는 `None`을
반환하지 않는다. 조용한 대체가 없다.

### 2. 판단 온톨로지 교체 — 자체 7차원 → BB1~BB10

**기획**: 자체 설계한 7차원(industry_fit·purpose_alignment·…).
**실측**: 그 축 집합에는 **실행·선결 게이트(BB6)와 신뢰·착취(BB8)가 없어서**,
"인증을 못 넘어 죽는 딜"이나 "상대가 우리 IP를 빼가려 한다"를 **표현할 언어 자체가
없었다**. 실 B2B에서 결정적인 두 가지인데도.
**지금**: 박사님(neurometry) `judge_cases/buyer_ontology.yaml`의 10축으로 교체.
축마다 `verdict_rule`이 명시돼 있어 판정선이 프롬프트 재량이 아니다.

동반 변경 셋:
- **`status`와 `verdict` 분리** — 예전엔 verdict만 있어 *"확인했는데 위험하다"*와
  *"확인을 못 했다"*가 둘 다 `caution`으로 뭉갰다. `AxisStatus(unknown/assumed/confirmed)`
  를 두면서 `"정보 부재 ≠ unfit"`이라는 우리 원칙이 비로소 **표현 가능**해졌다.
- **`terminate` 2분할** — `terminate_structural`(조건 바뀌면 다시 볼 상대) /
  `terminate_values`(다시 접촉 안 함). 후속 행동이 완전히 다른데 한 라벨이었다.
- **`purpose_by_buyer_type` 배선** — 대기업 CSR팀(`csr_org`→ESG KPI)과 지자체
  (`nonprofit_public`→정책 실적)와 유통사(`reseller_compounder`)는 **사는 이유가 다른데**
  같은 잣대로 목적 정합을 재고 있었다.

### 3. 결정은 코드가 내린다 — "축 판정=모델, 결정=코드"

**실측**: K-EXAONE은 축은 충실히 채우지만 **최종 결정 라벨이 `conditional`로 쏠린다**
(박사님 9세션 중 5/6). 프롬프트로 결정을 시키면 모델을 바꿀 때마다 흔들린다.
**지금**: `judge_cases/decision_gate.py`를 `_apply_decision_gate`로 이식 — 축 상태에서
코드가 결정을 유도한다. 박사님 원본 9세션 재현 검증 **8/9**(불일치 케이스까지 동일).
단 그 9건은 캘리브레이션 셋 자체라 in-sample 수치임에 유의.

**탐색 국면 분기**도 여기 붙었다. 웹 스카우트 후보는 프로필이 (이름·국가·한 줄 요약)
뿐이라 10축 중 8~10개가 필연적으로 `unknown`이고, 그러면 *"미검증≥3 → hold"* 규칙에
걸려 **모든 후보가 100% hold**로 수렴한다(실측). 증거가 없는 게 정상인 국면에서
증거 부족을 감점으로 쓰면 안 된다 — `objective=exploration_budget`이면 그 규칙을 끄고
증거가 얇아도 판정 가능한 것(명백한 결격·착취, 가설 부합)으로만 가른다.

### 4. represent 규율을 프롬프트 산문에서 온톨로지 파일로

**실측**: 규율이 프롬프트 산문에 흩어져 있던 동안 **하루에 5번 재작성했고 매번 한쪽이
무너졌다**. 공감만세(고향사랑기부 GovTech) 한 케이스로 전부 재현된다:

| 시도 | 타 업종 오염 | 구체성 | 자기참조 |
|---|---|---|---|
| 타 업종 예시 테이블 | ❌ 원문에 없는 `노후 시설` 환각 | 유실 | ✅ |
| "자료에 없는 낱말 금지" | ✅ | ❌ `"복잡성·비효율"` 공허한 일반론 | ✅ |
| 규율 느슨하게 | ✅ | ✅ | ❌ 재발 |
| **온톨로지 구조** | ✅ | ✅ **구체 사실 8개** | ✅ |

**지금**: [`app/ontology/represent_ontology.yaml`](app/ontology/represent_ontology.yaml)이
정규 소스다. 박사님 `buyer_ontology.yaml`과 **같은 형식**이고, `bases`가 판단 축 대신
추출 필드인 것만 다르다. 핵심은 **프롬프트와 코드가 같은 파일을 읽는다**는 것 —
`extract_block()`이 프롬프트 블록을 렌더링하고, `field_rule()`이 코드 게이트(R5)에
금칙 목록을 준다. 예전엔 프롬프트 지시와 코드 하드코딩이 각자 관리돼 조용히 벌어졌다.

**R5 자기참조 게이트**: 회사명은 **유저가 온보딩에서 직접 입력하는 값**이라 우리가
이미 아는데, LLM에게 "쓰지 마세요"라고 부탁만 하고 있었다. 이제 `ground_profile`이
R1·R3 옆에서 직접 검사한다(위반 시 폐기가 아니라 `provenance` 강등 — 값은 남기고
라벨을 정직하게).

### 5. 재랭커가 의도를 덮어쓰던 문제

**실측**: E9(학습 1.2B, held-out ρ=0.789)와 listwise(236B API)가 **서로 독립인데
골든셋의 같은 케이스에서 똑같이 실패**했다 — 지역 steering 붕괴, top1 0.400(재랭킹을
끄면 1.000). 두 모델이 같은 자리에서 무너지면 모델 품질이 아니라 구조 문제다.
원인: 둘 다 `(쿼리, 후보)` 텍스트만 받고 `intent.target_region`도 경쟁사 여부도 못 본다.
**지금**: `_intent_tier`(경쟁사 여부, 지역 일치)를 정렬 키 **최상위**에 둔다. 재랭커는
같은 티어 안에서만 순서를 매긴다 — E9 신호는 쓰되 의도는 못 지운다. E9 top1 0.400→0.800.

### 6. 학습 트랙 — 무엇을 했고 왜 멈췄나

- **E9 스코어러**(1.2B special-token, FFN LoRA): GPU 서버 복구 후 **런 12개 전부 구출**
  (safetensors 헤더 파싱으로 텐서 수·파라미터·오프셋 검증 — 크기만 보고 "구출됨"이라
  하지 않았고, 그 덕에 32B attn 가중치 누락을 잡았다). 현재 로컬 서빙 중(117~194ms).
- **E11 represent 증류**: 학습 완료(스키마 유효율 98%, 환각-stated 5%).
  **`교사 합치 0.399`는 낮은 게 아니었다** — 천장을 재보니 **교사 자기합치가 0.356**
  이었다(학생이 교사의 자기재현보다 잘 맞춘다). `_teacher_extract_quote`가 문서엔
  "결정적"이라 적혀 있는데 40번 중 1번만 재현했고, 원인은 `temperature=0.2`와
  **추출값의 20%가 빈 값**인 것이었다. 상세: [training/교사_천장_측정.md](training/교사_천장_측정.md)
- **judge 증류는 착수하지 않았다.** 교사가 지금 10축 중 4축을 빠뜨리고
  `dealbreaker` 불리언을 서술과 다르게 채우는 상태라, 그대로 라벨을 뽑으면 **결함이
  학생에 영구 각인된다**. 그리고 사람이 검증한 judge 정답이 **1건**뿐이라 증류해도
  "교사를 얼마나 닮았나"밖에 측정할 수 없다(E9의 ρ=0.789가 정확도가 아니었던 것과
  같은 함정). 교사 결함 수정 + 골든셋 확충이 선행 조건이다.

### 7. 계측 원칙 — 대리 지표에 세 번 속고 세운 규칙

같은 실수를 하루에 세 번 했다: **대리 지표를 최적화하고 라벨 지표에서 확인하니
나빠져 있었다.**

| 시도 | 대리 지표 | 라벨 지표(골든셋) | 판정 |
|---|---|---|---|
| judge k-표본 다수결 | 분산 12배↓ | top1 안정성 80%→50% | 기각 |
| synth 길이 제약 | 부수 효과 달성 | Jaccard 0.351→0.306 | 롤백 |
| overlap에 IDF 가중 | 상위5 분산 0.200→0.433 | top1 1.000→**0.533** | 기각 |

그래서 지금은 **가설·성공 기준을 먼저 문장으로 적고 나서** 실험한다. 기각된 가설도
코드 주석에 근거와 함께 남긴다(`retrieve.py`의 `_score` 주석이 그 예).

---

## 정직한 한계

- **골든셋이 작다.** retrieve 5케이스 / judge 5케이스이고, judge 케이스 중 **정답
  `decision`이 붙은 것은 1건**뿐이다. E9를 서빙에 쓸지, 재랭커 권한을 어디까지 줄지를
  이 규모로 판단하고 있다. 확충이 현재 최대 병목.
- **박사님 세션 9건은 전부 `provenance: simulated`, `outcome_anchor: false`** —
  LLM 시뮬레이션이지 사람이 검증한 결과가 아니다. 구조 참고로만 쓰고 판단 라벨로는
  쓰지 않는다. 진짜 시장 앵커는 `feedback_ledger.jsonl`의 실제 회신 1건뿐이다.
- **웹 스카우트가 경쟁사를 물어온다.** 가설이 자기 자신을 묘사하는 문장으로 나와
  검색이 자기+경쟁사로 채워진다(공감만세 실측: 발굴 4곳 중 1곳이 자사 서비스명).
  `grounded_in` 계약 불일치로 explore 가설이 전량 폐기되던 것은 진단했으나, 처방이
  순손실이라 되돌렸다 — 미해결.
- **3분 loop 목표 미달.** represent+retrieve+judge 실측 368초(공감만세). 추론 예산을
  깎아서 줄이는 길은 막았다(품질 붕괴 실측) — 캐스케이드·병렬화로 좁혀야 한다.
- **전체 테스트 스위트가 게이트로 못 쓴다** — 39분·73실패. 실 LLM을 타는 배선
  테스트가 간헐 실패하고, 일부는 유료 API를 실제로 호출하고 있었다(수정함).
  `test_scorer_client.py`는 12.5초→0.4초·간헐실패 0으로 고쳤고 같은 처방을 확산해야 한다.
- deal-breaker 리스트는 placeholder 2건 — **BD 확정 필요** (`engine/dealbreakers.py`).
