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


class TestReadingLayer:
    """축 나열은 "그래서 연락할까"에 답하지 못한다 — 읽기 층이 그 자리다.

    기획안 §7.2: 관측 사실과 AI 추론을 구분한다. situation·fit은 관측,
    inference는 추론, unknowns는 이메일에서 단정하면 안 되는 것들.
    """
    def test_reading_is_normalised(self):
        from app.engine.company_ontology import _clean_reading
        r = _clean_reading({"situation": " 상황 ", "fit": "접점",
                            "inference": " 추론", "unknowns": ["a", "", " b "]})
        assert r["situation"] == "상황" and r["inference"] == "추론"
        assert r["unknowns"] == ["a", "b"]

    def test_missing_reading_is_empty_not_absent(self):
        """화면이 바로 읽으므로 키가 없으면 안 된다 — 빈 값으로 채운다."""
        from app.engine.company_ontology import _clean_reading
        for bad in (None, "문자열", [], 3):
            r = _clean_reading(bad)
            assert set(r) == {"situation", "fit", "inference", "unknowns"}
            assert r["unknowns"] == []

    def test_unknowns_are_capped(self):
        from app.engine.company_ontology import _clean_reading
        assert len(_clean_reading({"unknowns": list("abcdefg")})["unknowns"]) == 4

    def test_prompt_separates_observation_from_inference(self):
        from app.engine.company_ontology import ONTOLOGY_SYSTEM
        assert "situation·fit은 관측, inference는 추론" in ONTOLOGY_SYSTEM
        assert "…로 보인다/추정된다" in ONTOLOGY_SYSTEM
        assert "비워 두지 마라" in ONTOLOGY_SYSTEM


class TestAxisFit:
    """축별 적합도 — 레이더로 강약을 보여주려면 축마다 값이 갈려야 한다."""

    def test_unknown_axis_is_neutral_not_bad(self):
        """모름을 0으로 두면 화면이 '나쁨'으로 그린다."""
        from app.engine.company_ontology import AXES, read_company

        class C:
            def extract_json(self, *a, **k):
                return {"axes": {x: {"value": "", "status": "unknown",
                                     "evidence": ""} for x, _ in AXES},
                        "search_keywords": [], "signals": [], "contacts": [],
                        "business_language": "", "reachability": {"p": .5, "why": ""},
                        "why_now": {"text": "", "source_url": ""},
                        "reading": {}}
        ont = read_company(C(), {"name": "Fit", "what": "w", "signal": "",
                                 "url": "https://fit.example"})
        assert all(a.fit == 0.5 for a in ont.axes.values())

    def test_prompt_forbids_a_flat_radar(self):
        """전 축 같은 점수는 레이더를 원으로 만든다 — 실측으로 겪었다."""
        from app.engine.company_ontology import ONTOLOGY_SYSTEM
        assert "축마다 다른 값이 나와야 한다" in ONTOLOGY_SYSTEM
        assert "레이더가 원이 되어" in ONTOLOGY_SYSTEM


class TestScoreCalibration:
    """로지스틱 보정 — 순위는 그대로, 폭만 넓힌다."""

    def test_monotonic(self):
        """순위를 바꾸면 보정이 아니라 재정렬이다."""
        from app.saas.router import calibrate_score
        raw = [0.005, 0.045, 0.076, 0.099, 0.13, 0.174, 0.236, 0.303]
        out = [calibrate_score(x) for x in raw]
        assert out == sorted(out)

    def test_spreads_the_measured_range(self):
        """실측 구간(0.005~0.303)이 0.2~0.95로 펴져야 한다."""
        from app.saas.router import calibrate_score
        assert calibrate_score(0.005) < 0.25
        assert calibrate_score(0.303) > 0.9
        # 중앙값은 한가운데
        assert abs(calibrate_score(0.10) - 0.5) < 0.01

    def test_bounded_and_safe(self):
        """0~1을 벗어나지 않고, 값이 없어도 터지지 않는다."""
        from app.saas.router import calibrate_score
        for x in (0.0, 1.0, 10.0, -5.0):
            assert 0.0 <= calibrate_score(x) <= 1.0
        assert calibrate_score(None) == 0.0


class TestAxisWhy:
    """축 수치의 근거 — 근거 없는 숫자는 판정이 아니라 장식이다."""

    def test_why_is_parsed(self):
        from app.schemas import OntologyAxis
        a = OntologyAxis(value="전세계 시장", status="confirmed",
                         fit=0.8, why="수출 경로가 이미 있어 개설이 불필요")
        assert "수출 경로" in a.why

    def test_why_defaults_empty(self):
        """모델이 이유를 안 주면 빈 문자열 — 있는 척하지 않는다."""
        from app.schemas import OntologyAxis
        assert OntologyAxis(value="x", status="assumed").why == ""

    def test_schema_requires_why(self):
        from app.engine.company_ontology import ONTOLOGY_SCHEMA
        ax = ONTOLOGY_SCHEMA["properties"]["axes"]["properties"]
        one = next(iter(ax.values()))
        assert "why" in one["required"]


class TestOntologyCacheVersion:
    """캐시가 구버전 판독을 붙잡지 못하게."""

    def test_version_is_in_the_key(self):
        """실측: fit·why를 넣었는데 배포 후 축 점수가 전부 비었다 — 캐시였다."""
        from app.engine.company_ontology import ONTOLOGY_VERSION, _cache_key
        k = _cache_key({"name": "A", "url": "https://a.com"},
                       "JP", "revenue", "우리", True)
        assert k[0] == ONTOLOGY_VERSION

    def test_bumping_version_changes_the_key(self):
        from app.engine import company_ontology as m
        args = ({"name": "A", "url": "https://a.com"}, "JP", "revenue", "우리", True)
        before = m._cache_key(*args)
        old = m.ONTOLOGY_VERSION
        try:
            m.ONTOLOGY_VERSION = old + 1
            assert m._cache_key(*args) != before
        finally:
            m.ONTOLOGY_VERSION = old
