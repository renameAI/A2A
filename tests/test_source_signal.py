"""출처 신호 → p_eff → 재랭킹 (할리케이 프로덕션 하위권 오염 회귀 테스트).

실측: 최종 10곳 중 5곳이 뉴스·채용공고·블로그에서 스쳐 언급된 회사였다
(Etsy·Mytheresa·Vestiaire…). 보완성 점수만 보는 재랭킹은 출처 신뢰도를
모른다. 고침: 모델이 source_kind를 판정 → 코드가 p_eff=p·w(source) →
retrieval_score = 보완성 × p_eff + 피드백.
"""
from app.engine.candidate_extract import (extract_companies, _source_weight,
                                          _SOURCE_W)


class _Canned:
    def __init__(self, companies):
        self.companies = companies

    def extract_json(self, *_a, **_k):
        return {"companies": self.companies}


HITS = [{"url": "https://cece.example/about", "title": "About", "snippet": "s"},
        {"url": "https://news.example/sustainable-bags", "title": "기사", "snippet": "s"}]


def test_source_weight_ordering():
    assert _SOURCE_W["own"] > _SOURCE_W["directory"] > _SOURCE_W["mention"]
    assert _source_weight("MENTION") == _SOURCE_W["mention"]
    assert 0 < _source_weight("") < 1            # 미상은 중간, 0이 아니다


def test_p_eff_multiplies_and_drops_below_tau():
    ext = _Canned([
        {"name": "Project Cece", "what": "지속가능 패션 편집숍", "signal": "",
         "url": HITS[0]["url"], "p": 0.8, "source_kind": "own"},
        {"name": "Mytheresa", "what": "명품 온라인 유통", "signal": "",
         "url": HITS[1]["url"], "p": 0.6, "source_kind": "mention"},
        {"name": "Etsy", "what": "핸드메이드 마켓", "signal": "",
         "url": HITS[1]["url"], "p": 0.3, "source_kind": "mention"},
    ])
    out = extract_companies(ext, HITS, "핸드백 유통 파트너")
    by = {c["name"]: c for c in out}
    assert by["Project Cece"]["p"] == 0.8 and by["Project Cece"]["source_kind"] == "own"
    assert by["Mytheresa"]["p"] == round(0.6 * _SOURCE_W["mention"], 3)
    assert by["Mytheresa"]["p_raw"] == 0.6
    assert "Etsy" not in by                       # 0.3×0.55=0.165 < τ=0.2 → 탈락


def test_missing_source_kind_is_neutral_not_fatal():
    ext = _Canned([{"name": "X", "what": "w", "signal": "", "url": HITS[0]["url"]}])
    out = extract_companies(ext, HITS, "c")
    assert out and out[0]["source_kind"] == "unknown" and out[0]["p"] > 0.2


def test_rank_pool_uses_p(monkeypatch):
    """보완성이 같아도 p_eff가 낮은 mention 후보가 아래로 간다."""
    from app.saas import router as R
    from types import SimpleNamespace as NS

    class _Res:
        def __init__(self, ids):
            self.candidates = [NS(company_id=i, retrieval_score=0.30,
                                  model_dump=lambda mode=None, i=i: {"company_id": i})
                               for i in ids]
    monkeypatch.setattr(R, "retrieve", lambda req, candidate_records: _Res(
        [r.company_id for r in candidate_records]))
    monkeypatch.setattr(R, "candidate_record_from_profile",
                        lambda cid, prof, url, pain_signal=None: NS(company_id=cid))
    monkeypatch.setattr(R, "RetrieveRequest", lambda **kw: NS(**kw))
    intent = NS(target_region="", target_industry="")
    mk = lambda cid, p, kind: {"company_id": cid, "name": cid, "what": "w", "signal": "",
                               "source_url": "u", "pain_signal": "w", "ontology": None,
                               "p": p, "source_kind": kind}
    pool = [mk("mention-big", 0.33, "mention"), mk("own-small", 0.8, "own"),
            mk("legacy-no-p", None, None)]
    pool[2].pop("p"); pool[2].pop("source_kind")
    ranked = R._rank_pool(profile=None, intent=intent, pool=pool,
                          liked=[], disliked=[], k=10)
    order = [r["company_id"] for r in ranked]
    assert order[0] == "own-small" and order[-1] == "mention-big"
    assert ranked[0]["complementarity"] == 0.30
    assert ranked[0]["retrieval_score"] == round(0.30 * 0.8, 4)
    assert ranked[1]["company_id"] == "legacy-no-p"   # 구 풀은 중립 0.7
