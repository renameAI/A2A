"""크롤러·감사 로그·프로필 갱신 테스트 — 전부 오프라인 (실제 네트워크 없음)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.ingest.crawler import (CrawlFailed, crawl_website, extract_main_text)
from app.main import app

client = TestClient(app)

ROOT_HTML = """<html><body>
<nav><a href="/about">회사소개</a><a href="/product">제품</a></nav>
<main><h1>다이브인그룹</h1>
<p>노후 호텔 객실을 예술 경험형 상품으로 전환하는 스타트업입니다. 호텔 오너의 선투자
부담 없이 시공하고 전환 객실의 매출을 쉐어합니다. 성수동 Poco Hotel 전환을 완료했고
부티크 호텔 업셀링 실측 데이터를 확보했습니다.</p></main>
<a href="/about">회사소개 자세히</a>
<a href="/product/rooms">제품 안내</a>
<a href="/secret/strategy">내부 전략</a>
<a href="https://external.example.org/partner">외부 파트너</a>
</body></html>"""

ABOUT_HTML = """<html><body><main><h1>회사소개</h1>
<p>다이브인그룹은 2021년 설립된 공간 전환 스타트업으로, 객실마다 다른 아티스트의
컨셉을 입혀 객실 자체를 체험 상품으로 만듭니다. 철거 없이 시공하며 시공 인력을
직접 운용합니다.</p></main></body></html>"""

PRODUCT_HTML = """<html><body><main><h1>제품 안내</h1>
<p>아트 스테이 전환 패키지는 노후 객실을 2주 내에 경험형 객실로 바꿉니다.
매출 쉐어 구조로 호텔의 초기 비용이 없으며 원상 복구를 보장합니다.</p></main></body></html>"""

ROBOTS_TXT = "User-agent: *\nDisallow: /secret/\n"

JS_SHELL = ('<html><body><div id="root"></div>'
            '<script src="/a.js"></script><script src="/b.js"></script>'
            '<script src="/c.js"></script></body></html>')


def _site_transport(hits: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url.path))
        routes = {"/robots.txt": ROBOTS_TXT, "/": ROOT_HTML,
                  "/about": ABOUT_HTML, "/product/rooms": PRODUCT_HTML,
                  "/secret/strategy": "<html><body>비밀 전략</body></html>"}
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body)
    return httpx.MockTransport(handler)


def _settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("A2A_CACHE_DIR", str(tmp_path / "cache"))
    return Settings()


class TestCrawler:
    def test_multipage_with_robots(self, tmp_path, monkeypatch):
        """멀티페이지 크롤 + robots.txt 차단 준수 + 외부 도메인 제외."""
        hits: list[str] = []
        http = httpx.Client(transport=_site_transport(hits))
        settings = _settings(tmp_path, monkeypatch)
        text = crawl_website("https://divein.example.com", settings, client=http)

        assert "노후 호텔 객실" in text                 # 루트
        assert "2021년 설립" in text                    # /about 하위 페이지
        assert "아트 스테이 전환 패키지" in text        # /product/rooms
        assert "비밀 전략" not in text                  # robots Disallow 준수 (ING-08)
        assert "/secret/strategy" not in hits           # 요청 자체를 안 보냄
        assert "[페이지: " in text                      # 출처 라벨 유지 (ING-02)
        assert not any("external" in h for h in hits)   # 외부 도메인 제외

    def test_cache_24h(self, tmp_path, monkeypatch):
        """ING-09: 같은 URL 재크롤 시 캐시 적중 — 네트워크 재요청 없음."""
        hits: list[str] = []
        settings = _settings(tmp_path, monkeypatch)
        http = httpx.Client(transport=_site_transport(hits))
        crawl_website("https://divein.example.com", settings, client=http)
        first_count = len(hits)

        http2 = httpx.Client(transport=_site_transport(hits))
        text = crawl_website("https://divein.example.com", settings, client=http2)
        assert len(hits) == first_count                 # 추가 요청 0
        assert "노후 호텔 객실" in text

    def test_js_shell_detected(self, tmp_path, monkeypatch):
        """CSR SPA 빈 껍데기는 조용한 빈 프로필 대신 명확한 안내 에러."""
        transport = httpx.MockTransport(lambda req: httpx.Response(
            200, text=JS_SHELL if req.url.path == "/" else ""))
        settings = _settings(tmp_path, monkeypatch)
        with pytest.raises(CrawlFailed) as exc:
            crawl_website("https://spa.example.com", settings,
                          client=httpx.Client(transport=transport))
        assert "JS 렌더링" in exc.value.details["reason"]

    def test_trafilatura_extraction(self):
        """본문 추출 — nav/footer 제거, 본문 유지."""
        text = extract_main_text(ROOT_HTML)
        assert "노후 호텔 객실" in text


class TestAuditLog:
    def test_judge_writes_audit_jsonl(self, tmp_path, monkeypatch):
        """SYS-04: 판단 출력이 감사 JSONL로 저장된다."""
        monkeypatch.setenv("A2A_AUDIT_DIR", str(tmp_path / "audit"))
        from tests.test_api import _judge_payload, _livi_profile, _poll_job
        res = client.post("/v1/judge", json=_judge_payload(_livi_profile("high")))
        job = _poll_job(res.json()["job_id"])
        assert job["status"] == "done"

        files = list((tmp_path / "audit").glob("*.jsonl"))
        assert files, "감사 로그 파일이 생성되어야 한다"
        entries = [json.loads(line) for line in files[0].read_text().splitlines()]
        judge_entries = [e for e in entries if e["kind"] == "judge"]
        assert judge_entries
        assert judge_entries[-1]["decision"]
        assert judge_entries[-1]["trajectory"]          # 재학습용 궤적 포함


class TestProfileUpdate:
    def test_reanalyze_updates_same_company(self):
        """REP-09: company_id 재분석은 새 회사를 만들지 않고 갱신한다."""
        from tests.test_product import DIVEIN_TEXT, _run_job
        job = _run_job("/product/onboard", {
            "assets": [{"type": "text", "content": DIVEIN_TEXT}]})
        company_id = job["result"]["company_id"]
        before = len(client.get("/product/companies").json())

        job = _run_job("/product/onboard", {
            "assets": [{"type": "text", "content": DIVEIN_TEXT}],
            "dialogue": [{"q": "구매의향", "a": "중간"}],
            "company_id": company_id})
        assert job["result"]["company_id"] == company_id   # 같은 회사
        assert len(client.get("/product/companies").json()) == before  # 증식 없음
        assert job["result"]["profile"]["willingness_purchase"] == "medium"
