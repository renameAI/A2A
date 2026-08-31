"""실제 발송과 회신 추적 — 되돌릴 수 없는 경계를 코드로 긋는다.

이 레포는 지금까지 초안까지만 만들었다(send_blocked). 사용자가 실제 발송을
요구하면서 경계가 옮겨졌지만, 없어진 것이 아니라 **한 곳으로 모였다**:
준비(되돌릴 수 있음)와 발사(되돌릴 수 없음)는 서로 다른 함수·다른 엔드포인트다.
이 테스트가 그 분리를 고정한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.connectors import smartlead


class _Rec:
    """호출을 기록하는 가짜 Smartlead."""

    def __init__(self, campaign_id=777):
        self.calls = []
        self.campaign_id = campaign_id

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if path == "/campaigns/create":
            return {"ok": True, "id": self.campaign_id}
        if path.endswith("/email-accounts") and method == "GET":
            return [{"id": 1, "from_email": "a@b.com", "message_per_day": 30}]
        return {"ok": True}

    def paths(self):
        return [p for _, p, _ in self.calls]

    def bodies(self):
        return [b for _, _, b in self.calls if b]


class TestSendBoundary:
    def test_prepare_never_starts(self):
        """준비가 발송까지 하면, 화면의 실수 한 번이 곧 발송이 된다."""
        rec = _Rec()
        out = smartlead.prepare("테스트", subject="s", body="b",
                                lead={"email": "x@y.com"}, mailbox_ids=[1],
                                _call=rec)
        assert out["status"] == "ok" and out["sent"] is False
        assert not any("/status" in p for p in rec.paths())
        assert not any((b or {}).get("status") == "START" for b in rec.bodies())

    def test_start_is_the_only_sender(self):
        rec = _Rec()
        out = smartlead.start_campaign(777, _call=rec)
        assert out["sent"] is True
        assert rec.calls == [("POST", "/campaigns/777/status",
                              {"status": "START"})]

    def test_only_start_campaign_mentions_START(self):
        """소스 전체에서 START를 보내는 함수가 하나뿐임을 고정한다."""
        import inspect
        src = inspect.getsource(smartlead)
        assert src.count("_STATUS_START") == 2      # 정의 1 + 사용 1
        assert "_STATUS_START" in inspect.getsource(smartlead.start_campaign)

    def test_prepare_attaches_mailbox_before_sequence(self):
        """메일함 없이 시퀀스를 저장하면 보낼 수 없는 캠페인이 남는다."""
        rec = _Rec()
        smartlead.prepare("t", subject="s", body="b",
                          lead={"email": "x@y.com"}, mailbox_ids=[9], _call=rec)
        paths = rec.paths()
        assert paths.index("/campaigns/777/email-accounts") \
            < paths.index("/campaigns/777/sequences")


class TestTracking:
    def test_reply_maps_to_reach_fact_field(self):
        """답장은 원장의 replied=yes를 쓴다 — 그래야 가능성 판정을 덮는다."""
        ev = smartlead.read_event(
            {"event_type": "EMAIL_REPLY", "lead": {"email": "A@B.com"}})
        assert ev["fields"]["replied"] == "yes"
        assert ev["to_email"] == "a@b.com"      # 소문자로 정규화

    def test_open_does_not_claim_a_reply(self):
        """열람은 답장이 아니다 — 미관측을 사실로 승격하지 않는다."""
        ev = smartlead.read_event(
            {"event_type": "EMAIL_OPEN", "lead": {"email": "a@b.com"}})
        assert "replied" not in ev["fields"]
        assert ev["fields"]["stage"] == "opened"

    def test_bounce_is_recorded_not_ignored(self):
        ev = smartlead.read_event(
            {"event_type": "EMAIL_BOUNCE", "lead": {"email": "a@b.com"}})
        assert ev["fields"]["replied"] == "bounced"

    def test_unknown_event_yields_nothing_to_record(self):
        ev = smartlead.read_event({"event_type": "LEAD_UNSUBSCRIBED",
                                   "lead": {"email": "a@b.com"}})
        assert ev["fields"] == {}

    def test_empty_payload_is_safe(self):
        assert smartlead.read_event({})["fields"] == {}
        assert smartlead.read_event(None)["to_email"] == ""


class TestConnectorContract:
    def test_no_key_is_a_status(self, monkeypatch):
        monkeypatch.delenv("SMARTLEAD_API_KEY", raising=False)
        assert smartlead.mailboxes()["status"] == "no_key"

    def test_webhook_token_is_separate_from_api_key(self):
        """웹훅 URL은 외부에 저장된다 — 새더라도 API 키가 함께 새면 안 된다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.smartlead_webhook)
        assert "SMARTLEAD_WEBHOOK_TOKEN" in src
        assert "SMARTLEAD_API_KEY" not in src

    def test_webhook_trusts_only_the_address(self):
        """payload는 데이터이지 지시가 아니다 — ws/후보를 payload에서 읽지 않는다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.smartlead_webhook)
        assert 'store.get("outreach_addr"' in src
        assert 'payload["ws"]' not in src and "payload.get(\"ws\")" not in src


class TestPrepareGates:
    """준비 단계가 조용히 고르거나 조용히 위험을 넘기지 않는다."""

    def test_multiple_drafts_must_be_chosen(self):
        """A/B로 만든 두 초안 중 코드가 집으면 무엇이 나갔는지 아무도 모른다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_prepare)
        assert "보낼 것을 고르세요" in src
        assert 'd = drafts[0]' in src        # 1개일 때만 자동 선택

    def test_invalid_recipient_is_blocked(self):
        """반송은 한 통으로 끝나지 않는다 — 발송 도메인 평판이 깎인다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_prepare)
        assert 'vres == "invalid"' in src
        assert "send_to_invalid" in src

    def test_unknown_verification_is_not_blocked(self):
        """모름은 나쁨이 아니다 — 한국 메일 서버는 검증 자체를 막는다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_prepare)
        assert 'vres == "unknown"' not in src

    def test_excluded_uncertain_travels_to_the_user(self):
        """미확인이라 본문에서 뺀 것이 경계에서 세탁되면 정직 표기가 무의미하다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_prepare)
        assert "excluded_uncertain" in src


class TestEventFeed:
    """답장은 우리가 만드는 사건이 아니라 상대가 만드는 사건이다 —
    원장에 조용히 쌓이지 말고 화면이 말해야 한다."""

    def test_only_speakable_events_are_returned(self):
        """Smartlead 어휘를 그대로 노출하지 않는다 — 옮길 말이 없으면 뺀다."""
        from app.saas.router import _EVENT_KO
        assert _EVENT_KO["EMAIL_REPLY"] == "회신이 도착했습니다"
        assert "LEAD_UNSUBSCRIBED" not in _EVENT_KO

    def test_since_filters_already_told_events(self):
        """전부 돌려주면 폴링마다 같은 답장을 다시 알린다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_events)
        assert 'float(r.get("at") or 0) > since' in src

    def test_events_are_ordered_oldest_first(self):
        """대화는 시간 순으로 읽힌다 — 최신이 위로 오면 순서가 뒤집힌다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_events)
        assert 'fresh.sort(key=lambda r: float(r.get("at") or 0))' in src

    def test_webhook_writes_an_event_log(self):
        """원장은 '지금 상태', 사건 로그는 '언제 무슨 일' — 둘 다 필요하다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.smartlead_webhook)
        assert 'store.put("outreach_event"' in src


class TestTestSend:
    """진짜 보내기 전에 자기 받은편지함으로 한 통 — 정당한 단계다.
    다만 그 한 통이 '이 회사에 연락했다'로 기록되면 원장이 거짓이 된다."""

    def test_override_does_not_touch_the_outcome_ledger(self):
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_prepare)
        assert "if not body.to_override:" in src

    def test_sent_test_is_not_recorded_as_contacted(self):
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_send)
        assert 'if not rec.get("test")' in src

    def test_test_flag_travels_in_both_responses(self):
        """화면이 '테스트였다'를 말할 수 있어야 오해가 없다."""
        import inspect
        from app.saas import router
        for fn in (router.outreach_prepare, router.outreach_send):
            assert '"test"' in inspect.getsource(fn)

    def test_override_skips_the_bounce_gate(self):
        """내 주소의 반송 위험은 내가 안다 — 게이트가 테스트를 막으면 안 된다."""
        import inspect
        from app.saas import router
        src = inspect.getsource(router.outreach_prepare)
        i, j = src.find("if body.to_override:"), src.find('vres == "invalid"')
        assert i != -1 and j != -1 and i < j


class TestSchedule:
    """실측: 스케줄 없이 START하면 "Cron Exp value is empty!"로 거부된다."""

    def test_prepare_sets_a_schedule(self):
        rec = _Rec()
        smartlead.prepare("t", subject="s", body="b",
                          lead={"email": "x@y.com"}, mailbox_ids=[1], _call=rec)
        assert "/campaigns/777/schedule" in rec.paths()

    def test_schedule_belongs_to_prepare_not_send(self):
        """되돌릴 수 있는 일은 전부 준비 쪽에 — 발송은 발송만 한다."""
        rec = _Rec()
        smartlead.start_campaign(777, _call=rec)
        assert not any("schedule" in p for p in rec.paths())
