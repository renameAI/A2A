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


class TestMailShape:
    """메일이 한 덩어리로 나오지 않게 — 실측: 스페인어 초안 519자 1단락."""

    def test_prompt_sends_paragraphs_as_an_array(self):
        """개행 지시로는 안 됐다 — 배열로 받아 코드가 잇는다."""
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM as P
        assert "`paragraphs` 배열" in P
        assert "개행을 넣지 마라" in P
        assert "글자 수로 세지 마라" in P

    def test_prompt_counts_sentences_not_characters(self):
        """글자 수만 적으면 언어마다 다르게 해석된다."""
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM as P
        assert "2~4문장" in P

    def test_each_paragraph_has_a_job(self):
        """길게가 아니라 전략적으로 — 라포·근거·경첩·연결·문턱."""
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM as P
        for role in ("라포", "근거", "만난다면", "연결", "문턱 낮추기"):
            assert role in P, role


class TestReferenceDerivedRules:
    """실제 발송 메일 160여 통에서 뽑은 규칙 — 각 규칙에 정량 근거가 붙어 있다."""

    def test_hinge_paragraph_required(self):
        """관측 뒤에 소개를 바로 붙이면 관측이 판매 미끼로 읽힌다(영어 64/64)."""
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM as P
        assert "만난다면 어떨까요" in P and "64/64" in P

    def test_references_are_used_but_never_invented(self):
        """_user()가 레퍼런스를 넘기는데 프롬프트가 쓰라고 한 적이 없었다."""
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM as P
        assert "종속절로" in P
        assert "절대 지어내지 마라" in P

    def test_hedge_is_capped(self):
        """이중·삼중 완충은 여지가 아니라 과제 자체를 지운다."""
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM as P
        assert "완충 표현은 문장당 하나까지" in P

    def test_subject_must_name_the_recipient(self):
        """회사명 없는 제목은 같은 업종 누구에게나 그대로 맞는다."""
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM as P
        assert "제목에는 수신 회사 이름을 넣는다" in P

    def test_variants_split_by_asset_not_tone(self):
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM as P
        assert "톤·인사말·CTA가 아니라" in P
        assert "초안도 하나만 낸다" in P

    def test_greeting_and_signoff_are_required(self):
        """우리 독일어 실측 출력에는 인사말도 맺음말도 없었다."""
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM as P
        assert "관용 인사" in P and "맺음 인사" in P

    def test_paragraph_count_grew_for_the_hinge(self):
        from app.engine.compose_lead import COMPOSE_LEAD_SCHEMA as S
        pg = S["properties"]["drafts"]["items"]["properties"]["paragraphs"]
        assert pg["minItems"] == 4 and pg["maxItems"] == 6

    def test_generic_flattery_is_named_as_the_failure(self):
        """어느 회사에나 붙는 칭찬은 라포가 아니라 대량 발송의 표식이다."""
        from app.engine.compose_lead import COMPOSE_LEAD_SYSTEM as P
        assert "회사에나" in P and "붙는 칭찬" in P


class TestBusinessLanguage:
    """번역 페이지를 읽고 그 언어로 메일을 쓰면 상대가 못 읽는다."""

    def test_prompt_prefers_legal_form_over_page_language(self):
        from app.engine.company_ontology import ONTOLOGY_SYSTEM as P
        assert "GmbH" in P and "법인 쪽을 따른다" in P


class TestParagraphAssembly:
    """단락 나눔은 모델의 판정, 이어 붙이는 것은 코드의 결정."""

    def test_joined_with_blank_lines(self):
        from app.engine.compose_lead import _join
        assert _join(["가", "나", "다"]) == "가\n\n나\n\n다"

    def test_empty_paragraphs_dropped(self):
        from app.engine.compose_lead import _join
        assert _join(["가", "  ", "", "나"]) == "가\n\n나"

    def test_none_is_empty(self):
        from app.engine.compose_lead import _join
        assert _join(None) == ""

    def test_schema_requires_paragraph_array(self):
        """실측: body를 문자열로 받는 한 지시를 세 번 고쳐도 한 덩어리였다."""
        from app.engine.compose_lead import COMPOSE_LEAD_SCHEMA as S
        item = S["properties"]["drafts"]["items"]
        assert "paragraphs" in item["required"]
        assert item["properties"]["paragraphs"]["minItems"] >= 3
        assert "body" not in item["properties"]


class TestHookUrlFallback:
    """근거 링크 — 우리가 읽은 주소가 있는데 메일이 링크 없이 나갔다."""

    def test_falls_back_to_the_page_we_read(self):
        from app.engine.compose_lead import _hook_url
        kit = {"hook_url": "", "channel_value": "https://fkur.com/es/contacto/"}
        assert _hook_url(kit, ["https://fkur.com/es/noticias/x"]) \
            == "https://fkur.com/es/noticias/x"

    def test_rejects_a_third_party_url(self):
        """제3자 주소를 '귀사의 페이지'로 인용하면 열어보고 어긋난다."""
        from app.engine.compose_lead import _hook_url
        kit = {"hook_url": "", "channel_value": "https://fkur.com/kontakt"}
        assert _hook_url(kit, ["https://news.example.com/a"]) == ""

    def test_explicit_hook_url_wins(self):
        from app.engine.compose_lead import _hook_url
        kit = {"hook_url": "https://a.com/x", "channel_value": "https://a.com/"}
        assert _hook_url(kit, ["https://a.com/y"]) == "https://a.com/x"

    def test_no_channel_means_no_guess(self):
        from app.engine.compose_lead import _hook_url
        assert _hook_url({}, ["https://a.com/y"]) == ""


class TestReachabilityWording:
    """설명과 표시의 방향이 어긋나면 안 된다."""

    def test_prompt_forbids_the_inverted_word(self):
        """화면은 '가능성'(클수록 좋음), 설명이 '문턱'이면 뒤집혀 읽힌다."""
        from app.engine.company_ontology import ONTOLOGY_SYSTEM as P
        assert '"문턱"이라는 말을 쓰지 마라' in P

    def test_version_bumped_so_old_reads_are_not_served(self):
        from app.engine.company_ontology import ONTOLOGY_VERSION
        assert ONTOLOGY_VERSION >= 4


class TestRerankOverLargePool:
    """풀이 상한을 넘어도 재랭킹이 죽지 않아야 한다."""

    def test_k_accepts_a_pool_past_fifty(self):
        """실측: 풀 59에서 '1 validation error … k … less_than_equal 50'."""
        from app.schemas import (RetrieveRequest, RetrieveDirection,
                                 PoolChoice, Profile, BasicInfo, ProvField,
                                 Provenance, Intent)
        prof = Profile(
            basic=BasicInfo(name="우리", country="KR", industry="x"),
            description="", problem_solved=ProvField(value="a",
                provenance=Provenance.inferred, confidence=0.5),
            solution=ProvField(value="b", provenance=Provenance.inferred,
                               confidence=0.5),
            target_customer=ProvField(value="c", provenance=Provenance.inferred,
                                      confidence=0.5))
        req = RetrieveRequest(requester_profile=prof, intent=Intent(value_props=["revenue_growth"]),
                              direction=RetrieveDirection.sell_outreach,
                              pool=PoolChoice.both, k=59)
        assert req.k == 59

    def test_call_site_clamps_to_the_schema_bound(self):
        """상한을 코드에 다시 적으면 어긋나는 순간 같은 사고가 난다."""
        from app.saas.router import _K_MAX
        from app.schemas import RetrieveRequest
        le = next(m.le for m in RetrieveRequest.model_fields["k"].metadata
                  if hasattr(m, "le"))
        assert _K_MAX == le
        assert min(max(30, 1200), _K_MAX) == _K_MAX


class TestGetRequestPayload:
    """클릭 한 번의 응답 — 실측 342KB 중 83%가 클라이언트가 안 읽는 pool."""

    def test_local_store_get_many(self, tmp_path):
        from app.saas.store import LocalSaasStore
        st = LocalSaasStore(str(tmp_path / "t.db"))
        for i in range(3):
            st.put("insight", "ws", f"k{i}", {"n": i})
        got = st.get_many("insight", "ws", ["k0", "k2", "없는키"])
        assert got == {"k0": {"n": 0}, "k2": {"n": 2}}

    def test_get_many_empty_ids(self, tmp_path):
        from app.saas.store import LocalSaasStore
        st = LocalSaasStore(str(tmp_path / "t.db"))
        assert st.get_many("insight", "ws", []) == {}

    def test_response_excludes_pool(self):
        """전송만 빼고 저장은 그대로 — 재랭킹은 저장소의 pool을 읽는다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.get_request)
        assert 'k not in ("pool", "search_brief")' in src


class TestRecombineIsSingle:
    """점수 재합성은 한 곳 — 활성 튜닝 계수가 두 벌이면 소리 없이 어긋난다."""

    def test_coefficient_appears_once(self):
        import pathlib
        src = pathlib.Path("app/saas/router.py").read_text(encoding="utf-8")
        assert src.count("0.35 + 0.65") == 1

    def test_replied_fact_beats_estimate(self):
        from app.saas.router import recombine_score
        raw, w = recombine_score(0.5, 0.8, 0.08, True, 0)
        assert w == 1.0 and abs(raw - 0.4) < 1e-9

    def test_none_reach_is_no_penalty(self):
        from app.saas.router import recombine_score
        _, w = recombine_score(0.5, 0.8, None, False, 0)
        assert w == 1.0

    def test_matches_the_tuned_formula(self):
        """귤메달 실측으로 고정된 계수 — 바꾸려면 평가 하네스를 통과해야 한다."""
        from app.saas.router import recombine_score
        raw, w = recombine_score(0.6, 0.9, 0.58, False, 0.01)
        assert abs(w - (0.35 + 0.65 * 0.58)) < 1e-9
        assert abs(raw - (0.6 * 0.9 * w + 0.01)) < 1e-9


class TestUnreadableProperNouns:
    """독일어 메일의 'OB맥주 und 아모레퍼시픽' — 상호에만 있던 규칙이
    레퍼런스에서 재발했다. 읽을 수 있는가는 코드가 판정한다."""

    def test_detects_hangul_hanja_kana(self):
        from app.engine.compose_lead import _unreadable
        assert _unreadable("아모레퍼시픽") and _unreadable("弊社") \
            and _unreadable("株式会社")
        assert not _unreadable("Amorepacific") and not _unreadable("")

    def _req(self, lang, refs, name="㈜더데이원랩", latin=""):
        from app.schemas import (BasicInfo, CandidateInsight,
                                 ComposeLeadRequest, Intent, Profile,
                                 ProvField, Provenance)
        pf = ProvField(value="x", provenance=Provenance.inferred, confidence=0.5)
        prof = Profile(basic=BasicInfo(name=name, name_latin=latin,
                                       country="KR", industry="i"),
                       description="", problem_solved=pf, solution=pf,
                       target_customer=pf, references=refs)
        return ComposeLeadRequest(
            requester_profile=prof, intent=Intent(value_props=["revenue_growth"]),
            candidate_profile=prof,
            candidate_insight=CandidateInsight(candidate_id="c1"), language=lang)

    def test_flags_unreadable_references(self):
        from app.engine.compose_lead import _name_notes
        note = _name_notes(self._req("de", ["OB맥주", "아모레퍼시픽"]))
        assert "OB맥주" in note and "확실히 아는 경우에만" in note

    def test_silent_when_everything_is_readable(self):
        from app.engine.compose_lead import _name_notes
        assert _name_notes(
            self._req("de", ["Samsung"], name="DayOne", latin="DayOne")) == ""

    def test_korean_mail_needs_no_note(self):
        """한국어 메일에서 한글 상호는 문제가 아니다."""
        from app.engine.compose_lead import _name_notes
        assert _name_notes(self._req("ko", ["OB맥주"])) == ""

    def test_forbids_both_hangul_and_invention(self):
        from app.engine.compose_lead import _name_notes
        note = _name_notes(self._req("de", ["아모레퍼시픽"]))
        assert "지어내는 것은" in note and "둘 다 금지" in note
