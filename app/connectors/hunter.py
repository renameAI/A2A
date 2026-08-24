"""Hunter.io 접점 커넥터 — 판정이 아니라 사실 조회다.

심층 판독은 사이트 본문에서 접점을 읽는다. 실측(프로덕션 47개 도메인,
2026-08-24): 절반 이상이 사이트에 공개 접점이 없었고, Hunter의 공개 웹
색인은 접점 없던 24곳 중 8곳의 메일을 찾았다 — 전부 출처 URL이 딸려서
우리 인용 계약에 맞는다. 한국 대기업(삼양·신세계·롯데)은 0건이었으니
이 커넥터가 못 찾는 것도 정보다: 없다고 지어내지 않는다.

인용 계약(코드가 집행):
- 후보 도메인과 일치하는 메일만 채택. 색인에는 제3자 주소가 섞일 수 있고,
  그것을 이 회사 접점으로 실으면 메일이 엉뚱한 곳에 간다.
- 출처(sources)가 없는 메일은 버린다 — 확인할 수 없는 주소는 싣지 않는다.
- 키가 없으면 status="no_key"로 정직하게. 조용한 비활성 금지.

발송은 하지 않는다 — 이 모듈은 주소를 찾고 검증할 뿐이고,
발송 차단(send_blocked)은 compose 층이 유지한다.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

_BASE = "https://api.hunter.io/v2"


def _default_get(path: str, params: dict) -> dict:
    url = f"{_BASE}{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def _key() -> str:
    return os.environ.get("HUNTER_API_KEY", "")


def find_contacts(domain: str, *, limit: int = 5, _get=None) -> dict:
    """도메인의 공개 색인 접점. {"status", "contacts", "note"}.

    contacts는 신뢰도 내림차순, 항목당 {email, type, confidence, position,
    department, name, sources(최대 3)}. 실패는 상태로 남긴다 — 빈 목록과
    "조회 실패"는 후속 행동이 다르다(전자는 접점 없음이 정보, 후자는 재시도).
    """
    key = _key()
    if not key:
        return {"status": "no_key", "contacts": [], "note": "HUNTER_API_KEY 없음"}
    if not domain:
        return {"status": "no_domain", "contacts": [], "note": ""}
    get = _get or _default_get
    try:
        raw = get("/domain-search", {"domain": domain, "limit": limit,
                                     "api_key": key})
    except urllib.error.HTTPError as e:
        return {"status": "error", "contacts": [],
                "note": f"HTTP {e.code}"}
    except Exception as e:                           # noqa: BLE001 — 네트워크는 통제 밖
        return {"status": "error", "contacts": [], "note": type(e).__name__}

    out = []
    for e in ((raw.get("data") or {}).get("emails") or []):
        email = (e.get("value") or "").strip().lower()
        # 인용 계약 ① — 후보 도메인의 주소만
        if not email.endswith("@" + domain.lower()):
            continue
        srcs = [s.get("uri") or s.get("url") or "" for s in (e.get("sources") or [])]
        srcs = [u for u in srcs if u][:3]
        # 인용 계약 ② — 출처 없는 주소는 싣지 않는다
        if not srcs:
            continue
        out.append({
            "email": email,
            "type": e.get("type") or "",
            "confidence": int(e.get("confidence") or 0),
            "position": e.get("position") or "",
            "department": e.get("department") or "",
            "name": " ".join(x for x in (e.get("first_name"),
                                         e.get("last_name")) if x),
            "sources": srcs,
        })
    out.sort(key=lambda c: -c["confidence"])
    return {"status": "ok", "contacts": out[:limit], "note": ""}


def verify_email(email: str, *, _get=None) -> dict:
    """배달 가능성 검증. {"status", "result", "score", "smtp"}.

    result는 Hunter 원문(valid/invalid/accept_all/unknown/…) 그대로 —
    번역하다 의미가 뭉개지면 발송 판단을 그르친다. 실측: 한국 메일 서버는
    검증을 막는 경우가 있어 unknown이 흔하다 — unknown은 나쁨이 아니라 모름.
    """
    key = _key()
    if not key:
        return {"status": "no_key", "result": "", "score": 0, "smtp": False}
    get = _get or _default_get
    try:
        raw = get("/email-verifier", {"email": email, "api_key": key})
    except urllib.error.HTTPError as e:
        return {"status": "error", "result": "", "score": 0, "smtp": False,
                "note": f"HTTP {e.code}"}
    except Exception as e:                           # noqa: BLE001
        return {"status": "error", "result": "", "score": 0, "smtp": False,
                "note": type(e).__name__}
    d = raw.get("data") or {}
    return {"status": "ok",
            "result": d.get("status") or "unknown",
            "score": int(d.get("score") or 0),
            "smtp": bool(d.get("smtp_check"))}
