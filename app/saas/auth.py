"""SaaS 인증 (이슈 #6-B) — Firebase ID 토큰 검증, key-ready.

모드는 SAAS_AUTH 하나로 고정한다 (조용한 대체 없음 — llm.py get_extractor와
같은 규칙):
- firebase: Authorization: Bearer <ID 토큰>을 firebase-admin으로 검증.
  자격증명(GOOGLE_APPLICATION_CREDENTIALS 또는 ADC)이 없으면 기동 시 즉시 실패.
- dev: X-Dev-User 헤더를 uid로 신뢰 — 로컬 개발·테스트 전용. 프로덕션 컨테이너에
  이 값이 설정되는 것 자체가 사고이므로 로그에 경고를 남긴다.
"""
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass
class SaasUser:
    uid: str
    email: str
    workspace_id: str   # MVP: uid 1개 = workspace 1개 (조직 권한은 Out of Scope)


def _mode() -> str:
    return os.environ.get("SAAS_AUTH", "dev").lower()


def _allowlist() -> "set[str]":
    """SAAS_ALLOWED_USERS — 접근 가능한 이메일·uid (쉼표 구분, 소문자 비교).

    비어 있으면 **전원 거부**한다(fail closed). 미설정을 '전체 허용'으로 읽으면
    배포 한 번에 API 예산이 녹는다 — 열려면 명시적으로 열어야 한다.
    Settings를 거쳐야 .env가 로딩된다(직접 os.environ을 읽으면 항상 빈 목록).
    """
    from ..config import get_settings
    return get_settings().saas_allowed_users


def _authorize(uid: str, email: str) -> None:
    allowed = _allowlist()
    if not allowed:
        raise HTTPException(403, "접근이 닫혀 있습니다 — SAAS_ALLOWED_USERS가 "
                                 "비어 있어 아무도 들어올 수 없습니다.")
    if uid.lower() not in allowed and email.lower() not in allowed:
        raise HTTPException(403, f"허용되지 않은 사용자입니다 ({email or uid}) — "
                                 "관리자에게 접근 요청하세요.")


_firebase_ready = False


def _verify_firebase(token: str) -> "tuple[str, str]":
    global _firebase_ready
    import firebase_admin
    from firebase_admin import auth as fb_auth
    if not _firebase_ready:
        firebase_admin.initialize_app()   # ADC/GOOGLE_APPLICATION_CREDENTIALS 사용
        _firebase_ready = True
    decoded = fb_auth.verify_id_token(token)
    return decoded["uid"], decoded.get("email", "")


def current_user(authorization: str = Header(default=""),
                 x_dev_user: str = Header(default="")) -> SaasUser:
    """FastAPI 의존성 — 라우터에서 Depends(current_user)로 사용."""
    mode = _mode()
    if mode == "firebase":
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Authorization: Bearer <Firebase ID 토큰> 필요")
        try:
            uid, email = _verify_firebase(authorization.removeprefix("Bearer ").strip())
        except Exception as e:                      # 만료·위조·미초기화 전부 401
            raise HTTPException(401, f"토큰 검증 실패: {type(e).__name__}") from e
        _authorize(uid, email)
        return SaasUser(uid=uid, email=email, workspace_id=f"ws-{uid}")
    if mode == "dev":
        if not x_dev_user:
            raise HTTPException(401, "dev 모드 — X-Dev-User 헤더 필요")
        email = f"{x_dev_user}@dev.local"
        _authorize(x_dev_user, email)   # dev 모드도 허용 목록을 거친다
        return SaasUser(uid=x_dev_user, email=email,
                        workspace_id=f"ws-{x_dev_user}")
    raise HTTPException(500, f"SAAS_AUTH={mode} — firebase|dev만 지원 (조용한 대체 없음)")
