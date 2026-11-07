# Work ID · SSoT 임시 운영

코드: `workid.js`(공통), `worktypes.js`(업무 유형 SSoT), `ssot.js`(임시 발행), `invest/*`(메리 딜룸), `invest/onto.js`(업무 지도)

## 한 줄
업무 지도는 SSoT가 아니다. 업무 지도는 **각 업무 유형의 SSoT를 가리키는 색인**이다. 탐색(대화·AI 초안)과 공식(사람의 Commit)을 구분해 남긴다.

## 구성

| 개념 | 이 브라우저에서 | 위치 |
|---|---|---|
| Work ID (FRN·PRN·IRN/INV·EIR·fund_id) | 투자 건마다 Work Context를 둔다. Primary Anchor는 FRN이고, 없으면 PRN을 쓴다. 형식이 틀린 값은 버리지 않고 `invalid`로 남긴다 | `workid.workContext`, 딜 머리 "Work ID" |
| 같은 ID는 같은 노드 | 투자 건 → `company:<FRN>`, `prn:<PRN>`, `fund:<id>`, `disb:<IRN/INV>`. 에이전트가 EMA에서 본 기업 노드와 같은 id라, 기업·사업 단위로 모인다 | `invest/onto.js` |
| 계층 | 모든 딜 이벤트에 `mode`를 붙인다: `conversation`(사람 탐색) · `shadow`(AI 초안) · `committed`(공식) | `invest/store.js` `write()` |
| 명시적 Commit (`G-COMMIT`) | 결정은 `proposed`로 시작한다. 사람이 역할(PM·책임자 / 투자·재무 검증자 / 중앙 관리자)을 골라 **공식화**하면 `invest.commit`이 남고, 지도에 ✔와 "공식화함" 관계가 생긴다. AI는 공식화할 수 없다 | 논의·결정 탭 "공식화" |
| AI의 범위 | 메리는 홈 화면 에이전트와 같은 기준으로 일한다. 읽기·분석·초안·웹 검색을 하고, 사내 팀 채널 게시는 대화창에서 승인받은 뒤에만 한다. 외부 발송·결정 공식화·KPI 실적 인정은 하지 않는다 | `invest/pipeline.js` POLICY |
| 이벤트 | `correlation_id`(= `deal-<id>`), `work`, `mode`를 이벤트 `data`에 넣는다 | `events.js` 봉투 + `invest` |
| 업무 유형마다 SSoT | 업무 유형에 `ssot` {system, anchor, related, guards, authority, note}를 붙인다. 이름으로 기본값을 고르고(`defaulted`), 맞는 패턴이 없으면 "SSoT 미정"으로 둔다. 사람이 `setSsot`로 정한 값만 확정이다 | `workid.ssotFor`, `worktypes.js` |
| 권한 매트릭스 | 업무별 AI·PM·검증자·관리자의 몫을 `AUTHORITY`에 둔다. 공식화할 때 역할을 남긴다 | `workid.js` |
| 임시 발행 | 업무 단위(작업 WE · 투자 건 DL · 결정 DC · 지시 DR)에 `TMP-<종류>-<YYMM>-<순번>`을 자동 발행한다. 한 번 붙은 ID는 바뀌지 않는다. 원문은 담지 않고 업무 지도 노드만 가리킨다 | `ssot.js`, 업무 지도 위쪽 막대·노트 |

## 한계
- 공식화 역할은 아직 자기 신고다(RBAC 아님). SSO·조직 역할이 붙기 전까지 "공식"은 "누가 어떤 역할로 확인했다는 기록"으로 읽는다.
- `company:<FRN>`을 노드 id로 쓴다. 정식 Registry가 생기면 내부 UUID 매핑으로 옮겨야 한다.
- 업무 유형 SSoT의 기본값은 이름 패턴으로 추정한 값이다.
- 임시 ID는 정식 정책이 확정되면 Registry 정식 ID로 옮긴다(external id mapping).
