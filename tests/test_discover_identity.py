"""T3 — 후보 식별자 정합성 회귀 테스트.

배경(감사 확정 high): _discover가 온톨로지를 URL로 키잉했다. extract_companies는
회사명으로만 중복을 거르므로 디렉터리·협회 회원사 목록 페이지 하나에서 회사
여러 곳을 뽑는 것이 **정상 동작**이다. 그때 뒤 회사가 앞 회사의 판독을 덮어써:
  - 사용자가 A사 카드에서 B사의 메일 주소·담당 부서를 본다 (아웃리치 제품에서
    가장 치명적인 무성 오염)
  - 진행 로그가 없던 실패를 보고한다 (len(companies) - len(onts))
  - 키워드 원장의 수확량이 1/n로 기록된다
  - clarify가 '갈림 없음'으로 오판해 질문을 못 만든다

딸린 결함(low): src_of가 딕셔너리 덮어쓰기라 '먼저 본 검색어가 독식' — 뒤
업종의 검색어가 같은 URL을 찾아내도 원장에 성과 0으로 남았다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import (AxisStatus, BasicInfo, CompanyOntology, Intent,
                         OntologyAxis, Profile, ProvField, Provenance, ValueProp)


class FakeStore:
    def __init__(self):
        self.docs = {}

    def put(self, kind, ws, doc_id, body):
        self.docs[(kind, ws, doc_id)] = dict(body)

    def get(self, kind, ws, doc_id):
        return self.docs.get((kind, ws, doc_id))

    def list(self, kind, ws, limit=None):
        rows = [v for (k, w, _), v in self.docs.items() if k == kind and w == ws]
        return rows[:limit] if limit is not None else rows

    def reserve_cost(self, *a, **k):
        pass


class FakeUser:
    workspace_id = "ws-t"
    uid = "t"
    email = "t@x.com"


def _profile():
    return Profile(
        basic=BasicInfo(name="귤메달", country="한국", industry="food"),
        description="건강음료 제조",
        problem_solved=ProvField(value="무가당 선택지 부족",
                                 provenance=Provenance.stated),
        solution=ProvField(value="감귤 무가당 음료", provenance=Provenance.stated),
        target_customer=ProvField(value="식품 유통사", provenance=Provenance.stated))


LIST_URL = "https://directory.example.jp/members"
OTHER_URL = "https://other.example.jp/company"

# 한 목록 페이지에서 3곳 + 다른 페이지에서 1곳
COMPANIES = [
    {"name": "㈜대성무역", "name_ko": "대성무역", "what": "냉동식품 수입",
     "signal": "", "url": LIST_URL},
    {"name": "㈜한성상사", "name_ko": "한성상사", "what": "주류 도매",
     "signal": "", "url": LIST_URL},
    {"name": "㈜동방식품", "name_ko": "동방식품", "what": "조미료 제조",
     "signal": "", "url": LIST_URL},
    {"name": "㈜서해유통", "name_ko": "서해유통", "what": "음료 유통",
     "signal": "", "url": OTHER_URL},
]


@pytest.fixture()
def patched(monkeypatch, tmp_path):
    """_discover의 외부 의존(웹검색·추출·판독)만 대역으로 바꾼다."""
    import app.connectors.tavily as tv
    import app.engine.candidate_extract as ce
    import app.engine.company_ontology as co
    import app.saas.router as r

    monkeypatch.chdir(tmp_path)          # 스니펫 로그가 저장소를 더럽히지 않게

    def fake_search(q, settings, max_results=8):
        # seg1 검색어는 목록 페이지를, seg2 검색어는 목록+다른 페이지를 찾는다
        if q == "q-seg2":
            return [{"url": LIST_URL, "title": "회원사 목록", "snippet": "s"},
                    {"url": OTHER_URL, "title": "서해유통", "snippet": "s"}]
        return [{"url": LIST_URL, "title": "회원사 목록", "snippet": "s"}]

    monkeypatch.setattr(tv, "search", fake_search)
    monkeypatch.setattr(r, "filter_company_hits", lambda hits: (hits, 0))
    monkeypatch.setattr(r, "extract_companies",
                        lambda *a, **k: [dict(c) for c in COMPANIES])

    def fake_read(extractor, company, region="", purpose="revenue"):
        # 회사마다 다른 판독 — 섞이면 테스트가 잡아낸다
        return CompanyOntology(
            axes={"offering": OntologyAxis(value=company["what"],
                                           status=AxisStatus.confirmed)},
            search_keywords=[f"kw-{company['name']}"],
            source_url=company["url"])

    monkeypatch.setattr(co, "read_company", fake_read)
    return r


def _run(r, store):
    doc = {"search_brief": {"synthesized_counterpart": "상대상"}, "pool": []}
    intent = Intent(value_props=[ValueProp.revenue_growth], target_region="일본")
    return r._discover(store, FakeUser(), "lr-x", doc, _profile(), intent,
                       None, object(), [("seg1", ["q-seg1"]), ("seg2", ["q-seg2"])],
                       wave=1), doc


def test_same_url_companies_keep_own_ontology(patched):
    """같은 URL에서 나온 3곳이 서로의 판독을 덮어쓰지 않는다."""
    r = patched
    out, _ = _run(r, FakeStore())
    assert len(out) == 4
    by_name = {c["name"]: c for c in out}
    assert by_name["㈜대성무역"]["ontology"]["axes"]["offering"]["value"] == "냉동식품 수입"
    assert by_name["㈜한성상사"]["ontology"]["axes"]["offering"]["value"] == "주류 도매"
    assert by_name["㈜동방식품"]["ontology"]["axes"]["offering"]["value"] == "조미료 제조"


def test_company_ids_unique(patched):
    r = patched
    out, _ = _run(r, FakeStore())
    ids = [c["company_id"] for c in out]
    assert len(set(ids)) == len(ids) == 4


def test_ontology_stored_per_company_not_per_url(patched):
    """저장 키도 회사 단위 — URL 키였을 땐 마지막 회사만 남았다."""
    r = patched
    store = FakeStore()
    out, _ = _run(r, store)
    saved = store.list("company_ontology", "ws-t")
    assert len(saved) == 4
    assert {d["name"] for d in saved} == {c["name"] for c in COMPANIES}
    for d in saved:
        assert d["source_url"] in (LIST_URL, OTHER_URL)


def test_yield_counts_every_company_from_a_url(patched):
    """목록 페이지 1건에서 3곳이 나왔으면 그 검색어의 수확은 3이다 — URL 수(1)가
    아니다. 이 수치가 다음 요청의 키워드 추천 가중이 된다."""
    r = patched
    store = FakeStore()
    _run(r, store)
    runs = {d["segment"]: d for d in store.list("keyword_run", "ws-t")}
    assert runs["seg1"]["yield_by_query"]["q-seg1"] == 3


def test_later_query_also_credited(patched):
    """뒤 업종의 검색어도 같은 URL을 찾아냈으면 성과를 인정받는다 —
    '먼저 본 검색어 독식'이 아니다."""
    r = patched
    store = FakeStore()
    _run(r, store)
    runs = {d["segment"]: d for d in store.list("keyword_run", "ws-t")}
    # q-seg2는 목록(3곳) + 다른 페이지(1곳) = 4
    assert runs["seg2"]["yield_by_query"]["q-seg2"] == 4


def test_segment_ontologies_not_cross_contaminated(patched):
    """업종별 원장에 그 업종이 데려온 기업의 판독만 담긴다."""
    r = patched
    store = FakeStore()
    _run(r, store)
    runs = {d["segment"]: d for d in store.list("keyword_run", "ws-t")}
    # 4곳 모두 seg1이 먼저 찾은 URL(3곳)이거나 seg2 전용(1곳)
    seg1_kw = set(runs["seg1"]["derived_keywords"])
    seg2_kw = set(runs["seg2"]["derived_keywords"])
    assert "kw-㈜서해유통" in seg2_kw          # seg2만 찾은 회사
    assert "kw-㈜서해유통" not in seg1_kw      # seg1 원장엔 없어야 한다


def test_failure_count_is_real(patched, monkeypatch, capsys):
    """판독 실패 수는 별도 카운터로 센다 — len(companies)-len(onts)로 세면
    URL 충돌 때 없던 실패가 보고됐다."""
    import app.engine.company_ontology as co
    calls = {"n": 0}

    def flaky(extractor, company, region="", purpose="revenue"):
        calls["n"] += 1
        if company["name"] == "㈜한성상사":
            raise RuntimeError("판독 실패")
        return CompanyOntology(
            axes={"offering": OntologyAxis(value=company["what"],
                                           status=AxisStatus.confirmed)},
            search_keywords=[], source_url=company["url"])

    monkeypatch.setattr(co, "read_company", flaky)
    out, _ = _run(patched, FakeStore())
    assert calls["n"] == 4
    assert sum(1 for c in out if c["ontology"] is None) == 1
    assert sum(1 for c in out if c["ontology"] is not None) == 3
