"""제품 레이어 상태 저장소 — 엔진은 stateless, 상태는 제품이 보유 (SYS-01).

v0: 인메모리. Phase 5에서 PostgreSQL로 영속화.
"""
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..schemas import CommentThread, PrivateState, Profile, ThreadComment, VisualEvidence


@dataclass
class CompanyRecord:
    company_id: str
    profile: Profile
    private_state: PrivateState
    open_questions: list[str] = field(default_factory=list)
    evidence: Optional[dict] = None
    engine_mode: str = "mock"
    # 근거 시각화 (bbox) — IR덱 페이지 위 근거 위치 + 댓글 스레드 (v1.2 확장)
    visual_evidence: list[VisualEvidence] = field(default_factory=list)
    threads: dict[str, CommentThread] = field(default_factory=dict)


class ProductStore:
    def __init__(self):
        self._companies: dict[str, CompanyRecord] = {}

    def save_company(self, profile: Profile, private_state: PrivateState,
                     open_questions: list[str], evidence: Optional[dict],
                     engine_mode: str) -> CompanyRecord:
        rec = CompanyRecord(
            company_id=f"co-{uuid.uuid4().hex[:8]}",
            profile=profile, private_state=private_state,
            open_questions=open_questions, evidence=evidence,
            engine_mode=engine_mode)
        self._companies[rec.company_id] = rec
        return rec

    def update_company(self, company_id: str, profile: Profile,
                       private_state: PrivateState, open_questions: list[str],
                       evidence: Optional[dict], engine_mode: str
                       ) -> Optional[CompanyRecord]:
        """자료 추가·보강 답변 반영 재분석 시 같은 회사를 갱신 (REP-09)."""
        rec = self._companies.get(company_id)
        if rec is None:
            return None
        rec.profile = profile
        rec.private_state = private_state
        rec.open_questions = open_questions
        rec.evidence = evidence
        rec.engine_mode = engine_mode
        return rec

    def get(self, company_id: str) -> Optional[CompanyRecord]:
        return self._companies.get(company_id)

    def list(self) -> list[CompanyRecord]:
        return list(self._companies.values())

    # ── 근거 시각화 (bbox) — 온보딩마다 재생성, 댓글 스레드는 사람이 닫는다 ──

    def set_visual_evidence(self, company_id: str, evidence: list[VisualEvidence],
                            threads: list[CommentThread]) -> None:
        rec = self._companies.get(company_id)
        if rec is None:
            return
        rec.visual_evidence = evidence
        rec.threads = {t.thread_id: t for t in threads}

    def open_thread_count(self, company_id: str) -> int:
        rec = self._companies.get(company_id)
        if rec is None:
            return 0
        return sum(1 for t in rec.threads.values() if t.status == "open")

    def reply_thread(self, company_id: str, thread_id: str, text: str,
                     ts: str) -> Optional[CommentThread]:
        """사람의 답변을 스레드에 붙이고 resolved로 닫는다 (강제 응답 해제)."""
        rec = self._companies.get(company_id)
        if rec is None:
            return None
        thread = rec.threads.get(thread_id)
        if thread is None:
            return None
        thread.comments.append(ThreadComment(author="human", text=text, ts=ts))
        thread.status = "resolved"
        return thread


store = ProductStore()
