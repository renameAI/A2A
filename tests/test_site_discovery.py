"""공식 사이트 발견 — 기사·디렉터리 출처 후보의 사이트를 이름으로 찾는다.

실측: 심층 판독 대상 10곳 중 5곳이 mention·directory 출처라 사이트가 없었다.
오채택 비용이 크므로(남의 사이트를 이 회사로 판독) 인용 계약 + 높은 임계.
"""
from app.engine import site_discovery as SD


class _Canned:
    def __init__(self, payload): self.payload = payload; self.src = None
    def extract_json(self, system, src, schema, **k):
        self.src = src; return self.payload


def _hits(*urls):
    return [{"title": "t", "url": u, "snippet": "s"} for u in urls]


def test_picks_model_choice_when_confident_and_cited(monkeypatch):
    monkeypatch.setattr(SD, "web_search", lambda q, s, max_results=8: _hits(
        "https://www.varaha.earth/", "https://en.wikipedia.org/wiki/Varaha"), raising=False)
    import app.ingest.websearch as ws
    monkeypatch.setattr(ws, "web_search", lambda q, s, max_results=8: _hits(
        "https://www.varaha.earth/", "https://en.wikipedia.org/wiki/Varaha"))
    x = _Canned({"url": "https://www.varaha.earth/", "p": 0.95, "why": "own site"})
    url, p = SD.find_official_site(x, None, "Varaha", "탄소 제거")
    assert url == "https://www.varaha.earth/" and p == 0.95
    assert "wikipedia" not in x.src            # 비기업 도메인은 후보에서 미리 빠진다


def test_low_confidence_returns_nothing(monkeypatch):
    import app.ingest.websearch as ws
    monkeypatch.setattr(ws, "web_search", lambda q, s, max_results=8: _hits("https://a.com/"))
    url, p = SD.find_official_site(_Canned({"url": "https://a.com/", "p": 0.4, "why": ""}),
                                   None, "A")
    assert url == "" and p == 0.4               # 임계 미만 — 못 찾음으로


def test_uncited_url_is_rejected(monkeypatch):
    """모델이 후보에 없는 URL을 지어내면 버린다 — 인용 계약."""
    import app.ingest.websearch as ws
    monkeypatch.setattr(ws, "web_search", lambda q, s, max_results=8: _hits("https://a.com/"))
    url, _ = SD.find_official_site(_Canned({"url": "https://made-up.com/", "p": 0.99, "why": ""}),
                                   None, "A")
    assert url == ""


def test_source_article_site_is_excluded(monkeypatch):
    """발굴된 기사 사이트 자체가 다시 나오면 후보에서 뺀다."""
    import app.ingest.websearch as ws
    monkeypatch.setattr(ws, "web_search", lambda q, s, max_results=8: _hits(
        "https://www.worldfootwear.com/news/x", "https://mytheresa.com/"))
    x = _Canned({"url": "https://mytheresa.com/", "p": 0.9, "why": ""})
    url, _ = SD.find_official_site(x, None, "Mytheresa", exclude_site="worldfootwear.com")
    assert url == "https://mytheresa.com/" and "worldfootwear" not in x.src


def test_falls_back_to_hint_query_when_name_alone_is_empty(monkeypatch):
    import app.ingest.websearch as ws
    seen = []
    def fake(q, s, max_results=8):
        seen.append(q)
        return [] if q.startswith('"') else _hits("https://mati.earth/")
    monkeypatch.setattr(ws, "web_search", fake)
    url, _ = SD.find_official_site(_Canned({"url": "https://mati.earth/", "p": 0.9, "why": ""}),
                                   None, "Mati", "암석 풍화 탄소 제거")
    assert url == "https://mati.earth/"
    assert seen[0] == '"Mati" official website' and seen[1] == "Mati 암석 풍화 탄소 official site"


def test_no_hits_returns_empty_without_calling_model():
    import app.ingest.websearch as ws
    class Boom:
        def extract_json(self, *a, **k): raise AssertionError("모델을 부르면 안 된다")
    import pytest
    ws_orig = ws.web_search
    ws.web_search = lambda q, s, max_results=8: []
    try:
        assert SD.find_official_site(Boom(), None, "없는회사") == ("", 0.0)
    finally:
        ws.web_search = ws_orig
