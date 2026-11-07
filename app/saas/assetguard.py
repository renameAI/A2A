"""사용자가 보낸 자료(Asset) URL 검사 — 받는 시점에 거른다.

발견(2026-10-21, 멤버십 통합 중): 자료 URL을 아무 검사 없이 받아 엔진이 그대로 읽었다.
  - ir_deck의 url에 로컬 경로를 넣으면 fetch_pdf_bytes가 서버 파일을 읽었다(Path.read_bytes)
  - supabase://<남의 워크스페이스>/... 를 넣으면 남의 업로드 파일을 읽을 수 있었다
    (파일 이름이 128비트 난수라 추측은 어렵지만, 소유 확인이 없다는 사실은 그대로다)
  - website·article URL로 내부 주소(169.254.169.254, localhost, 사설망)를 주면 서버가
    대신 가져와 그 내용이 프로필에 들어갔다(SSRF)
여기서 막는 범위: 형식·소유·명백한 내부 주소(빠른 400 안내용). 공개 주소가 내부로
리다이렉트하거나 검사 뒤 DNS가 바뀌는 경우(DNS rebinding)는 가져오는 계층이 연결
시점에 다시 막는다 — app/ingest/netguard.py(IP 판정도 그쪽과 같은 함수를 쓴다).
"""
from urllib.parse import urlparse

from ..errors import EngineError
from ..ingest.netguard import host_is_public

_WEB = {"website", "article", "portfolio"}


def _public_host(host: str) -> bool:
    return host_is_public(host)           # 풀리지 않는 이름도 False — 가져올 수 없다


def check(assets, workspace_id: str) -> None:
    for i, a in enumerate(assets, 1):
        t = a.type.value if hasattr(a.type, "value") else str(a.type)
        url = (a.url or "").strip()
        if not url:
            continue
        p = urlparse(url)
        if t == "ir_deck":
            # 업로드 자료는 서버가 서명한 내 워크스페이스 경로만
            if not url.startswith(f"supabase://{workspace_id}/"):
                raise EngineError(400, "invalid_input",
                                  f"자료 {i}: 업로드한 파일 경로가 아닙니다 — 파일을 다시 올려 주세요")
            continue
        if t == "instagram" and not p.scheme:
            continue                      # 계정 이름(@handle) — URL이 아니다
        if p.scheme not in ("http", "https") or not p.hostname:
            raise EngineError(400, "invalid_input", f"자료 {i}: http(s) 주소만 받습니다")
        if t in _WEB or t == "instagram":
            if not _public_host(p.hostname):
                raise EngineError(400, "invalid_input",
                                  f"자료 {i}: 공개 인터넷 주소가 아닙니다 ({p.hostname})")
