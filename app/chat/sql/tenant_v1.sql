-- 테넌트 스키마 v1 — 셀 DB 안에서 테넌트 하나가 쓰는 전부.
--
-- 자리표시자(프로비저너가 검증된 식별자로 치환한다):
--   __SCHEMA__  테넌트 스키마        (t_<slug>)
--   __OWNER__   객체 소유 역할       (t_<slug>_owner, NOLOGIN)
--   __APP__     앱 실행 역할         (t_<slug>_app,   NOLOGIN, RLS 적용 대상)
--
-- 격리 3중:
--   1) 스키마 — 테넌트마다 다른 이름공간
--   2) 권한(GRANT) — __APP__은 자기 스키마에만 USAGE가 있다. 질의가 남의 스키마
--      이름을 적어도 permission denied (search_path 실수로 새지 않는다)
--   3) RLS — 같은 테넌트 안에서도 비공개 채널은 멤버만, 게스트는 초대받은 채널만
-- 앱은 쓰기를 테이블에 직접 하지 않는다: INSERT/UPDATE/DELETE 권한이 없고
-- SECURITY DEFINER 함수만 실행할 수 있다. 함수가 순서(seq)·멱등·카운터를
-- 한 트랜잭션에서 원자적으로 처리한다.

create schema if not exists __SCHEMA__ authorization __OWNER__;
set local search_path = __SCHEMA__;
set local role __OWNER__;

create table schema_meta (key text primary key, value text not null);
insert into schema_meta values ('version', '1');

-- ── 권한 (ABAC) ─────────────────────────────────────────────────────
-- 역할은 권한 묶음이다. 기본 역할은 builtin=true로 지울 수 없지만 권한은
-- 테넌트 관리자가 바꿀 수 있다. 정책(policies)은 속성 조건으로 허용/거부를
-- 덧씌운다 — 거부 우선(deny overrides). 평가기는 app/chat/authz.py.
create table roles (
  name         text primary key check (name ~ '^[a-z][a-z0-9_]{1,30}$'),
  permissions  text[] not null default '{}',
  builtin      boolean not null default false,
  description  text not null default ''
);

create table members (
  user_id       text primary key,
  email         text not null,
  display_name  text not null default '',
  role          text not null references roles(name),
  attrs         jsonb not null default '{}',     -- {"department":"sales","region":"kr"}
  status        text not null default 'active' check (status in ('active','deactivated')),
  joined_at     timestamptz not null default now()
);

create table policies (
  id           bigserial primary key,
  effect       text not null check (effect in ('allow','deny')),
  actions      text[] not null,
  condition    jsonb not null default '{}',
  description  text not null default '',
  created_by   text not null,
  created_at   timestamptz not null default now()
);

-- ── 채널 ────────────────────────────────────────────────────────────
create table channels (
  id               bigserial primary key,
  name             text not null unique check (length(name) between 1 and 80),
  topic            text not null default '',
  visibility       text not null default 'public' check (visibility in ('public','private')),
  kind             text not null default 'channel' check (kind in ('channel','dm','announcements')),
  attrs            jsonb not null default '{}',
  created_by       text not null,
  created_at       timestamptz not null default now(),
  archived_at      timestamptz,
  last_seq         bigint not null default 0,   -- 채널 전체(답글 포함) 단조 증가 — 실시간 공백 감지 기준
  last_top_seq     bigint not null default 0,   -- 본문(답글 제외) 마지막 — 안 읽음 판정 기준
  last_message_at  timestamptz
);

create table channel_members (
  channel_id     bigint not null references channels(id) on delete cascade,
  user_id        text not null references members(user_id),
  role           text not null default 'member' check (role in ('member','channel_admin')),
  last_read_seq  bigint not null default 0,
  muted          boolean not null default false,
  joined_at      timestamptz not null default now(),
  primary key (channel_id, user_id)
);
create index channel_members_user on channel_members(user_id);

-- ── 메시지 (월 단위 파티션) ─────────────────────────────────────────
-- 파티션 키가 created_at이므로 PK에 포함된다. seq의 유일성은 제약이 아니라
-- channels 행 잠금으로 보장한다(post_message가 last_seq를 잠그고 올린다).
create table messages (
  channel_id     bigint not null,
  seq            bigint not null,
  parent_seq     bigint,                       -- 스레드 답글이면 루트의 seq
  author         text not null,
  body           text not null,
  kind           text not null default 'text' check (kind in ('text','activity','system')),
  created_at     timestamptz not null default now(),
  edited_at      timestamptz,
  deleted_at     timestamptz,
  deleted_by     text,
  reply_count    int not null default 0,
  last_reply_at  timestamptz,
  client_msg_id  text,
  primary key (channel_id, seq, created_at)
) partition by range (created_at);

create index messages_top on messages(channel_id, seq) where parent_seq is null;
create index messages_thread on messages(channel_id, parent_seq, seq) where parent_seq is not null;
create index messages_search on messages using gin (to_tsvector('simple', body));
-- 파티션이 하나도 없을 때의 안전망 — 파티션 관리가 밀려도 쓰기는 실패하지 않는다
create table messages_default partition of messages default;

-- 멱등 원장 — 클라이언트 재시도가 메시지를 두 번 만들지 않게. 파티션 테이블엔
-- (channel_id, client_msg_id) 유일 제약을 걸 수 없어(파티션 키 미포함) 따로 둔다.
-- 7일 지난 항목은 maintenance()가 지운다(재시도 창은 그보다 훨씬 짧다).
create table message_dedupe (
  channel_id     bigint not null,
  client_msg_id  text not null,
  seq            bigint not null,
  created_at     timestamptz not null default now(),
  primary key (channel_id, client_msg_id)
);

-- 멘션은 카운터가 아니라 행이다 — "안 읽은 멘션 수"는 last_read_seq보다 큰
-- 행을 세는 질의라, 동시 게시가 카운터를 덮어써 잃는 일이 원천적으로 없다.
create table mentions (
  user_id     text not null,
  channel_id  bigint not null,
  seq         bigint not null,
  created_at  timestamptz not null default now(),
  primary key (user_id, channel_id, seq)
);

create table reactions (
  channel_id  bigint not null,
  seq         bigint not null,
  user_id     text not null,
  emoji       text not null check (length(emoji) between 1 and 32),
  created_at  timestamptz not null default now(),
  primary key (channel_id, seq, user_id, emoji)
);

-- ── 감사·열람 기록 ──────────────────────────────────────────────────
create table audit_log (
  id      bigserial primary key,
  at      timestamptz not null default now(),
  actor   text not null,
  action  text not null,
  target  text,
  detail  jsonb not null default '{}'
);
-- 플랫폼 어드민의 본문 열람 기록 — 테넌트 관리자가 직접 확인할 수 있다.
create table access_log (
  id          bigserial primary key,
  at          timestamptz not null default now(),
  actor       text not null,
  actor_kind  text not null,
  action      text not null,
  channel_id  bigint,
  reason      text,
  detail      jsonb not null default '{}'
);

-- 아카이브 객체는 불변·추가 전용이다. 같은 달을 다시 보관하면(늦게 도착한 쓰기,
-- 과거 데이터 이관) 기존 객체를 덮지 않고 새 객체를 하나 더 만든다 — 실측:
-- 키가 달 하나에 하나였을 때 재보관이 이전 30건을 덮어써 잃었다.
create table archive_index (
  id           bigserial primary key,
  month        date not null,
  object_key   text not null unique,
  sha256       text not null,
  rows         bigint not null,
  channels     jsonb not null,                 -- {channel_id: [min_seq, max_seq]}
  archived_at  timestamptz not null default now()
);
create index archive_index_month on archive_index(month desc);

-- ── 실행 주체 ────────────────────────────────────────────────────────
-- 현재 사용자는 트랜잭션 로컬 설정으로 들어온다(SET LOCAL chat.uid).
-- 비어 있으면 아무 행도 보이지 않는다 — 실수로 사용자 없이 질의해도 새지 않는다.
create function cur_uid() returns text language sql stable as $$
  select nullif(current_setting('chat.uid', true), '')
$$;

create function is_guest(p_uid text) returns boolean
language sql stable security definer set search_path = __SCHEMA__ as $$
  select coalesce((select role = 'guest' from members
                    where user_id = p_uid and status = 'active'), true)
$$;

-- 채널을 볼 수 있는가: 활성 멤버이고, (공개 채널 & 게스트 아님) 또는 채널 멤버
create function can_see_channel(p_channel bigint, p_uid text) returns boolean
language sql stable security definer set search_path = __SCHEMA__ as $$
  select exists (select 1 from members m where m.user_id = p_uid and m.status = 'active')
     and (exists (select 1 from channel_members cm
                   where cm.channel_id = p_channel and cm.user_id = p_uid)
          or exists (select 1 from channels c
                      where c.id = p_channel and c.visibility = 'public'
                        and c.kind <> 'dm' and not is_guest(p_uid)))
$$;

-- ── RLS ─────────────────────────────────────────────────────────────
alter table channels enable row level security;
alter table channel_members enable row level security;
alter table messages enable row level security;
alter table mentions enable row level security;
alter table reactions enable row level security;
alter table members enable row level security;
alter table access_log enable row level security;
alter table audit_log enable row level security;
alter table policies enable row level security;
alter table roles enable row level security;

create policy p_channels on channels for select using (can_see_channel(id, cur_uid()));
create policy p_cm on channel_members for select using (can_see_channel(channel_id, cur_uid()));
create policy p_messages on messages for select using (can_see_channel(channel_id, cur_uid()));
create policy p_mentions on mentions for select using (user_id = cur_uid());
create policy p_reactions on reactions for select using (can_see_channel(channel_id, cur_uid()));
-- 멤버 목록은 활성 멤버끼리 보인다(게스트는 자기 자신만)
create policy p_members on members for select using (
  user_id = cur_uid() or (not is_guest(cur_uid())));
create policy p_roles on roles for select using (cur_uid() is not null);
create policy p_policies on policies for select using (cur_uid() is not null);
-- 감사·열람 기록은 권한 판정 후 앱이 보여 준다 — RLS로는 행 단위 판정이
-- 불가능한 역할 기반 조건이라, 여기서는 '사용자가 있을 때만'으로 막고
-- 실제 권한(audit.read)은 authz가 판정한다.
create policy p_access on access_log for select using (cur_uid() is not null);
create policy p_audit on audit_log for select using (cur_uid() is not null);

-- ── 쓰기 함수 ───────────────────────────────────────────────────────

-- 게시 — 순서·멱등·스레드 카운터·멘션을 한 트랜잭션에서.
-- 채널 행을 먼저 잠그므로 같은 채널의 게시는 직렬화되고, 멱등 확인을 잠금
-- 이후에 하므로 같은 client_msg_id의 동시 재시도도 하나만 들어간다.
create function post_message(p_channel bigint, p_author text, p_body text,
                             p_kind text, p_parent_seq bigint,
                             p_client_msg_id text, p_mentions text[])
returns table (seq bigint, created_at timestamptz, duplicate boolean)
language plpgsql security definer set search_path = __SCHEMA__ as $$
declare
  v_seq bigint;
  v_at  timestamptz := clock_timestamp();
  v_dup record;
begin
  if not can_see_channel(p_channel, p_author) then
    raise exception 'forbidden: % cannot post to channel %', p_author, p_channel
      using errcode = '42501';
  end if;
  -- 잠금 → 멱등 확인 → 순서 발급. 먼저 올리고 중복이면 되돌리는 방식은 틀린다:
  -- 직전 메시지가 답글이면 last_top_seq의 이전 값을 복원할 수 없다(실측 결함).
  perform 1 from channels c where c.id = p_channel and c.archived_at is null for update;
  if not found then
    raise exception 'channel % not found or archived', p_channel using errcode = 'P0002';
  end if;
  if p_client_msg_id is not null then
    select d.seq, d.created_at into v_dup from message_dedupe d
     where d.channel_id = p_channel and d.client_msg_id = p_client_msg_id;
    if found then
      return query select v_dup.seq, v_dup.created_at, true;
      return;
    end if;
  end if;
  update channels c
     set last_seq = c.last_seq + 1,
         last_top_seq = case when p_parent_seq is null then c.last_seq + 1 else c.last_top_seq end,
         last_message_at = v_at
   where c.id = p_channel
  returning c.last_seq into v_seq;
  if p_client_msg_id is not null then
    insert into message_dedupe values (p_channel, p_client_msg_id, v_seq, v_at);
  end if;
  if p_parent_seq is not null then
    update messages m set reply_count = m.reply_count + 1, last_reply_at = v_at
     where m.channel_id = p_channel and m.seq = p_parent_seq and m.parent_seq is null;
    if not found then
      raise exception 'thread root % not found in channel %', p_parent_seq, p_channel
        using errcode = 'P0002';
    end if;
  end if;
  insert into messages (channel_id, seq, parent_seq, author, body, kind, created_at, client_msg_id)
  values (p_channel, v_seq, p_parent_seq, p_author, p_body, p_kind, v_at, p_client_msg_id);
  -- 멘션은 채널을 볼 수 있는 활성 멤버만, 자기 자신은 제외
  insert into mentions (user_id, channel_id, seq, created_at)
  select distinct u, p_channel, v_seq, v_at from unnest(coalesce(p_mentions, '{}')) u
   where u <> p_author and can_see_channel(p_channel, u)
  on conflict do nothing;
  return query select v_seq, v_at, false;
end $$;

-- 시스템 게시 — 플랫폼 공지·운영 알림. 멤버가 아닌 주체('platform')가 쓰므로
-- 가시성 검사를 하지 않는다. 그래서 관리 경로(owner) 전용이고 앱 역할엔 주지 않는다.
-- p_dedupe로 같은 공지의 재전달이 두 번 게시되지 않는다.
create function post_system(p_channel bigint, p_body text, p_dedupe text)
returns bigint language plpgsql security definer set search_path = __SCHEMA__ as $$
declare v_seq bigint; v_at timestamptz := clock_timestamp();
begin
  perform 1 from channels c where c.id = p_channel and c.archived_at is null for update;
  if not found then
    raise exception 'channel % not found or archived', p_channel using errcode = 'P0002';
  end if;
  if exists (select 1 from message_dedupe where channel_id = p_channel
                                              and client_msg_id = p_dedupe) then
    return null;
  end if;
  update channels c set last_seq = c.last_seq + 1, last_top_seq = c.last_seq + 1,
         last_message_at = v_at
   where c.id = p_channel
  returning c.last_seq into v_seq;
  insert into message_dedupe values (p_channel, p_dedupe, v_seq, v_at);
  insert into messages (channel_id, seq, author, body, kind, created_at, client_msg_id)
  values (p_channel, v_seq, 'platform', p_body, 'system', v_at, p_dedupe);
  return v_seq;
end $$;

-- 수정·삭제 — 작성자 본인이거나 앱이 moderate 권한을 확인했을 때만.
create function edit_message(p_channel bigint, p_seq bigint, p_actor text, p_body text)
returns boolean language plpgsql security definer set search_path = __SCHEMA__ as $$
begin
  update messages set body = p_body, edited_at = clock_timestamp()
   where channel_id = p_channel and seq = p_seq and author = p_actor and deleted_at is null;
  return found;
end $$;

create function delete_message(p_channel bigint, p_seq bigint, p_actor text, p_moderate boolean)
returns boolean language plpgsql security definer set search_path = __SCHEMA__ as $$
begin
  -- 흔적은 남긴다: 답글이 달린 루트가 통째로 사라지면 스레드가 고아가 된다
  update messages set body = '', deleted_at = clock_timestamp(), deleted_by = p_actor
   where channel_id = p_channel and seq = p_seq and deleted_at is null
     and (author = p_actor or p_moderate);
  if found then
    delete from mentions where channel_id = p_channel and seq = p_seq;
    if p_moderate then
      insert into audit_log(actor, action, target, detail)
      values (p_actor, 'message.delete_any', p_channel || ':' || p_seq, '{}');
    end if;
  end if;
  return found;
end $$;

-- 읽음 — 되감지 않는다(GREATEST). 늦게 도착한 오래된 커서가 진행을 지우지 않게.
create function mark_read(p_channel bigint, p_uid text, p_seq bigint)
returns bigint language plpgsql security definer set search_path = __SCHEMA__ as $$
declare v bigint;
begin
  update channel_members set last_read_seq = greatest(last_read_seq, p_seq)
   where channel_id = p_channel and user_id = p_uid
  returning last_read_seq into v;
  if v is null and can_see_channel(p_channel, p_uid) then
    -- 공개 채널을 구경만 하던 사람이 읽으면 그때 멤버가 된다
    insert into channel_members (channel_id, user_id, last_read_seq)
    values (p_channel, p_uid, p_seq)
    on conflict (channel_id, user_id) do update
      set last_read_seq = greatest(channel_members.last_read_seq, excluded.last_read_seq)
    returning last_read_seq into v;
  end if;
  return v;
end $$;

create function react(p_channel bigint, p_seq bigint, p_uid text, p_emoji text, p_on boolean)
returns boolean language plpgsql security definer set search_path = __SCHEMA__ as $$
begin
  if not can_see_channel(p_channel, p_uid) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if p_on then
    insert into reactions values (p_channel, p_seq, p_uid, p_emoji, clock_timestamp())
    on conflict do nothing;
  else
    delete from reactions where channel_id = p_channel and seq = p_seq
       and user_id = p_uid and emoji = p_emoji;
  end if;
  return found;
end $$;

-- ── 파티션 관리 ─────────────────────────────────────────────────────
-- 이번 달부터 p_ahead개월 뒤까지 월 파티션을 만든다. default 파티션에 이미
-- 들어간 행이 있으면 그 달 파티션 생성이 실패하므로, 그 행들을 옮긴다.
create function ensure_partitions(p_ahead int default 2)
returns int language plpgsql security definer set search_path = __SCHEMA__ as $$
declare
  m date := date_trunc('month', now())::date;
  stop date := (date_trunc('month', now()) + make_interval(months => p_ahead))::date;
  part text; made int := 0;
begin
  while m <= stop loop
    part := 'messages_' || to_char(m, 'YYYYMM');
    if to_regclass(part) is null then
      create temp table if not exists _moving (like messages) on commit drop;
      with moved as (
        delete from messages_default
         where created_at >= m and created_at < (m + interval '1 month')
        returning *)
      insert into _moving select * from moved;
      execute format('create table %I partition of messages for values from (%L) to (%L)',
                     part, m, (m + interval '1 month')::date);
      insert into messages select * from _moving;
      truncate _moving;
      made := made + 1;
    end if;
    m := (m + interval '1 month')::date;
  end loop;
  return made;
end $$;

-- 임의의 달 파티션 — 과거 데이터 이관(backfill)과 시험용. 관리 경로 전용.
create function create_month(p_month date)
returns boolean language plpgsql security definer set search_path = __SCHEMA__ as $$
declare part text := 'messages_' || to_char(p_month, 'YYYYMM');
        m date := date_trunc('month', p_month)::date;
begin
  if to_regclass(part) is not null then return false; end if;
  execute format('create table %I partition of messages for values from (%L) to (%L)',
                 part, m, (m + interval '1 month')::date);
  return true;
end $$;

-- 보관용 — 특정 달 파티션을 detach 한다(아카이버가 내보낸 뒤 호출).
create function detach_month(p_month date)
returns boolean language plpgsql security definer set search_path = __SCHEMA__ as $$
declare part text := 'messages_' || to_char(p_month, 'YYYYMM');
begin
  if to_regclass(part) is null then return false; end if;
  execute format('alter table messages detach partition %I', part);
  execute format('drop table %I', part);
  return true;
end $$;

create function maintenance() returns jsonb
language plpgsql security definer set search_path = __SCHEMA__ as $$
declare made int; pruned int;
begin
  made := ensure_partitions(2);
  delete from message_dedupe where created_at < now() - interval '7 days';
  get diagnostics pruned = row_count;
  return jsonb_build_object('partitions_created', made, 'dedupe_pruned', pruned);
end $$;

select ensure_partitions(2);

-- ── 기본 역할 ────────────────────────────────────────────────────────
insert into roles (name, permissions, builtin, description) values
 ('owner', array['*'], true, '테넌트 소유자 — 모든 권한'),
 ('admin', array['channel.create','channel.create_private','channel.archive','channel.manage_members',
                 'message.post','message.edit_own','message.delete_own','message.delete_any',
                 'member.invite','member.remove','member.update','role.manage','policy.manage',
                 'audit.read','export.run','retention.manage','reaction.add'], true, '관리자'),
 ('member', array['channel.create','channel.create_private','message.post','message.edit_own',
                  'message.delete_own','reaction.add'], true, '일반 멤버'),
 ('guest', array['message.post','message.edit_own','message.delete_own','reaction.add'], true,
  '게스트 — 초대받은 채널만');

insert into channels (name, topic, visibility, kind, created_by)
values ('general', '전체 채널', 'public', 'channel', 'system'),
       ('announcements', '플랫폼·관리자 공지', 'public', 'announcements', 'system');

-- ── 권한 부여 ────────────────────────────────────────────────────────
reset role;
grant usage on schema __SCHEMA__ to __APP__;
grant select on all tables in schema __SCHEMA__ to __APP__;
revoke select on message_dedupe from __APP__;
-- 함수는 기본적으로 PUBLIC에 EXECUTE가 열린다 — 먼저 걷고 필요한 것만 준다.
revoke execute on all functions in schema __SCHEMA__ from public;
grant execute on function __SCHEMA__.cur_uid(), __SCHEMA__.is_guest(text),
  __SCHEMA__.can_see_channel(bigint, text),
  __SCHEMA__.post_message(bigint, text, text, text, bigint, text, text[]),
  __SCHEMA__.edit_message(bigint, bigint, text, text),
  __SCHEMA__.delete_message(bigint, bigint, text, boolean),
  __SCHEMA__.mark_read(bigint, text, bigint),
  __SCHEMA__.react(bigint, bigint, text, text, boolean) to __APP__;
-- ensure_partitions·detach_month·maintenance는 관리 경로(owner)만 — 앱 역할이
-- 파티션을 떼어 낼 수 있으면 RLS를 지켜도 데이터를 지울 수 있다.
-- 앱 역할은 기본 역할·정책·멤버·채널 메타 관리를 함수 없이 하지 않는다 —
-- 관리 작업은 권한 판정 뒤 관리 경로(owner 역할)로만 실행된다.
