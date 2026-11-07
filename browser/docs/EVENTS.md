# 업무 이벤트 수집 — 스키마와 규칙

A2A 브라우저를 지나가는 업무(웹 탭, 에이전트 작업, 승인, 팀 채팅)를 **출처·시간·맥락이 붙은 원자료**로 남기는 계층이다.
지식 그래프나 "검증된 사실"을 만드는 곳이 아니다. 사내 온톨로지 계획(MYSCdoc 6-pager)의 주장 튜플로 나중에 올릴 수 있는
원자료와 실행 궤적을 빠짐없이, 중복 없이 쌓는다.

코드: `events.js`(수집기·저장소) · `collect.config.json`(제외 도메인 등 설정) · `events-cli.js`(보기·내보내기·검증) ·
`collect-adapters.js`(다음 단계 어댑터 인터페이스) · `selftest-events.js`(자체 점검)

## 원칙 (계획서에서 가져온 것)

1. **이벤트 수집은 검증된 지식과 분리한다.** 이 로그의 어떤 줄도 "참"이라는 뜻이 아니다. 계획서 식 (12)의 `Valid`·`label ∈ {0,1}`·`verified(r)=1` 단계는 이 계층 밖에 있다.
2. **승인 기록은 독립 라벨이 아니다.** 작업 중 사용자가 누른 승인/거절은 `label_kind: "approval_signal"`로만 남는다. 식 (3)의 독립 라벨 D_t(도메인 검토자 2명이 플랫폼을 가리고 판정, κ 통제)와 섞이지 않게 한다.
3. **에이전트 산출물이 다시 증거로 들어오는 순환을 막는다.** 식 (10) 아래 문장("인용·복제는 독립 증거로 중복 집계하지 않는다")과 §6("복제 문서는 독립 증거 수를 늘리지 않아야")의 수집 시점 구현이다. 아래 `derived_from`, `observation`, `evidence_candidate` 참고.

## 저장 형식: JSONL + 파일 블롭 (SQLite 아님)

| | JSONL(고름) | SQLite |
|---|---|---|
| append-only 보장 | 파일에 한 줄 덧붙이기뿐 — 수정·삭제 API 자체가 없다 | UPDATE/DELETE가 가능해서 규율로 막아야 한다 |
| 강제 종료 | 마지막 줄만 잘린다(읽을 때 건너뜀) | WAL이 보호하지만 네이티브 모듈이 필요 |
| 의존성 | 없음(Electron 45의 node 내장만) | `better-sqlite3`는 Electron ABI 재빌드, 내장 `node:sqlite`는 실험적 |
| 검사·전달 | `jq`·`grep`·`export`로 그대로 다음 단계(추출·라벨링)의 입력 | 쿼리는 편하지만 내보내기 단계가 필요 |
| 약점 | 날짜 파일을 훑어야 한다(하루 수천 줄 규모면 문제없음) | — |

수집 계층에는 쿼리 성능보다 **고쳐 쓸 수 없다는 것**과 **받는 쪽이 읽기 쉽다는 것**이 중요하다. 규모가 커지면 JSONL을 읽어 색인(SQLite)을
**파생물로** 만들면 된다 — 원본은 JSONL로 남는다. (남은 결정 참고)

```
<userData>/events/
  log/YYYY-MM-DD.jsonl        이벤트 한 줄씩, append-only (날짜는 UTC)
  blobs/ab/<sha256>.txt       원문(마스킹 뒤) — 파일 이름이 콘텐츠 해시 → 같은 원문은 한 번만
  blobs/ab/<sha256>.json      그 원문을 처음 본 이벤트(first_event)·시각·출처 종류
  chat-seen.jsonl             이미 본 채팅 메시지(tenant:cid:seq → 해시) — 다시 켜도 같은 메시지를 또 쌓지 않는다
```

`userData`는 실제 앱에서 `~/Library/Application Support/a2a-browser`, 자체 점검에서는 `<A2A_SELFTEST_DIR>/profile`.
블롭은 임시 파일에 쓴 뒤 rename한다(반쯤 쓰인 원문이 없다). 이벤트는 원문을 담지 않고 `content_hash`로만 가리킨다.

## 이벤트 봉투 (모든 이벤트가 가진 필드)

| 필드 | 뜻 |
|---|---|
| `schema_version` | 지금 1. 필드를 바꾸면 올린다 |
| `event_id` | `ev_<시각>_<세션>_<순번>` |
| `session_id` | 앱을 켠 한 번 |
| `kind` | `web.snapshot` `web.selection` `agent.instruction` `agent.read` `agent.tool_call` `agent.text` `agent.page` `agent.note` `agent.output_ref` `agent.run_end` `approval.decision` `chat.message` `chat.deleted` `collector.start`, 어댑터는 `<platform>.<동사>` |
| `recorded_at` | **b 기록 시점** — 우리가 본 시각(ISO, UTC). 원문 쪽 시각은 `observed_at`(채팅 `created_at` 등)로 따로 둔다 |
| `actor_kind` | `human` / `agent` — 이 행동을 한 쪽 (시스템 이벤트는 `system`) |
| `actor` | 사람 id 또는 `agent:<사용자>` |
| `content_origin` | 원문을 쓴 쪽: `external`(웹) / `human` / `agent`. 같은 `actor_kind: human`이어도 **사람이 붙여 보낸 에이전트 글**은 `content_origin: agent` |
| `user`, `tenant` | 이 브라우저의 프로필 |
| `platform` | ζ — `web` · `a2a-chat` · `a2a-browser` · (어댑터) `myscube` 등 |
| `doc_type` | ζ — `web_page` `selection` `chat_message` `instruction` `agent_answer` `agent_narration` `result_page_spec` `tool_call` `approval` … |
| `author` | ζ — 원문 작성자(채팅 작성자, 지시한 사용자, 에이전트) |
| `approval_status` | ζ — 승인 이벤트에서 `approved`/`rejected`. (플랫폼 결재 상태는 어댑터가 채움) |
| `source_url` | 원문 위치. 웹은 주소, 채팅은 `a2a-chat://<tenant>/<채널>/<seq>`. 제외 도메인이면 `null` |
| `title` | 문서 제목 |
| `content_hash` | **v 원문 버전** — `sha256:<64 hex>` (마스킹·정규화 뒤 원문). 없으면 `null` |
| `content_bytes` | 원문 크기 |
| `span` | **π 원문 구간** — `{kind, start, end, within, truncated}` |
| `observation` | `first`(이 해시를 처음 봄) / `revisit`(같은 해시를 다시 봄) / `echo`(처음엔 에이전트 글로 본 해시가 사람 글로 다시 들어옴) |
| `first_event` | `revisit`·`echo`일 때 그 원문을 처음 기록한 이벤트 |
| `evidence_candidate` | 증거 후보로 셀 수 있는가. **`observation:first` 이고 `content_origin`이 에이전트가 아닐 때만 true.** 재방문·메아리·에이전트 글은 항상 false |
| `task_id` | **c 맥락** — 에이전트 작업(`t<ms>`). 모르면 `null` |
| `task_bound` | `task_id`가 있는가 (`task_id === null`의 다른 표현 — 필터용) |
| `context` | **c 맥락** — `instruction`(지시 이벤트), `thread`(채널·부모 메시지), `tab_id`, `reason` 등 |
| `derived_from` | 이 산출물이 근거로 쓴 원문 해시 목록(`sha256:…`). 아래 참고 |
| `derived_from_unknown` | (있으면 true) 에이전트 산출물인데 근거를 알 수 없다 — 다른 사람의 에이전트 글 |
| `label_kind` | 승인 이벤트만 `approval_signal`. 독립 라벨이 아니다 |
| `data` | 종류별 덧붙임(도구 이름·입력·결과 요약 등). 마스킹·제외 규칙이 같이 적용된다 |
| `masked` | 마스킹된 건수 `{card, rrn, secret}` (원문은 남기지 않고 건수만) |

## 계획서 식 (1)의 주장 튜플과 대응

계획서: 주장 `a = (s, r, o, I, b, m, π, v, c, ζ)` — 주어 s, 관계 r, 목적어 o, 유효 구간 I, 기록 시점 b, 양태 m, 원문 구간 π, 원문 버전 v, 전후 맥락 c, 출처 정보 ζ.

| 튜플 | 수집 시점에 채우나 | 이벤트 필드 |
|---|---|---|
| **s** 주어 | ✗ 추출 단계 | — |
| **r** 관계 | ✗ 추출 단계 | — |
| **o** 목적어 | ✗ 추출 단계 | — |
| **I** 유효 구간 | ✗ 추출 단계 (원문 속 "2027년 1월부터…"는 원문에 있고, 해석은 나중) | `observed_at`은 I가 아니다 — 원문 쪽 시각 단서일 뿐 |
| **m** 양태 | ✗ 추출 단계 (의견·제안·관찰·확정·철회) | — |
| **b** 기록 시점 | ✓ | `recorded_at` (+ 있으면 `observed_at`) |
| **π** 원문 구간 | ✓ 구간의 "자리"만 | `span`: 웹 전체 `page_text{start,end}`, 선택 `selection{within, start, end}`(그 탭 마지막 본문 해시 안의 위치), 채팅 `message`, 에이전트가 읽은 범위 `page_text{end: 12000}` / `viewport_image`. **주장이 가리키는 문장 단위 π는 추출 단계가 이 `span` 안에서 좁힌다** |
| **v** 원문 버전 | ✓ | `content_hash` — 같은 주소의 내용이 바뀌면 해시가 바뀌므로 버전이 구분된다. 같은 내용은 주소가 달라도 같은 해시 |
| **c** 맥락 | ✓ 일부 | `task_id`, `context.instruction`(지시 이벤트), `context.thread`(채널·스레드), `context.tab_id`, `user`/`tenant`. 질문(competency question)과 루틴 id는 어댑터가 `context.question`·`context.routine`으로 넣는다 |
| **ζ** 출처 정보 | ✓ | `platform`, `doc_type`, `author`/`actor`, `approval_status`, `content_origin`, `source_url` |

계획서 §3의 기억 레코드(식 12)가 요구하는 "원문 해시·버전·후보·근거·독립 라벨·실패유형·시점" 중 이 계층이 채우는 것은
**해시·버전·시점·근거(`derived_from`)** 뿐이다. 후보·독립 라벨·실패유형은 다음 단계에서 붙는다.

## 중복 제거: 같은 해시는 증거를 늘리지 않는다

- 원문은 정규화(줄바꿈 통일, 줄 끝 공백 제거, 앞뒤 공백 제거)와 마스킹 **뒤에** SHA-256으로 이름이 붙는다.
- 같은 해시가 이미 있으면 블롭을 쓰지 않고 `observation: revisit` + `first_event`로만 기록한다. `evidence_candidate`는 false.
- **복제 문서는 주소가 달라도 같은 해시** — 독립 증거가 아니다(§6). 이 계층은 "같은 해시"까지만 안다. 내용이 조금 다른 사본·인용·요약은
  다른 해시이므로 근접 중복(군집) 판단은 다음 단계(계획서 §4 "동일 사건·동일 프로젝트·질문 변형 군집")가 한다.
- 같은 탭·같은 주소·같은 해시가 1분 안에 다시 오면(SPA의 해시 이동 등) 이벤트 자체를 늘리지 않는다.
- 채팅은 `tenant:채널:seq → 해시`를 기억해서 목록을 다시 불러와도, 앱을 다시 켜도 같은 메시지를 또 쌓지 않는다. 수정(해시 변경)과 삭제는 `data.revision`·`chat.deleted`로 새 이벤트가 된다.

## 에이전트 산출물 순환 방지

에이전트 글이 근거 없이 "사람이 쓴 증거"처럼 다시 들어오는 경로 셋을 각각 막는다.

1. **`derived_from`**: 에이전트가 쓴 글·결과 페이지 명세·채널 게시는 그 작업이 근거로 본 원문 해시를 단다.
   - 근거 = 에이전트가 `read_page`/`screenshot`으로 읽은 탭의 본문 해시 + 사용자가 `@`로 붙인 탭·채팅·이전 결과의 해시 + 지시 자신의 해시.
   - 채널에서 불렀다면 읽어 준 최근 대화 메시지 해시도 근거다.
   - 에이전트는 12,000자까지만 본다(`page-tools.js` MAX_TEXT). 본문 해시는 전체 본문이고, 읽은 범위는 `agent.read`의 `span.end`에 있다.
2. **`content_origin: agent` + `evidence_candidate: false`**: 에이전트 글은 처음 봐도 증거 후보가 아니다.
3. **`echo`**: 에이전트가 쓴 글이 사람 글로 그대로(또는 일부로) 팀 채팅에 올라오면 — 해시가 같으면 `observation: echo`, 안에 에이전트 글이 들어 있으면 `data.contains_agent_output: true` + `derived_from`에 그 산출물 해시 — 후보에서 뺀다.
   - 다른 사람의 에이전트가 올린 글(`🤖`로 시작)은 우리 쪽에서 근거를 모른다: `actor_kind: agent`, `derived_from_unknown: true`.

한계: 에이전트가 쓴 문장을 사람이 **다시 타이핑**하거나 요약해서 올리면 해시가 다르므로 이 계층은 알아채지 못한다. 그 판단(군집·의미 중복)은 추출/라벨링 단계 몫이다.

## 맥락: task_id가 null일 때 (리뷰 노트 C4)

`runAgent`의 경쟁 조건 때문에 `currentTask`가 도중에 `null`이 될 수 있다. 수집은 `currentTask`를 믿지 않는다.
- 작업 id는 `runAgent`가 `agent.run`을 부르는 순간 `agentStart({taskId})`로 **고정**한다(실행이 끝날 때까지). 실행 중 이벤트는 그 값을 쓰고, 값이 없을 때만 `emitAgent`의 `currentTask?.id`를 쓴다.
- 그래도 모르면 `task_id: null`, `task_bound: false`. 에이전트 이벤트에서 `null`이 나오면 안 되는 종류(`agent.text` 등)가 있는지는 `events-cli.js stats`의 `task_unbound_agent`로 센다.
- 이미 다른 실행이 돌고 있어 **실행되지 않은 지시**(C4의 사라지는 요청)는 지시 이벤트에 `data.dropped: true`로 남고, 실행 상태를 바꾸지 않는다 — 돌고 있는 작업의 task_id를 덮어쓰지 않는다.
- 이 수집 때문에 리뷰 문제를 고치지는 않았다. 수집은 그 문제들이 있어도 돈다.

## 제외와 마스킹

**기록하지 않는 것 (절대)**
- **비밀번호 칸 값**: 수집기는 페이지의 `innerText`만 읽는다(`input` 값은 `innerText`에 들어가지 않는다). 에이전트의 `type` 도구가 비밀번호 칸이라 거부된 경우 입력한 글도 `[비밀번호 칸 — 기록 안 함]`으로 바꾼다. 금고(`vault.js`·`passwords.js`)는 수집과 연결되지 않는다.
  - 한계: 사용자가 "비밀번호 보기"를 눌러 `type=text`로 바뀐 칸의 값, 또는 페이지가 비밀번호를 **본문 글자로** 보여 주는 경우는 구별할 수 없다(리뷰 노트 A4와 같은 한계). 이런 사이트는 제외 도메인에 넣는다.
- **제외 도메인**의 웹 본문·제목·주소·선택 구간, 에이전트가 거기서 읽은 내용, 도구 입력과 결과 속의 그 도메인 주소(`[제외 도메인]`으로 바뀜).

**제외 도메인 목록** — `collect.config.json`의 `exclude_domains` 한 곳. 도메인과 그 하위 도메인 전체에 적용된다.
기본값: 개인 메일(Gmail·Naver·Daum·Outlook·Yahoo·iCloud·Proton), 은행·결제(KB·신한·우리·하나·IBK·농협·카카오뱅크·토스·PayPal·카카오페이 등), 정부·건강보험·국세(홈택스·정부24·건보·국민연금).
바꾸면 앱을 다시 켠다. 같은 파일에서 `capture`로 종류별 수집을 끄고(`web`/`agent`/`approval`/`chat`), `exclude_channels`로 팀 채널 하나를 뺄 수 있다.
`A2A_COLLECT=0`으로 앱을 켜면 수집 전체가 꺼진다. 필요 없는 사이트가 더 있으면 직원이 이 목록에 추가한다.

**마스킹 (저장 전, 해시 전)** — 이벤트 필드와 블롭 모두에 마스킹된 글만 남는다.
| 패턴 | 규칙 | 대체 |
|---|---|---|
| 카드번호 | 13~19자리(공백·하이픈 허용) **+ Luhn 검사 통과** | `[카드번호]` |
| 주민·외국인등록번호 | `YYMMDD-[1~8]XXXXXX` (월·일 모양 검사) | `[주민번호]` |
| API 키·토큰 | `sk-…` `ghp_…` `xox?-…` `AKIA…` | `[비밀키]` |

한계: 정규식 규칙이다. 띄어쓰기가 다른 표기, 이미지 속 번호, 은행 계좌번호·여권번호는 잡지 못한다. 해시는 마스킹된 글로 계산하므로,
번호만 다른 같은 문서는 같은 해시가 될 수 있다 — 개인정보를 남기지 않는 쪽을 택한 결과다. 직원 동의와 제외 원칙은 이미 받은 것으로 둔다(이 계층이 다시 묻지 않는다).

## 수집 지점

| 지점 | 이벤트 | 어디서 |
|---|---|---|
| 웹 탭 navigation·문서 | `web.snapshot` — 로드가 끝나고 1.5초 뒤(동적 내용이 자리 잡게) 본문 텍스트를 해시로 저장. URL·제목·시각. 같은 해시면 `revisit` | `main.js` `attachView` → `collect.watchTab` |
| 선택한 글 | `web.selection` — 오른쪽 클릭 "AI에게 물어보기". π는 그 탭 본문 해시 안의 위치 | `contextTemplate` |
| 사용자 지시 | `agent.instruction` — 지시 글, 붙인 맥락 라벨, 근거(붙인 원문 해시) | `runAgent` → `collect.agentStart` |
| 도구 호출·결과 | `agent.tool_call` — 도구 이름, 입력, 성공 여부, 결과 요약(160자) | `emitAgent` → `collect.agentEvent` |
| 에이전트가 읽은 원문 | `agent.read` — 읽은 탭의 해시와 범위 → `derived_from`에 쌓임 | `agent.js` `read_page`/`screenshot` → `host.observed` |
| 에이전트 글 | `agent.text` — 도구 사이의 설명(`agent_narration`)과 최종 답(`agent_answer`) | `emitAgent` |
| 결과 페이지 명세 | `agent.page` — 명세 JSON을 블롭으로, 인용 URL 목록 | `emitAgent` (`create_page`) |
| 승인/거절 | `approval.decision` — `label_kind: approval_signal`, 어떤 동작이었는지 | `main.js` `confirm` |
| 팀 채팅 | `chat.message` — 보낸 글(`direction: sent`), 받은 글(`received`). 목록을 불러올 때·붙일 때·에이전트가 읽을 때 | `chatCall.messages/post`, `chatApi.post`, `mentionContext`, `channelContext` |

**보지 못하는 것**: 셸 바깥(다른 앱)과 SSE의 "변했다" 신호(본문이 없다 — 셸이 불러온 만큼만 남는다), 셸을 열지 않은 채널의 글,
사용자가 보지 않은 탭. 방에 들어가 불러온 글만 수집된다. 그래서 "빠짐없이"는 **이 브라우저를 지난 것**에 한해서다.

## 확인 방법

```bash
node events-cli.js                 # 최근 30건 (기본: 실제 앱 프로필)
node events-cli.js stats           # 종류별 건수, first/revisit/echo, 증거 후보 수, 마스킹 건수
node events-cli.js task t1730…     # 한 작업의 궤적: 지시 → 읽은 원문 → 도구 → 승인 → 산출물(derived_from)
node events-cli.js show 3fa9c1     # 원문 블롭과 메타
node events-cli.js export --since 2026-10-28 --with-content > events.jsonl   # 다음 단계 입력
node events-cli.js verify          # 해시가 맞는지, derived_from이 가리키는 원문이 있는지
node events-cli.js --dir <A2A_SELFTEST_DIR>/profile stats                  # 자체 점검 프로필
```

`--kind web.` `--task <id>` `--since <날짜>` `-n 50`으로 거를 수 있다. `*`는 증거 후보, `←n`은 `derived_from` 수.
`export`의 출력은 이벤트 JSONL 그대로이고, `--with-content`는 원문을 `content` 필드로 붙인다.

자체 점검:
```bash
cd browser
A2A_SELFTEST_DIR=/tmp/a2a-test A2A_SELFTEST_ONLY=events npm run dev      # 수집 점검만(약 40초)
A2A_SELFTEST_DIR=/tmp/a2a-test npm start                                  # 전체 점검 안에서 함께
A2A_SELFTEST_DIR=/tmp/a2a-test A2A_SELFTEST_AGENT=1 npm start             # 실제 에이전트 궤적(afterAgent)까지
```
결과는 `/tmp/a2a-test/report.json`의 `steps`(`events-…` 항목)에, 이벤트는 `/tmp/a2a-test/profile/events/`에 남는다. 사용 중인 앱 프로필과 섞이지 않는다.

## 어댑터 (다음 단계, 정의만)

`collect-adapters.js`가 MCP(큐브, 스테이션)와 사내 루틴 에이전트(dot, 메리)의 이벤트가 같은 스키마로 들어올 입구를 정의한다.
어댑터는 플랫폼 고유의 것(인증, 폴링/웹훅, 문서 모양)만 맡고, 마스킹·해시·중복 제거·`first/revisit/echo`·증거 후보 판정은 수집기가 한다.
- `ExternalRecord`: 위 봉투의 필드 중 어댑터가 채울 수 있는 것(`kind` = `<platform>.<동사>`, `platform`, `doc_type`, `author`, `approval_status`, `observed_at`, `content`+`content_origin`, `task_id`, `context`, `derived_from`).
- `validateRecord`가 막는 것: 브라우저 안쪽 kind(`web.`·`agent.`·`chat.`·`approval.`) 사칭, `content`가 있는데 `content_origin`이 없는 것, **에이전트 산출물인데 `derived_from`도 `derived_from_unknown`도 없는 것**, 승인 신호가 아닌 `label_kind`(독립 라벨 위조).
- 루틴 에이전트의 산출물(`content_origin: agent`)은 그 루틴이 읽은 원문의 해시를 `derived_from`으로 달고 들어와야 한다. 원문 쪽 어댑터는 `sink.hashOf(text)`로 같은 해시를 계산할 수 있다.

### 진입 라우터 (`collect-router.js`)

바깥 소스가 들어오는 단일 입구. `ingest(record)` → 등록된 `platform`인지(+허용 `kind`) → `validateRecord` → 수집기.
- `router.register({platform, kinds?})`: 등록한 플랫폼만 받는다. 브라우저 안쪽 이름(`web`·`a2a-chat`·`a2a-browser`)은 등록 불가. 지금 등록된 플랫폼은 없다.
- 로컬 HTTP(**기본 꺼짐**): `A2A_COLLECT_INGEST_PORT=<포트>`(0이면 임의)로 앱을 켜면 `127.0.0.1`에서 `POST /ingest`. `Authorization: Bearer <토큰>` 필수(토큰은 `userData/events/ingest.token`, 0600), `Origin` 헤더가 있으면 거절(웹페이지 차단), 본문 1MB 제한, `{records:[…]}`로 500건까지. 회사 PC의 다른 프로세스가 "증거"를 심을 수 있는 입구라 토큰 없이는 열지 않는다.
- 어댑터 구현(큐브·스테이션 MCP, dot·메리)은 이 라우터에 `register`하고 `ingest`만 부르면 된다. 아직 구현은 없다.

**가정(미확인)**: "dot·메리는 사내 루틴 에이전트, 스테이션은 ema-station, 큐브는 MYSCube"는 확인되지 않았다.
인터페이스는 이 가정에 기대지 않는다 — 바뀌는 것은 `platform` 문자열과 `doc_type` 목록뿐이다. 다만 확인이 필요한 것: (1) 루틴 에이전트가 자기 입력(읽은 문서)을 목록으로 줄 수 있는가 — 못 주면 `derived_from_unknown`이 된다,
(2) MCP 서버가 문서의 수정 시각·버전을 주는가(`observed_at`, `context.source_version`), (3) 사람 승인이 플랫폼 안에서 일어난다면 그 기록을 `approval_signal`로 들일지.

## 남은 결정

- **저장 형식**: JSONL로 시작. 하루 수만 건·수 GB를 넘거나 질의가 잦아지면 JSONL에서 SQLite 색인을 **파생**한다(원본은 JSONL).
- **보관·삭제 정책**: 지금은 지우지 않는다. 직원이 자기 이벤트를 지워 달라고 하면 어떻게 할지(로그는 append-only라 전체 재작성), 블롭의 보관 기간.
- **로그 암호화**: 마스킹은 하지만 업무 원문이 평문으로 userData에 있다. 디스크 암호화(FileVault)에 기대는지, `safeStorage`로 감쌀지.
- **서버 전송**: 지금은 로컬 전용이다. 사내 온톨로지 쪽으로 올리는 방법(주기 내보내기 vs 동기화)은 정해지지 않았다.
- **제외 목록 소유**: 회사 공통 목록과 개인 추가 목록을 나눌지.
- **웹 본문의 범위**: 로그인 뒤 페이지(사내 도구·CRM)를 얼마나 남길지. 지금은 제외 도메인 외에는 전부 남긴다 — 사내 도구 도메인을 제외하거나 허용 목록 방식으로 바꿀지.
