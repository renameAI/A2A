"""왜 지금 — 카드 전면의 한 문장과, 그것을 만들 재료가 모델에 닿는지.

실측으로 두 번 막혔다:
1) "문의 창구가 있다"처럼 늘 그런 상태를 why_now로 냈다 — 접점이지 타이밍이
   아니다. 반년 전에 써도 맞는 문장이면 why_now가 아니라는 기준을 넣었다.
2) 기준을 넣자 전부 빈칸이 됐는데, 원인은 판정이 아니라 **자료 절단**이었다.
   앞에서부터 9,000자를 자르니 크롤러가 우선순위로 담은 뒷페이지(채용·뉴스)가
   통째로 날아갔다 — UNDO 19,589자 중 앞 9,000자에 'career' 0회.
"""
from app.engine.company_ontology import SITE_TEXT_MAX, _fit_pages


def _site(*pages):
    return "".join(f"[페이지: https://x.com/{n}]\n{body}\n" for n, body in pages)


class TestPageBudget:
    def test_short_text_is_untouched(self):
        t = _site(("", "홈"), ("careers", "채용"))
        assert _fit_pages(t, SITE_TEXT_MAX) == t

    def test_every_page_survives_the_budget(self):
        """뒷페이지가 통째로 사라지면 크롤 우선순위가 무의미해진다."""
        t = _site(("", "홈" * 6000), ("contact", "문의처 010"),
                  ("careers", "물류센터 담당 채용"), ("news", "신규 출점 공시"))
        fit = _fit_pages(t, 8000)
        assert len(fit) <= 8600
        for marker in ("/contact", "/careers", "/news"):
            assert marker in fit, marker
        assert "물류센터 담당 채용" in fit and "신규 출점 공시" in fit

    def test_long_pages_share_the_leftover(self):
        """짧은 페이지가 남긴 몫은 긴 페이지가 나눠 갖는다 — 예산을 버리지 않는다."""
        t = _site(("", "짧"), ("a", "가" * 5000), ("b", "나" * 5000))
        fit = _fit_pages(t, 6000)
        assert fit.count("가") > 1000 and fit.count("나") > 1000

    def test_text_without_page_markers_falls_back_to_truncation(self):
        assert len(_fit_pages("가" * 5000, 1000)) == 1000


class TestWhyNowPrompt:
    def test_prompt_demands_an_event_not_a_standing_state(self):
        from app.engine.company_ontology import ONTOLOGY_SYSTEM
        assert "반년 전에 써도 똑같이 맞다면 why_now가 아니다" in ONTOLOGY_SYSTEM
        assert "늘 그래 온 상태" in ONTOLOGY_SYSTEM
        # 채용에 국한하지 않는다 — 출점·투자·신사업도 근거다
        assert "채용에 국한하지 않는다" in ONTOLOGY_SYSTEM

    def test_ongoing_recruiting_counts_as_an_event(self):
        """날짜가 없어도 '모집 중'은 지금 벌어지는 일이다."""
        from app.engine.company_ontology import ONTOLOGY_SYSTEM
        assert "날짜가 적혀 있어야만 사건인 것은 아니다" in ONTOLOGY_SYSTEM

    def test_source_must_be_a_page_actually_read(self):
        from app.engine.company_ontology import _cited_url
        text = "[페이지: https://x.com/news]\n본문"
        assert _cited_url("https://x.com/news", text) == "https://x.com/news"
        assert _cited_url("https://x.com/made-up", text) == ""


def test_why_now_reaches_the_mail_kit():
    """판독이 고른 근거를 메일이 다시 지어내지 않게, 재료로 넘긴다."""
    from app.engine.candidate_insight import _ontology_block
    blk = _ontology_block({"contacts": [], "signals": [], "axes": {},
                           "why_now": "신규 물류센터 가동",
                           "why_now_source": "https://x.com/news"})
    assert "판독이 이미 고른 근거" in blk and "신규 물류센터 가동" in blk
    assert "https://x.com/news" in blk
    # 없으면 아무 말도 하지 않는다
    assert "판독이 이미 고른 근거" not in _ontology_block(
        {"contacts": [], "signals": [], "axes": {}})
