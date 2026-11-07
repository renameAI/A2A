"""Supabase Realtime 연결 — 토픽 한정 토큰.

왜 토큰인가: Realtime의 비공개 채널 인가는 Realtime 프로젝트 DB의
`realtime.messages` RLS 정책이 판정한다. 그런데 채널 권한의 원본은 각 셀의
테넌트 스키마다 — Realtime 쪽 DB가 셀을 조회할 수 없다(셀은 다른 서버).
그래서 판정은 우리 API가 하고, 그 결과(구독 가능한 토픽 목록)를 짧은 수명의
JWT 클레임 `chat_topics`에 담아 준다. Realtime 정책은 "요청 토픽이 토큰에
있는가"만 본다(sql/realtime_policy.sql).

  - 권한 회수 반영: 토큰 수명(기본 300초) 안. 클라이언트는 만료 전에 다시 받고,
    그때 가시성이 다시 판정된다. SSE 경로의 60초 재확인과 같은 역할.
  - 서명 키: Realtime 프로젝트의 JWT 비밀(SUPABASE_JWT_SECRET). HS256.
  - 메시지 본문은 여전히 싣지 않는다 — 토큰이 새도 '언제 변화가 있었는가'까지만.
"""
import base64
import hashlib
import hmac
import json
import os
import time

from ..errors import EngineError

TTL = int(os.environ.get("CHAT_RT_TOKEN_TTL", "300"))


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(claims: dict, secret: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(sig)}"


def topic_token(uid: str, topics: list[str], ttl: int = TTL) -> dict:
    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if len(secret) < 32:
        raise EngineError(500, "config_error", "SUPABASE_JWT_SECRET이 없거나 짧습니다")
    now = int(time.time())
    claims = {"sub": uid, "role": "authenticated", "aud": "authenticated",
              "iat": now, "exp": now + ttl, "chat_topics": sorted(topics)}
    return {"token": sign(claims, secret), "expires_at": now + ttl, "topics": sorted(topics),
            "url": os.environ.get("SUPABASE_URL", "")}
