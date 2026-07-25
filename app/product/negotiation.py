"""interview_agent(전략 인터뷰 CLI)를 협상 준비 세션으로 서비스에 연결하는 어댑터.

interview_agent/interview_test.py는 단일 프로세스·동기 CLI로 설계됐다
(전역 TARGET_COMPANY, 블로킹 input()). 여기서는 세션마다 백그라운드 스레드로
실행하고, read_human_answer()를 큐 기반으로 바꿔치기해 웹 요청/응답 주기에
맞춘다. 전역 상태(TARGET_COMPANY, read_human_answer)를 건드리므로 세션은
한 번에 하나만 실행한다(_SESSION_LOCK).
"""
import queue
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

_AGENT_DIR = Path(__file__).resolve().parents[2] / "interview_agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

import interview_test as ia  # noqa: E402

_SESSION_LOCK = threading.Lock()
_SESSIONS: dict[str, "NegotiationSession"] = {}

# interview_agent의 key_benefit_priority(자유 카테고리) → A2A Intent.value_props(고정 4종).
# 근사 매핑이다 — 원문은 notes에 그대로 남겨 손실을 숨기지 않는다.
_BENEFIT_TO_VALUE_PROP = {
    "problem_solving": "problem_solving",
    "revenue_growth": "revenue_growth",
    "cost_reduction": "cost_reduction",
    "risk_reduction": "cost_reduction",
    "regulatory_compliance": "cost_reduction",
    "social_impact": "impact",
}


class NegotiationSession:
    def __init__(self, session_id: str, company: str, hints: str = ""):
        self.session_id = session_id
        self.company = company
        self.hints = hints
        self.answer_queue: queue.Queue = queue.Queue(maxsize=1)
        self.status = "running"  # running | waiting_answer | done | error
        self.pending: Optional[dict[str, Any]] = None
        self.summary: Optional[list[str]] = None
        self.state: Optional[dict[str, Any]] = None
        self.error: Optional[str] = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _patched_read_human_answer(self) -> str:
        state = self.state
        qid = list(state["question_states"].keys())[-1]
        q = state["question_states"][qid]
        self.pending = {
            "question_id": qid,
            "kind": q["kind"],
            "kind_label": ia.KIND_DISPLAY.get(q["kind"], q["kind"]),
            "text": q["question_text"],
        }
        self.status = "waiting_answer"
        answer = self.answer_queue.get()
        self.status = "running"
        self.pending = None
        return answer

    def submit_answer(self, answer: str) -> None:
        self.answer_queue.put(answer)

    def _run(self) -> None:
        if not _SESSION_LOCK.acquire(blocking=False):
            self.error = "다른 협상 준비 인터뷰가 이미 진행 중입니다. 완료 후 다시 시도하세요."
            self.status = "error"
            return
        try:
            ia.TARGET_COMPANY = self.company
            ia.COMPANY_HINTS = self.hints
            ia.read_human_answer = self._patched_read_human_answer
            client = ia.make_client()
            research = ia.phase_research(client)
            self.state = research["state"]
            transcript = ia.phase_interview(
                client, self.state, research["research_doc"], research["research_facts"],
            )
            ia.phase_output(self.state, transcript)
            self.summary = ia.strategy_summary_lines(self.state)
            self.status = "done"
        except Exception as exc:  # 세션 스레드 — 예외를 상태로 남기고 삼킨다
            self.error = str(exc)
            self.status = "error"
        finally:
            _SESSION_LOCK.release()

    def to_intent(self) -> Optional[dict[str, Any]]:
        """완료된 인터뷰 상태(주 트랙 A) → Intent 스키마 dict. /negotiate·/match에 그대로 전달 가능.

        Intent.value_props는 min_length=1 — 매핑 실패 시 problem_solving으로 안전 폴백한다
        (인터뷰가 최소 1개는 확보하도록 라우터가 강제하므로 완전 공백은 없음).
        """
        state = self.state
        if state is None:
            return None
        gp = ia.get_path
        benefits = gp(state, "strategy_tracks.A.purchase_logic.key_benefit_priority") or []
        value_props = sorted({_BENEFIT_TO_VALUE_PROP.get(b, "problem_solving") for b in benefits}) \
            or ["problem_solving"]
        notes_parts = []
        reason = gp(state, "strategy_tracks.A.purchase_logic.purchase_reason")
        if reason:
            notes_parts.append(f"구매 이유: {reason}")
        cta = gp(state, "strategy_tracks.A.cta_strategy.primary_cta")
        if cta:
            notes_parts.append(f"1차 요청: {cta}")
        return {
            "value_props": value_props,
            "target_region": gp(state, "strategy_tracks.A.market.country_or_region"),
            "target_type": gp(state, "strategy_tracks.A.target.organization_type"),
            "proposal_type": gp(state, "transaction_strategy.primary_goal"),
            "notes": " / ".join(notes_parts) or None,
            "differentiator": gp(state, "offer.differentiator"),
            "key_proof": gp(state, "strategy_tracks.A.proof_strategy.primary_proof"),
            "entry_channel": gp(state, "strategy_tracks.A.entry_strategy.primary_channel"),
        }

    def payload(self) -> dict[str, Any]:
        if self.status == "error":
            return {"session_id": self.session_id, "status": "error", "error": self.error}
        if self.status == "done":
            return {"session_id": self.session_id, "status": "done", "summary": self.summary,
                     "intent": self.to_intent()}
        if self.status == "waiting_answer":
            return {"session_id": self.session_id, "status": "waiting_answer",
                     "question": self.pending}
        return {"session_id": self.session_id, "status": "running"}


def start_session(company: str, hints: str = "") -> NegotiationSession:
    session_id = uuid.uuid4().hex[:12]
    sess = NegotiationSession(session_id, company, hints)
    _SESSIONS[session_id] = sess
    sess.start()
    return sess


def get_session(session_id: str) -> Optional[NegotiationSession]:
    return _SESSIONS.get(session_id)
