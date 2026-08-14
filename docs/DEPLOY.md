# 배포 — Supabase + Cloud Run + Vercel

```
브라우저 ──▶ Vercel (Next.js)  ──rewrites──▶ Cloud Run (FastAPI 엔진)
              │  Supabase Auth                  │  Supabase Postgres
              │  (매직링크 세션)                 │  (service_role, RLS 우회)
              └──────────── 같은 Supabase 프로젝트 ────────────┘
```

세 조각의 책임:
- **Supabase** — 인증(GoTrue 매직링크)과 보존(Postgres). 비용 예약은 DB 함수로
  원자 실행한다.
- **Cloud Run** — 엔진(무상태 계산기). `max-instances=1` 고정: job 폴링이
  인스턴스 로컬 메모리를 쓰므로 스케일아웃하면 폴링이 다른 인스턴스에 붙어
  '결과 없음'이 된다.
- **Vercel** — 웹. `rewrites`로 `/api/*`를 엔진에 중계해 동일 출처를 유지한다
  (CORS 불필요, 엔진 URL 비노출).

## 왜 Firebase가 아니라 Supabase인가

이미 `FirestoreSaasStore`가 있고 그대로 둔다(`SAAS_STORE=firestore`). Supabase를
더한 이유는 **비용 캡의 원자성**이다. Firestore 트랜잭션도 원자적이지만,
Postgres에서는 캡 검사와 가산을 DB 함수 한 덩어리로 밀어넣어 앱이 실수할 여지를
없앨 수 있다. 예산 사고는 되돌릴 수 없으므로 방어선을 데이터베이스에 둔다.

## 준비 (사람이 해야 하는 것)

로그인은 브라우저 대화형이라 스크립트가 대신하지 않는다.

```bash
supabase login          # 액세스 토큰 발급
gcloud auth login       # rename 계정으로
vercel login            # rename 계정으로
```

Supabase 대시보드에서 프로젝트를 만든 뒤:
- Settings → API에서 `URL` / `anon` / `service_role` 키 복사 → `.env`
- Authentication → URL Configuration → **Redirect URLs**에 Vercel 도메인 추가
  (이걸 빠뜨리면 매직링크가 localhost로 돌아와 로그인이 안 된다)

`.env.example`를 복사해 `.env`를 채운다. **`SAAS_ALLOWED_USERS`가 비면 전원
거부**다 — 이건 버그가 아니라 fail-closed 설계다.

## 실행

```bash
source .env && export $(grep -v '^#' .env | cut -d= -f1)
scripts/deploy.sh check     # 무엇이 빠졌는지 알려준다 (파괴적 동작 없음)
scripts/deploy.sh all       # db → engine → web
```

개별 실행도 된다: `db` / `engine` / `web`.

`engine`은 배포 후 URL을 `.engine_url`에 남기고, `web`이 그 값을 읽어
`ENGINE_URL`로 Vercel에 심는다.

## 스키마

`supabase/migrations/*.sql` — `supabase db push`가 **미적용분만** 올린다.
기존 데이터를 지우지 않는다.

- `saas_docs (kind, workspace_id, doc_id, body jsonb)` — 세 백엔드가 공유하는
  같은 계약. JSONB인 이유는 프로필·온톨로지가 중첩 구조이고, 백엔드 전환 시
  형태 변환 코드가 생기지 않아야 하기 때문이다.
- `reserve_cost(...)` — 캡 검사+가산 원자 함수. 초과 시 `P0001` 예외를 올리고
  앱이 `EngineError(402, cost_cap)`으로 변환한다.
- RLS 활성 + `workspace_id = 'ws-' || auth.uid()` 정책. 엔진은 service_role로
  우회하지만, anon 키가 유출되거나 클라이언트 직결 경로가 생겼을 때 기본이
  '거부'여야 한다. 전역 비용 원장(`__global__`)에는 정책을 만들지 않는다 —
  정책 부재가 곧 거부다.

## 검증 결과 (실측)

로컬 PostgreSQL 17에 마이그레이션을 실제 적용해 확인했다.

| 항목 | 결과 |
|---|---|
| 마이그레이션 적용 | 테이블·함수·정책 생성 확인 |
| 요청 캡 초과 | 거부 + **잔액 미가산** (1.0 유지) |
| 월 캡 초과 | 거부 |
| **동시 30건 경쟁** (캡 10.0, 각 1.0) | **정확히 10건 통과 / 20건 거부, 최종 잔액 10.0** |

동시성 검증이 핵심이다 — 앱 레벨 read-then-write였다면 캡이 뚫렸을 지점이다.

스토어 클라이언트는 스텁 PostgREST로 8건 검증(`tests/test_supabase_store.py`):
왕복·upsert 헤더·URL이 든 doc_id 인코딩·워크스페이스 격리·캡 402 변환·
`inf` JSON 안전성·키 없을 때 즉시 실패.

## 아직 검증 못 한 것 (정직하게)

- **실 Supabase 프로젝트에 붙여본 적 없다.** 로그인이 필요해서다. `deploy.sh
  db` 이후 `check`가 통과해도, 첫 `engine` 배포 뒤 매직링크 로그인까지는 직접
  확인해야 한다.
- Cloud Run 빌드는 로컬 Docker 데몬이 꺼져 있어 이미지 빌드를 못 돌려봤다.
  `--source .` 방식이라 Cloud Build가 빌드하므로 로컬 Docker는 불필요하지만,
  첫 배포에서 requirements 설치 실패 가능성은 남아 있다.
- Vercel 프로젝트 최초 링크(`vercel link`)는 대화형이라 첫 `web` 실행 시
  프로젝트 선택 프롬프트가 뜰 수 있다.

## 되돌리기

- 웹: Vercel 대시보드에서 이전 배포로 Promote (즉시)
- 엔진: `gcloud run services update-traffic a2a-engine --to-revisions=<이전>=100`
- DB: 마이그레이션은 전진만 한다. 되돌릴 일이 생기면 새 마이그레이션을 쓴다
  (down 스크립트를 두면 프로덕션에서 실수로 데이터가 날아간다)
