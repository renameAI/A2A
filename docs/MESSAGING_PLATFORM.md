# 메시징 플랫폼 — 멀티테넌트 셀 아키텍처

> 상태: 1~4단계 구현·실측 완료 (2026-10-20). Supabase Realtime·Storage 로컬 실연결 검증,
> 지연 꼬리 원인 규명, 셀 이동 추가 (2026-10-21). 5단계 UI 연결·어드민 콘솔·Claude 전환·
> docker compose (2026-10-21).
> 코드: `app/chat/` · 시험: `tests/test_chat_platform.py`(실 Postgres 2대) ·
> 부하: `scripts/chat_loadtest.py`

## 1. 요구사항 → 결정

| 요구 | 결정 |
|---|---|
| 기업(테넌트) ≥100, 각자 분리된 망·채널·DB | **셀 + 테넌트별 스키마**. 셀 = 독립 Postgres. 셀 안에서 테넌트마다 스키마·전용 DB 역할·RLS. 엔터프라이즈는 전용 셀 |
| 중규모: 동시접속 1,000 · 초당 200건 | 단일 쓰기 함수(행 잠금 직렬화) + seq 커서 + 알림형 팬아웃 |
| 실시간 | 운영: Supabase Realtime broadcast / 로컬·자체 게이트웨이: Redis pub/sub + SSE |
| 권한 | ABAC — 역할(권한 묶음) + 속성 조건 정책(거부 우선) + DB RLS 2차 방어 |
| 보존 | 핫 12개월(월 파티션) → 객체 저장소 아카이브 무기한, 테넌트별 오버라이드·법적 보존 |
| 플랫폼 어드민 | 컨트롤 플레인: 테넌트·셀 관리, 메트릭·알림, 운영 채널, 전체 공지, **본문 전체 열람(양쪽 감사 기록)** |

## 2. 구조

```
             ┌──────────── 컨트롤 플레인 DB ─────────────┐
             │ cells · tenants(배치) · tenant_users       │
             │ platform_admins · platform_audit           │
             │ metrics_minute · alerts · ops_* · announce │
             └──────────────┬─────────────────────────────┘
                            │ 라우팅(tenant → cell, TTL 15s)
   API 인스턴스 N개 ────────┼──────────────────────────────
   (서버리스)               │
          ┌─────────────────┴────────────┐
     셀 A (Postgres)                셀 B (Postgres)          … 전용 셀
     ├ t_acme   (스키마·역할·RLS)    ├ t_globex
     ├ t_initech                     └ …
     └ …
          │ 커밋 후 {type, seq} 알림
          ▼
     팬아웃: Supabase Realtime | Redis pub/sub ─▶ SSE/WebSocket 구독자
```

### 격리 3중 (같은 셀 안의 이웃 테넌트로부터)
1. **스키마** — 이름공간 분리
2. **DB 역할** — 요청 트랜잭션은 `SET LOCAL ROLE t_<slug>_app`. 이 역할은 자기 스키마에만
   USAGE가 있어 남의 스키마를 이름으로 적어도 `permission denied` (시험으로 확인)
3. **RLS** — 비공개 채널은 멤버만, 게스트는 초대받은 채널만, 사용자 없는 트랜잭션은 0행

앱 역할은 테이블에 직접 쓰지 못한다(INSERT/UPDATE/DELETE 권한 없음). 쓰기는
`SECURITY DEFINER` 함수(`post_message` 등)만 — 순서·멱등·카운터를 DB가 원자적으로 보장.
파티션 분리 같은 관리 함수는 앱 역할에 EXECUTE가 없다.

## 3. 쓰기 경로와 정합성

`post_message()` 한 트랜잭션:
**채널 행 잠금 → 멱등 확인(client_msg_id) → seq 발급 → (답글이면) 루트 reply_count +1 → 삽입 → 멘션 행**

- **순서는 seq**(채널별 단조 증가, 공백 없음). 시계는 표시용 — 서버리스 인스턴스 간 시계 차이로
  메시지를 놓치는 문제가 구조적으로 없다
- **멱등**: 같은 client_msg_id 재시도는 같은 seq를 돌려준다(동시 10건 경합에서도 1건)
- **카운터 유실 없음**: 멘션은 카운터가 아니라 행. 안 읽은 멘션 = `seq > last_read_seq`인 행 수
- **읽음 커서는 되감기지 않는다** (`GREATEST`)

## 4. 실시간 — "알림만, 본문은 조회"

이벤트는 `{type, seq}`만 싣는다. 클라이언트는 받은 seq와 자기 마지막 seq 사이 공백을
`GET …/messages?after=`로 메운다. 그래서:
- 전파가 유실·중복·역순이어도 화면은 결국 DB와 같아진다 (정합성 = DB 책임)
- 전파 경로에서 권한을 다시 판정할 필요가 없다 (본문은 조회 시 RLS를 통과해야만 나온다)
- 프로세스당 Redis 구독 1개를 참조 계수로 공유 — 접속자 수 ≠ Redis 연결 수
- SSE는 60초(±지터)마다 가시성을 재확인해 쫓겨난 채널의 이벤트를 끊는다

## 5. 권한 (ABAC)

판정 순서(거부 우선): 비활성 → **deny 정책** → 역할 권한 → 채널 관리자 → allow 정책 → 거부.
조건 언어: `all/any/not/eq/ne/in/exists`, 피연산자 `user.role`, `user.attrs.*`,
`channel.visibility`, `channel.attrs.*`, `env.hour_utc`, `env.weekday`. 저장 시점에 형식 검증.

예) "부서 채널엔 그 부서만 쓴다":
```json
{"effect":"deny","actions":["message.post"],
 "condition":{"all":[{"exists":"channel.attrs.department"},
                     {"ne":["user.attrs.department","channel.attrs.department"]}]}}
```
보호 규칙: 마지막 소유자 강등·비활성 불가, 전권(`*`) 역할은 소유자만 생성, owner 역할 불변.

## 6. 아카이빙

1. 파티션 내보내기(JSONL.gz, mtime=0 → 같은 내용 = 같은 sha) → 2. 저장(키에 내용 해시) →
3. **다시 읽어 sha·행 수 검증** → 4. 색인 기록 + 파티션 detach (같은 트랜잭션)

- 검증 실패 시 파티션은 그대로 — 재실행 안전
- **객체는 불변·추가 전용** — 같은 달 재보관이 이전 객체를 덮지 않는다(실측 결함을 고친 규칙)
- 조회: 핫 데이터가 바닥나면 `list_messages`가 아카이브로 이어 읽는다(같은 커서, `archived: true`)
- 영구 삭제: `archive_purge_months` 설정 시만, **법적 보존 중이면 건너뜀**
- 내보내기: 채널 단위 NDJSON(아카이브+핫, 서버 측 커서로 스트리밍), `export.run` 권한·감사 기록

## 7. 컨트롤 플레인

| 기능 | 내용 |
|---|---|
| 어드민 역할 | superadmin(전부·어드민 관리) / operator(테넌트·본문·공지·운영 채널) / viewer(목록·메트릭·알림) |
| 테넌트 | 생성(자동 배치: 공용 셀 중 가장 한가한 곳, advisory lock으로 직렬화)·정지·보존·법적 보존 |
| 셀 | 실제 접속 헬스(직접 1회 연결 — 풀 미사용), DB 크기·연결 수·스키마 수, 상태 전환 |
| 메트릭 | 요청별 메모리 버킷 → 10초마다 컨트롤 DB에 원자 합산. 지연은 **히스토그램 합산**으로 분위수 계산 |
| 알림 | 오류율 >2%(≥50건), 게시 p95 >500ms, 셀 접속 불가(critical), 셀 연결 >80%, 셀 용량. 열림/해소가 #incidents에 자동 게시 |
| 운영 채널 | 플랫폼 어드민끼리 #ops, #incidents |
| 전체 공지 | 모든 활성 테넌트 #announcements에 셀을 넘어 게시. 테넌트별 전달 상태 기록, 재시도는 미전달분만, 테넌트 쪽 멱등 키로 중복 0 |
| 본문 열람 | **모든 열람이 platform_audit + 해당 테넌트 access_log 양쪽에 기록** — 고객이 직접 확인 가능 |
| 주기 작업 | `POST /platform/v1/jobs/tick` (크론, `X-Cron-Secret`) — 분 단위 알림, `daily=true`면 파티션·아카이브·영구 삭제·프로비저닝 정리 |

> ⚠ 법무: 플랫폼 어드민의 본문 열람은 이용약관·개인정보 처리방침에 고지·동의가 필요하다.

## 8. 실측 (로컬: M-series 10코어, Postgres 17 × 2, Redis 1, uvicorn 4 워커 — 전부 한 대)

**프로비저닝**: 100개 테넌트 3.9초(테넌트당 39ms), 셀 a/b에 50/50 배치.

**부하**: 100 테넌트 × 10명 = **1,000 SSE 동시 구독**, **초당 200건 × 90초 = 18,000건**

| 지표 | 값 |
|---|---|
| 달성 처리량 | 200.0 건/초, 상태 코드 200 × 18,000 |
| 전파 완결성 | **180,000 / 180,000 수신 (유실 0)** |
| 정합성 | seq 공백 채널 0, 게시 수 불일치 0 |
| 서버 처리 시간 | p50 3.0ms · p95 9.0ms · p99 25~50ms |
| 전파 지연(발행→수신) | p50 0.9ms · p95 1.4~17ms · p99 6~29ms |
| 클라이언트 관측 게시 지연 | p50 3.7~4.2ms · **p95 7~110ms · p99 58~480ms (실행마다 편차)** |

부하 시험이 찾아낸 결함과 수정:
- **SSE 재확인 thundering herd** — 1,000 연결이 60초에 동시에 무거운 질의 → 게시 p50 **19초**로 붕괴.
  가벼운 가시성 질의 + 연결별 지터로 수정 → p50 3.7ms

## 9. 시험 (실 Postgres 2대 50건 + Supabase 실연결 10건, 반복 실행 안정)

배치·용량 초과, 이웃 스키마 접근 거부, 앱 역할의 직접 쓰기·관리 함수 거부, 비공개 채널(목록·조회·
검색·게시), 게스트, 비활성 멤버, 교차 테넌트, ABAC(조건 언어·deny 우선·allow 확장·저장 검증·
관리 권한·전권 역할·마지막 소유자), 모더레이션 감사, **동시 500건 seq 연속성, 멱등 경합, 답글 카운터
경합, 멘션 집계, 커서 단조성, 페이지네이션·공백 보충**, 파티션 이동, 아카이브(왕복·이어 읽기·불변 객체·
비공개 유지·검증 실패 시 보존·내보내기 권한·법적 보존), 플랫폼(역할 계층·양쪽 감사·정지·셀 경계
공지 부분 실패/재시도/중복 0·메트릭·알림 수명주기·셀 장애 감지·운영 채널·크론 비밀).

시험이 찾아낸 실제 결함: 게스트 자동 가입으로 #general 노출 / 역할 강등으로 소유자 0명 /
중복 재시도 시 `last_top_seq` 오염 / **같은 달 재보관이 이전 아카이브를 덮어써 30건 유실** — 모두 수정·회귀 시험 추가.

## 10. Supabase 실연결 검증 (로컬 Supabase 스택, 2026-10-21)

`supabase start`(Realtime v2.143.4·Storage·Auth·Postgres, colima 리눅스 VM)에 목·스텁 없이 붙었다.
시험: `tests/test_supabase_live.py` — Phoenix 웹소켓 프로토콜을 직접 말하는 구독자.

**인가 — 토픽 한정 토큰.** Realtime 비공개 채널 인가는 Realtime 쪽 DB의 `realtime.messages` RLS가
판정하는데, 채널 권한의 원본은 다른 서버(셀)에 있다. 그래서 판정은 우리 API가 하고 결과(구독 가능한
토픽 목록)를 300초짜리 JWT 클레임 `chat_topics`에 담는다(`GET /chat/v1/t/{slug}/realtime-token`).
정책(`sql/realtime_policy.sql`)은 "요청 토픽이 토큰에 있는가"만 본다.

| 검증 | 결과 |
|---|---|
| 토큰에 있는 토픽 구독 → 서버 게시 수신 | ✅ |
| 다른 토픽 / anon 키 / 만료 토큰 / 위조 서명 | ✅ 4종 모두 거부 |
| 거부된 구독자에게 실제 배달 | ✅ 0건 |
| 클라이언트가 직접 broadcast 송신 | ✅ 다른 구독자에게 안 닿음(INSERT 정책 없음) |
| 종단: 서비스 게시 → Realtime → 구독자, 비공개 채널은 비멤버 토큰에서 빠짐 | ✅ |
| Storage: 비공개 버킷 보관·검증·이어 읽기, 익명 읽기 거부, 영구 삭제 후 객체 없음 | ✅ |

**실연결이 찾아낸 결함 4개** (모두 수정):
1. `realtime.messages`에 `ALTER … ENABLE RLS` → `must be owner` (Supabase가 이미 켜 둠, 정책만 추가)
2. Storage `delete()`가 httpx에서 본문을 못 실어 영구 삭제가 터졌을 것 → `request("DELETE", json=…)`
3. **무료 플랜 한도(동시 200·초당 100이벤트)에서 알림 1/3 유실** — 429를 받으면 그 묶음을 버렸고,
   한도는 **배달(fan-out) 기준**이라 202로 받아 놓고도 넘치면 조용히 버린다
   (Realtime 로그 `MessagePerSecondRateLimitReached`)
4. 고정 0.1초 합치기 창은 채널 20개에 흩어진 게시를 거의 못 합쳐 대기열이 끝없이 길어졌다
   (알게 되는 시간 p95 6.1초)

→ 발행기 재설계(`events._Supabase`): **토픽당 최신 알림 하나만 대기**(대기열이 게시 수가 아니라
토픽 수로 묶임), 초당 `CHAT_RT_MAX_EPS` 이하로 보내기, 429/5xx는 되돌려 놓고 지수 백오프.

| 실험 (지표 = 게시 s를 구독자가 "알게 되는" 시간) | 유실 | p50 | p95 | p99 |
|---|---|---|---|---|
| 무료 플랜 한도 · 구독 200 · 초당 100건 · `MAX_EPS=8`(= 8 × 구독 10 ≤ 100) | **0 / 15,000** | 1.2s | 2.4s | 2.5s |
| 한도 상향 · **구독 1,000 · 초당 200건 × 30초** | **0 / 60,000** | 9.9ms | 122ms | 348ms |

**운영 결론**: Realtime 한도는 *배달 수*로 센다. 목표(동시 1,000·초당 200건·채널당 ~10명 ≈ 초당 2,000 배달)는
**Pro 기본 한도(동시 500·초당 500)도 넘는다** — 한도 상향 요청이 필요하고, 안 되면 큰 테넌트는 자체
게이트웨이(Redis+SSE 경로)로 보낸다. 한도 안에서는 실시간이 "수 초 이내"로 저하되지만 유실은 없다.
`CHAT_RT_MAX_EPS`는 (플랜 이벤트 한도 ÷ 채널당 평균 구독자 수)로 잡는다.

로컬 스택 한계(운영과 다름): Kong이 nginx 워커 1개·512연결이라 ~850 웹소켓에서 500 → 실험 B는 Realtime에
직접 붙었다. Realtime 컨테이너 nofile 1024 → `emfile`, 65536으로 재생성. 재기동 시 테넌트 한도가 기본값으로
재시드된다(`SEED_SELF_HOST`) — 관리 API(`PATCH /api/tenants/realtime-dev`)로 올렸다.
**Supabase 클라우드 프로젝트에서의 측정은 아직 없다.**

## 11. 지연 꼬리 원인 (2026-10-21 규명)

어제 보고: 서버 처리 p99 25~50ms인데 클라이언트 p99 58~480ms(실행마다 편차). 가설을 하나씩 반증했다.

| 가설 | 실험 | 결과 |
|---|---|---|
| 한 워커에 SSE가 몰려 루프가 막힘 | 응답에 워커 pid, 워커별 분해 | ✗ 꼬리가 모든 워커에 |
| 긴 콜백이 루프를 붙잡음 | 20ms 넘게 루프를 쥔 콜백 기록 | ✗ 0건 (단, 이 탐침 자체가 지연을 키움 — 폐기) |
| GIL 경합(스레드풀 40) | 스레드풀 40/8/4 | ✗ 단조 관계 없음 |
| SYN 유실(macOS backlog 128) | 새 연결마다 TCP connect 시간 | ✗ connect 최대 175ms, 1초대 0건 |
| **부하 생성기 자신** | 구간 분해: 작업 생성→소켓 쓰기 / 소켓→서버 앱 / 서버 처리 | **✓** |

통제 실험 — CPU 버너 8개로 호스트 유휴율 0% 주입:

| | 게시기 1프로세스 | 게시기 4프로세스 |
|---|---|---|
| 게시 p99 | **129,520ms** | **107~119ms** |
| 클라이언트 안에서 기다림 p99 | 129,311ms | 21~23ms |
| 소켓 → 서버 앱 p99 | 331ms | 13~15ms |
| 서버 처리 p99 | 355ms | 82~92ms |

**결론**: 어제 꼬리의 대부분은 **측정 도구**였다 — 호스트 CPU가 순간적으로 모자랄 때(다른 앱·VM) 단일
파이썬 프로세스 게시기가 초당 200건을 못 내보내고, 개루프 스케줄러가 만든 요청이 **클라이언트 안에** 쌓였다.
경합이 없을 때 실측: 게시 p95 6.1~7.6ms · p99 8.2~15.8ms (5회 반복). **서버 쪽 실제 영향은 호스트 CPU 100%
경합에서도 처리 p99 ≈ 90ms.** 부하 생성기는 이제 자기 루프 지연이 100ms를 넘으면 경고하고, 게시기를 여러
프로세스로 나눌 수 있다. 운영 권고: CPU 여유분 확보·지연 기반 자동 확장, 서버 측 히스토그램(`metrics_minute`)을
SLO 기준으로 쓸 것(클라이언트 측정은 측정기 자원에 오염된다).

## 12. 셀 이동 (전용 셀 승격) — `app/chat/move.py`

`POST /platform/v1/tenants/{slug}/move {"to_cell": …}` (superadmin) · `POST /platform/v1/moves/{id}/finalize`

1. **복사(서비스 유지)**: 원본 REPEATABLE READ 스냅샷 + `pg_current_snapshot()` 기록 → 대상 셀에 스키마 생성 →
   전 테이블 COPY BINARY
2. **펜스(짧은 쓰기 중단)**: 컨트롤 `migrating` → 원본 앱 역할의 스키마 USAGE 회수(라우팅 캐시가 낡은 인스턴스도
   DB가 거부) → 쓰기 테이블 EXCLUSIVE 잠금으로 **진행 중이던** 쓰기를 기다림 → 스냅샷에 안 보이는 행(xmin)만 델타 복사
3. **검증**: 테이블마다 행 수 + 행 해시 합 비교 → 4. **전환**: 컨트롤 cell_id 변경. 원본은 롤백 대비 보관 → `finalize`가 삭제
- 낡은 경로로 온 요청: `tenant_tx`가 같은 질의에 `has_schema_privilege`를 얹어 확인 → `503 tenant_moving`(재시도),
  SSE는 `reconnect` 이벤트
- 실패(전환 전): 원본 권한 복구 + 대상 삭제 + `rolled_back` 기록
- 같은 물리 DB로의 이동·동시 이동 거부, 이동 중(대기 포함) 아카이빙·파티션 유지 건너뜀

**배경 작업(2026-10-21)**: 이동은 HTTP 요청에서 돌지 않는다. API는 `queued`로 넣고 `202 {move_id}`를 돌려준다.
상주 작업자 `python -m app.chat.worker`(compose `worker` 서비스)가 `FOR UPDATE SKIP LOCKED`로 하나씩 집어
`phase`(provision → copy:<테이블> → fence → delta → verify → switch)와 하트비트를 기록하며 돈다.
- 진행: `GET /platform/v1/moves/{id}` — 어드민 콘솔이 2초마다 단계·작업자·하트비트를 보여 준다
- 취소: `POST /moves/{id}/cancel` — 대기 중이면 즉시 `cancelled`, 복사 중이면 다음 하트비트에서 멈추고
  대상 삭제. **펜스 이후는 취소 불가**(409) — 쓰기를 이미 멈춘 상태라 끝내거나 되돌리는 것뿐이다
- 작업자 사망: 하트비트가 `STALE_SEC`(300초) 끊긴 `copying·fencing` 이동은 다른 작업자(또는 `/jobs/tick`)의
  `recover_stale`이 원본 USAGE 복구·대상 삭제·테넌트 `active`로 되돌린다(시험: 펜스 직후 강제 종료 → 복구 → 쓰기 재개)
- `CHAT_MOVE_INLINE=1`(기본)이면 작업자 없는 단일 서버에서도 돌도록 API 프로세스가 스레드로 바로 집어 간다.
  작업자를 띄운 배포(compose)는 `0`

실측(다른 서버의 전용 셀로, 6개 스레드가 계속 쓰는 중): 복사 0.19초, **쓰기 중단 42ms**, 복사 중 들어온 232건 델타로
반영, **확인 응답 2,166건 전부 대상 셀에 같은 seq·본문**, 재시도 8건(503), 체크섬 일치, 권한·읽음·멘션·아카이브 색인 동일.

## 13. 한계 — 정직하게

- **Supabase 클라우드 프로젝트 실측 없음** — 로컬 스택에서만. 클라우드의 플랜 한도·엣지 지연은 다르다
- Realtime 목표 규모는 Pro 기본 한도를 넘는다(§10) — 한도 상향 또는 자체 게이트웨이 필요
- 토큰 기반 인가: 권한 회수가 토큰 수명(300초) 안에 반영된다(즉시는 아님)
- 클라이언트가 이벤트를 놓쳐도 다음 이벤트나 토큰 갱신 때 메운다 — UI가 주기적 last_seq 확인(안전망)을 해야 한다(5단계)
- 셀 이동은 같은 Postgres 메이저 버전 사이만(COPY BINARY), 원본의 xid 순환 감지 시 메시지 전체 재복사
- **DB 역할은 클러스터 전역** — 한 Postgres 클러스터에 플랫폼(예: 스테이징과 운영)을 둘 이상 두지 말 것
  (실측: 시험 정리가 같은 서버의 부하 시험 환경 테넌트 역할을 지웠다 — 시험 정리는 고쳤다)
- 아카이브 본문 검색 미지원, 라우팅 캐시 TTL(15초), Redis 장애 시 속도 제한 강등은 그대로
- 셀 상태 `down`은 배치·이동에만 영향, 이미 있는 테넌트의 트래픽을 막지 않는다

## 14. 5단계 — UI·어드민 콘솔·Claude (2026-10-21)

**웹 채팅** (`web/app/chat.tsx`, `page.tsx`): 레일의 워크스페이스(테넌트) 전환, 채널 목록(안 읽음 굵게·멘션
배지·🔒 비공개·📣 공지), 채널 만들기(비공개는 권한 있을 때만), 메시지(연속 묶음·멘션 강조·수정·삭제·
리액션·보관 표시·보내기 실패 재시도), 이전 메시지 무한 스크롤(아카이브까지 같은 커서), 스레드 패널,
검색(볼 수 있는 채널만), 사람 패널(멤버·초대·역할 변경·비활성화·플랫폼 열람 기록·감사 기록 — `me.can` 권한으로
노출, 판정은 서버), 비공개 채널 멤버 추가, 채널 내보내기. Lead Request는 AI 작업 화면 그대로이고, 팀 대화는
요청마다 딸린 채널 `lead-<rid>`에 저장된다. 후보 토론 스레드의 루트는 고정 client_msg_id로 게시해 서버 멱등이
대응표 역할을 한다.

**실시간**: 기본 SSE(fetch 스트림, 지터 백오프 재접속, 30초 tick 안전망). `NEXT_PUBLIC_CHAT_REALTIME=supabase`면
브라우저가 토픽 토큰으로 Supabase Realtime 비공개 채널을 구독(만료 1분 전 갱신 → 권한 재판정).
※ 브라우저의 Supabase Realtime 경로는 **아직 화면에서 돌려 보지 않았다**(서버·인가는 §10에서 실연결 검증).

브라우저 실측(두 사용자 탭): 상대 게시가 새로고침 없이 2초 안에 표시, 멘션 강조·배지 해제, 스레드 답글 시
본문의 '답글 N개'가 실시간 갱신, 게스트는 초대받은 채널·자기 자신만 보임, 다른 테넌트 게시가 섞이지 않음.

화면 검증이 찾아낸 결함(수정):
- **SSE가 Next 프록시의 gzip에 갇혀 영영 안 옴** → `Cache-Control: no-transform`
- 스레드 루트에 `reactions`가 없어 화면이 TypeError → 서버가 같은 모양으로 반환 + 클라이언트 방어
- 셀 헬스의 테넌트 스키마 수가 틀림(LIKE 이스케이프) → 정규식 + schema_meta 확인
- 개발 서버 리로드가 열린 SSE 때문에 멈춤 → dev에 `--timeout-graceful-shutdown 3`
- 시험 정리가 같은 서버 개발 환경의 `acme`를 지움(역할 공유) → 시험은 자기 DB만 정리

**어드민 콘솔** (`web/app/admin/`, `/admin`): 개요(테넌트·사용자·알림 KPI, 15분 경로별 요청·오류·p50/95/99, 팬아웃,
상위 테넌트), 테넌트(목록·상세: 60분 스파크라인, 보존·법적 보존 설정, 정지/재개 사유 필수, 셀 이동·보고서, 본문
열람 — 사유 입력 전엔 불러오지 않음·감사 경고 고정), 셀 헬스, 알림, 이동 기록(finalize), 운영 채널(#ops·#incidents),
전체 공지(전달 현황·재시도), 감사 로그. 역할(viewer/operator/superadmin)별 버튼 제한, 375px 대응.
콘솔 작업이 찾아낸 서버 결함(수정·회귀 시험): **2.5초 넘는 요청이 하나만 있어도 메트릭 API 500**(inf를 JSON이
거부) / 열람·정지 사유를 서버가 강제하지 않음 / 영구 삭제 설정을 지울 수 없음 / 감사 행에 slug 없음 /
운영 채널 이름 오타가 새 채널을 만듦 / 공지 재시도 미감사.

**Claude 전환** (`app/engine/llm.py`): 기본 공급자 `anthropic`, 모델 `claude-opus-5-5`. 사고는 항상 켜짐(adaptive) —
effort로 조절(일반 medium, deep 경로 high). 스트리밍(`get_final_message`), 구조화 출력(`output_config.format`),
접두어 캐시(`cache_control`), 안전 분류기 거절 시 서버 측 대체(`fallbacks: "default"`, Haiku 5.5 제외), 사고 블록을
건너뛰고 text만 모음(이전 코드의 `content[0].text`는 사고 블록이 앞에 오면 깨졌다). **외부 모델 API는 Claude 하나로 통일**(2026-10-21) — OpenAI·Friendli(EXAONE)·로컬·Gemini
공급자와 웹 토글을 지우고, 비교 채점·IR덱 bbox 비전·협상 인터뷰(웹 검색 리서치)까지 Claude로 옮겼다. 웹은 상태 배지만, 키 입력은 `/saas/settings/anthropic-key`(sk-ant- 검사, 마스킹 응답). **실 API 호출 검증은 키가 없어 아직 안 했다** —
요청 모양은 스텁 시험(`tests/test_claude_provider.py`)으로 고정.

**docker compose** (`docker-compose.yml`): 셀 Postgres 2대(다른 서버) + Redis + 시드 + 엔진 + 웹.
`docker compose up --build` → http://localhost:13200/?as=boram (어드민 `/admin`).

## 15. 멤버십 통합 — 테넌트가 리드 데이터 워크스페이스 (2026-10-21) — `app/saas/workspace.py`

예전엔 메시징 테넌트 멤버십과 리드 데이터 공유 팀(`ws-{uid}` 공유, `app/saas/team.py`)이 따로 있었다. 이제
테넌트에 속한 사용자의 리드 데이터(요청·후보·초안·발송 기록) 워크스페이스는 `t-<tenant_id>`다.
- 어느 테넌트인가: `X-Tenant` 헤더(웹은 레일에서 고른 워크스페이스) — **내가 속한 테넌트일 때만** 따르고,
  아니면 이름순 첫 테넌트. 테넌트를 바꾸면 웹은 화면 전체를 다시 읽는다
- **예전 데이터가 남아 있으면 예전 공간에 머문다** — 바로 넘기면 리드가 '사라진' 것처럼 보인다. 화면 상단 안내와
  사람 패널의 '리드 데이터' 카드가 옮기기를 안내한다
- 옮기기 `POST /saas/workspace/migrate {tenant_slug}`: 예전 공간 **소유자** + 대상 테넌트의 `member.invite` 권한.
  예전 팀원을 테넌트 멤버로 들이고, 문서를 **복사**(대상에 같은 id가 있으면 건너뜀 — 나중 수정을 덮지 않음),
  원본은 지우지 않고 `migrated_to` 표시, 플랫폼·테넌트 양쪽 감사 기록. 다시 실행하면 409
- **누가 보나 — `lead.access` 권한**(스키마 v2): owner·admin·member는 보고 **게스트는 못 본다**(외부 협력사에게 회사
  리드 파이프라인이 열리면 안 된다 — 통합 직후 dev 게스트 `vendor`가 리드 워크스페이스로 해석되는 것을 실측으로
  발견해 막았다). 채팅과 같은 ABAC라 deny 정책으로 특정 사람을 뺄 수도 있다. 셀 이동 펜스 중(권한을 모름)이면
  다른 공간으로 바꾸지 않고 503(재시도)
- 테넌트가 없는 사용자·메시징 플랫폼이 꺼진 배포는 예전 그대로
- 스키마 이전(`tenant_vN.sql`)은 작업자 tick·크론 `/jobs/tick`이 `migrate_all`로 올린다(이동 중인 테넌트는 끝난 뒤).
  compose 실측: 작업자 기동 직후 `acme`·`rename`이 v1→v2

시험 `tests/test_workspace_unify.py`(실 Postgres, 18건): 테넌트 멤버 공유·비멤버·게스트 차단·헤더 위조 무시·예전 데이터 보존·v1→v2 이전·
권한(예전 팀원·일반 멤버 403)·복사/건너뜀 수·팀원 편입·재실행 409·감사.

## 16. 자료 URL 검사 — `app/saas/assetguard.py`

멤버십 통합 중 발견: 자료 URL을 검사 없이 받아 엔진이 그대로 읽었다 — `ir_deck`에 로컬 경로(서버 파일 읽기),
남의 워크스페이스 `supabase://` 경로, website·article에 내부 주소(169.254.169.254·localhost·사설망 = SSRF).
이제 받는 시점에: `ir_deck`은 `supabase://<내 워크스페이스>/`만, 웹 자료는 http(s)만 + 이름이 **전부 공개 IP**로
풀릴 때만.

받는 시점 검사만으로는 공개 주소가 내부로 **리다이렉트**하거나 검사 뒤 **DNS가 바뀌는**(rebinding) 경우가 남았다.
이건 가져오는 계층이 연결 시점에 막는다 — `app/ingest/netguard.py`. website·article·portfolio(`fetch_url`,
`crawl_website` — robots.txt·하위 페이지·딥리드 크롤 포함)와 http(s) PDF(`fetch_pdf_bytes`)가 모두 이 클라이언트를 쓴다:
- 연결할 때마다 이름을 직접 풀고, 결과가 **전부** 공개 IP일 때만 **그 IP로** 접속한다(httpcore 네트워크 백엔드 교체).
  검사한 IP = 접속한 IP라 rebinding이 끼어들 틈이 없다. Host 헤더·TLS SNI·인증서 검증은 원래 이름 그대로
- 리다이렉트는 최대 5홉, 홉마다 새 연결이 같은 검사를 받는다. http(s) 외 스킴 거부. 환경변수 프록시 무시
- IPv4를 감싼 IPv6(`::ffff:`, NAT64 `64:ff9b::/96`)는 속 주소로 판정. IP 판정은 assetguard와 같은 함수(`ip_allowed`)
- 연결 5초·읽기 `INGEST_FETCH_TIMEOUT`·요청당 전체 시간 상한(그 2배), 응답 크기 상한(`INGEST_FETCH_MAX_BYTES`, 기본 5MB; PDF 50MB)

인스타그램은 사용자 URL로 접속하지 않는다(계정 이름만 뽑아 고정 주소 Apify API로) — 대상 밖.
시험 `tests/test_ingest_ssrf.py`(12건): 127.0.0.1·169.254.169.254·file:로 302 차단(내부 서버 요청 0건),
무한 302 홉 상한, 사설 IP로 풀리는 이름 차단, rebinding(1차 공개→2차 루프백) 차단, 리터럴 내부 IP, 정상 공개 URL은
검사한 IP로 접속·Host 헤더 보존, 크기 상한.

## 17. 로컬 실행

```bash
# Postgres 두 셀 + Redis (예: Homebrew)
initdb -D /tmp/pg-a -U postgres --auth=trust && initdb -D /tmp/pg-b -U postgres --auth=trust
LC_ALL=C pg_ctl -D /tmp/pg-a -o "-p 55432" start && LC_ALL=C pg_ctl -D /tmp/pg-b -o "-p 55433" start
redis-server --port 56379 --daemonize yes

# 시험
CHAT_TEST_PG_A=postgresql://postgres@127.0.0.1:55432 \
CHAT_TEST_PG_B=postgresql://postgres@127.0.0.1:55433 \
  .venv/bin/python -m pytest tests/test_chat_platform.py -q

# 부하 (DB 생성 후)
export CHAT_CONTROL_DSN=postgresql://postgres@127.0.0.1:55432/control \
       CELL_A_DSN=postgresql://postgres@127.0.0.1:55432/cell_a \
       CELL_B_DSN=postgresql://postgres@127.0.0.1:55433/cell_b
python scripts/chat_loadtest.py setup --tenants 100 --users 10
# 서버: CHAT_FANOUT=redis CHAT_REDIS_URL=… SAAS_AUTH=dev SAAS_ALLOWED_USERS=… uvicorn … --workers 4
python scripts/chat_loadtest.py run --rate 200 --duration 90
# 게시기는 여러 프로세스로 나눌 것(단일 프로세스는 호스트 CPU가 모자랄 때 측정을 오염시킨다)

# Supabase 실연결 (Docker/colima 필요)
supabase start
psql "$DB_URL" -f app/chat/sql/realtime_policy.sql
SUPA_API_URL=… SUPA_ANON_KEY=… SUPA_SERVICE_ROLE_KEY=… SUPA_JWT_SECRET=… \
  .venv/bin/python -m pytest tests/test_supabase_live.py -q
```
