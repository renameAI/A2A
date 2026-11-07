-- 테넌트 스키마 v4 — A2A 브라우저의 업무 이벤트(수집 계층, browser/docs/EVENTS.md)를 워크스페이스로 올린다.
--
-- 브라우저는 로컬 append-only 로그를 그대로 두고, 사용자가 켠 범위(mode)만 올린다:
--   signals  행동 신호만(종류·도구 이름·승인/거절·하네스 버전·작업 id·시각) — 원문·주소·해시 없음
--   hashes   + 원문 해시·문서 종류·주소의 호스트
--   full     + 마스킹한 원문(work_blobs) — 서버에서 재실행 비교(하네스 개선)를 하려면 필요
-- 같은 event_id는 한 번만 들어온다(재전송 안전). 원문은 해시가 이름이고, 서버가 해시를 다시 계산해 맞을 때만 받는다.
-- 행은 RLS로 본인 것만 보이고, 워크스페이스 전체는 audit.read 권한이 있는 사람이 관리 경로로 본다.
set local search_path = __SCHEMA__;
set local role __OWNER__;

create table if not exists work_events (
  event_id     text primary key,
  user_id      text not null,
  recorded_at  timestamptz not null,
  kind         text not null,
  task_id      text,
  mode         text not null check (mode in ('signals','hashes','full')),
  body         jsonb not null,
  received_at  timestamptz not null default now()
);
create index if not exists work_events_user_time on work_events (user_id, recorded_at);
create index if not exists work_events_task on work_events (task_id) where task_id is not null;

create table if not exists work_blobs (
  hash         text primary key,              -- sha256 hex (마스킹·정규화 뒤 원문)
  body         text not null,
  first_user   text not null,
  received_at  timestamptz not null default now()
);

alter table work_events enable row level security;
alter table work_blobs enable row level security;
drop policy if exists work_events_own on work_events;
create policy work_events_own on work_events
  using (user_id = current_setting('chat.uid', true))
  with check (user_id = current_setting('chat.uid', true));
drop policy if exists work_blobs_own on work_blobs;
create policy work_blobs_own on work_blobs
  using (first_user = current_setting('chat.uid', true))
  with check (first_user = current_setting('chat.uid', true));

-- 앱 역할은 읽기만(RLS로 본인 것). 쓰기는 권한 판정 뒤 관리 경로로만 — 다른 v1 표와 같은 규칙
reset role;
-- 다른 역할로 먼저 만들어졌던 경우에도 소유자를 테넌트 owner로 맞춘다(관리 경로가 쓸 수 있게)
alter table __SCHEMA__.work_events owner to __OWNER__;
alter table __SCHEMA__.work_blobs owner to __OWNER__;
grant select on __SCHEMA__.work_events, __SCHEMA__.work_blobs to __APP__;
