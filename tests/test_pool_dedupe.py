"""풀 단위 중복 병합 — 할리케이 프로덕션 실데이터 기반 회귀 테스트.

실측 두 가지가 이 규칙을 정했다:
- Clother가 clother.ch/en과 clother.ch/de로 최종 10곳에 두 번 올랐다.
  extract_companies의 이름 중복 제거는 호출 1회 안에서만 듣고, 웨이브가
  갈리면 호출도 갈린다. _discover의 known_urls는 URL이 정확히 같을 때만
  막으므로 같은 사이트의 다른 경로는 통과한다.
- 같은 실행에서 Vestiaire Collective와 Mytheresa가 둘 다
  worldfootwear.com에서 나왔다. 서로 다른 회사다 — 사이트만으로 합치면
  기사·디렉터리 한 페이지의 발굴 결과가 통째로 뭉개진다.
"""
from app.engine.candidate_extract import dedupe_pool, _norm_name, _site_of


def _c(name, url, p=0.7, cid="x"):
    return {"company_id": cid, "name": name, "source_url": url, "p": p}


def test_same_company_same_site_different_path_merges():
    pool = [_c("Clother", "https://clother.ch/en", 0.72, "web-1-01"),
            _c("Clother", "https://clother.ch/de", 0.78, "web-1-05")]
    kept, merged = dedupe_pool(pool)
    assert merged == 1 and len(kept) == 1
    # 더 잘 본 기록(p가 높은 쪽)이 남는다
    assert kept[0]["company_id"] == "web-1-05" and kept[0]["p"] == 0.78


def test_different_companies_on_one_article_are_not_merged():
    """디렉터리·기사 한 페이지에서 나온 여러 회사는 서로 다른 회사다."""
    host = "https://www.worldfootwear.com/news/luxury-resale"
    pool = [_c("Vestiaire Collective", host), _c("Mytheresa", host)]
    kept, merged = dedupe_pool(pool)
    assert merged == 0 and len(kept) == 2


def test_same_name_on_different_sites_is_not_merged():
    """이름이 같아도 사이트가 다르면 합치지 않는다 — 동명이인 오병합 방지."""
    pool = [_c("Clother", "https://clother.ch/en"),
            _c("Clother", "https://clother.example.com/")]
    kept, merged = dedupe_pool(pool)
    assert merged == 0 and len(kept) == 2


def test_www_and_case_and_spacing_do_not_split_a_company():
    pool = [_c("Project Cece", "https://www.projectcece.com/about", 0.6),
            _c("project  cece", "https://projectcece.com/", 0.5)]
    kept, merged = dedupe_pool(pool)
    assert merged == 1 and kept[0]["p"] == 0.6


def test_order_is_stable_and_missing_fields_never_merge():
    """이름이나 출처가 비면 합치지 않는다 — 빈 키끼리 뭉치면 안 된다."""
    pool = [_c("A", "https://a.com"), _c("", ""), _c("", ""),
            _c("B", "https://b.com")]
    kept, merged = dedupe_pool(pool)
    assert merged == 0 and [c["name"] for c in kept] == ["A", "", "", "B"]


def test_real_hallikay_pool_collapses_exactly_one():
    """실행에서 나온 10곳을 그대로 넣으면 Clother 한 쌍만 합쳐진다."""
    pool = [
        _c("Clother", "https://clother.ch/en", 0.72),
        _c("NUVONDA", "https://nuvonda.ch", 0.78),
        _c("YUZU", "https://yuzu.ch", 0.75),
        _c("Orderchamp", "https://www.orderchamp.com/page/eco", 0.68),
        _c("Clother", "https://clother.ch/de", 0.78),
        _c("MOONBAT", "https://www.moonbat.co.jp/company", 0.55),
        _c("Project Cece", "https://www.projectcece.com", 0.58),
        _c("GRIFFATI", "https://www.griffati.com/en", 0.70),
        _c("SustainYourStyle", "https://www.sustainyourstyle.org/en/", 0.55),
        _c("Rifò", "https://rifo-lab.com/fr/pages/la-bou", 0.68),
    ]
    kept, merged = dedupe_pool(pool)
    assert merged == 1
    assert [c["name"] for c in kept].count("Clother") == 1
    assert len(kept) == 9


def test_normalizers():
    assert _norm_name("  Clother ") == _norm_name("clother")
    assert _norm_name("Fast Retailing Co., Ltd") == "fast retailing co ltd"
    assert _site_of("https://www.a.com/x") == _site_of("https://a.com/y") == "a.com"
    assert _site_of("") == ""


def test_company_ids_never_repeat_after_a_merge():
    """병합으로 풀이 줄어도 다음 웨이브가 쓴 번호를 재발급하면 안 된다.

    len(pool)로 번호를 매기면 웨이브1이 01~10을 발급하고 한 건이 병합돼
    9곳이 됐을 때, 웨이브2가 base=9에서 시작해 10번을 다시 발급한다. 그러면
    서로 다른 회사가 같은 company_id를 갖고, 저장 스냅샷·명확화 인용·사용자
    반응이 엉뚱한 회사에 붙는다.
    """
    doc: dict = {"pool": []}

    def issue(n):
        """_discover의 식별자 발급부와 같은 규칙."""
        base = doc.get("cid_seq", len(doc.get("pool", [])))
        ids = [f"web-r-{base + i + 1:02d}" for i in range(n)]
        doc["cid_seq"] = base + n
        return ids

    w1 = issue(10)
    doc["pool"] = [_c("Clother", "https://clother.ch/en", 0.7, w1[0]),
                   _c("Clother", "https://clother.ch/de", 0.8, w1[4])] + \
                  [_c(f"C{i}", f"https://c{i}.com", 0.7, w1[i]) for i in range(1, 4)]
    doc["pool"], merged = dedupe_pool(doc["pool"])
    assert merged == 1 and len(doc["pool"]) == 4      # 풀이 줄었다

    w2 = issue(3)
    assert set(w1) & set(w2) == set()                 # 번호가 겹치지 않는다
    assert w2[0] == "web-r-11"                        # 9가 아니라 10 다음부터
    all_ids = [c["company_id"] for c in doc["pool"]] + w2
    assert len(all_ids) == len(set(all_ids))
