# Phase 2 — 자료 수집·추출(Ingestion) 설계

### 요구사항 · 시나리오 · 보안 규약 (v1.0)

> **목적**: Represent의 입력을 "구조화 텍스트"에서 **실제 기업 자료**(IR덱 PDF·웹사이트·기사·인스타그램)로
> 확장한다. API 키만 `.env`에 넣으면 LLM 추출이 켜지고, 없으면 Mock으로 동작한다.
> **연계**: 엔진_PRD REP-01~09 / 기획서 5장 / 데이터스키마 §3.2 (provenance).

---

## 0. 아키텍처

```
Company Source Assets
  (IR덱 PDF · 웹사이트 URL · 기사 URL · 인스타그램 · 텍스트)
        │
        ▼
┌─ Ingestion 파이프라인 (app/ingest/) ─────────────────────────┐
│ ① Fetcher   : URL → 본문 텍스트 (웹/기사), 핸들 → 프로필+캡션 │
│ ② Chunking  : PDF/텍스트 → 출처 라벨이 달린 청크 (~2000자)    │
│ ③ Extractor : 청크들 → 프로필 필드 + provenance + 근거 청크   │
│               (LLM: claude-opus-4-8 구조화 출력 / 폴백: Mock) │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
Represent (기존 계약 유지: 3형 출력 + 최소 프로필 게이트 REP-06)
```

**어댑터 원칙**: Extractor는 `extract_json(system, user, schema) -> dict` 인터페이스 하나만 본다.
`ANTHROPIC_API_KEY` 유무로 AnthropicExtractor ↔ Mock(규칙 파서)이 자동 선택되며,
향후 EXAONE 파인튜닝 모델로 교체할 때도 이 인터페이스만 구현하면 된다 (JDG-12 경로와 동일 전략).

---

## 1. 요구사항 (ING) — 검증형

| ID | P | 요구사항 | 검증 방법 |
|---|---|---|---|
| ING-01 | P0 | 자산 유형별 수집기를 지원해야 한다: `website`/`article`/`portfolio`(URL→본문), `ir_deck`(PDF 파일/URL→페이지 텍스트), `instagram`(핸들/URL→프로필+최근 캡션), `text`(그대로). 수집 실패 시 명시적 에러(`fetch_failed`)를 반환해야 한다(무시·빈값 금지). | 유형별 성공/실패 테스트 |
| ING-02 | P0 | 추출 텍스트는 청크(기본 ~2,000자, 문단 경계 우선)로 분할되고, 각 청크는 **출처 라벨**(자산 순번·유형)을 유지해야 한다. | 청크 스키마 검사 |
| ING-03 | P0 | LLM 추출은 REP-02 필드 전체를 대상으로 하며, 각 필드에 provenance(`stated`/`inferred`/`ask`)와 `inferred` 확신도를 부여해야 한다. 자료에 없는 항목은 비워두지 않고 `ask`로 마킹해야 한다. | 스키마 자동 검사 (구조화 출력) |
| ING-04 | P0 | 추론된 각 핵심 필드는 **근거 청크 ID**를 기록해야 한다(응답 `evidence`). 사람이 "이 추론이 어디서 왔나"를 역추적할 수 있어야 한다(SYS-04 감사 연계). | evidence 존재 검사 |
| ING-05 | P0 | **API 키는 환경변수(`.env`)로만 주입**한다. 코드·저장소에 키 금지(`.gitignore`에 `.env`). 키가 없으면 Mock으로 degrade하고 응답에 `engine_mode: "mock"`을 표기해야 한다(조용한 실패 금지). | 코드 검사 + mock 모드 테스트 |
| ING-06 | P1 | 비한국어 자료는 추출 단계에서 한국어 표준 프로필로 정규화해야 한다(REP-08). 원문 언어는 `lang` 힌트로 전달. | 다국어 자료 테스트 |
| ING-07 | P1 | 외부 호출은 타임아웃(기본 15초)·SDK 자동 재시도(429/5xx)를 갖춰야 한다. | 타임아웃 설정 검사 |
| ING-08 | P0 | **ToS·robots 준수**: 인스타그램은 공식 Graph API 또는 계약된 서드파티(Apify 등) 경유만 허용. 무단 스크레이핑 구현 금지. 토큰 미설정 시 명확한 안내 에러(`instagram_not_configured`). | 코드 검수 |
| ING-09 | P2 | 동일 URL 수집 결과 캐시(24h) — 비용·중복 방지. | (Phase 5) |

**API 계약 변경점 (v1.0 → v1.1)**
- `AssetType`에 `instagram` 추가.
- `RepresentResponse`에 선택 필드 추가: `engine_mode`(`"llm"`/`"mock"`), `evidence`(필드명 → 근거 청크 ID 목록). 기존 필드는 불변 — 하위 호환.

---

## 2. 시나리오

**S1 — 신규 기업 온보딩 (풀스택 자료)**
```json
POST /v1/represent
{ "assets": [
    {"type": "ir_deck",   "url": "/Users/.../다이브인_IR.pdf"},
    {"type": "website",   "url": "https://divein.example.com"},
    {"type": "instagram", "url": "https://instagram.com/divein_official"}
] }
```
→ 수집 → 청킹 → LLM 추출 → `profile`(provenance 부착) + `evidence` + `open_questions`(ask 항목만).
Willingness 등 자료로 알 수 없는 항목은 `ask` → 보강 대화(`dialogue`)로 재호출.

**S2 — 외부 풀 충원 (웹사이트만 있는 해외 기업)**
website URL 1건 → 추출 → 최소 프로필(REP-06) 판정. 미달이면 `409` + 보강 질문 —
외부 풀 후보는 buy-side·Willingness 공란이 정상(RET-04)이므로 4개 핵심 필드만 게이트.

**S3 — 자료 추가 갱신 (REP-09)**
기존 프로필 보유 기업에 기사 URL 추가 → 재호출 → 갱신된 프로필. (프로필 저장·병합은 제품 레이어 책임 — 엔진은 stateless.)

**S4 — 키 미설정 개발 모드**
`.env` 없음 → 구조화 텍스트("키: 값") 자산만 파싱되는 Mock 경로. 응답 `engine_mode: "mock"`.
CI·로컬 테스트는 전부 이 모드로 돌아 API 비용 0.

**S5 — 인바운드 진위 게이트 보조 (JDG-11 연계)**
인바운드 요청자의 인스타그램/웹사이트를 수집해 실재 신호(계정 존재·활동·프로필 일치)를 확보 →
진위 게이트 판단 재료로 Judge에 전달.

---

## 3. 보안 규약 (SEC) — 필수 준수

| ID | 규약 |
|---|---|
| SEC-01 | API 키는 `.env`로만. **코드·커밋·로그에 키 금지.** `.env`는 `.gitignore` 등록 완료. |
| SEC-02 | 키가 노출됐다면 **즉시 폐기·재발급** (Anthropic Console / Apify Console). |
| SEC-03 | 키는 **최소 권한**으로 발급 (Anthropic: workspace 스코프 / Apify: 해당 actor만). |
| SEC-04 | `.env.example`에는 키 이름만 두고 값은 절대 넣지 않는다. |

**셋업 (키 받은 후 이것만)**
```bash
cp .env.example .env
# .env 열어서 ANTHROPIC_API_KEY=sk-ant-... 붙여넣기
# (인스타그램 쓰려면 APIFY_TOKEN도)
.venv/bin/python -m pytest tests/ -q   # mock 테스트는 키 없이도 통과
```

---

## 4. 비용·모델 노트

- 모델: `claude-opus-4-8` (기본, `.env`의 `ANTHROPIC_MODEL`로 변경 가능). 입력 $5/1M · 출력 $25/1M 토큰.
- 추출 1회 ≈ 자료 분량에 비례 (IR덱 20p ≈ 입력 1.5~3만 토큰 ≈ $0.1~0.2 수준). 대량 배치는 Batches API(50% 할인)로 확장 여지.
- 구조화 출력(`output_config.format`)으로 스키마 강제 — 파싱 실패·재시도 비용 없음.
- SDK가 429/5xx 자동 재시도(기본 2회). 레이트리밋 초과가 잦으면 AXR팀과 티어 협의.
