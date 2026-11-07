-- 컨트롤 플레인 — 플랫폼 전체의 원장. 메시지 본문은 여기에 없다(셀에 있다).
--
-- 무엇이 여기 있고 무엇이 없나:
--   있음: 셀 목록, 테넌트 → 셀 배치, 사용자 → 테넌트 색인, 플랫폼 어드민,
--         플랫폼 감사 로그, 테넌트별 분 단위 메트릭, 알림, 운영 채널
--   없음: 테넌트의 채널·메시지·멤버 권한 — 각 셀의 테넌트 스키마가 원본이다.
-- 컨트롤 플레인이 죽어도 이미 라우팅 캐시를 가진 인스턴스는 대화를 계속한다.

create table if not exists cells (
  id            text primary key,              -- 'a', 'b', 'ent-acme'
  dsn_env       text not null,                 -- DSN을 담은 환경변수 이름 (비밀은 DB에 두지 않는다)
  region        text not null default 'ap-southeast-1',
  status        text not null default 'open'   -- open | full | draining | down
                check (status in ('open','full','draining','down')),
  dedicated     boolean not null default false, -- 전용 셀: 자동 배치 대상에서 제외
  max_tenants   int not null default 50,
  created_at    timestamptz not null default now()
);

create table if not exists tenants (
  id                    uuid primary key default gen_random_uuid(),
  slug                  text not null unique check (slug ~ '^[a-z][a-z0-9_]{1,40}$'),
  name                  text not null,
  cell_id               text not null references cells(id),
  schema_name           text not null,
  plan                  text not null default 'standard'
                        check (plan in ('standard','enterprise')),
  status                text not null default 'provisioning'
                        check (status in ('provisioning','active','suspended','migrating','deleted')),
  schema_version        int not null default 0,
  retention_hot_months  int not null default 12 check (retention_hot_months between 1 and 120),
  legal_hold            boolean not null default false,
  -- 아카이브 영구 삭제 시점(개월). null = 무기한 보존(기본). 법적 보존 중이면 무시된다.
  archive_purge_months  int check (archive_purge_months is null or archive_purge_months >= 12),
  created_at            timestamptz not null default now(),
  unique (cell_id, schema_name)
);
create index if not exists tenants_cell on tenants(cell_id) where status <> 'deleted';

-- 사용자 → 테넌트 색인. 한 사용자가 여러 테넌트(게스트 포함)에 속할 수 있다.
-- 역할·속성은 여기 없다 — 테넌트 스키마의 members가 원본이다(권한 판정은
-- 셀 안에서 끝나야 컨트롤 플레인 장애가 권한 판정을 멈추지 않는다).
create table if not exists tenant_users (
  tenant_id   uuid not null references tenants(id) on delete cascade,
  user_id     text not null,
  email       text not null,
  added_at    timestamptz not null default now(),
  primary key (tenant_id, user_id)
);
create index if not exists tenant_users_user on tenant_users(user_id);

create table if not exists platform_admins (
  user_id     text primary key,
  email       text not null,
  role        text not null default 'operator'
              check (role in ('superadmin','operator','viewer')),
  added_at    timestamptz not null default now()
);

-- 플랫폼 감사 로그 — 추가만 한다. 어드민의 테넌트 본문 열람은 전부 여기 남고,
-- 같은 내용이 해당 테넌트 스키마의 access_log에도 남아 테넌트 관리자가 본다.
create table if not exists platform_audit (
  id          bigserial primary key,
  at          timestamptz not null default now(),
  actor       text not null,
  action      text not null,
  tenant_id   uuid,
  target      text,
  reason      text,
  detail      jsonb not null default '{}'
);
create index if not exists platform_audit_tenant on platform_audit(tenant_id, at desc);
create index if not exists platform_audit_at on platform_audit(at desc);

-- 분 단위 메트릭 롤업. 인스턴스는 요청마다 쓰지 않고, 메모리 버킷을 주기적으로
-- 합산(upsert)한다 — 같은 (tenant, minute, route)에 여러 인스턴스가 더해도
-- 카운트는 원자 가산이고, 지연 분포는 히스토그램 버킷을 더하므로 합산 가능하다
-- (p95 같은 분위수를 그냥 평균내면 틀린다).
create table if not exists metrics_minute (
  tenant_id   uuid not null,
  minute      timestamptz not null,
  route       text not null,
  requests    bigint not null default 0,
  errors      bigint not null default 0,
  -- 지연 히스토그램: 경계 [5,10,25,50,100,250,500,1000,2500,inf] ms
  lat_hist    bigint[] not null default array_fill(0::bigint, array[10]),
  primary key (tenant_id, minute, route)
);
create index if not exists metrics_minute_minute on metrics_minute(minute desc);

create table if not exists alerts (
  id          bigserial primary key,
  tenant_id   uuid,
  cell_id     text,
  kind        text not null,
  severity    text not null default 'warning' check (severity in ('info','warning','critical')),
  opened_at   timestamptz not null default now(),
  closed_at   timestamptz,
  detail      jsonb not null default '{}'
);
-- 같은 대상의 같은 종류 알림은 열린 것이 하나뿐이다 — 평가가 반복돼도 중복 생성 안 함
create unique index if not exists alerts_open_once
  on alerts(coalesce(tenant_id::text, ''), coalesce(cell_id, ''), kind) where closed_at is null;

-- 플랫폼 운영 채널(#ops, #incidents 등) — 플랫폼 어드민끼리의 대화.
create table if not exists ops_channels (
  id          bigserial primary key,
  name        text not null unique,
  last_seq    bigint not null default 0,
  created_at  timestamptz not null default now()
);
create table if not exists ops_messages (
  channel_id  bigint not null references ops_channels(id),
  seq         bigint not null,
  author      text not null,
  body        text not null,
  kind        text not null default 'text',
  created_at  timestamptz not null default now(),
  primary key (channel_id, seq)
);

-- 전체 공지 — 셀을 넘어 모든 테넌트에 전달. 테넌트별 전달 상태를 남겨
-- 일부 셀이 실패해도 재시도가 이미 전달된 테넌트에 중복 게시하지 않는다.
create table if not exists announcements (
  id          bigserial primary key,
  author      text not null,
  body        text not null,
  created_at  timestamptz not null default now()
);
create table if not exists announcement_delivery (
  announcement_id  bigint not null references announcements(id),
  tenant_id        uuid not null references tenants(id),
  delivered_at     timestamptz,
  error            text,
  primary key (announcement_id, tenant_id)
);

create table if not exists control_meta (
  key   text primary key,
  value text not null
);

-- 테넌트 셀 이동 기록 — 한 테넌트에 진행 중인 이동은 하나뿐이다.
-- 진행 중인 이동이 있으면 아카이빙·파티션 유지가 그 테넌트를 건너뛴다(복사 중에
-- 원본 파티션이 사라지면 복사본과 원본이 어긋난다).
create table if not exists tenant_moves (
  id            bigserial primary key,
  tenant_id     uuid not null references tenants(id),
  from_cell     text not null references cells(id),
  to_cell       text not null references cells(id),
  state         text not null default 'copying'
                check (state in ('copying','fencing','switched','retired','failed','rolled_back')),
  actor         text not null,
  started_at    timestamptz not null default now(),
  fenced_at     timestamptz,
  switched_at   timestamptz,
  finished_at   timestamptz,
  report        jsonb not null default '{}',
  error         text
);
create unique index if not exists tenant_moves_one_active
  on tenant_moves(tenant_id) where state in ('copying','fencing');

-- 이동을 배경 작업으로(2026-10-21): 대기열·하트비트·단계·취소. 이미 만든 DB에도
-- 그대로 돌릴 수 있게 전부 멱등(add column if not exists, 제약 재정의).
alter table tenant_moves add column if not exists worker text;
alter table tenant_moves add column if not exists heartbeat_at timestamptz;
alter table tenant_moves add column if not exists phase text not null default 'queued';
alter table tenant_moves add column if not exists cancel_requested boolean not null default false;
alter table tenant_moves alter column state set default 'queued';
alter table tenant_moves drop constraint if exists tenant_moves_state_check;
alter table tenant_moves add constraint tenant_moves_state_check
  check (state in ('queued','copying','fencing','switched','retired','failed','rolled_back','cancelled'));
drop index if exists tenant_moves_one_active;
create unique index if not exists tenant_moves_one_active2
  on tenant_moves(tenant_id) where state in ('queued','copying','fencing');
