-- 테넌트 스키마 v2 — 리드 데이터 접근 권한(lead.access).
--
-- 멤버십 통합(2026-10-21)으로 테넌트 멤버가 리드 요청·후보·초안·발송 기록을 함께 본다
-- (app/saas/workspace.py). 그 범위를 역할로 정한다: owner('*')·admin·member는 보고,
-- **게스트는 못 본다** — 게스트는 초대받은 채널만 보는 외부인(협력사·고객)이라 회사의
-- 리드 파이프라인이 열리면 안 된다(실측: 통합 직후 dev 'vendor' 게스트가 rename의
-- 리드 워크스페이스로 해석됐다).
-- 사용자 정의 역할에는 넣지 않는다 — 관리자가 역할 관리에서 명시적으로 준다.
set local search_path = __SCHEMA__;

update roles set permissions = permissions || array['lead.access']
 where builtin and name in ('admin', 'member')
   and not ('lead.access' = any(permissions));
