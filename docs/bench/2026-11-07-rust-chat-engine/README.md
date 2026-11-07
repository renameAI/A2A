# 2026-11-07 — 채팅 데이터 플레인 Rust 이식 전·후 실측 원본

- 기계: M2 Pro 10코어/16GB 한 대, Postgres 17 하나(셀 DB 2개), Redis 1. 요약은 `docs/MESSAGING_PLATFORM.md` §8b.
- `py-*` = uvicorn 4 워커(이식 전) · `rust-*` = a2a-chat 단일 프로세스(셀 풀 합계 동일 80)
- `*-200.json`/`*-500.json`: `scripts/chat_loadtest.py run`(1,000 SSE 구독 + 초당 200/500건).
  앞머리 몇 줄은 진행 로그고 JSON은 첫 `{`부터. **500건/초 실행은 부하 생성기(단일 파이썬
  asyncio)가 먼저 포화해 서버 비교로 쓸 수 없다** — 생성기 자체 경고가 파일에 남아 있다.
- `*-read.json`/`*-write.json`: oha 동시 64 × 25초, 메시지 50건 목록 GET / 멱등키 없는 게시 POST.
