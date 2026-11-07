-- Supabase Realtime 비공개 채널 인가 — Realtime을 담당하는 Supabase 프로젝트에 적용.
--
-- 판정은 우리 API가 이미 했다(app/chat/realtime.py): 토큰 클레임 chat_topics에
-- 구독 가능한 토픽만 들어 있다. 여기서는 "요청 토픽이 토큰에 있는가"만 본다.
-- 클라이언트의 broadcast 송신(INSERT)은 정책이 없으므로 전부 거부된다 — 서버만
-- service_role로 REST broadcast를 보낸다(service_role은 RLS를 우회한다).

-- RLS는 Supabase가 이미 켜 둔다(소유자 supabase_realtime_admin). 여기서 ALTER를
-- 하면 'must be owner' 오류다 — 로컬 Supabase 실연결에서 확인. 정책만 추가한다.

drop policy if exists chat_topic_listen on realtime.messages;
create policy chat_topic_listen on realtime.messages
  for select to authenticated
  using (
    realtime.messages.extension = 'broadcast'
    and realtime.topic() like 't:%:c:%'
    and realtime.topic() in (
      select jsonb_array_elements_text(coalesce(auth.jwt() -> 'chat_topics', '[]'::jsonb)))
  );
