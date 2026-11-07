"""팀 워크스페이스 + 채널 채팅 (app/saas/team.py) — 오프라인·결정적.

정상 흐름보다 **경계**를 더 본다: 남의 워크스페이스가 보이는가, 권한 없는
사람이 초대·내보내기·삭제를 할 수 있는가, 팀원의 '내 자료 지우기'가 팀 전체를
지우는가, 내보낸 팀원의 접근이 즉시 끊기는가.
"""
import pytest
from fastapi.testclient import TestClient

from app.saas import store as store_module
from app.saas import team as team_module
from app.saas.store import LocalSaasStore


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "_load_dotenv", lambda: None)
    monkeypatch.setenv("SAAS_AUTH", "dev")
    monkeypatch.setenv("SAAS_DB_PATH", str(tmp_path / "saas.db"))
    monkeypatch.setenv("SAAS_ALLOWED_USERS", "alice,bob,carol,mallory")
    store_module._store = None
    team_module._ws_cache.clear()          # 프로세스 전역 캐시 — 테스트 간 격리
    team_module._presence_seen.clear()
    from app.main import app
    return TestClient(app)


def H(uid):
    return {"X-Dev-User": uid}


def email(uid):
    return f"{uid}@dev.local"


def _join(client, owner, member):
    r = client.post("/saas/team/invites", json={"email": email(member)},
                    headers=H(owner))
    assert r.status_code == 200, r.text
    r = client.post("/saas/team/accept", headers=H(member))
    assert r.status_code == 200, r.text
    return r.json()["workspace_id"]


def _request(ws, rid="lr-1", title="베트남 호텔"):
    store_module.get_saas_store().put(
        "lead_request", ws, rid,
        {"request_id": rid, "title": title, "status": "draft",
         "profile_version_id": "pv", "intent": {}, "candidates": []})
    return rid


# ── 저장소 ───────────────────────────────────────────────────────────

class TestListPrefix:
    def test_orders_by_doc_id_desc_and_limits(self, tmp_path):
        s = LocalSaasStore(str(tmp_path / "s.db"))
        for k in ["a::m::003", "a::m::001", "a::m::002", "b::m::009"]:
            s.put("chat_msg", "ws", k, {"id": k})
        got = [d["id"] for d in s.list_prefix("chat_msg", "ws", "a::m::")]
        assert got == ["a::m::003", "a::m::002", "a::m::001"]
        assert len(s.list_prefix("chat_msg", "ws", "a::m::", limit=2)) == 2

    def test_isolated_by_workspace_and_kind(self, tmp_path):
        s = LocalSaasStore(str(tmp_path / "s.db"))
        s.put("chat_msg", "ws1", "a::m::1", {"id": 1})
        s.put("chat_msg", "ws2", "a::m::2", {"id": 2})
        s.put("other", "ws1", "a::m::3", {"id": 3})
        assert [d["id"] for d in s.list_prefix("chat_msg", "ws1", "a::")] == [1]

    def test_underscore_in_prefix_is_not_a_wildcard(self, tmp_path):
        # 스레드 키는 특수문자를 _로 바꾼다 — LIKE의 _가 살아 있으면
        # 'cand:a_b' 스레드가 'cand:aXb' 스레드의 글을 끌어온다.
        s = LocalSaasStore(str(tmp_path / "s.db"))
        s.put("chat_msg", "ws", "g::t::cand:aXb::1", {"id": "other"})
        s.put("chat_msg", "ws", "g::t::cand:a_b::1", {"id": "mine"})
        got = s.list_prefix("chat_msg", "ws", "g::t::cand:a_b::")
        assert [d["id"] for d in got] == ["mine"]

    def test_delete_prefix_does_not_overdelete(self, tmp_path):
        s = LocalSaasStore(str(tmp_path / "s.db"))
        s.put("insight", "ws", "lr_1::c", {})
        s.put("insight", "ws", "lrX1::c", {})
        assert s.delete_prefix("insight", "ws", "lr_1::") == 1
        assert s.get("insight", "ws", "lrX1::c") == {}

    def test_percent_in_prefix_is_not_a_wildcard(self, tmp_path):
        s = LocalSaasStore(str(tmp_path / "s.db"))
        s.put("chat_msg", "ws", "zzz", {"id": "z"})
        assert s.list_prefix("chat_msg", "ws", "%") == []


# ── 팀 ──────────────────────────────────────────────────────────────

class TestTeam:
    def test_solo_user_unchanged(self, client):
        r = client.get("/saas/team", headers=H("alice")).json()
        assert r["workspace_id"] == "ws-alice"
        assert r["me"]["role"] == "owner"
        assert [m["uid"] for m in r["members"]] == ["alice"]
        # 기존 SaaS 경로도 그대로 ws-{uid}
        assert client.get("/saas/me", headers=H("alice")).json()[
            "workspace"]["workspace_id"] == "ws-alice"

    def test_invite_is_not_auto_accepted(self, client):
        client.post("/saas/team/invites", json={"email": email("bob")},
                    headers=H("alice"))
        r = client.get("/saas/team", headers=H("bob")).json()
        assert r["workspace_id"] == "ws-bob"            # 아직 자기 공간
        assert r["pending_invite"]["ws"] == "ws-alice"

    def test_accept_shares_workspace_data(self, client):
        rid = _request("ws-alice")
        assert _join(client, "alice", "bob") == "ws-alice"
        r = client.get(f"/saas/lead-requests/{rid}", headers=H("bob"))
        assert r.status_code == 200
        assert r.json()["title"] == "베트남 호텔"
        members = client.get("/saas/team", headers=H("alice")).json()["members"]
        assert {m["uid"]: m["role"] for m in members} == {
            "alice": "owner", "bob": "member"}

    def test_outsider_sees_nothing(self, client):
        rid = _request("ws-alice")
        _join(client, "alice", "bob")
        assert client.get(f"/saas/lead-requests/{rid}",
                          headers=H("carol")).status_code == 404
        assert client.get("/saas/chat/general/messages",
                          headers=H("carol")).json()["messages"] == []
        client.post("/saas/chat/general/messages", json={"text": "비밀"},
                    headers=H("alice"))
        assert client.get("/saas/chat/general/messages",
                          headers=H("carol")).json()["messages"] == []

    def test_only_owner_invites_and_removes(self, client):
        _join(client, "alice", "bob")
        r = client.post("/saas/team/invites", json={"email": email("carol")},
                        headers=H("bob"))
        assert r.status_code == 403
        r = client.delete("/saas/team/members/alice", headers=H("bob"))
        assert r.status_code == 403

    def test_invite_validation(self, client):
        bad = client.post("/saas/team/invites", json={"email": "not-an-email"},
                          headers=H("alice"))
        assert bad.status_code == 400
        me = client.post("/saas/team/invites", json={"email": email("alice")},
                         headers=H("alice"))
        assert me.status_code == 400
        _join(client, "alice", "bob")
        dup = client.post("/saas/team/invites", json={"email": email("bob")},
                          headers=H("alice"))
        assert dup.status_code == 409

    def test_cannot_hijack_pending_invite_of_other_team(self, client):
        client.post("/saas/team/invites", json={"email": email("carol")},
                    headers=H("alice"))
        r = client.post("/saas/team/invites", json={"email": email("carol")},
                        headers=H("mallory"))
        assert r.status_code == 409
        r = client.post("/saas/team/accept", headers=H("carol"))
        assert r.json()["workspace_id"] == "ws-alice"

    def test_invite_reports_allowlist(self, client):
        r = client.post("/saas/team/invites", json={"email": "new@corp.com"},
                        headers=H("alice")).json()
        assert r["allowed"] is False
        r = client.post("/saas/team/invites", json={"email": email("bob")},
                        headers=H("alice")).json()
        assert r["allowed"] is False    # 목록엔 uid 'bob'만 — 메일은 따로 판정
        # 초대가 허용 목록을 우회하지 못한다: 목록 밖 사용자는 여전히 403
        assert client.get("/saas/team", headers=H("zed")).status_code == 403

    def test_accept_without_invite(self, client):
        assert client.post("/saas/team/accept",
                           headers=H("bob")).status_code == 404

    def test_decline(self, client):
        client.post("/saas/team/invites", json={"email": email("bob")},
                    headers=H("alice"))
        assert client.post("/saas/team/decline",
                           headers=H("bob")).json()["declined"] is True
        assert client.get("/saas/team", headers=H("bob")).json()[
            "pending_invite"] is None
        assert client.get("/saas/team", headers=H("alice")).json()["invites"] == []

    def test_cancel_invite(self, client):
        client.post("/saas/team/invites", json={"email": email("bob")},
                    headers=H("alice"))
        client.delete(f"/saas/team/invites/{email('bob')}", headers=H("alice"))
        assert client.post("/saas/team/accept",
                           headers=H("bob")).status_code == 404

    def test_removed_member_loses_access_immediately(self, client):
        rid = _request("ws-alice")
        _join(client, "alice", "bob")
        assert client.delete("/saas/team/members/bob",
                             headers=H("alice")).status_code == 200
        assert client.get(f"/saas/lead-requests/{rid}",
                          headers=H("bob")).status_code == 404
        assert client.get("/saas/team", headers=H("bob")).json()[
            "workspace_id"] == "ws-bob"

    def test_leave_returns_to_own_workspace_with_own_data(self, client):
        mine = _request("ws-bob", rid="lr-bob", title="내 요청")
        _join(client, "alice", "bob")
        assert client.get(f"/saas/lead-requests/{mine}",
                          headers=H("bob")).status_code == 404   # 안 보일 뿐
        client.post("/saas/team/leave", headers=H("bob"))
        r = client.get(f"/saas/lead-requests/{mine}", headers=H("bob"))
        assert r.status_code == 200 and r.json()["title"] == "내 요청"

    def test_owner_cannot_leave_or_remove_self(self, client):
        assert client.post("/saas/team/leave",
                           headers=H("alice")).status_code == 409
        assert client.delete("/saas/team/members/alice",
                             headers=H("alice")).status_code == 400

    def test_owner_with_members_cannot_join_elsewhere(self, client):
        _join(client, "alice", "bob")
        client.post("/saas/team/invites", json={"email": email("alice")},
                    headers=H("mallory"))
        assert client.post("/saas/team/accept",
                           headers=H("alice")).status_code == 409


class TestDeletion:
    def test_member_cannot_wipe_team_workspace(self, client):
        rid = _request("ws-alice")
        _join(client, "alice", "bob")
        r = client.post("/saas/me/delete", json={"confirm": email("bob")},
                        headers=H("bob"))
        assert r.status_code == 409
        assert client.get(f"/saas/lead-requests/{rid}",
                          headers=H("alice")).status_code == 200

    def test_owner_wipe_releases_members(self, client):
        _request("ws-alice")
        _join(client, "alice", "bob")
        client.post("/saas/team/invites", json={"email": email("carol")},
                    headers=H("alice"))
        r = client.post("/saas/me/delete", json={"confirm": email("alice")},
                        headers=H("alice"))
        assert r.status_code == 200, r.text
        assert client.get("/saas/team", headers=H("bob")).json()[
            "workspace_id"] == "ws-bob"
        # 사라진 워크스페이스로의 초대도 거둬졌다
        assert client.post("/saas/team/accept",
                           headers=H("carol")).status_code == 404

    def test_request_delete_cascades_channel(self, client):
        rid = _request("ws-alice")
        client.post(f"/saas/chat/{rid}/messages", json={"text": "hi"},
                    headers=H("alice"))
        client.post(f"/saas/chat/{rid}/messages",
                    json={"text": "re", "thread": "cand:x"}, headers=H("alice"))
        r = client.delete(f"/saas/lead-requests/{rid}", headers=H("alice")).json()
        assert r["removed"]["chat_msg"] == 2
        assert r["removed"]["thread_meta"] == 1
        assert r["removed"]["channel_meta"] == 1
        s = store_module.get_saas_store()
        assert s.list_prefix("chat_msg", "ws-alice", f"{rid}::") == []


# ── 채팅 ────────────────────────────────────────────────────────────

class TestChat:
    def test_general_roundtrip_between_teammates(self, client):
        _join(client, "alice", "bob")
        m = client.post("/saas/chat/general/messages", json={"text": "안녕"},
                        headers=H("alice")).json()
        assert m["author"]["uid"] == "alice" and m["thread"] is None
        got = client.get("/saas/chat/general/messages",
                         headers=H("bob")).json()["messages"]
        assert [x["text"] for x in got] == ["안녕"]

    def test_unknown_channel_rejected(self, client):
        r = client.post("/saas/chat/lr-nope/messages", json={"text": "x"},
                        headers=H("alice"))
        assert r.status_code == 404
        assert client.get("/saas/chat/lr-nope/messages",
                          headers=H("alice")).status_code == 404

    def test_other_teams_request_is_not_a_channel_for_me(self, client):
        rid = _request("ws-alice")
        r = client.post(f"/saas/chat/{rid}/messages", json={"text": "침입"},
                        headers=H("mallory"))
        assert r.status_code == 404

    def test_empty_and_oversized_rejected(self, client):
        assert client.post("/saas/chat/general/messages", json={"text": "   "},
                           headers=H("alice")).status_code == 400
        assert client.post("/saas/chat/general/messages",
                           json={"text": "x" * 4001},
                           headers=H("alice")).status_code == 400   # 앱 계약: 검증 실패 = 400

    def test_since_returns_only_newer_in_order(self, client):
        for t in ["1", "2", "3"]:
            client.post("/saas/chat/general/messages", json={"text": t},
                        headers=H("alice"))
        allm = client.get("/saas/chat/general/messages",
                          headers=H("alice")).json()["messages"]
        assert [m["text"] for m in allm] == ["1", "2", "3"]
        newer = client.get(f"/saas/chat/general/messages?since={allm[0]['ts']}",
                           headers=H("alice")).json()["messages"]
        assert [m["text"] for m in newer] == ["2", "3"]

    def test_thread_replies_stay_out_of_channel_and_are_counted(self, client):
        root = client.post("/saas/chat/general/messages", json={"text": "루트"},
                           headers=H("alice")).json()
        tk = "m:" + root["id"].split("::m::")[1]
        for t in ["a", "b"]:
            client.post("/saas/chat/general/messages",
                        json={"text": t, "thread": tk}, headers=H("alice"))
        main = client.get("/saas/chat/general/messages",
                          headers=H("alice")).json()
        assert [m["text"] for m in main["messages"]] == ["루트"]
        assert main["threads"][tk]["count"] == 2
        th = client.get(f"/saas/chat/general/messages?thread={tk}",
                        headers=H("alice")).json()["messages"]
        assert [m["text"] for m in th] == ["a", "b"]

    def test_thread_key_validated(self, client):
        r = client.post("/saas/chat/general/messages",
                        json={"text": "x", "thread": "../../etc"},
                        headers=H("alice"))
        assert r.status_code == 400
        r = client.get("/saas/chat/general/messages?thread=a%20b",
                       headers=H("alice"))
        assert r.status_code == 400

    def test_thread_reply_does_not_mark_channel_unread(self, client):
        _join(client, "alice", "bob")
        root = client.post("/saas/chat/general/messages", json={"text": "r"},
                           headers=H("alice")).json()
        client.post("/saas/chat/general/read", json={"ts": root["ts"]},
                    headers=H("bob"))
        client.post("/saas/chat/general/messages",
                    json={"text": "답", "thread": "m:x"}, headers=H("alice"))
        st = client.get("/saas/chat/channels", headers=H("bob")).json()["channels"]
        assert st["general"]["last_ts"] <= st["general"]["read_ts"]

    def test_unread_and_read_marker(self, client):
        _join(client, "alice", "bob")
        m = client.post("/saas/chat/general/messages", json={"text": "새 소식"},
                        headers=H("alice")).json()
        bob = client.get("/saas/chat/channels", headers=H("bob")).json()["channels"]
        assert bob["general"]["last_ts"] > bob["general"]["read_ts"]
        assert bob["general"]["mine"] is False
        alice = client.get("/saas/chat/channels",
                           headers=H("alice")).json()["channels"]
        assert alice["general"]["mine"] is True      # 내 글은 안 읽음이 아니다
        client.post("/saas/chat/general/read", json={"ts": m["ts"]},
                    headers=H("bob"))
        bob = client.get("/saas/chat/channels", headers=H("bob")).json()["channels"]
        assert bob["general"]["read_ts"] >= bob["general"]["last_ts"]

    def test_read_marker_never_moves_backwards(self, client):
        client.post("/saas/chat/general/read", json={"ts": 100.0},
                    headers=H("alice"))
        client.post("/saas/chat/general/read", json={"ts": 50.0},
                    headers=H("alice"))
        s = store_module.get_saas_store()
        assert s.get("chat_read", "ws-alice", "alice::general")["ts"] == 100.0

    def test_mentions_resolve_to_teammates_and_count_until_read(self, client):
        _join(client, "alice", "bob")
        _join(client, "alice", "carol")
        m = client.post("/saas/chat/general/messages",
                        json={"text": "@bob 이거 봐줘, @carol@dev.local 도. @nobody"},
                        headers=H("alice")).json()
        assert sorted(m["mentions"]) == ["bob", "carol"]
        client.post("/saas/chat/general/messages", json={"text": "@bob 한 번 더"},
                    headers=H("alice"))
        st = client.get("/saas/chat/channels", headers=H("bob")).json()["channels"]
        assert st["general"]["mentions"] == 2
        client.post("/saas/chat/general/read", json={"ts": 0},
                    headers=H("bob"))
        st = client.get("/saas/chat/channels", headers=H("bob")).json()["channels"]
        assert st["general"]["mentions"] == 0

    def test_self_mention_and_outsider_mention_ignored(self, client):
        _join(client, "alice", "bob")
        m = client.post("/saas/chat/general/messages",
                        json={"text": "@alice @mallory"},
                        headers=H("alice")).json()
        assert m["mentions"] == []

    def test_delete_only_own_and_leaves_tombstone(self, client):
        _join(client, "alice", "bob")
        m = client.post("/saas/chat/general/messages", json={"text": "지울 글"},
                        headers=H("alice")).json()
        r = client.delete(f"/saas/chat/general/messages?id={m['id']}",
                          headers=H("bob"))
        assert r.status_code == 403
        r = client.delete(f"/saas/chat/general/messages?id={m['id']}",
                          headers=H("alice"))
        assert r.status_code == 200
        got = client.get("/saas/chat/general/messages",
                         headers=H("bob")).json()["messages"]
        assert got[0]["deleted"] is True and got[0]["text"] == ""

    def test_delete_rejects_cross_channel_id(self, client):
        rid = _request("ws-alice")
        m = client.post(f"/saas/chat/{rid}/messages", json={"text": "x"},
                        headers=H("alice")).json()
        r = client.delete(f"/saas/chat/general/messages?id={m['id']}",
                          headers=H("alice"))
        assert r.status_code == 400

    def test_activity_kind_and_request_channel(self, client):
        rid = _request("ws-alice")
        _join(client, "alice", "bob")
        client.post(f"/saas/chat/{rid}/messages",
                    json={"text": "검색을 마쳤어요", "kind": "activity"},
                    headers=H("alice"))
        got = client.get(f"/saas/chat/{rid}/messages",
                         headers=H("bob")).json()["messages"]
        assert got[0]["kind"] == "activity"
        bad = client.post(f"/saas/chat/{rid}/messages",
                          json={"text": "x", "kind": "system"}, headers=H("alice"))
        assert bad.status_code == 400

    def test_presence_shows_active_teammate(self, client):
        _join(client, "alice", "bob")
        client.get("/saas/chat/channels", headers=H("bob"))
        members = client.get("/saas/team", headers=H("alice")).json()["members"]
        bob = next(m for m in members if m["uid"] == "bob")
        assert bob["last_seen"] is not None
