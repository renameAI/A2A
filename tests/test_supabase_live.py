"""Supabase Realtime 실연결 검증 — 데이터 플레인 이식으로 재작성 대기.

검증하던 두 가지(토픽 한정 토큰 클레임, 브로드캐스트 묶음·속도·재시도)는 이제
Rust(rust/chat-engine/src/realtime.rs · events.rs)에 있다. 이 파일은 원래도
SUPABASE_* 환경변수가 없으면 전부 skip이라 CI에서 돈 적이 없고, 실연결 재검증은
Rust 쪽에 아직 없다(rust/chat-engine/README.md '아직 안 옮긴 것').
"""
import pytest

pytest.skip("Supabase 실연결 검증은 Rust 데이터 플레인 기준으로 재작성 필요 — rust/chat-engine/src/events.rs",
            allow_module_level=True)
