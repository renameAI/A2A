-- 테넌트 스키마 v3 — 멤버 아바타 색.
--
-- 프로필 사진 대신 색(색상환의 각도 0~359)만 둔다. 비어 있으면 화면이 아이디로 정한 기본 색을 쓴다.
-- 본인이 마이페이지에서 바꾸고(PATCH /me), 팀원 화면에도 같은 색으로 보인다.
set local search_path = __SCHEMA__;

alter table members add column if not exists avatar_hue smallint
  check (avatar_hue is null or avatar_hue between 0 and 359);
