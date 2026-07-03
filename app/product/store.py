"""제품 레이어 상태 저장소 — 엔진은 stateless, 상태는 제품이 보유 (SYS-01).

v0: 인메모리. Phase 5에서 PostgreSQL로 영속화.
"""
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..schemas import PrivateState, Profile


@dataclass
class CompanyRecord:
    company_id: str
    profile: Profile
    private_state: PrivateState
    open_questions: list[str] = field(default_factory=list)
    evidence: Optional[dict] = None
    engine_mode: str = "mock"


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

    def get(self, company_id: str) -> Optional[CompanyRecord]:
        return self._companies.get(company_id)

    def list(self) -> list[CompanyRecord]:
        return list(self._companies.values())


store = ProductStore()
