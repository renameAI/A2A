"""Phase 2 수집·추출 테스트 — 전부 오프라인 (실제 네트워크·LLM 호출 없음)."""
import httpx
import pytest
from fastapi.testclient import TestClient

import app.engine.represent as represent_module
from app.config import Settings
from app.errors import EngineError
from app.ingest.chunking import chunk_text
from app.ingest.extractor import EXTRACTION_SCHEMA, extract_profile
from app.ingest.fetchers import FetchFailed, fetch_instagram, fetch_url
from app.main import app

client = TestClient(app)


def _settings(**env) -> Settings:
    s = Settings()
    s.anthropic_api_key = env.get("key", "")
    s.apify_token = env.get("apify", "")
    return s


# ── ING-02: 청킹 ─────────────────────────────────────────────────────

class TestChunking:
    def test_source_labels_preserved(self):
        text = "문단 하나.\n\n문단 둘.\n\n" + ("가" * 2500)
        chunks = chunk_text(text, "a1:website")
        assert all(c.source == "a1:website" for c in chunks)
        assert chunks[0].chunk_id == "a1:website#1"
        assert all(len(c.text) <= 2000 for c in chunks)
        assert len(chunks) >= 2   # 2500자 문단은 하드 분할

    def test_content_not_lost(self):
        text = "\n\n".join(f"문단 {i}" for i in range(50))
        chunks = chunk_text(text, "s")
        joined = "".join(c.text for c in chunks)
        for i in range(50):
            assert f"문단 {i}" in joined


# ── ING-01: 수집기 ───────────────────────────────────────────────────

class TestFetchers:
    def test_website_text_extraction(self):
        html = ("<html><head><script>bad()</script></head><body>"
                "<nav>메뉴</nav><h1>다이브인그룹</h1>"
                "<p>노후 호텔 객실을 예술 경험형으로 전환합니다.</p>"
                "<footer>푸터</footer></body></html>")
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=html))
        http = httpx.Client(transport=transport)
        text = fetch_url("https://divein.example.com", _settings(), client=http)
        assert "다이브인그룹" in text
        assert "노후 호텔 객실" in text
        assert "bad()" not in text      # script 제거
        assert "메뉴" not in text        # nav 제거

    def test_fetch_failure_is_explicit(self):
        """ING-01: 실패는 명시적 에러 — 빈값으로 조용히 넘어가지 않는다."""
        transport = httpx.MockTransport(lambda req: httpx.Response(404))
        http = httpx.Client(transport=transport)
        with pytest.raises(FetchFailed):
            fetch_url("https://없는사이트.example.com", _settings(), client=http)

    def test_instagram_requires_token(self):
        """ING-08: 토큰 없이는 명확한 안내 에러 (무단 스크레이핑 금지)."""
        with pytest.raises(EngineError) as exc:
            fetch_instagram("https://instagram.com/divein_official", _settings())
        assert exc.value.code == "instagram_not_configured"

    def test_instagram_via_provider(self):
        items = [{"username": "divein_official", "fullName": "다이브인그룹",
                  "biography": "호텔 객실 아트 전환",
                  "latestPosts": [{"caption": "하노이 프로젝트 완공"}]}]
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=items))
        http = httpx.Client(transport=transport)
        text = fetch_instagram("divein_official", _settings(apify="tok"), client=http)
        assert "다이브인그룹" in text and "하노이 프로젝트" in text


# ── ING-03/04: LLM 추출 (Fake 어댑터) ───────────────────────────────

FAKE_EXTRACTION = {
    "basic": {"name": "다이브인그룹", "country": "한국", "city": "서울",
              "founded_year": None, "industry": "hospitality_renovation"},
    "description": "노후 호텔 객실을 예술 경험형 상품으로 전환하는 스타트업",
    "problem_solved": {"value": "노후 호텔 객실의 매출 정체와 리뉴얼 자본 부담",
                       "provenance": "inferred", "confidence": 0.85,
                       "evidence_chunk_ids": ["a1:website#1"]},
    "solution": {"value": "저자본·무철거 예술 경험형 객실 전환 (매출 쉐어)",
                 "provenance": "stated", "confidence": None,
                 "evidence_chunk_ids": ["a1:website#1", "a2:instagram#1"]},
    "target_customer": {"value": "노후 객실을 보유한 중소 호텔 오너",
                        "provenance": "inferred", "confidence": 0.7,
                        "evidence_chunk_ids": ["a1:website#2"]},
    "references": ["성수 Poco Hotel 전환"],
    "traction": None,
    "sell_value_props": ["revenue_growth", "cost_reduction"],
    "purchase_value_props": [],
    "willingness_sell": None,
    "willingness_purchase": None,
    "portrait": {
        "identity": "저자본·무철거 전환으로 노후 호텔의 매출 정체를 푸는 공간 전환 스타트업",
        "business_model": "선투자는 자사 부담, 이후 객실 매출 쉐어로 회수 (반복 매출)",
        "edge": "예술 경험형 컨셉 전환 역량 — 통일된 인테리어가 표준인 시장에서 희소",
        "stage_narrative": "국내 레퍼런스 확보 단계 — 해외 첫 레퍼런스가 전략적으로 절실",
        "assets": "한국 부티크 전환 레퍼런스, 업셀링 실측 데이터, 시공 통제 역량",
        "gaps": "해외 현지 실행 파트너·레퍼런스 부재. 구매자로서는 해외 유통 채널 필요",
        "risk_signals": "해외 시장 언급 없음 — 해외 경험 부재 신호",
    },
    "open_questions": ["협력 의향(판매/구매)은 어느 정도인가요?"],
}


class FakeExtractor:
    def __init__(self):
        self.calls = []

    def extract_json(self, system, user, schema, deep=False):
        assert schema == EXTRACTION_SCHEMA
        self.calls.append(user)
        return FAKE_EXTRACTION


class TestExtractor:
    def test_extract_profile_with_provenance_and_evidence(self):
        chunks = chunk_text("다이브인그룹은 노후 객실을 전환한다", "a1:website")
        fake = FakeExtractor()
        profile, open_qs, evidence = extract_profile(chunks, fake)
        assert profile.problem_solved.provenance.value == "inferred"
        assert profile.problem_solved.confidence == 0.85       # REP-03
        assert evidence["solution"] == ["a1:website#1", "a2:instagram#1"]  # ING-04
        assert open_qs
        assert "[a1:website#1]" in fake.calls[0]               # 청크 라벨 전달
        # 회사의 상(像) — 다층 독해 결과가 프로필 계약으로 전달된다
        assert profile.portrait is not None
        assert "절실" in profile.portrait.stage_narrative
        assert profile.portrait.gaps


# ── ING-05: 키 유무에 따른 모드 전환 (E2E) ──────────────────────────

class TestEngineMode:
    def test_mock_mode_without_key(self):
        """키 없음 → mock 모드 표기 (조용한 degrade 금지)."""
        res = client.post("/v1/represent", json={"assets": [{
            "type": "text",
            "content": ("이름: 미니컴퍼니\n국가: 한국\n산업: saas\n설명: 스타트업\n"
                        "문제: 재고 비효율\n솔루션: AI 예측\n타겟: 유통사\n판매가치: 비용")}]})
        assert res.status_code == 200
        assert res.json()["engine_mode"] == "mock"

    def test_llm_mode_with_extractor(self, monkeypatch):
        """추출기 주입 시 → llm 모드 + evidence 포함 응답."""
        monkeypatch.setattr(represent_module, "get_extractor",
                            lambda settings: FakeExtractor())
        res = client.post("/v1/represent", json={"assets": [
            {"type": "website",
             "content": "다이브인그룹 — 노후 호텔 객실을 예술 경험형 상품으로 전환"}]})
        assert res.status_code == 200
        data = res.json()
        assert data["engine_mode"] == "llm"
        assert data["evidence"]["problem_solved"] == ["a1:website#1"]
        assert data["profile"]["problem_solved"]["provenance"] == "inferred"
        assert data["minimum_met"] is True

    def test_llm_mode_below_minimum_still_gated(self, monkeypatch):
        """LLM 경로에서도 최소 프로필 게이트(REP-06)는 동일하게 작동."""
        sparse = {**FAKE_EXTRACTION,
                  "problem_solved": {"value": "", "provenance": "ask",
                                     "confidence": None, "evidence_chunk_ids": []},
                  "open_questions": ["귀사가 해결하는 문제는 무엇인가요?"]}

        class SparseExtractor:
            def extract_json(self, system, user, schema, deep=False):
                return sparse

        monkeypatch.setattr(represent_module, "get_extractor",
                            lambda settings: SparseExtractor())
        res = client.post("/v1/represent", json={"assets": [
            {"type": "website", "content": "자료가 빈약한 회사 소개"}]})
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "profile_below_minimum"
