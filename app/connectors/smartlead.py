"""Smartlead 커넥터 — 발송과 **회신 추적**.

이 파일이 이 레포에서 처음으로 메일을 실제로 보낸다. 그래서 경계를 코드로
긋는다: 캠페인을 만들고 초안과 수신자를 올리는 것(준비)과, 그것을 실제로
쏘는 것(start_campaign)은 **서로 다른 함수**다. 준비는 되돌릴 수 있고
발송은 되돌릴 수 없다 — 되돌릴 수 없는 쪽은 사용자가 명시적으로 부른다.

추적이 이 통합의 진짜 값이다. 지금 답장 사실(reach_fact)은 사람이 손으로
입력해야 랭킹에 반영된다. 웹훅이 그 자리를 자동으로 채우면, 실제로 답장한
회사의 도메인이 이후 모든 검색의 가능성 판정을 덮어쓴다 — 파이프라인이
자기 결과로 학습하는 유일한 고리다.

키는 환경변수에서만 읽는다(SMARTLEAD_API_KEY). 웹훅 검증용 토큰
(SMARTLEAD_WEBHOOK_TOKEN)은 API 키와 **별개**다: 웹훅 URL은 외부 서비스에
저장되므로, 그 URL이 새더라도 API 키가 함께 새면 안 된다.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

_BASE = "https://server.smartlead.ai/api/v1"

# 이 상태로 바꾸면 메일이 나간다. 상수로 이름을 붙여 두는 이유는, 테스트가
# "이 문자열을 보내는 코드 경로는 start_campaign 하나뿐"임을 고정하기 위해서다.
_STATUS_START = "START"


def _key() -> str:
    return os.environ.get("SMARTLEAD_API_KEY", "")


def configured() -> bool:
    return bool(_key())


def _req(method: str, path: str, body=None, _call=None):
    """Smartlead 호출. 실패는 예외가 아니라 상태로 돌려준다 — 조용한 폴백도,
    초안 화면이 통째로 죽는 것도 원하지 않는다."""
    if _call is not None:                     # 테스트 주입
        return _call(method, path, body)
    key = _key()
    url = f"{_BASE}{path}{'&' if '?' in path else '?'}api_key={urllib.parse.quote(key)}"
    data = json.dumps(body).encode() if body is not None else None
    # User-Agent를 밝힌다 — Smartlead 앞의 Cloudflare가 urllib 기본 UA를
    # 차단한다(실측: 로컬은 200, Vercel에서 403 "error code: 1010").
    # 로컬에서만 통과하는 커넥터는 통합이 아니다.
    headers = {"User-Agent": "a2a-matching-engine/1.0"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def _wrap(fn, _call=None) -> dict:
    """{"status": ok|no_key|error, ...} 규약 — hunter 커넥터와 같은 계약.

    fn은 인자 없는 클로저다. _call은 fn이 이미 닫아 들고 있으므로 여기서는
    "키 없이도 돌아가는 테스트인가"를 판정하는 데만 쓴다.
    """
    if not _key() and _call is None:
        return {"status": "no_key", "note": "SMARTLEAD_API_KEY 없음"}
    try:
        return {"status": "ok", **(fn() or {})}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        return {"status": "error", "http": e.code, "note": detail}
    except Exception as e:                    # noqa: BLE001 — 네트워크는 통제 밖
        return {"status": "error", "note": type(e).__name__}


def mailboxes(_call=None) -> dict:
    """연결된 발송 계정. **0개면 발송은 물리적으로 불가능하다** — 실측:
    통합 시점의 계정이 그 상태였다. 화면은 이 사실을 먼저 말해야 한다."""
    def go():
        rows = _req("GET", "/email-accounts?offset=0&limit=50", _call=_call) or []
        return {"accounts": [{"id": a.get("id"), "from_email": a.get("from_email"),
                              "daily_limit": a.get("message_per_day")}
                             for a in rows]}
    return _wrap(go, _call=_call)


def prepare(name: str, *, subject: str, body: str, lead: dict,
            mailbox_ids: list, _call=None) -> dict:
    """캠페인을 만들고 초안·수신자·메일함을 붙인다. **보내지는 않는다.**

    되돌릴 수 있는 작업만 여기 모은다: 이 단계까지 끝난 캠페인은 Smartlead
    안에 초안으로 서 있을 뿐이고, 지우면 흔적이 없다.
    """
    def go():
        camp = _req("POST", "/campaigns/create",
                    {"name": name}, _call=_call) or {}
        cid = camp.get("id")
        if not cid:
            raise RuntimeError("campaign_id 없음")
        # 순서가 있다: 메일함 → 시퀀스 → 리드. 메일함 없이 시퀀스를 저장하면
        # Smartlead가 캠페인을 보낼 수 없는 상태로 둔다.
        if mailbox_ids:
            _req("POST", f"/campaigns/{cid}/email-accounts",
                 {"email_account_ids": mailbox_ids}, _call=_call)
        _req("POST", f"/campaigns/{cid}/sequences",
             {"sequences": [{"seq_number": 1, "seq_delay_details":
                             {"delay_in_days": 0},
                             "subject": subject,
                             "email_body": body}]}, _call=_call)
        _req("POST", f"/campaigns/{cid}/leads",
             {"lead_list": [lead]}, _call=_call)
        # 스케줄이 없으면 Smartlead가 START를 거부한다(실측: "Cron Exp value
        # is empty!"). 준비 단계에 넣는 이유는, 발송 함수가 스케줄까지
        # 만지면 "보내기"가 아니라 "설정하고 보내기"가 되기 때문이다 —
        # 되돌릴 수 있는 일은 전부 준비 쪽에 있어야 경계가 선명하다.
        # 창을 넓게 두는 것은 의도다: 좁히면 사용자가 누른 뒤 몇 시간 뒤에
        # 나가서, 방금 보낸 것인지 아닌지 알 수 없게 된다.
        _req("POST", f"/campaigns/{cid}/schedule",
             {"timezone": "Asia/Seoul",
              "days_of_the_week": [0, 1, 2, 3, 4, 5, 6],
              "start_hour": "00:00", "end_hour": "23:59",
              "min_time_btw_emails": 10,
              "max_new_leads_per_day": 20}, _call=_call)
        return {"campaign_id": cid, "sent": False}
    return _wrap(go, _call=_call)


def start_campaign(campaign_id: int, _call=None) -> dict:
    """**메일이 나간다.** 이 레포에서 되돌릴 수 없는 유일한 외부 작업이다.

    별도 함수로 떼어 둔 이유: prepare()를 부르는 코드가 실수로 발송까지
    하는 일이 없어야 한다. 라우터에서도 별도 엔드포인트이며, 사용자가
    화면에서 확인 문구를 거쳐야 도달한다.
    """
    def go():
        _req("POST", f"/campaigns/{campaign_id}/status",
             {"status": _STATUS_START}, _call=_call)
        return {"campaign_id": campaign_id, "sent": True}
    return _wrap(go, _call=_call)


def register_webhook(campaign_id: int, url: str, _call=None) -> dict:
    """추적 웹훅 등록 — 열람·답장·반송이 우리 원장으로 돌아오는 경로."""
    def go():
        _req("POST", f"/campaigns/{campaign_id}/webhooks",
             {"name": "a2a-tracking", "webhook_url": url,
              "event_types": ["EMAIL_SENT", "EMAIL_OPEN", "EMAIL_REPLY",
                              "EMAIL_BOUNCE"]}, _call=_call)
        return {"campaign_id": campaign_id, "webhook": True}
    return _wrap(go, _call=_call)


def delete_campaign(campaign_id: int, _call=None) -> dict:
    """준비 단계에서 실패했을 때 흔적을 남기지 않기 위해 — 그리고 테스트가
    자기가 만든 것을 치울 수 있게."""
    def go():
        _req("DELETE", f"/campaigns/{campaign_id}", _call=_call)
        return {"deleted": campaign_id}
    return _wrap(go, _call=_call)


# ── 웹훅 수신 ──────────────────────────────────────────────────────
# Smartlead가 부르는 쪽. 이벤트 이름은 Smartlead 어휘를 그대로 쓴다 —
# 번역하면 나중에 문서와 대조가 안 된다.
_EVENT_TO_OUTCOME = {
    "EMAIL_SENT": {"stage": "sent"},
    "EMAIL_OPEN": {"stage": "opened"},
    "EMAIL_REPLY": {"replied": "yes"},        # ← reach_fact를 쓰는 유일한 이벤트
    "EMAIL_BOUNCE": {"replied": "bounced"},
}


def read_event(payload: dict) -> dict:
    """웹훅 payload → 우리 원장 갱신값. 모르는 이벤트는 조용히 무시하지 않고
    빈 fields로 돌려준다(호출부가 '기록할 것 없음'을 판단하게).

    to_email을 함께 돌려주는 이유: 어느 후보의 사건인지 되짚는 열쇠가
    캠페인 이름이 아니라 수신 주소여야 한다. 캠페인은 지워질 수 있다.
    """
    ev = (payload or {}).get("event_type") or (payload or {}).get("event") or ""
    lead = (payload or {}).get("lead") or {}
    return {
        "event": ev,
        "to_email": (lead.get("email") or (payload or {}).get("to_email") or "").lower(),
        "fields": dict(_EVENT_TO_OUTCOME.get(ev, {})),
    }
