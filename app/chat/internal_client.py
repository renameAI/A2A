"""데이터 플레인(Rust, rust/chat-engine) 내부 API 클라이언트 — 관리 플레인이 판정·팬아웃을
다시 구현하지 않기 위한 입구다(규칙 사본 금지).

CHAT_DATA_PLANE_URL(기본 http://127.0.0.1:8423)로 간다. CHAT_INTERNAL_KEY가 설정돼 있으면
같은 값을 헤더로 보낸다(데이터 플레인이 대조한다).
"""
import json
import os
import urllib.error
import urllib.request

from ..errors import EngineError


def _base() -> str:
    return os.environ.get("CHAT_DATA_PLANE_URL", "http://127.0.0.1:8423").rstrip("/")


def _call(method: str, path: str, body: "dict | None" = None, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(f"{_base()}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    key = os.environ.get("CHAT_INTERNAL_KEY", "")
    if key:
        req.add_header("X-Internal-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", {})
        except Exception:  # noqa: BLE001
            err = {}
        raise EngineError(e.code, err.get("code", "data_plane_error"),
                          err.get("message", f"데이터 플레인 호출 실패({e.code})")) from e
    except OSError as e:
        raise EngineError(503, "data_plane_unreachable",
                          "데이터 플레인(채팅 엔진)에 연결하지 못했습니다") from e


def publish(slug: str, channel_id: int, event: dict) -> None:
    """커밋 뒤 전파 — 실패해도 게시를 실패로 만들지 않는다(공백 보충이 메운다)."""
    try:
        _call("POST", "/chat/v1/internal/publish",
              {"slug": slug, "channel_id": channel_id, "event": event})
    except EngineError:
        pass


def forget_route(slug: str) -> None:
    """상태·배치 변경 직후 데이터 플레인의 라우팅 캐시를 깬다 — 실패해도 TTL이 상한이다."""
    try:
        _call("POST", "/chat/v1/internal/forget-route", {"slug": slug}, timeout=2.0)
    except EngineError:
        pass


def me(slug: str, uid: str) -> dict:
    return _call("GET", f"/chat/v1/internal/me?slug={slug}&uid={uid}")


def members(slug: str, uid: str) -> list:
    return _call("GET", f"/chat/v1/internal/members?slug={slug}&uid={uid}")["members"]


def stats() -> dict:
    try:
        return _call("GET", "/chat/v1/internal/stats", timeout=2.0)
    except EngineError:
        return {}
