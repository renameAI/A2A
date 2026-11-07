"""사용자가 준 URL을 가져올 때의 SSRF 방어 — 연결 시점에 막는다.

받는 시점 검사(app/saas/assetguard.py)만으로는 두 구멍이 남았다(2026-10-21):
  1) 공개 주소가 30x로 내부 주소(127.0.0.1·169.254.169.254·사설망)로 리다이렉트
  2) DNS rebinding — 검사 때는 공개 IP, 실제 연결 때는 내부 IP로 풀림
그래서 이름 풀이와 연결을 한 곳에서 한다: 연결할 때마다(리다이렉트 매 홉 포함)
이름을 직접 풀고, 결과가 전부 공개 IP일 때만 **그 IP로** 접속한다. 검사한 IP와
접속한 IP가 같으니 rebinding이 끼어들 틈이 없다. Host 헤더와 TLS SNI·인증서 검증은
httpcore가 원래 호스트 이름으로 하므로 그대로다.

그 밖에: http(s)만, 리다이렉트 최대 5홉, 연결·전체 시간 상한, 응답 크기 상한,
환경변수 프록시 무시(프록시가 대신 내부 이름을 풀면 검사가 무의미하다).
"""
import ipaddress
import os
import socket
import time
from urllib.parse import urlparse

import httpcore
import httpx

MAX_REDIRECTS = 5
MAX_BYTES = int(os.environ.get("INGEST_FETCH_MAX_BYTES", str(5 * 1024 * 1024)))
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class BlockedAddress(httpcore.ConnectError):
    """공개 인터넷이 아닌 주소로의 연결 — httpx.ConnectError로 올라간다."""


def ip_allowed(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(addr, ipaddress.IPv6Address):
        # ::ffff:127.0.0.1, 64:ff9b::a9fe:a9fe 처럼 IPv4를 감싼 주소는 속을 본다
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        elif addr in _NAT64:
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return addr.is_global and not addr.is_multicast


def public_ips(host: str, port: int | None = None) -> list[str]:
    """host가 풀리는 IP 전부. 하나라도 공개 IP가 아니면 BlockedAddress."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise BlockedAddress(f"주소를 풀 수 없음: {host} ({e})")
    ips = list(dict.fromkeys(info[4][0] for info in infos))
    if not ips:
        raise BlockedAddress(f"주소를 풀 수 없음: {host}")
    for ip in ips:
        if not ip_allowed(ip):
            raise BlockedAddress(f"공개 인터넷 주소가 아님: {host} → {ip}")
    return ips


def host_is_public(host: str) -> bool:
    try:
        public_ips(host)
    except BlockedAddress:
        return False
    return True


class _GuardedBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None,
                    socket_options=None):
        last: Exception | None = None
        for ip in public_ips(host, port):
            try:
                return super().connect_tcp(ip, port, timeout, local_address,
                                           socket_options)
            except httpcore.ConnectError as e:
                last = e
        raise last  # type: ignore[misc]

    def connect_unix_socket(self, *a, **kw):
        raise BlockedAddress("유닉스 소켓 연결 금지")


class GuardedTransport(httpx.HTTPTransport):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._pool._network_backend = _GuardedBackend()


def make_client(timeout: float, headers: dict | None = None) -> httpx.Client:
    return httpx.Client(
        transport=GuardedTransport(),
        timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
        follow_redirects=True, max_redirects=MAX_REDIRECTS,
        trust_env=False, headers=headers)


def get(client: httpx.Client, url: str, *, total_timeout: float,
        max_bytes: int = MAX_BYTES) -> httpx.Response:
    """GET — 본문을 max_bytes까지만 읽고, 리다이렉트 포함 total_timeout 안에 끝낸다.

    리다이렉트는 httpx가 따라가되(최대 MAX_REDIRECTS) 홉마다 새 연결이
    GuardedTransport를 거치므로 연결 시점 검사를 똑같이 받는다.
    """
    if urlparse(url).scheme not in ("http", "https"):
        raise httpx.UnsupportedProtocol(f"http(s)만 가져옵니다: {url}")
    deadline = time.monotonic() + total_timeout
    with client.stream("GET", url) as r:
        declared = r.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise httpx.HTTPError(f"응답이 너무 큼: {declared} bytes > {max_bytes}")
        chunks, size = [], 0
        for chunk in r.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise httpx.HTTPError(f"응답이 너무 큼: > {max_bytes} bytes")
            if time.monotonic() > deadline:
                raise httpx.TimeoutException(f"전체 시간 {total_timeout:.0f}초 초과",
                                             request=r.request)
            chunks.append(chunk)
    # iter_bytes는 이미 압축을 푼 바이트 — 인코딩 헤더를 남기면 두 번 풀린다
    headers = [(k, v) for k, v in r.headers.multi_items()
               if k.lower() not in ("content-encoding", "content-length",
                                    "transfer-encoding")]
    return httpx.Response(r.status_code, headers=headers, content=b"".join(chunks),
                          request=r.request, history=r.history)
