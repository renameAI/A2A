"""업로드 자료 보관 — Supabase Storage (버킷 `assets`).

왜 로컬 디스크를 버렸나: 엔진이 업로드 파일을 디스크에 쓰고 나중에 경로로
읽었는데, Vercel 서버리스는 /tmp 외 읽기 전용이고 /tmp조차 호출 간에 공유되지
않는다. 실측으로 193바이트 PDF도 500이 났다(PermissionError → OSError →
FastAPI 500). 크기와 무관한 구조 결함이었다.

왜 서명 업로드 URL인가: 파일이 Vercel 함수를 통과하면 요청 본문 4.5MB 상한에
걸린다(413, 요금제와 무관한 플랫폼 제한). 브라우저가 스토리지로 직접 올리면
함수를 거치지 않으므로 그 상한이 적용되지 않는다.

인가는 서명이 한다: 경로를 **엔진이** 정해 서명하므로, 클라이언트가 워크스페이스를
속여도 남의 접두사에 못 쓴다. 그래서 storage.objects에 RLS 정책을 따로 두지
않는다(정책이 없다는 것은 곧 anon·authenticated 직접 접근이 전부 막혔다는 뜻).
"""
import os
import urllib.error
import urllib.parse
import urllib.request

from ..errors import EngineError

BUCKET = "assets"

# 확장자 → MIME. 버킷의 allowed_mime_types와 같은 목록이어야 한다 —
# 여기서 통과시켜도 스토리지가 거절하면 사용자는 이유를 모른다.
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": ("application/vnd.openxmlformats-officedocument"
              ".wordprocessingml.document"),
}


def _base() -> "tuple[str, str]":
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    if not url or not key:
        raise EngineError(500, "storage_not_configured",
                          "SUPABASE_URL 또는 SUPABASE_SERVICE_KEY가 없습니다.")
    return url, key


def _call(method: str, path: str, body: bytes | None = None,
          content_type: str = "application/json") -> bytes:
    url, key = _base()
    req = urllib.request.Request(
        f"{url}/storage/v1{path}", data=body, method=method,
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 **({"Content-Type": content_type} if body is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        if e.code == 404:
            raise EngineError(404, "asset_missing",
                              "업로드한 파일을 찾을 수 없습니다.") from e
        raise EngineError(502, "storage_error",
                          f"스토리지 오류({e.code}): {detail}") from e


def sign_upload(object_path: str) -> dict:
    """업로드용 서명 URL 발급. 경로는 호출자(엔진)가 정한다."""
    import json
    raw = _call("POST", f"/object/upload/sign/{BUCKET}/"
                        f"{urllib.parse.quote(object_path)}", b"{}")
    d = json.loads(raw)
    # 응답의 url은 /storage/v1 이후 경로다 — 클라이언트가 붙일 수 있게 토큰만 준다.
    return {"path": object_path, "token": d.get("token", "")}


def download(object_path: str) -> bytes:
    """service_role로 원본을 가져온다 (버킷이 private이라 서명 없이 읽는 유일한 길)."""
    return _call("GET", f"/object/{BUCKET}/"
                        f"{urllib.parse.quote(object_path)}")


def remove_prefix(prefix: str) -> int:
    """접두사(=워크스페이스) 아래를 전부 지운다. 삭제한 개수를 돌려준다."""
    import json
    listing = json.loads(_call(
        "POST", f"/object/list/{BUCKET}",
        json.dumps({"prefix": prefix, "limit": 1000}).encode()))
    names = [f"{prefix}/{o['name']}" for o in listing if o.get("name")]
    if not names:
        return 0
    _call("DELETE", f"/object/{BUCKET}",
          json.dumps({"prefixes": names}).encode())
    return len(names)
