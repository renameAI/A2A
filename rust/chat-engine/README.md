# a2a-chat — 메시징 데이터 플레인 (Rust)

`/chat/v1/*` 전부를 직접 서빙하는 앞문이다. 나머지 경로(`/v1`, `/saas`, `/platform/v1`, 웹 정적)는
`A2A_UPSTREAM`(Python 엔진)으로 그대로 중계한다. `run-mac.sh`·`Dockerfile`이 이 구성을 만든다.

```
브라우저/확장 ──▶ a2a-chat :8423 ──(chat/v1)──▶ Postgres 셀 · Redis 팬아웃 · SSE
                        └──(그 외 경로)──▶ uvicorn app.main :8425 (LLM·SaaS·관리 플레인)
```

## 단일 소스 지도 — 무엇이 어디에 하나씩 있는가

| 규칙 | 단일 소스 |
|---|---|
| 쓰기 원자 규칙 (seq·멱등·답글 카운터·읽음 커서·리액션) | 셀 DB SQL 함수 (`app/chat/sql/tenant_v1.sql`) |
| 멤버 upsert + 기본 채널 자동 가입 | 셀 DB `add_member()` (`tenant_v5.sql`) — Python 프로비저닝과 Rust 초대가 같은 함수를 부른다 |
| 테넌트 펜스 (셀 이동 안전) | `app/chat/sql/tx_fence.sql` — Python `db.py`와 Rust `db.rs`가 같은 파일을 읽는다 |
| ABAC 판정 (역할·deny 우선·조건 언어) | `src/authz.rs` — 시험은 `a2a-chat eval-cond`로 같은 평가기를 부른다 |
| 팬아웃 (redis/supabase/memory)·SSE 허브 | `src/events.rs` — 관리 플레인(공지)은 `/chat/v1/internal/publish`로 들어온다 |
| 속도 제한 (토큰 버킷 Lua) | `src/ratelimit.rs` |
| 메트릭 히스토그램 버킷 | `src/metrics.rs` `BOUNDS` — `tests/test_rust_parity.py`가 Python 읽기 쪽과 대조 |
| 라우팅 캐시 무효화 | 관리 플레인이 상태·배치를 바꾼 직후 `/chat/v1/internal/forget-route` |

관리 플레인(프로비저닝·셀 이동·아카이브 쓰기·플랫폼 콘솔)은 Python(`app/chat/`)에 남는다 —
차갑고 SQL이 무거운 경로라 옮겨도 얻는 게 없다. 아카이브 **읽기**(핫이 바닥나면 이어 읽기,
내보내기)는 여기(`src/archive.rs`) 있다: 객체(JSONL.gz)·색인 계약을 바꾸면 양쪽을 함께 바꾼다.

## 실행

```
cargo build --release
A2A_CHAT_PORT=8423 A2A_UPSTREAM=http://127.0.0.1:8425 target/release/a2a-chat serve
a2a-chat print-config     # 공유 상수(JSON) — 파이썬 쪽 parity 시험이 대조
a2a-chat eval-cond '<조건 JSON>' '<컨텍스트 JSON>'
```

환경변수는 Python 때와 같다: `CHAT_CONTROL_DSN`, `<dsn_env>`/`<dsn_env>_APP`, `CHAT_FANOUT`
(memory|redis|supabase|none), `CHAT_REDIS_URL`, `CHAT_RL_*`, `CHAT_SSE_*`, `CHAT_ROUTE_TTL`,
`CHAT_ARCHIVE_*`, `SAAS_AUTH`(dev|supabase|firebase), `SAAS_ALLOWED_USERS`(비면 전원 거부;
`__all__`은 시험 하네스 전용 와일드카드), `SUPABASE_*`, `CHAT_INTERNAL_KEY`(내부 API 보호),
`A2A_CHAT_BIND`(기본 127.0.0.1 — 컨테이너에서는 0.0.0.0).

성능 수치와 측정 방법은 `docs/MESSAGING_PLATFORM.md` §8b.

아직 안 옮긴 것: 진단 탐침(CHAT_LOOP_PROBE — asyncio 전용이라 의미가 없어졌다),
Postgres TLS 연결(셀 DSN은 지금 로컬/사설망 평문 전제 — Supabase 셀을 붙이려면 rustls 배선 필요),
Vercel 서버리스 단일 함수 모드의 /chat/v1(그 모드는 SSE에 원래 부적합했고, 자체 호스팅·Cloud Run
경로가 공식이다).
