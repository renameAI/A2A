"""학습 스코어러 배관 테스트 — HTTP mock, 재랭킹, 정직 폴백. 완전 오프라인.

계약: τ 게이트는 휴리스틱이 유지하고, 학습 점수는 게이트 통과 후보의 '순서'만
바꾼다. 서버 부재 시 조용한 대체 없이 휴리스틱 순서 그대로 (learned=None).
"""
import httpx
import pytest

import app.engine.retrieve as R
import app.engine.scorer_client as SC
from app.engine.pool import CandidateRecord
from app.schemas import (BasicInfo, Intent, PoolKind, Profile, ProvField,
                         Provenance, RetrieveDirection, RetrieveRequest,
                         ValueProp)


def _prof(name="다이브인그룹"):
    def f(v):
        return ProvField(value=v, provenance=Provenance.stated, confidence=None)
    return Profile(basic=BasicInfo(name=name, country="한국", industry="hospitality"),
                   description="노후 호텔 재생", problem_solved=f("노후 호텔 객실의 매출 정체"),
                   solution=f("저자본 예술 전환"), target_customer=f("중소 호텔 오너"),
                   sell_value_props=[ValueProp.revenue_growth])


def _cand(cid, pain):
    p = _prof(name=f"기업{cid}")
    return CandidateRecord(company_id=cid, pool=PoolKind.external, profile=p,
                           pain_points=pain, tags=["노후 객실"])


def _req():
    return RetrieveRequest(requester_profile=_prof(),
                           intent=Intent(value_props=[ValueProp.revenue_growth],
                                         target_region="한국"),
                           direction=RetrieveDirection.sell_outreach)


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._p


class TestScoreBatch:
    def test_off_without_url(self, monkeypatch):
        # delenv로는 못 지운다 — get_settings()가 매번 .env를 setdefault로 다시 부어
        # 삭제한 키가 되살아난다(.env에 A2A_SCORER_URL이 있다). 빈 값으로 덮어야 이긴다.
        monkeypatch.setenv("A2A_SCORER_URL", "")
        assert SC.score_batch([("a", "b")]) is None

    def test_success(self, monkeypatch):
        monkeypatch.setenv("A2A_SCORER_URL", "http://localhost:8500")
        monkeypatch.setattr(SC.httpx, "post", lambda *a, **k: _Resp(
            {"scores": [{"score": 7.2}, {"score": 2.1}]}))
        assert SC.score_batch([("a", "b"), ("c", "d")]) == [7.2, 2.1]

    def test_connection_error_falls_back(self, monkeypatch):
        monkeypatch.setenv("A2A_SCORER_URL", "http://localhost:1")   # 연결 불가
        def boom(*a, **k):
            raise httpx.ConnectError("refused")
        monkeypatch.setattr(SC.httpx, "post", boom)
        assert SC.score_batch([("a", "b")]) is None

    def test_count_mismatch_falls_back(self, monkeypatch):
        """부분 응답은 순서 정합성이 깨지므로 전체 폴백 — 부분 채택 없음."""
        monkeypatch.setenv("A2A_SCORER_URL", "http://localhost:8500")
        monkeypatch.setattr(SC.httpx, "post", lambda *a, **k: _Resp(
            {"scores": [{"score": 7.2}]}))
        assert SC.score_batch([("a", "b"), ("c", "d")]) is None


class TestRetrieveRerank:
    PAIN = "노후 호텔 객실 매출 정체로 저자본 해법이 필요"

    @pytest.fixture(autouse=True)
    def _deterministic_anchor(self, monkeypatch):
        """상 합성을 결정적 템플릿으로 고정 — 여기서 검증하는 건 τ 게이트와 순서다.

        실 LLM을 타면 앵커가 실행마다 흔들려 후보가 τ 아래로 떨어지고, 배선과
        무관한 이유로 간헐 실패한다(실측: 같은 파일 두 번 돌려 매번 다른 2~3건 실패).
        """
        monkeypatch.setattr(R, "synthesize_counterpart", R.template_counterpart)

    def test_learned_order_overrides_heuristic_ties(self, monkeypatch):
        """동점(휴리스틱) 후보의 순서를 학습 점수가 결정 + 필드 채움."""
        pool = [_cand("co-aaa", self.PAIN), _cand("co-bbb", self.PAIN)]
        monkeypatch.setattr(R, "get_pool", lambda: pool)
        # co-bbb에 더 높은 학습 점수 — 휴리스틱 tie-break(id순)면 co-aaa가 앞이었다
        # retrieve()는 함수 안에서 scorer_client를 import하므로 호출 시점에
        # SC.score_batch_timed를 다시 읽는다 — 패치는 SC(원본 모듈)에 해야
        # 닿는다. 기존 테스트는 SC.score_batch(미사용 함수)를 패치해
        # 재랭킹 경로가 무검증으로 남아 있었다.
        monkeypatch.setattr(SC, "score_batch_timed", lambda pairs: ([3.0, 8.5], 5))
        res = R.retrieve(_req())
        assert [c.company_id for c in res.candidates][:2] == ["co-bbb", "co-aaa"]
        assert res.candidates[0].learned_relatedness == 8.5
        assert res.candidates[0].retrieval_score > 0    # 휴리스틱 점수도 보존

    def test_fallback_keeps_heuristic_order(self, monkeypatch):
        """서버 부재(None) — 순서·필드 모두 기존 동작 그대로 (회귀 0)."""
        pool = [_cand("co-aaa", self.PAIN), _cand("co-bbb", self.PAIN)]
        monkeypatch.setattr(R, "get_pool", lambda: pool)
        monkeypatch.setattr(SC, "score_batch_timed", lambda pairs: (None, None))
        res = R.retrieve(_req())
        assert [c.company_id for c in res.candidates][:2] == ["co-aaa", "co-bbb"]
        assert all(c.learned_relatedness is None for c in res.candidates)

    def test_gate_stays_heuristic(self, monkeypatch):
        """학습 점수가 높아도 τ 미달 후보는 못 들어온다 — 게이트 불변."""
        pool = [_cand("co-aaa", self.PAIN),
                _cand("co-zzz", "무관한 반도체 장비 수출")]   # τ 미달 예상
        monkeypatch.setattr(R, "get_pool", lambda: pool)
        monkeypatch.setattr(SC, "score_batch_timed",
                            lambda pairs: ([1.0] * len(pairs), 5))
        res = R.retrieve(_req())
        ids = [c.company_id for c in res.candidates]
        assert "co-zzz" not in ids                     # 학습 점수로 게이트 못 뚫음


def test_profile_facts_matches_training_format():
    t = SC.profile_facts("한화", "화학", "한국", "방산·화학 대기업")
    assert t.startswith("한화 — 산업 섹터: 화학, 국가: 한국.")
    assert "방산" in t


class TestListwisePermutation:
    """RankGPT(arXiv:2304.09542) listwise 순열 파서 — 후보 소실 0이 계약.

    LLM은 항목을 빠뜨리거나 중복·범위밖을 뱉는다. 조용히 후보를 삭제하면 랭킹에서
    회사가 증발하므로, 누락분은 원래 순서로 뒤에 붙여 **항상 전수를 보존**한다.
    """

    @staticmethod
    def _all_present(order, n):
        return order is None or sorted(order) == list(range(n))

    def test_normal_permutation(self):
        assert SC._parse_permutation("[2] > [1] > [3]", 3) == [1, 0, 2]

    def test_missing_items_appended_in_original_order(self):
        # [2]가 누락 — 뒤에 붙어 전수 보존
        assert SC._parse_permutation("[3] > [1]", 3) == [2, 0, 1]

    def test_duplicates_ignored(self):
        assert SC._parse_permutation("[1] > [1] > [2] > [3]", 3) == [0, 1, 2]

    def test_out_of_range_dropped(self):
        assert SC._parse_permutation("[9] > [2] > [1] > [3]", 3) == [1, 0, 2]

    def test_prose_and_fence_tolerated(self):
        assert SC._parse_permutation("순위: [2] > [3] > [1] 입니다", 3) == [1, 2, 0]
        fence = "```" + "[2] > [1] > [3]" + "```"
        assert SC._parse_permutation(fence, 3) == [1, 0, 2]

    def test_no_permutation_returns_none_for_fallback(self):
        # 정직 폴백 — 못 읽으면 휴리스틱 순서를 유지해야 하므로 None
        assert SC._parse_permutation("죄송합니다 순위를 매길 수 없습니다", 3) is None
        assert SC._parse_permutation("", 3) is None
        assert SC._parse_permutation("2 > 1 > 3", 3) is None   # 대괄호 없음

    def test_never_loses_candidates(self):
        for text in ("[2] > [1] > [3]", "[3] > [1]", "[9] > [2]", "[1]"):
            assert self._all_present(SC._parse_permutation(text, 3), 3)

    def test_listwise_off_without_key(self, monkeypatch):
        """키 없으면 HTTP 전에 (None, None) — 키를 명시적으로 비우고 검증한다.

        예전엔 conftest가 토큰을 비워줘서 그냥 통과했는데, mock 제거로 그 처리가
        사라진 뒤 이 테스트는 .env의 실 키로 **유료 호출을 때리고 있었다**
        (실측: (None, None) 대신 ([0, 1], 413ms) 반환). 전제를 테스트 안으로 옮긴다.
        """
        monkeypatch.setenv("FRIENDLI_TOKEN", "")   # .env는 setdefault라 env가 이긴다
        assert SC.api_rank_listwise("q", ["a", "b"]) == (None, None)


class TestIntentTierBeatsReranker:
    """재랭커는 **의도 제약을 덮어쓸 수 없다** (실측 사고에서 나온 계약).

    E9(학습 1.2B, held-out ρ=0.789)와 listwise(236B API)는 서로 독립인데 골든의 같은
    케이스에서 똑같이 실패했다 — 지역 steering 붕괴, top1 0.400(재랭킹 끄면 1.000).
    둘 다 (쿼리, 후보) 텍스트만 받아 target_region도 경쟁사 여부도 못 보기 때문이다.
    의도 충족은 코드가 정하고 재랭커는 같은 티어 안에서만 순서를 매긴다.
    """
    PAIN = "노후 호텔 객실 매출 정체로 저자본 해법이 필요"

    def _pool(self):
        dom = _cand("co-dom", self.PAIN)          # 한국 — intent.target_region 일치
        off = _cand("co-off", self.PAIN)          # 해외 — 불일치
        off.profile.basic.country = "베트남"
        return [dom, off]

    def test_learned_score_cannot_promote_region_mismatch(self, monkeypatch):
        pool = self._pool()
        monkeypatch.setattr(R, "get_pool", lambda: pool)
        monkeypatch.setattr(R, "synthesize_counterpart", R.template_counterpart)
        # 해외 후보에 압도적 학습 점수 — 티어가 없으면 이게 1위가 된다
        monkeypatch.setattr(SC, "score_batch_timed",
                            lambda pairs: ([1.0, 9.9], 5))
        res = R.retrieve(_req())
        assert res.candidates[0].company_id == "co-dom"

    def test_listwise_permutation_cannot_promote_region_mismatch(self, monkeypatch):
        pool = self._pool()
        monkeypatch.setattr(R, "get_pool", lambda: pool)
        monkeypatch.setattr(R, "synthesize_counterpart", R.template_counterpart)
        monkeypatch.setattr(SC, "score_batch_timed", lambda pairs: (None, None))
        monkeypatch.setattr(R, "_TIE_SPREAD", 1.1)          # 항상 동점 → listwise 발동
        # 휴리스틱 순서를 통째로 뒤집는 순열 — 후보 수는 실제 docs에서 받아 맞춘다
        monkeypatch.setattr(SC, "api_rank_listwise",
                            lambda q, d: (list(reversed(range(len(d)))), 7))
        res = R.retrieve(_req())
        assert res.candidates[0].company_id == "co-dom"

    def test_reranker_still_orders_within_the_same_tier(self, monkeypatch):
        """티어가 같으면(둘 다 지역 일치) 학습 점수가 그대로 순서를 정한다 — 신호 보존."""
        pool = [_cand("co-aaa", self.PAIN), _cand("co-bbb", self.PAIN)]   # 둘 다 한국
        monkeypatch.setattr(R, "get_pool", lambda: pool)
        monkeypatch.setattr(R, "synthesize_counterpart", R.template_counterpart)
        monkeypatch.setattr(SC, "score_batch_timed", lambda pairs: ([1.0, 9.9], 5))
        res = R.retrieve(_req())
        assert res.candidates[0].company_id == "co-bbb"


class TestListwiseTieGate:
    """listwise는 **휴리스틱이 동점일 때만** 개입한다 (실측 회귀에서 나온 계약).

    E9 부재면 무조건 재랭킹하게 뒀더니 골든 top1이 1.000→0.400으로 무너졌다.
    휴리스틱 점수는 의도(지역 가산·산업 인접·경쟁사 강등)를 담는데 LLM은 그 구조를
    못 보고 지역 steering을 지운다. 임계값 근거와 배선을 따로 검증한다.
    """
    PAIN = "노후 호텔 객실 매출 정체로 저자본 해법이 필요"

    def test_spread_separates_the_two_observed_situations(self):
        """임계 0.15의 근거 — 실측 두 상황이 이 값을 사이에 두고 갈린다."""
        healthy = [0.355, 0.300, 0.2667, 0.205]      # 골든 R-02 (휴리스틱 건강)
        mushed = [0.1040, 0.1038, 0.1038, 0.0997, 0.0958]   # 공감만세 (뭉개짐)
        assert R.score_spread(healthy) >= R._TIE_SPREAD      # 0.423 → 개입 안 함
        assert R.score_spread(mushed) < R._TIE_SPREAD        # 0.079 → 개입

    def test_spread_degenerate_inputs(self):
        assert R.score_spread([]) == 0.0                     # 후보 없음 → 동점 취급
        assert R.score_spread([0.0, 0.0]) == 0.0             # 0 나눗셈 방어
        assert R.score_spread([0.2, 0.2]) == 0.0             # 완전 동점

    def _setup(self, monkeypatch, calls):
        """E9 부재 + 오프라인 synth. listwise 호출 여부만 기록한다."""
        pool = [_cand("co-aaa", self.PAIN), _cand("co-bbb", self.PAIN)]
        monkeypatch.setattr(R, "get_pool", lambda: pool)
        monkeypatch.setattr(R, "synthesize_counterpart", R.template_counterpart)
        monkeypatch.setattr(SC, "score_batch_timed", lambda pairs: (None, None))

        def spy(query, docs):
            calls.append(query)
            return None, None            # 순서 불변 — 게이트 발동만 관찰
        monkeypatch.setattr(SC, "api_rank_listwise", spy)

    def test_no_listwise_when_heuristic_discriminates(self, monkeypatch):
        """변별이 충분하면 LLM을 부르지 않는다 — 골든 0.400 회귀의 재발 방지."""
        calls = []
        self._setup(monkeypatch, calls)
        monkeypatch.setattr(R, "_TIE_SPREAD", 0.0)   # 무엇도 동점이 아님
        R.retrieve(_req())
        assert calls == []

    def test_malformed_permutation_falls_back_not_crashes(self, monkeypatch):
        """후보 수와 안 맞는 순열이 와도 죽지 않는다 — 재랭킹 실패는 항상 순서 유지.

        파서 계약상 나올 수 없는 입력이지만, 그대로 인덱싱하면 IndexError로 요청
        전체가 죽는다(실측: 이 테스트를 쓰다가 발견). 재랭킹은 부가 기능이지
        요청을 실패시킬 권한이 없다.
        """
        calls = []
        self._setup(monkeypatch, calls)
        monkeypatch.setattr(R, "_TIE_SPREAD", 1.1)
        monkeypatch.setattr(SC, "api_rank_listwise", lambda q, d: ([7, 3], 9))
        res = R.retrieve(_req())                 # 크래시 없음이 계약
        assert [c.company_id for c in res.candidates][:2] == ["co-aaa", "co-bbb"]

    def test_listwise_fires_on_tie(self, monkeypatch):
        """뭉개진 점수는 LLM이 깬다 — 동점 해소 경로가 살아 있는지 확인."""
        calls = []
        self._setup(monkeypatch, calls)
        monkeypatch.setattr(R, "_TIE_SPREAD", 1.1)   # 무엇이든 동점
        R.retrieve(_req())
        assert len(calls) == 1                       # listwise는 1회 호출이 계약
