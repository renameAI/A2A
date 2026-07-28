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
    """임계 통과 후보 없음 (RET-06) → 422. 억지 후보를 채우지 않는다.

    메시지는 사용자에게 그대로 노출될 수 있으므로 등급 라벨('강한/약한 후보')을
    쓰지 않는다 — 실명 기업에 붙는 등급은 엔진이 사람 대신 결론을 내리는 것이고,
    화면에 남으면 명예훼손 소지가 있다. 사실만 말한다: 조건에 맞는 상대를 못 찾음.
    """

    def __init__(self):
        super().__init__(422, "no_strong_candidate",
                         "지금 조건에 맞는 상대를 찾지 못했습니다.")


class DealBreaker(EngineError):
    """결격 사유 발생 (JDG-04) → 423. 매칭 결렬·비노출."""

    def __init__(self, dimension: str, reason: str):
        super().__init__(423, "deal_breaker",
                         "deal-breaker 결격 — 매칭 결렬 처리되었습니다.",
                         {"dimension": dimension, "reason": reason})
