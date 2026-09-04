"""보낸 메일 대시보드 — 세션(요청)을 넘어 워크스페이스 전체를 하나로 본다.

실측 요청: "저 세션이 모두 동일한 사용자라고 생각해서 내가 뿌린 메일
전체를 볼 수 있도록". outreach_tracker는 애초에 request 필터가 없었으므로
집계 대상은 이미 워크스페이스 전체였다 — 이 테스트는 그것과, 새로 얹은
퍼널·요청 제목·일별 추이가 세션 경계 없이 합산되는 것을 고정한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.saas.router import outreach_tracker
from app.saas.store import LocalSaasStore


class _U:
    workspace_id = "ws1"


def _seed(st):
    st.put("lead_request", "ws1", "lr-a", {"request_id": "lr-a", "title": "프랑스 요청"})
    st.put("lead_request", "ws1", "lr-b", {"request_id": "lr-b", "title": "베트남 요청"})
    rows = [
        ("lr-a", "c1", "회사A", "EMAIL_SENT", 100),
        ("lr-a", "c1", "회사A", "EMAIL_OPEN", 200),
        ("lr-a", "c1", "회사A", "EMAIL_REPLY", 300),
        ("lr-b", "c2", "회사B", "EMAIL_SENT", 150),          # 다른 세션
        ("lr-b", "c3", "회사C", "EMAIL_SENT", 160),
        ("lr-b", "c3", "회사C", "EMAIL_BOUNCE", 161),
    ]
    for i, (rid, cid, name, ev, at) in enumerate(rows):
        st.put("outreach_event", "ws1", f"{at}-{i}",
              {"at": at, "event": ev, "request_id": rid,
               "company_id": cid, "name": name, "source_url": ""})
    return st


def test_aggregates_across_sessions(tmp_path, monkeypatch):
    """세션 두 개(lr-a, lr-b)의 메일이 하나의 목록·퍼널로 합쳐진다."""
    st = _seed(LocalSaasStore(str(tmp_path / "t.db")))
    monkeypatch.setattr("app.saas.router.get_saas_store", lambda: st)
    r = outreach_tracker(user=_U())
    assert r["total"] == 3
    rids = {l["request_id"] for l in r["leads"]}
    assert rids == {"lr-a", "lr-b"}


def test_request_title_is_attached(tmp_path, monkeypatch):
    """리스트만 보고도 어느 요청에서 나온 메일인지 알아야 필터가 된다."""
    st = _seed(LocalSaasStore(str(tmp_path / "t.db")))
    monkeypatch.setattr("app.saas.router.get_saas_store", lambda: st)
    r = outreach_tracker(user=_U())
    titles = {l["company_id"]: l["request_title"] for l in r["leads"]}
    assert titles["c1"] == "프랑스 요청" and titles["c2"] == "베트남 요청"


def test_funnel_counts_only_sent_mail(tmp_path, monkeypatch):
    """'보내지도 않은 메일의 오픈율'은 의미가 없다 — 분모는 발송된 것만."""
    st = _seed(LocalSaasStore(str(tmp_path / "t.db")))
    monkeypatch.setattr("app.saas.router.get_saas_store", lambda: st)
    f = outreach_tracker(user=_U())["funnel"]
    assert f["sent"] == 3 and f["opened"] == 1 and f["replied"] == 1
    assert f["bounced"] == 1
    assert abs(f["open_rate"] - 1 / 3) < 1e-3   # round(4)라 정밀 일치는 안 됨


def test_by_day_has_fourteen_days(tmp_path, monkeypatch):
    st = _seed(LocalSaasStore(str(tmp_path / "t.db")))
    monkeypatch.setattr("app.saas.router.get_saas_store", lambda: st)
    r = outreach_tracker(user=_U())
    assert len(r["by_day"]) == 14


def test_empty_workspace_is_safe(tmp_path, monkeypatch):
    st = LocalSaasStore(str(tmp_path / "t.db"))
    monkeypatch.setattr("app.saas.router.get_saas_store", lambda: st)
    r = outreach_tracker(user=_U())
    assert r["total"] == 0 and r["funnel"]["sent"] == 0
