"""질문 위치 탐지(bbox) 테스트 — GEMINI_API_KEY 없이도 전부 오프라인.

역할 분리: 엑사원(추론)이 질문을 만들고, VLM은 위치만 찾는다. 실제 Gemini
호출은 FakeVisionExtractor로 대체하되, 엑사원 질문(open_questions)은 실제
represent 파이프라인이 만든 것을 그대로 받는다. render_pdf_pages/이미지 저장/
스레드 강제 응답 게이트는 전부 실제 코드 경로를 탄다.
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
    """엑사원 질문을 받아 위치를 돌려주는 VLM 대역. questions 인자로 실제 질문이
    흘러오는지 검증할 수 있게 마지막 호출의 질문 목록을 기록한다."""
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[int] = []
        self.seen_questions: list[str] = []

    def locate(self, image_png, questions, page):
        self.calls.append(page)
        self.seen_questions = questions
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
    def test_no_key_means_no_pins(self, tmp_path):
        """GEMINI_API_KEY 없으면 아무 일도 안 하고 온보딩은 평소대로 성공한다."""
        result = _onboard_with_deck(tmp_path)
        assert result["question_pin_count"] == 0
        assert result["open_thread_count"] == 0


class TestQuestionPinning:
    def test_exaone_question_pinned_and_thread_created(self, tmp_path, monkeypatch):
        """엑사원 질문이 VLM에 전달되고, 찾은 위치마다 핀 + 열린 스레드가 생긴다.
        DIVEIN은 의향 미기재라 open_question이 정확히 1개('협력 의향...')다."""
        fake = FakeVisionExtractor({1: [
            {"question_index": 0, "quote": "매출 쉐어 구조",
             "box_2d": [300, 100, 400, 500]},
        ]})
        result = _onboard_with_deck(tmp_path, fake, monkeypatch)
        company_id = result["company_id"]
        assert result["question_pin_count"] == 1
        assert result["open_thread_count"] == 1
        assert fake.calls == [1]        # 1페이지 PDF → 1회 호출

        # VLM이 받은 질문 = 엑사원이 만든 실제 open_question (VLM이 만든 게 아님)
        assert fake.seen_questions == result["open_questions"]
        assert any("의향" in q for q in fake.seen_questions)

        ev = client.get(f"/product/companies/{company_id}/evidence").json()
        assert len(ev["pins"]) == 1
        assert len(ev["threads"]) == 1
        thread = ev["threads"][0]
        assert thread["status"] == "open"
        assert thread["comments"][0]["author"] == "ai"
        # 스레드 첫 댓글 = 엑사원 질문 원문 그대로
        assert thread["comments"][0]["text"] == result["open_questions"][0]

        # 핀에 질문 원문이 담기고, 박스 좌표가 그대로 보존됐는지 (ymin,xmin,ymax,xmax)
        pin = ev["pins"][0]
        assert pin["question"] == result["open_questions"][0]
        assert pin["box"] == {"ymin": 300, "xmin": 100, "ymax": 400, "xmax": 500}

    def test_out_of_range_index_dropped(self, tmp_path, monkeypatch):
        """모델이 범위 밖 question_index를 주면 그 위치는 버린다 (질문 1개뿐인데 5번)."""
        fake = FakeVisionExtractor({1: [
            {"question_index": 5, "quote": "엉뚱", "box_2d": [0, 0, 10, 10]},
        ]})
        result = _onboard_with_deck(tmp_path, fake, monkeypatch)
        assert result["question_pin_count"] == 0
        assert result["open_thread_count"] == 0

    def test_open_thread_blocks_match_until_answered(self, tmp_path, monkeypatch):
        """강제 응답 — 미응답 스레드가 열려있으면 매칭이 막히고, 답하면 풀린다."""
        fake = FakeVisionExtractor({1: [
            {"question_index": 0, "quote": "매출 쉐어 구조",
             "box_2d": [10, 10, 50, 200]},
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
            json={"text": "판매 의향은 매우 적극적입니다."})
        assert reply.status_code == 200
        body = reply.json()
        assert body["thread"]["status"] == "resolved"
        assert body["thread"]["comments"][-1]["author"] == "human"
        assert body["open_thread_count"] == 0
        assert body["answered_count"] == 1     # 소통 루프에 답변 1건 축적

        job = _run_job("/product/match", {
            "company_id": company_id,
            "intent": {"value_props": ["revenue_growth"], "target_region": "베트남"}})
        assert job["status"] == "done"

    def test_answer_feeds_reanalysis(self, tmp_path, monkeypatch):
        """소통 루프 — 핀에 단 답변이 재분석 때 엑사원에게 되먹임돼 프로필이 개선되고,
        그 질문이 open_questions에서 사라진다 (핀 자동 해소)."""
        fake = FakeVisionExtractor({1: [
            {"question_index": 0, "quote": "매출 쉐어 구조",
             "box_2d": [10, 10, 50, 200]},
        ]})
        result = _onboard_with_deck(tmp_path, fake, monkeypatch)
        company_id = result["company_id"]
        assert any("의향" in q for q in result["open_questions"])

        thread_id = client.get(f"/product/companies/{company_id}/evidence") \
            .json()["threads"][0]["thread_id"]
        # Mock 파서가 읽는 형태로 의향을 답한다 (질문↔필드 매핑은 백엔드가 처리)
        client.post(f"/product/companies/{company_id}/threads/{thread_id}/reply",
                    json={"text": "매우 적극적"})

        # 같은 회사 재온보딩 → 서버가 답변을 dialogue로 병합해 엑사원에 전달
        pdf_path = _make_pdf(tmp_path)
        reanalyzed = _run_job("/product/onboard", {
            "company_id": company_id,
            "assets": [{"type": "ir_deck", "content": "", "url": pdf_path},
                       {"type": "text", "content": DIVEIN_TEXT}]})
        assert reanalyzed["status"] == "done"
        # 의향 질문이 답변으로 채워져 open_questions에서 사라진다
        assert not any("의향" in q for q in reanalyzed["result"]["open_questions"])

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
