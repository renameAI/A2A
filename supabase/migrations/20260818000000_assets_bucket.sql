-- 업로드 자료(IR덱 PDF·Word) 보관용 버킷.
--
-- 왜 스토리지인가: 엔진이 업로드 파일을 로컬 디스크에 쓰고 나중에 경로로
-- 읽었는데, Vercel 서버리스는 /tmp 외 읽기 전용이고 그 /tmp조차 호출 간에
-- 공유되지 않는다. 193바이트 PDF도 500이 났다(실측). 파일은 인스턴스 밖에
-- 있어야 한다.
--
-- 왜 private인가: 업로드 자료는 고객사 IR덱이다. 공개 버킷은 URL만 알면
-- 누구나 받는다. 엔진은 service_role로 읽고, 브라우저는 서명 URL로만 쓴다.
--
-- 왜 storage.objects에 정책을 만들지 않는가: 클라이언트가 직접 경로를 정해
-- 올리는 대신, 엔진이 경로를 정해 **서명 업로드 URL**을 발급한다. 인가는
-- 그 서명이 하므로 정책이 필요 없고, 클라이언트가 워크스페이스를 속여도
-- 경로를 바꿀 수 없다(경로는 서명에 묶여 있다).
--
-- 파일은 <workspace_id>/<uuid>.<ext> 로 쌓인다. 워크스페이스 삭제는
-- 그 접두사를 지운다.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'assets', 'assets', false,
  52428800,                         -- 50MB (실측 IR덱 35MB에 여유)
  array[
    'application/pdf',
    -- .docx만 받는다. 구형 .doc은 OLE 복합문서라 파서가 다르다.
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  ]
)
on conflict (id) do update
  set file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types,
      public = excluded.public;
