"""제품 레이어 상태 저장소 — 엔진은 stateless, 상태는 제품이 보유 (SYS-01).

v0: 인메모리. Phase 5에서 PostgreSQL로 영속화.
"""
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..schemas import (CommentThread, DialogueTurn, PrivateState, Profile,
                       QuestionPin, ThreadComment)


@dataclass
class CompanyRecord:
    company_id: str
    profile: Profile
    private_state: PrivateState
    open_questions: list[str] = field(default_factory=list)
    evidence: Optional[dict] = None
    engine_mode: str = "mock"
    # 질문 위치 탐지 (bbox) — 엑사원 질문을 IR덱 페이지에 핀 꽂기 + 댓글 스레드 (v1.2)
    question_pins: list[QuestionPin] = field(default_factory=list)
    threads: dict[str, CommentThread] = field(default_factory=dict)
    # 소통 루프 — 핀에 단 답변(질문↔답)을 재분석 입력(dialogue)으로 축적.
    # 다음 온보딩(같은 company_id)에서 엑사원에게 그대로 전달돼 프로필이 개선된다.
    answered_questions: list[DialogueTurn] = field(default_factory=list)


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

    # ── 질문 위치 탐지 (bbox) — 온보딩마다 재생성, 댓글 스레드는 사람이 닫는다 ──

    def set_question_pins(self, company_id: str, pins: list[QuestionPin],
                          threads: list[CommentThread]) -> None:
        rec = self._companies.get(company_id)
        if rec is None:
            return
        rec.question_pins = pins
        rec.threads = {t.thread_id: t for t in threads}

    def open_thread_count(self, company_id: str) -> int:
        rec = self._companies.get(company_id)
        if rec is None:
            return 0
        return sum(1 for t in rec.threads.values() if t.status == "open")

    def reply_thread(self, company_id: str, thread_id: str, text: str,
                     ts: str) -> Optional[CommentThread]:
        """사람의 답변을 스레드에 붙이고 resolved로 닫는다 (강제 응답 해제).

        동시에 소통 루프를 완성한다 — 스레드 첫 댓글(엑사원 질문)과 이 답변을
        (질문, 답) DialogueTurn으로 축적해, 다음 재분석 때 엑사원에게 전달한다.
        같은 질문에 다시 답하면 최신 답으로 갱신한다 (중복 축적 방지)."""
        rec = self._companies.get(company_id)
        if rec is None:
            return None
        thread = rec.threads.get(thread_id)
        if thread is None:
            return None
        question = thread.comments[0].text if thread.comments else ""
        thread.comments.append(ThreadComment(author="human", text=text, ts=ts))
        thread.status = "resolved"
        if question:
            existing = next((d for d in rec.answered_questions
                             if d.q == question), None)
            if existing is not None:
                existing.a = text
            else:
                rec.answered_questions.append(DialogueTurn(q=question, a=text))
        return thread

    def answered_dialogue(self, company_id: str) -> list[DialogueTurn]:
        """재분석에 실어 보낼, 지금까지 핀에 답한 (질문, 답) 목록."""
        rec = self._companies.get(company_id)
        return list(rec.answered_questions) if rec else []


store = ProductStore()
