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
        return SaasUser(uid=uid, email=email, workspace_id=f"ws-{uid}")
    if mode == "dev":
        if not x_dev_user:
            raise HTTPException(401, "dev 모드 — X-Dev-User 헤더 필요")
        return SaasUser(uid=x_dev_user, email=f"{x_dev_user}@dev.local",
                        workspace_id=f"ws-{x_dev_user}")
    raise HTTPException(500, f"SAAS_AUTH={mode} — firebase|dev만 지원 (조용한 대체 없음)")
