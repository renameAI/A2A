"""연결 시점 SSRF 방어(app/ingest/netguard.py) — 리다이렉트·DNS rebinding·정상 경로.

받는 시점 검사(assetguard)를 통과한 URL이 가져오는 순간 내부로 새지 않는지 본다.
네트워크는 쓰지 않는다: 로컬 HTTP 서버 + getaddrinfo 스텁.
"""
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpcore
import pytest

from app.config import Settings
from app.ingest import netguard
from app.ingest.crawler import CrawlFailed, crawl_website
from app.ingest.fetchers import FetchFailed, fetch_url
from app.saas import assetguard

PAGE = "<html><body><main>" + ("공개 회사 소개 본문입니다. " * 20) + "</main></body></html>"


def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def internal():
    """내부 전용 서비스 흉내 — 여기에 요청이 한 번이라도 닿으면 실패."""
    hits: list[str] = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html><body>INTERNAL SECRET</body></html>")

        def log_message(self, *a):
            pass

    srv = _serve(H)
    yield srv.server_address[1], hits
    srv.shutdown()


@pytest.fixture
def redirector():
    """공개 사이트 흉내 — 모든 요청을 self.target으로 302."""
    class H(BaseHTTPRequestHandler):
        target = ""
        seen_hosts: list[str] = []

        def do_GET(self):
            H.seen_hosts.append(self.headers.get("Host", ""))
            if self.path == "/robots.txt":
                self.send_response(404)
                self.end_headers()
                return
            if H.target:
                self.send_response(302)
                self.send_header("Location", H.target)
                self.end_headers()
                return
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    H.seen_hosts = []
    srv = _serve(H)
    yield srv.server_address[1], H
    srv.shutdown()


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("A2A_CACHE_DIR", str(tmp_path / "cache"))
    s = Settings()
    s.fetch_timeout = 5.0
    return s


def _pretend_public(monkeypatch, name: str):
    """시험용: name만 '공개 IP'로 취급해 로컬 서버(127.0.0.1)에 붙게 한다.
    다른 이름·IP(리다이렉트 대상)는 진짜 검사를 그대로 받는다."""
    real = netguard.public_ips

    def fake(host, port=None):
        return ["127.0.0.1"] if host == name else real(host, port)
    monkeypatch.setattr(netguard, "public_ips", fake)


def _fake_dns(monkeypatch, answers):
    """getaddrinfo 스텁 — answers: 호출마다 돌려줄 IP(마지막 값 반복)."""
    calls = []
    real = socket.getaddrinfo

    def fake(host, port=None, *a, **kw):
        if host == "127.0.0.1":                  # 시험 스텁 자신의 로컬 접속
            return real(host, port, *a, **kw)
        ip = answers[min(len(calls), len(answers) - 1)]
        calls.append(host)
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (ip, port or 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return calls


class TestRedirect:
    def test_redirect_to_loopback_blocked(self, monkeypatch, settings, internal, redirector):
        iport, ihits = internal
        rport, H = redirector
        H.target = f"http://127.0.0.1:{iport}/admin"
        _pretend_public(monkeypatch, "public.example")
        with pytest.raises(FetchFailed) as exc:
            fetch_url(f"http://public.example:{rport}/", settings)
        assert "공개 인터넷 주소가 아님" in exc.value.details["reason"]
        assert ihits == []                         # 내부 서비스에 요청이 닿지 않았다

    def test_redirect_to_metadata_blocked(self, monkeypatch, settings, redirector):
        rport, H = redirector
        H.target = "http://169.254.169.254/latest/meta-data/"
        _pretend_public(monkeypatch, "public.example")
        with pytest.raises(FetchFailed) as exc:
            fetch_url(f"http://public.example:{rport}/", settings)
        assert "169.254.169.254" in exc.value.details["reason"]

    def test_crawler_redirect_blocked(self, monkeypatch, settings, internal, redirector):
        iport, ihits = internal
        rport, H = redirector
        H.target = f"http://localhost:{iport}/"
        _pretend_public(monkeypatch, "public.example")
        with pytest.raises(CrawlFailed):
            crawl_website(f"http://public.example:{rport}/", settings)
        assert ihits == []

    def test_redirect_to_file_scheme_blocked(self, monkeypatch, settings, redirector):
        rport, H = redirector
        H.target = "file:///etc/passwd"
        _pretend_public(monkeypatch, "public.example")
        with pytest.raises(FetchFailed):
            fetch_url(f"http://public.example:{rport}/", settings)

    def test_redirect_hop_limit(self, monkeypatch, settings, redirector):
        rport, H = redirector
        H.target = f"http://public.example:{rport}/again"   # 자기 자신으로 무한 302
        _pretend_public(monkeypatch, "public.example")
        with pytest.raises(FetchFailed) as exc:
            fetch_url(f"http://public.example:{rport}/", settings)
        assert "redirect" in exc.value.details["reason"].lower()
        assert len(H.seen_hosts) == netguard.MAX_REDIRECTS + 1


class TestResolve:
    def test_private_resolution_blocked(self, monkeypatch, settings):
        _fake_dns(monkeypatch, ["10.0.0.5"])
        with pytest.raises(FetchFailed) as exc:
            fetch_url("https://intranet.example.com/", settings)
        assert "10.0.0.5" in exc.value.details["reason"]

    def test_dns_rebinding_blocked(self, monkeypatch, settings):
        """받는 시점엔 공개 IP, 가져오는 시점엔 루프백 — 연결 시점 검사가 막는다."""
        calls = _fake_dns(monkeypatch, ["93.184.216.34", "127.0.0.1"])
        assert assetguard._public_host("rebind.example.com")     # 받는 시점 통과
        with pytest.raises(FetchFailed) as exc:
            fetch_url("http://rebind.example.com/", settings)
        assert "127.0.0.1" in exc.value.details["reason"]
        assert len(calls) == 2

    def test_literal_internal_ip_blocked(self, settings):
        for url in ("http://127.0.0.1:1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
                    "http://0.0.0.0/", "http://192.168.0.1/"):
            with pytest.raises(FetchFailed):
                fetch_url(url, settings)

    def test_ip_allowed(self):
        assert netguard.ip_allowed("93.184.216.34")
        assert netguard.ip_allowed("2606:2800:220:1:248:1893:25c8:1946")
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                   "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1",
                   "::ffff:10.0.0.1", "64:ff9b::a9fe:a9fe"):
            assert not netguard.ip_allowed(ip), ip

    def test_non_http_scheme_rejected(self, settings):
        with pytest.raises(FetchFailed):
            fetch_url("file:///etc/passwd", settings)


class TestPublicPath:
    def test_public_url_pinned_ip_and_host_header(self, monkeypatch, settings, redirector):
        """정상 공개 URL은 통과 — 검사한 IP로 접속하고 Host 헤더는 원래 이름."""
        rport, H = redirector
        _fake_dns(monkeypatch, ["93.184.216.34"])
        dialed = []
        real_connect = httpcore.SyncBackend.connect_tcp

        def stub_connect(self, host, port, *a, **kw):
            dialed.append(host)                  # 네트워크 대신 로컬 서버로
            return real_connect(self, "127.0.0.1", rport, *a, **kw)
        monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", stub_connect)

        text = fetch_url(f"http://www.example.com:{rport}/", settings)
        assert "공개 회사 소개 본문" in text
        assert dialed == ["93.184.216.34"]                 # 이름이 아니라 검사한 IP
        assert H.seen_hosts == [f"www.example.com:{rport}"]

    def test_response_size_cap(self, monkeypatch, settings, redirector):
        rport, H = redirector
        _pretend_public(monkeypatch, "public.example")
        monkeypatch.setattr(netguard, "MAX_BYTES", 100)
        monkeypatch.setattr(netguard.get, "__kwdefaults__", {"max_bytes": 100})
        with pytest.raises(FetchFailed) as exc:
            fetch_url(f"http://public.example:{rport}/", settings)
        assert "너무 큼" in exc.value.details["reason"]
