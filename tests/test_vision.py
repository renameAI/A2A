"""근거 시각화(bbox) 테스트 — GEMINI_API_KEY 없이도 전부 오프라인.

실제 Gemini 호출은 FakeVisionExtractor로 대체한다. render_pdf_pages/이미지
저장/스레드 강제 응답 게이트는 전부 실제 코드 경로를 그대로 탄다.
"""
import fitz
import pytest
from fastapi.testclient import TestClient

import app.product.router as router_module
from app.main import app
from tests.test_product import DIVEIN_TEXT, _run_job

client = TestClient(app)


def _make_pdf(tmp_path) -> str:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "DiveIn Group IR Deck")
    path = tmp_path / "deck.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


class FakeVisionExtractor:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[int] = []

    def locate(self, image_png, target_fields, page):
        self.calls.append(page)
        return self.responses.get(page, [])


def _onboard_with_deck(tmp_path, fake=None, monkeypatch=None):
    if fake is not None:
        monkeypatch.setattr(router_module, "get_vision_extractor", lambda settings: fake)
    pdf_path = _make_pdf(tmp_path)
    job = _run_job("/product/onboard", {
        "assets": [{"type": "ir_deck", "content": "", "url": pdf_path},
                   {"type": "text", "content": DIVEIN_TEXT}]})
    assert job["status"] == "done", job.get("error")
    return job["result"]


class TestVisionDisabledByDefault:
    def test_no_key_means_no_grounding(self, tmp_path):
        """GEMINI_API_KEY 없으면 아무 일도 안 하고 온보딩은 평소대로 성공한다."""
        result = _onboard_with_deck(tmp_path)
        assert result["visual_evidence_count"] == 0
        assert result["open_thread_count"] == 0


class TestVisionGrounding:
    def test_evidence_and_unclear_thread_created(self, tmp_path, monkeypatch):
        fake = FakeVisionExtractor({1: [
            {"field": "problem_solved", "quote": "노후 호텔 객실 매출 정체",
             "box_2d": [100, 100, 200, 400], "confidence": 0.9, "unclear": False},
            {"field": "portrait.stage_narrative", "quote": "동남아 진출 준비",
             "box_2d": [300, 100, 400, 500], "confidence": 0.4,
             "unclear": True, "unclear_reason": "연도 표기가 없어 시점을 특정할 수 없음"},
        ]})
        result = _onboard_with_deck(tmp_path, fake, monkeypatch)
        company_id = result["company_id"]
        assert result["visual_evidence_count"] == 2
        assert result["open_thread_count"] == 1
        assert fake.calls == [1]        # 1페이지 PDF → 1회 호출

        ev = client.get(f"/product/companies/{company_id}/evidence").json()
        assert len(ev["evidence"]) == 2
        assert len(ev["threads"]) == 1
        thread = ev["threads"][0]
        assert thread["status"] == "open"
        assert thread["comments"][0]["author"] == "ai"
        assert "연도 표기" in thread["comments"][0]["text"]

        # 박스 좌표가 그대로 보존됐는지 (ymin,xmin,ymax,xmax)
        unclear_ev = next(e for e in ev["evidence"] if e["unclear"])
        assert unclear_ev["box"] == {"ymin": 300, "xmin": 100, "ymax": 400, "xmax": 500}

    def test_open_thread_blocks_match_until_answered(self, tmp_path, monkeypatch):
        """강제 응답 — 불명확 스레드가 열려있으면 매칭이 막히고, 답하면 풀린다."""
        fake = FakeVisionExtractor({1: [
            {"field": "portrait.gaps", "quote": "해외 실행 경험 부재",
             "box_2d": [10, 10, 50, 200], "confidence": 0.3,
             "unclear": True, "unclear_reason": "표현이 모호함"},
        ]})
        result = _onboard_with_deck(tmp_path, fake, monkeypatch)
        company_id = result["company_id"]
        thread_id = client.get(f"/product/companies/{company_id}/evidence") \
            .json()["threads"][0]["thread_id"]

        blocked = client.post("/product/match", json={
            "company_id": company_id,
            "intent": {"value_props": ["revenue_growth"], "target_region": "베트남"}})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "unclear_evidence_unresolved"

        reply = client.post(
            f"/product/companies/{company_id}/threads/{thread_id}/reply",
            json={"text": "맞습니다 — 첫 해외 레퍼런스입니다."})
        assert reply.status_code == 200
        assert reply.json()["status"] == "resolved"
        assert reply.json()["comments"][-1]["author"] == "human"

        job = _run_job("/product/match", {
            "company_id": company_id,
            "intent": {"value_props": ["revenue_growth"], "target_region": "베트남"}})
        assert job["status"] == "done"

    def test_reply_unknown_thread_404(self, tmp_path):
        result = _onboard_with_deck(tmp_path)
        res = client.post(
            f"/product/companies/{result['company_id']}/threads/th-없음/reply",
            json={"text": "답변"})
        assert res.status_code == 404

    def test_page_image_served_and_traversal_blocked(self, tmp_path, monkeypatch):
        fake = FakeVisionExtractor({1: []})
        result = _onboard_with_deck(tmp_path, fake, monkeypatch)
        company_id = result["company_id"]

        res = client.get(f"/product/pages/{company_id}/a0_p1.png")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/png"

        traversal = client.get(f"/product/pages/{company_id}/..%2f..%2fapp.py")
        assert traversal.status_code == 404

        missing = client.get(f"/product/pages/{company_id}/a9_p9.png")
        assert missing.status_code == 404
