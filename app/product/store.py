"""제품 레이어 상태 저장소 — 엔진은 stateless, 상태는 제품이 보유 (SYS-01).

Phase 6 운영화: 인메모리 → SQLite 영속화 (재시작 생존). 무인프라(파이썬 stdlib
sqlite3) — docker 없이 즉시 돌고, 리포지토리 경계가 깔끔해 나중에 PostgreSQL로
무손실 이관할 수 있다(메서드 시그니처·CompanyRecord는 그대로, _connect만 교체).

CompanyRecord는 중첩 pydantic(Profile·QuestionPin·CommentThread…)이 많아 관계형
정규화 대신 JSON 블롭 1컬럼으로 저장한다 — 모델이 이미 model_dump/검증을 제공하므로
문서형 저장이 정확하고 단순하다. 커넥션은 호출마다 새로 연다(audit·crawler와 동일
패턴) — 백그라운드 job 스레드가 store를 호출해도 커넥션을 공유하지 않아 안전하다.
"""
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
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


# ── 직렬화 (CompanyRecord ↔ JSON 블롭) ────────────────────────────

def _serialize(rec: CompanyRecord) -> str:
    return json.dumps({
        "company_id": rec.company_id,
        "profile": rec.profile.model_dump(mode="json"),
        "private_state": rec.private_state.model_dump(mode="json"),
        "open_questions": rec.open_questions,
        "evidence": rec.evidence,
        "engine_mode": rec.engine_mode,
        "question_pins": [p.model_dump(mode="json") for p in rec.question_pins],
        "threads": [t.model_dump(mode="json") for t in rec.threads.values()],
        "answered_questions": [d.model_dump(mode="json")
                               for d in rec.answered_questions],
    }, ensure_ascii=False)


def _deserialize(blob: str) -> CompanyRecord:
    d = json.loads(blob)
    return CompanyRecord(
        company_id=d["company_id"],
        profile=Profile(**d["profile"]),
        private_state=PrivateState(**d["private_state"]),
        open_questions=d.get("open_questions", []),
        evidence=d.get("evidence"),
        engine_mode=d.get("engine_mode", "mock"),
        question_pins=[QuestionPin(**p) for p in d.get("question_pins", [])],
        threads={t["thread_id"]: CommentThread(**t)
                 for t in d.get("threads", [])},
        answered_questions=[DialogueTurn(**x)
                            for x in d.get("answered_questions", [])])


def _db_path() -> Path:
    override = os.environ.get("A2A_DB_PATH")
    return Path(override) if override else \
        Path(__file__).resolve().parent.parent.parent / "data" / "a2a.db"


class ProductStore:
    """SQLite 백엔드 write-through. get은 매번 DB에서 역직렬화하므로 반환 레코드를
    수정해도 저장은 명시적 메서드(save/update/set_pins/reply)를 통해서만 일어난다."""

    def _connect(self) -> sqlite3.Connection:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")   # 동시 읽기 허용
        conn.execute("CREATE TABLE IF NOT EXISTS companies "
                     "(company_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        return conn

    def _put(self, rec: CompanyRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO companies(company_id, data) VALUES(?, ?) "
                "ON CONFLICT(company_id) DO UPDATE SET data=excluded.data",
                (rec.company_id, _serialize(rec)))

    def save_company(self, profile: Profile, private_state: PrivateState,
                     open_questions: list[str], evidence: Optional[dict],
                     engine_mode: str) -> CompanyRecord:
        rec = CompanyRecord(
            company_id=f"co-{uuid.uuid4().hex[:8]}",
            profile=profile, private_state=private_state,
            open_questions=open_questions, evidence=evidence,
            engine_mode=engine_mode)
        self._put(rec)
        return rec

    def update_company(self, company_id: str, profile: Profile,
                       private_state: PrivateState, open_questions: list[str],
                       evidence: Optional[dict], engine_mode: str
                       ) -> Optional[CompanyRecord]:
        """자료 추가·보강 답변 반영 재분석 시 같은 회사를 갱신 (REP-09).
        핀·스레드·answered_questions는 보존하고 프로필 계열 5필드만 덮어쓴다."""
        rec = self.get(company_id)
        if rec is None:
            return None
        rec.profile = profile
        rec.private_state = private_state
        rec.open_questions = open_questions
        rec.evidence = evidence
        rec.engine_mode = engine_mode
        self._put(rec)
        return rec

    def get(self, company_id: str) -> Optional[CompanyRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM companies WHERE company_id=?",
                (company_id,)).fetchone()
        return _deserialize(row[0]) if row else None

    def list(self) -> list[CompanyRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM companies").fetchall()
        return [_deserialize(r[0]) for r in rows]

    # ── 질문 위치 탐지 (bbox) — 온보딩마다 재생성, 댓글 스레드는 사람이 닫는다 ──

    def set_question_pins(self, company_id: str, pins: list[QuestionPin],
                          threads: list[CommentThread]) -> None:
        rec = self.get(company_id)
        if rec is None:
            return
        rec.question_pins = pins
        rec.threads = {t.thread_id: t for t in threads}
        self._put(rec)

    def open_thread_count(self, company_id: str) -> int:
        rec = self.get(company_id)
        if rec is None:
            return 0
        return sum(1 for t in rec.threads.values() if t.status == "open")

    def reply_thread(self, company_id: str, thread_id: str, text: str,
                     ts: str) -> Optional[CommentThread]:
        """사람의 답변을 스레드에 붙이고 resolved로 닫는다 (강제 응답 해제).

        동시에 소통 루프를 완성한다 — 스레드 첫 댓글(엑사원 질문)과 이 답변을
        (질문, 답) DialogueTurn으로 축적해, 다음 재분석 때 엑사원에게 전달한다.
        같은 질문에 다시 답하면 최신 답으로 갱신한다 (중복 축적 방지)."""
        rec = self.get(company_id)
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
        self._put(rec)
        return thread

    def answered_dialogue(self, company_id: str) -> list[DialogueTurn]:
        """재분석에 실어 보낼, 지금까지 핀에 답한 (질문, 답) 목록."""
        rec = self.get(company_id)
        return list(rec.answered_questions) if rec else []


store = ProductStore()
