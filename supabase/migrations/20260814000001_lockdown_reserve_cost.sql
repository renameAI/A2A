-- reserve_cost 실행 권한 회수 (T2, 감사 확정 high).
--
-- 문제: SECURITY DEFINER 함수는 기본적으로 PUBLIC(= anon·authenticated 포함)이
-- 실행할 수 있다. Supabase는 함수를 PostgREST RPC로 자동 노출하므로,
-- 브라우저에 박힌 anon 키만으로 누구나 POST /rest/v1/rpc/reserve_cost를
-- 호출할 수 있었다. SECURITY DEFINER는 함수 소유자 권한으로 실행되어 RLS를
-- 우회하므로, saas_docs_own_workspace 정책("자기 워크스페이스만")이 있어도
-- 임의 workspace_id(다른 테넌트, 심지어 __global__ 전역 원장)를 조작할 수
-- 있었다 — 비용 하드캡 3단 중 두 단(요청·전역)을 브라우저에서 그대로 무력화.
--
-- 고침: 실행 권한을 service_role에만 남긴다. 엔진은 service_role 키로만
-- 이 함수를 부른다(app/saas/store.py SupabaseSaasStore).
revoke execute on function public.reserve_cost(
  text, text, text, double precision, double precision, double precision
) from public, anon, authenticated;

grant execute on function public.reserve_cost(
  text, text, text, double precision, double precision, double precision
) to service_role;

-- 방어 심층화: 실행 권한이 service_role로 좁혀졌어도, 앱 코드에 음수 호출이
-- 섞여 들어가면(버그) 캡을 거꾸로 돌려 무제한으로 만들 수 있다. 함수 자체가
-- 계약을 강제한다 — 호출자를 신뢰하지 않는다.
create or replace function public.reserve_cost(
  p_ws         text,
  p_request_id text,
  p_month_key  text,
  p_add        double precision,
  p_req_cap    double precision,
  p_month_cap  double precision
) returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_req   double precision;
  v_month double precision;
begin
  if p_add < 0 then
    raise exception 'reserve_cost: 음수 예약 금지 (p_add=%)', p_add
      using errcode = 'P0001';
  end if;

  insert into public.saas_docs (kind, workspace_id, doc_id, body)
    values ('cost_request', p_ws, p_request_id, '{"usd": 0}'::jsonb)
    on conflict do nothing;
  insert into public.saas_docs (kind, workspace_id, doc_id, body)
    values ('cost_month', p_ws, p_month_key, '{"usd": 0}'::jsonb)
    on conflict do nothing;

  select (body->>'usd')::double precision into v_req
    from public.saas_docs
   where kind = 'cost_request' and workspace_id = p_ws and doc_id = p_request_id
     for update;
  select (body->>'usd')::double precision into v_month
    from public.saas_docs
   where kind = 'cost_month' and workspace_id = p_ws and doc_id = p_month_key
     for update;

  if v_req + p_add > p_req_cap then
    raise exception 'cost_cap_request:%:%', p_req_cap, v_req
      using errcode = 'P0001';
  end if;
  if v_month + p_add > p_month_cap then
    raise exception 'cost_cap_month:%:%', p_month_cap, v_month
      using errcode = 'P0001';
  end if;

  update public.saas_docs
     set body = jsonb_build_object('usd', v_req + p_add), updated_at = now()
   where kind = 'cost_request' and workspace_id = p_ws and doc_id = p_request_id;
  update public.saas_docs
     set body = jsonb_build_object('usd', v_month + p_add), updated_at = now()
   where kind = 'cost_month' and workspace_id = p_ws and doc_id = p_month_key;
end;
$$;

revoke execute on function public.reserve_cost(
  text, text, text, double precision, double precision, double precision
) from public, anon, authenticated;
grant execute on function public.reserve_cost(
  text, text, text, double precision, double precision, double precision
) to service_role;
