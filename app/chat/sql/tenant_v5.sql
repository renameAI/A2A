-- 테넌트 스키마 v5 — 멤버 upsert + 기본 채널 자동 가입을 SQL 함수 하나로.
--
-- 이 규칙은 지금까지 Python(provision._add_member_admin)에만 있었는데, Rust 데이터 플레인의
-- 멤버 초대도 같은 규칙이 필요해졌다. 두 언어에 사본을 두지 않고 셀 DB의 함수 하나가 단일 소스가 된다.
-- 게스트는 자동 가입에서 제외한다 — 채널 멤버십이 곧 열람 권한이라, 게스트를 넣으면
-- '초대받은 채널만'이 무너진다(실측: 게스트에게 #general이 보였다).
set local search_path = __SCHEMA__;

create or replace function add_member(p_uid text, p_email text, p_role text, p_attrs jsonb)
returns void language plpgsql security definer set search_path = __SCHEMA__ as $$
begin
  insert into members (user_id, email, display_name, role, attrs)
  values (p_uid, p_email, split_part(p_email, '@', 1), p_role, coalesce(p_attrs, '{}'::jsonb))
  on conflict (user_id) do update set role = excluded.role,
    attrs = excluded.attrs, status = 'active';
  if p_role <> 'guest' then
    insert into channel_members (channel_id, user_id)
    select id, p_uid from channels
     where kind in ('channel', 'announcements') and visibility = 'public' and archived_at is null
    on conflict do nothing;
  end if;
end $$;
-- 관리 경로 전용 — 앱 역할에는 EXECUTE를 주지 않는다(판정을 거친 owner 트랜잭션만 부른다).
revoke execute on function add_member(text, text, text, jsonb) from public;
grant execute on function add_member(text, text, text, jsonb) to __OWNER__;
