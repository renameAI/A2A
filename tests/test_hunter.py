"""Hunter 커넥터 — 인용 계약을 코드가 집행하는지.

실측 배경(2026-08-24, 프로덕션 47개 도메인): 접점 없던 24곳 중 8곳을 색인이
채웠고 결과 전부 출처가 딸려 있었다. 이 테스트는 API 형태가 아니라 **우리
계약**을 고정한다 — 제3자 도메인 배제, 무출처 배제, 정직한 실패 상태.
"""
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.connectors.hunter import find_contacts, verify_email


def _resp(emails):
    return {"data": {"emails": emails}}


def _email(value, sources=1, confidence=90, **kw):
    return {"value": value, "confidence": confidence, "type": "personal",
            "sources": [{"uri": f"https://x.com/{i}"} for i in range(sources)],
            **kw}


def test_rejects_other_domain(monkeypatch):
    """색인에 섞인 제3자 주소를 '이 회사 접점'으로 실으면 메일이 엉뚱한 곳에 간다."""
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    r = find_contacts("fkur.com", _get=lambda p, q: _resp(
        [_email("a@fkur.com"), _email("b@partner-agency.de")]))
    assert [c["email"] for c in r["contacts"]] == ["a@fkur.com"]


def test_rejects_sourceless(monkeypatch):
    """출처 없는 주소는 확인할 방법이 없다 — 싣지 않는다."""
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    r = find_contacts("fkur.com", _get=lambda p, q: _resp(
        [_email("a@fkur.com", sources=0), _email("b@fkur.com", sources=2)]))
    assert [c["email"] for c in r["contacts"]] == ["b@fkur.com"]


def test_sorted_by_confidence(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    r = find_contacts("fkur.com", _get=lambda p, q: _resp(
        [_email("lo@fkur.com", confidence=40),
         _email("hi@fkur.com", confidence=99)]))
    assert [c["confidence"] for c in r["contacts"]] == [99, 40]


def test_no_key_is_a_status_not_silence(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    r = find_contacts("fkur.com", _get=lambda p, q: 1 / 0)
    assert r["status"] == "no_key" and r["contacts"] == []


def test_http_error_is_a_status(monkeypatch):
    """빈 목록(접점 없음=정보)과 조회 실패(재시도)는 후속 행동이 다르다."""
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    def boom(p, q):
        raise urllib.error.HTTPError("u", 429, "too many", {}, None)
    r = find_contacts("fkur.com", _get=boom)
    assert r["status"] == "error" and "429" in r["note"]


def test_verify_keeps_hunter_vocabulary(monkeypatch):
    """valid/accept_all/unknown을 번역하지 않는다 — 발송 판단의 원문이다."""
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    r = verify_email("a@fkur.com", _get=lambda p, q: {
        "data": {"status": "accept_all", "score": 71, "smtp_check": True}})
    assert (r["result"], r["score"], r["smtp"]) == ("accept_all", 71, True)


def test_case_insensitive_domain_match(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    r = find_contacts("FKuR.com", _get=lambda p, q: _resp(
        [_email("A@FKUR.COM")]))
    assert r["contacts"][0]["email"] == "a@fkur.com"
