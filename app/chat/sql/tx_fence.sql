-- 테넌트 트랜잭션 펜스 — Python(db.py)과 Rust(chat-engine)가 같은 파일을 읽는다(규칙 사본 금지).
-- 자리표시자는 psycopg 식 %s 셋: search_path 스키마, chat.uid, USAGE 확인 스키마.
-- Rust는 %s를 $1..$3으로 바꿔 쓴다. 셀 이동 뒤 옛 셀로 온 요청은 USAGE가 없어 ok=false.
select set_config('search_path', %s, true), set_config('chat.uid', %s, true), has_schema_privilege(%s, 'USAGE') as ok
