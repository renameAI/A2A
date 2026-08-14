-- SaaS 보존 계층 (Supabase/Postgres) — LocalSaasStore·FirestoreSaasStore와
-- 동일한 계약: (kind, workspace_id, doc_id) → JSON 문서.
--
-- 왜 컬럼 정규화가 아니라 JSONB인가: 프로필·인사이트·온톨로지가 전부 중첩
-- 구조이고, 세 백엔드가 같은 형태를 주고받아야 전환 시 변환 코드가 안 생긴다.
-- 기획서 §10.1의 JSONB 활용 취지와도 같다.

create table if not exists public.saas_docs (
  kind         text        not null,
  workspace_id text        not null,
  doc_id       text        not null,
  body         jsonb       not null,
  updated_at   timestamptz not null default now(),
  primary key (kind, workspace_id, doc_id)
);

-- list()는 (kind, ws)로 최신순 조회한다 — 그 경로만 인덱싱한다.
create index if not exists saas_docs_kind_ws_updated
  on public.saas_docs (kind, workspace_id, updated_at desc);

-- ── 비용 예약 (원자) ────────────────────────────────────────────────
-- 앱에서 read-then-write로 하면 동시 요청이 캡을 함께 통과한다. 캡의 존재
-- 이유가 예산 사고 방지이므로 검사와 가산은 한 트랜잭션이어야 한다.
-- 초과 시 예외를 던지고(P0001), 앱이 EngineError(402, cost_cap)로 변환한다.
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
  -- 행 잠금으로 직렬화. 없으면 0으로 시작한다.
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

-- ── RLS ────────────────────────────────────────────────────────────
-- 엔진은 service_role 키로 접속하므로 RLS를 우회한다. 그럼에도 RLS를 켜는
-- 이유: anon 키가 유출되거나 클라이언트가 직접 붙는 경로가 생겼을 때
-- 기본이 '거부'여야 한다. fail-closed는 접근 제어의 기본값이다.
alter table public.saas_docs enable row level security;

-- 워크스페이스 규칙: workspace_id = 'ws-' || auth.uid()  (auth.py와 동일)
drop policy if exists saas_docs_own_workspace on public.saas_docs;
create policy saas_docs_own_workspace on public.saas_docs
  for all
  using  (workspace_id = 'ws-' || auth.uid()::text)
  with check (workspace_id = 'ws-' || auth.uid()::text);

-- 전역 비용 원장(__global__)은 어떤 사용자도 직접 읽고 쓸 수 없다 —
-- service_role만 접근한다. 정책을 만들지 않는 것이 곧 거부다.
