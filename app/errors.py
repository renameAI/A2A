"""공통 에러 계약 (API_계약서 §0.1)."""
from typing import Optional


class EngineError(Exception):
    """엔진 도메인 에러 → HTTP {"error": {code, message, details}} 로 변환된다."""

    def __init__(self, http_status: int, code: str, message: str,
                 details: Optional[dict] = None):
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message
        self.details = details

    def payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message,
                          "details": self.details}}


class ProfileBelowMinimum(EngineError):
    """최소 프로필 기준 미달 (REP-06) → 409."""

    def __init__(self, open_questions: list[str],
                 clarify: Optional[list] = None):
        super().__init__(409, "profile_below_minimum",
                         "최소 프로필 기준 미달 — 보강 질문에 답해 주세요.",
                         {"open_questions": open_questions,
                          "clarify": clarify or []})


class NoStrongCandidate(EngineError):
    """강한 후보 없음 (RET-06) → 422. 억지 후보를 채우지 않는다."""

    def __init__(self):
        super().__init__(422, "no_strong_candidate",
                         "강한 후보 없음 — 약한 후보를 억지로 채우지 않습니다.")


class DealBreaker(EngineError):
    """결격 사유 발생 (JDG-04) → 423. 매칭 결렬·비노출."""

    def __init__(self, dimension: str, reason: str):
        super().__init__(423, "deal_breaker",
                         "deal-breaker 결격 — 매칭 결렬 처리되었습니다.",
                         {"dimension": dimension, "reason": reason})
