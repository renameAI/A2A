"""메시징 플랫폼 1단계 — 실제 Postgres 두 셀에서 검증한다.

SQLite·목으로는 이 계층의 핵심(RLS, 행 잠금 직렬화, 파티션, 역할 권한)을 볼 수
없다. 그래서 환경변수로 실제 서버를 받는다:
  CHAT_TEST_PG_A=postgresql://postgres@127.0.0.1:55432
  CHAT_TEST_PG_B=postgresql://postgres@127.0.0.1:55433
없으면 이 파일 전체를 건너뛴다(조용히 통과한 것처럼 보이지 않게 skip 사유를 남긴다).
"""
import os
import time
import threading
import uuid
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg import sql

from app.chat import authz
from app.errors import EngineError

PG_A = os.environ.get("CHAT_TEST_PG_A")
PG_B = os.environ.get("CHAT_TEST_PG_B")
pytestmark = pytest.mark.skipif(not (PG_A and PG_B),
                                reason="CHAT_TEST_PG_A/B 미설정 — 실 Postgres 필요")


# 시험이 만드는 테넌트의 역할. DB 역할은 클러스터 전역이라, 같은 서버의 다른 DB(개발·
# 부하 시험 환경)에 같은 slug의 테넌트가 있으면 역할을 공유한다. 그래서 정리는 **시험이
# 만든 DB 안에서만** 한다 — 다른 DB의 객체는 절대 건드리지 않는다(실측 2회: 't_%' 일괄
# 삭제가 부하 시험 테넌트를, 'drop owned'를 모든 DB에 돌린 것이 개발 환경의 acme를 지웠다).
# 다른 DB에 객체가 남아 역할을 못 지우면 그대로 두고 재사용한다(프로비저너가 'if not exists').
TEST_SLUGS = ["acme", "globex", "initech", "bigcorp", "rtco", "wsone", "wstwo"] + [f"fill{i}" for i in range(5)]


def _reset(base: str, dbs: list[str]) -> None:
    """시험 DB만 새로 만든다. 시험 역할은 다른 DB에 객체가 없을 때만 지운다."""
    roles = [f"t_{s}_{k}" for s in TEST_SLUGS for k in ("owner", "app")]
    with psycopg.connect(f"{base}/postgres", autocommit=True) as c:
        for db in dbs:
            c.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(db)))
        live = sorted({r[0] for r in c.execute(
            "select rolname from pg_roles where rolname = any(%s)", (roles,))})
        for r in live:
            try:
                c.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(r)))
            except psycopg.errors.DependentObjectsStillExist:
                pass                     # 다른 DB가 쓰는 역할 — 지우지 않고 재사용한다
        for db in dbs:
            c.execute(sql.SQL("create database {}").format(sql.Identifier(db)))


@pytest.fixture(scope="module")
def plat():
    from app.chat import db, provision
    _reset(PG_A, ["t_control", "t_cell_a"])
    _reset(PG_B, ["t_cell_b", "t_cell_ded"])
    os.environ["CHAT_CONTROL_DSN"] = f"{PG_A}/t_control"
    os.environ["T_CELL_A"] = f"{PG_A}/t_cell_a"
    os.environ["T_CELL_B"] = f"{PG_B}/t_cell_b"
    os.environ["T_CELL_DED"] = f"{PG_B}/t_cell_ded"      # 다른 서버의 전용 셀
    db.reset_registry()
    provision.init_control()
    provision.register_cell("a", "T_CELL_A", max_tenants=3)
    provision.register_cell("b", "T_CELL_B", max_tenants=3)
    acme = provision.create_tenant("acme", "Acme", owner_uid="acme_owner",
                                   owner_email="owner@acme.com")
    glx = provision.create_tenant("globex", "Globex", owner_uid="glx_owner",
                                  owner_email="owner@globex.com")
    yield {"acme": acme, "globex": glx}
    db.reset_registry()


def _ch(svc, t, uid, name):
    return next(c for c in svc.list_channels(t, uid) if c["name"] == name)


# ── 배치·격리 ───────────────────────────────────────────────────────

class TestPlacementAndIsolation:
    def test_tenants_spread_across_cells(self, plat):
        assert {plat["acme"].cell_id, plat["globex"].cell_id} == {"a", "b"}

    def test_dedicated_cell_not_auto_assigned(self, plat):
        from app.chat import provision
        provision.register_cell("ent", "T_CELL_B", dedicated=True, max_tenants=1)
        t = provision.create_tenant("initech", "Initech", owner_uid="ini", owner_email="o@ini.com")
        assert t.cell_id in ("a", "b")
        big = provision.create_tenant("bigcorp", "BigCorp", plan="enterprise", cell="ent",
                                      owner_uid="big", owner_email="o@big.com")
        assert big.cell_id == "ent"

    def test_capacity_exhaustion_is_explicit(self, plat):
        from app.chat import provision
        # a·b 각 3개 한도 — 이미 acme, globex, initech = 3개. 3개 더 채우면 6
        made = []
        with pytest.raises(EngineError) as e:
            for i in range(5):
                made.append(provision.create_tenant(f"fill{i}", "F", owner_uid=f"f{i}",
                                                    owner_email=f"o@f{i}.com"))
        assert e.value.http_status == 503 and len(made) == 3

    def test_same_cell_tenant_cannot_read_neighbor_schema(self, plat):
        """같은 셀의 다른 테넌트 스키마는 이름을 직접 적어도 권한 거부."""
        from app.chat.db import registry, tenant_tx
        acme = plat["acme"]
        with registry().control_tx() as c:
            rows = c.execute("select slug, schema_name from tenants where cell_id = %s "
                             "and slug <> %s and status = 'active'",
                             (acme.cell_id, acme.slug)).fetchall()
        assert rows, "같은 셀에 이웃 테넌트가 있어야 의미 있는 시험이다"
        neighbor = rows[0]["schema_name"]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_tx(acme, "acme_owner") as conn:
                conn.execute(sql.SQL("select * from {}.messages").format(sql.Identifier(neighbor)))

    def test_app_role_cannot_write_tables_or_run_admin_functions(self, plat):
        from app.chat.db import tenant_tx
        acme = plat["acme"]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_tx(acme, "acme_owner") as conn:
                conn.execute("insert into messages (channel_id, seq, author, body) "
                             "values (1, 999, 'x', 'forged')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_tx(acme, "acme_owner") as conn:
                conn.execute("select detach_month(date_trunc('month', now())::date)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_tx(acme, "acme_owner") as conn:
                conn.execute("select * from message_dedupe")

    def test_no_user_means_no_rows(self, plat):
        from app.chat.db import tenant_tx
        with tenant_tx(plat["acme"], "") as conn:
            assert conn.execute("select count(*) as n from channels").fetchone()["n"] == 0


# ── 권한 (RLS + ABAC) ───────────────────────────────────────────────

class TestVisibility:
    def test_private_channel_hidden_from_non_members(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        svc.add_member(t, "acme_owner", "alice", "alice@acme.com")
        svc.add_member(t, "acme_owner", "bob", "bob@acme.com")
        ch = svc.create_channel(t, "alice", "secret-deal", visibility="private")
        svc.post_message(t, "alice", ch["id"], "기밀 단가 12%")
        assert "secret-deal" not in [c["name"] for c in svc.list_channels(t, "bob")]
        with pytest.raises(EngineError) as e:
            svc.list_messages(t, "bob", ch["id"])
        assert e.value.http_status == 404              # 존재 여부도 새지 않는다
        with pytest.raises(EngineError):
            svc.post_message(t, "bob", ch["id"], "끼어들기")
        assert svc.search(t, "bob", "기밀") == []
        assert len(svc.search(t, "alice", "기밀")) == 1
        # 초대받으면 보인다
        svc.set_channel_member(t, "alice", ch["id"], "bob", True)
        assert svc.list_messages(t, "bob", ch["id"])["messages"][0]["body"] == "기밀 단가 12%"

    def test_member_cannot_add_people_to_private_channel(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        svc.add_member(t, "acme_owner", "carol", "carol@acme.com")
        ch = svc.create_channel(t, "alice", "alice-only", visibility="private")
        svc.set_channel_member(t, "alice", ch["id"], "bob", True)
        with pytest.raises(EngineError) as e:   # bob은 채널 관리자가 아니다
            svc.set_channel_member(t, "bob", ch["id"], "carol", True)
        assert e.value.http_status == 403

    def test_guest_sees_only_invited_channels(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        svc.add_member(t, "acme_owner", "vendor", "rep@vendor.com", role="guest")
        names = [c["name"] for c in svc.list_channels(t, "vendor")]
        assert "general" not in names
        ch = svc.create_channel(t, "alice", "vendor-proj")
        svc.set_channel_member(t, "alice", ch["id"], "vendor", True)
        assert [c["name"] for c in svc.list_channels(t, "vendor")] == ["vendor-proj"]
        gen = _ch(svc, t, "alice", "general")
        with pytest.raises(EngineError):
            svc.post_message(t, "vendor", gen["id"], "hi")

    def test_deactivated_member_loses_everything(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        svc.add_member(t, "acme_owner", "leaver", "leaver@acme.com")
        gen = _ch(svc, t, "leaver", "general")
        svc.update_member(t, "acme_owner", "leaver", active=False)
        with pytest.raises(EngineError) as e:      # 빈 목록이 아니라 명시적 거부
            svc.list_channels(t, "leaver")
        assert e.value.http_status == 403
        from app.chat.db import tenant_tx           # DB 바닥(RLS)에서도 아무것도 안 보인다
        with tenant_tx(t, "leaver") as conn:
            assert conn.execute("select count(*) as n from messages").fetchone()["n"] == 0
        with pytest.raises(EngineError):
            svc.post_message(t, "leaver", gen["id"], "아직 있나요")

    def test_cross_tenant_user_is_not_a_member(self, plat):
        from app.chat import service as svc
        with pytest.raises(EngineError) as e:
            svc.list_channels(plat["globex"], "alice")
        assert e.value.http_status == 403


class TestAbac:
    def test_condition_language(self):
        ctx = {"user": {"role": "member", "attrs": {"department": "sales"}},
               "channel": {"visibility": "private", "attrs": {"department": "sales"}},
               "env": {"hour_utc": 3}}
        assert authz.evaluate({"eq": ["user.attrs.department", "channel.attrs.department"]}, ctx)
        assert authz.evaluate({"all": [{"eq": ["channel.visibility", "private"]},
                                       {"in": ["user.role", ["member", "admin"]]}]}, ctx)
        assert not authz.evaluate({"exists": "user.attrs.nope"}, ctx)
        assert authz.evaluate({"not": {"eq": ["user.role", "guest"]}}, ctx)
        with pytest.raises(ValueError):
            authz.validate_condition({"lol": [1]})

    def test_deny_overrides_role(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        ch = svc.create_channel(t, "alice", "finance", attrs={"department": "finance"})
        svc.update_member(t, "acme_owner", "bob", attrs={"department": "sales"})
        pid = svc.add_policy(t, "acme_owner", "deny", ["message.post"],
                             {"all": [{"exists": "channel.attrs.department"},
                                      {"ne": ["user.attrs.department", "channel.attrs.department"]}]},
                             "부서 채널엔 그 부서만 쓴다")
        with pytest.raises(EngineError) as e:
            svc.post_message(t, "bob", ch["id"], "영업팀인데요")
        assert e.value.http_status == 403 and f"#{pid}" in e.value.message
        svc.update_member(t, "acme_owner", "bob", attrs={"department": "finance"})
        assert svc.post_message(t, "bob", ch["id"], "재무팀 합류")["seq"] >= 1
        svc.remove_policy(t, "acme_owner", pid)

    def test_allow_policy_grants_beyond_role(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        svc.add_member(t, "acme_owner", "lead", "lead@acme.com", attrs={"title": "lead"})
        ch = svc.create_channel(t, "alice", "lead-can-archive")
        with pytest.raises(EngineError):
            svc.archive_channel(t, "lead", ch["id"])
        svc.add_policy(t, "acme_owner", "allow", ["channel.archive"],
                       {"eq": ["user.attrs.title", "lead"]}, "팀장은 채널 보관 가능")
        svc.archive_channel(t, "lead", ch["id"])
        assert "lead-can-archive" not in [c["name"] for c in svc.list_channels(t, "alice")]

    def test_invalid_policy_rejected_at_save(self, plat):
        from app.chat import service as svc
        with pytest.raises(EngineError) as e:
            svc.add_policy(plat["acme"], "acme_owner", "deny", ["message.post"], {"xor": []})
        assert e.value.http_status == 400

    def test_policy_management_requires_permission(self, plat):
        from app.chat import service as svc
        with pytest.raises(EngineError) as e:
            svc.add_policy(plat["acme"], "bob", "allow", ["*"], {})
        assert e.value.http_status == 403

    def test_only_owner_creates_star_role(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        svc.add_member(t, "acme_owner", "adm", "adm@acme.com", role="admin")
        with pytest.raises(EngineError):
            svc.upsert_role(t, "adm", "superuser", ["*"])
        svc.upsert_role(t, "adm", "moderator", ["message.delete_any", "message.post"])
        with pytest.raises(EngineError):
            svc.upsert_role(t, "adm", "weird", ["message.fly"])

    def test_last_owner_protected(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        with pytest.raises(EngineError) as e:
            svc.update_member(t, "acme_owner", "acme_owner", role="admin")
        assert e.value.http_status == 409
        with pytest.raises(EngineError):
            svc.update_member(t, "acme_owner", "acme_owner", active=False)

    def test_moderation_is_audited(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        gen = _ch(svc, t, "alice", "general")
        m = svc.post_message(t, "bob", gen["id"], "스팸 링크")
        with pytest.raises(EngineError):
            svc.delete_message(t, "alice", gen["id"], m["seq"])     # 일반 멤버
        svc.delete_message(t, "adm", gen["id"], m["seq"])            # 관리자
        logs = svc.read_logs(t, "acme_owner", "audit_log")
        assert any(l["action"] == "message.delete_any" for l in logs)
        with pytest.raises(EngineError):
            svc.read_logs(t, "bob", "audit_log")


class TestCollect:
    """A2A 브라우저 업무 이벤트 받기(app/chat/collect.py, 스키마 v4)."""

    @pytest.fixture(autouse=True)
    def _members(self, plat):
        # 이 묶음만 따로 돌려도 되게 — 앞 시험이 멤버를 만들어 두었으면 그대로 쓴다
        from app.chat import service as svc
        for uid in ("cl_bob", "cl_alice"):
            try:
                svc.add_member(plat["acme"], "acme_owner", uid, f"{uid}@acme.com")
            except EngineError:
                pass
    EV = {"event_id": "ev_c_1", "kind": "agent.tool_call", "recorded_at": "2026-10-28T03:00:00Z", "user": "cl_bob",
          "task_id": "t1", "content_hash": "sha256:" + "a" * 64, "source_url": "https://secret.example/x?q=1",
          "data": {"tool": "read_page", "ok": True, "input": {"url": "https://secret.example"}}}

    def test_signals_strip_content_and_dedupe(self, plat):
        from app.chat import collect
        t = plat["acme"]
        assert collect.ingest(t, "cl_bob", "signals", [self.EV])["accepted"] == 1
        assert collect.ingest(t, "cl_bob", "signals", [self.EV])["duplicates"] == 1           # 재전송 안전
        from app.chat.db import tenant_admin_tx
        with tenant_admin_tx(t) as conn:
            body = conn.execute("select body from work_events where event_id = 'ev_c_1'").fetchone()["body"]
        assert "content_hash" not in body and "source_url" not in body and "input" not in body["data"]
        assert body["data"] == {"tool": "read_page", "ok": True}

    def test_hashes_keep_host_only(self, plat):
        from app.chat import collect
        from app.chat.db import tenant_admin_tx
        t = plat["acme"]
        ev = {**self.EV, "event_id": "ev_c_2"}
        collect.ingest(t, "cl_bob", "hashes", [ev])
        with tenant_admin_tx(t) as conn:
            body = conn.execute("select body from work_events where event_id = 'ev_c_2'").fetchone()["body"]
        assert body["content_hash"] == ev["content_hash"] and body["source_host"] == "secret.example" and "source_url" not in body

    def test_cannot_upload_others_events_or_bad_blobs(self, plat):
        from app.chat import collect
        t = plat["acme"]
        with pytest.raises(EngineError) as e:
            collect.ingest(t, "cl_bob", "signals", [{**self.EV, "event_id": "ev_c_3", "user": "cl_alice"}])
        assert e.value.http_status == 403
        with pytest.raises(EngineError):                                                     # 원문은 full에서만
            collect.ingest(t, "cl_bob", "hashes", [], {"a" * 64: "x"})
        with pytest.raises(EngineError):                                                     # 해시가 안 맞으면 거부
            collect.ingest(t, "cl_bob", "full", [], {"b" * 64: "x"})
        import hashlib
        h = hashlib.sha256("원문".encode()).hexdigest()
        assert collect.ingest(t, "cl_bob", "full", [], {h: "원문"})["blobs"] == 1
        with pytest.raises(EngineError):                                                     # 멤버가 아닌 사람
            collect.ingest(t, "nobody", "signals", [{**self.EV, "event_id": "ev_c_4", "user": "nobody"}])

    def test_rls_mine_only(self, plat):
        from app.chat import collect
        t = plat["acme"]
        collect.ingest(t, "cl_bob", "signals", [{**self.EV, "event_id": "ev_c_5"}])
        assert collect.summary(t, "cl_alice")["mine"]["events"] == 0          # 남이 올린 것은 내 것으로 안 보인다(RLS)
        assert collect.summary(t, "cl_bob")["mine"]["events"] >= 1


class TestProfile:
    def test_member_renames_self_and_is_audited(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        out = svc.update_me(t, "bob", display_name="  밥   스미스 ")
        assert out["display_name"] == "밥 스미스"                    # 공백 정리
        names = {m["user_id"]: m["display_name"] for m in svc.list_members(t, "alice")}
        assert names["bob"] == "밥 스미스"                           # 팀원에게도 보인다
        logs = svc.read_logs(t, "acme_owner", "audit_log")
        assert any(l["action"] == "profile.update" and l["target"] == "bob" for l in logs)

    def test_invalid_name_rejected(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        for bad in ("", "   ", "가" * 41):
            with pytest.raises(EngineError) as e:
                svc.update_me(t, "bob", display_name=bad)
            assert e.value.http_status == 400

    def test_avatar_color_visible_to_team(self, plat):
        from app.chat import service as svc
        t = plat["acme"]
        before = svc.me(t, "bob")["display_name"]
        out = svc.update_me(t, "bob", avatar_hue=200)
        assert out["avatar_hue"] == 200 and out["display_name"] == before      # 이름은 그대로
        hues = {m["user_id"]: m["avatar_hue"] for m in svc.list_members(t, "alice")}
        assert hues["bob"] == 200                                              # 팀원 화면에도
        assert svc.update_me(t, "bob", avatar_hue=None)["avatar_hue"] is None  # 기본 색으로
        for bad in (360, -1, True, "12", 3.5):
            with pytest.raises(EngineError) as e:
                svc.update_me(t, "bob", avatar_hue=bad)
            assert e.value.http_status == 400
        with pytest.raises(EngineError):                                       # 바꿀 게 없으면 거부
            svc.update_me(t, "bob")

    def test_non_member_cannot_rename(self, plat):
        from app.chat import service as svc
        with pytest.raises(EngineError):
            svc.update_me(plat["acme"], "glx_owner", display_name="침입자")
        with pytest.raises(EngineError):                             # 비활성화된 멤버도
            svc.update_me(plat["acme"], "leaver", display_name="돌아왔어요")


# ── 순서·동시성 ─────────────────────────────────────────────────────

def _hammer(n_threads, fn):
    errs, barrier = [], threading.Barrier(n_threads)

    def run(i):
        try:
            barrier.wait()
            fn(i)
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run, args=(i,)) for i in range(n_threads)]
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    return errs


class TestConcurrency:
    def test_concurrent_posts_get_contiguous_unique_seq(self, plat):
        from app.chat import service as svc
        t = plat["globex"]
        for i in range(10):
            svc.add_member(t, "glx_owner", f"u{i}", f"u{i}@globex.com")
        ch = svc.create_channel(t, "u0", "load")
        for i in range(1, 10):
            svc.set_channel_member(t, f"u{i}", ch["id"], f"u{i}", True)
        seqs, lock = [], threading.Lock()

        def work(i):
            for k in range(25):
                r = svc.post_message(t, f"u{i % 10}", ch["id"], f"{i}-{k}")
                with lock:
                    seqs.append(r["seq"])
        errs = _hammer(20, work)
        assert errs == []
        assert sorted(seqs) == list(range(1, 501))           # 공백·중복 없음
        page = svc.list_messages(t, "u0", ch["id"], limit=200)
        assert [m["seq"] for m in page["messages"]] == list(range(301, 501))
        assert page["has_more"] is True

    def test_idempotent_retry_under_race(self, plat):
        from app.chat import service as svc
        t = plat["globex"]
        ch = _ch(svc, t, "u0", "load")
        before = ch["last_seq"]
        cid = uuid.uuid4().hex
        got, lock = [], threading.Lock()

        def work(i):
            r = svc.post_message(t, "u1", ch["id"], "한 번만", client_msg_id=cid)
            with lock:
                got.append((r["seq"], r["duplicate"]))
        assert _hammer(10, work) == []
        assert len({s for s, _ in got}) == 1
        assert sum(1 for _, d in got if not d) == 1
        after = _ch(svc, t, "u0", "load")["last_seq"]
        assert after == before + 1                           # 되돌린 seq로 공백 없음
        nxt = svc.post_message(t, "u1", ch["id"], "다음")
        assert nxt["seq"] == after + 1

    def test_duplicate_after_reply_keeps_unread_state(self, plat):
        """회귀: 직전 메시지가 답글일 때 중복 재시도가 last_top_seq를 망가뜨리지 않는다."""
        from app.chat import service as svc
        t = plat["globex"]
        ch = svc.create_channel(t, "u5", "dup-after-reply")
        cid = uuid.uuid4().hex
        top = svc.post_message(t, "u5", ch["id"], "본문", client_msg_id=cid)
        svc.post_message(t, "u5", ch["id"], "답글", parent_seq=top["seq"])
        svc.post_message(t, "u5", ch["id"], "답글2", parent_seq=top["seq"])
        before = next(c for c in svc.list_channels(t, "u5") if c["id"] == ch["id"])
        again = svc.post_message(t, "u5", ch["id"], "본문", client_msg_id=cid)
        after = next(c for c in svc.list_channels(t, "u5") if c["id"] == ch["id"])
        assert again["duplicate"] and again["seq"] == top["seq"]
        assert (after["last_seq"], after["last_top_seq"]) == \
            (before["last_seq"], before["last_top_seq"]) == (3, 1)

    def test_thread_reply_count_has_no_lost_updates(self, plat):
        from app.chat import service as svc
        t = plat["globex"]
        ch = _ch(svc, t, "u0", "load")
        root = svc.post_message(t, "u0", ch["id"], "스레드 루트")

        def work(i):
            for k in range(10):
                svc.post_message(t, f"u{i}", ch["id"], f"답 {i}-{k}", parent_seq=root["seq"])
        assert _hammer(10, work) == []
        th = svc.list_messages(t, "u0", ch["id"], thread=root["seq"], limit=200)
        assert th["root"]["reply_count"] == 100
        assert len(th["messages"]) == 100
        main = svc.list_messages(t, "u0", ch["id"], after=root["seq"] - 1)
        assert [m["seq"] for m in main["messages"]] == [root["seq"]]   # 답글은 본문에 안 섞임

    def test_mentions_counted_without_counters(self, plat):
        from app.chat import service as svc
        t = plat["globex"]
        ch = _ch(svc, t, "u0", "load")

        def work(i):
            svc.post_message(t, f"u{i}", ch["id"], "@u0 확인 부탁")
        assert _hammer(9, lambda i: work(i + 1)) == []
        st = _ch(svc, t, "u0", "load")
        assert st["mentions"] == 9 and st["unread"] is True
        svc.mark_read(t, "u0", ch["id"], st["last_seq"])
        st = _ch(svc, t, "u0", "load")
        assert st["mentions"] == 0 and st["unread"] is False

    def test_read_cursor_never_moves_backwards(self, plat):
        from app.chat import service as svc
        t = plat["globex"]
        ch = _ch(svc, t, "u2", "load")
        assert svc.mark_read(t, "u2", ch["id"], 400) == 400
        assert svc.mark_read(t, "u2", ch["id"], 10) == 400

    def test_cursor_pagination_and_gap_fill(self, plat):
        from app.chat import service as svc
        t = plat["globex"]
        ch = svc.create_channel(t, "u3", "pages")
        for i in range(30):
            svc.post_message(t, "u3", ch["id"], f"m{i}")
        last = svc.list_messages(t, "u3", ch["id"], limit=10)
        assert [m["body"] for m in last["messages"]] == [f"m{i}" for i in range(20, 30)]
        older = svc.list_messages(t, "u3", ch["id"], before=last["messages"][0]["seq"], limit=10)
        assert [m["body"] for m in older["messages"]] == [f"m{i}" for i in range(10, 20)]
        gap = svc.list_messages(t, "u3", ch["id"], after=25, limit=100)
        assert [m["seq"] for m in gap["messages"]] == [26, 27, 28, 29, 30]

    def test_posting_to_archived_channel_fails(self, plat):
        from app.chat import service as svc
        t = plat["globex"]
        ch = svc.create_channel(t, "u4", "temp")
        svc.archive_channel(t, "u4", ch["id"])
        with pytest.raises(EngineError):
            svc.post_message(t, "u4", ch["id"], "늦었다")


class TestPartitions:
    def test_rows_in_default_partition_move_when_month_is_created(self, plat):
        from app.chat.db import tenant_admin_tx
        t = plat["acme"]
        future = datetime(datetime.now(timezone.utc).year + 1, 6, 15, tzinfo=timezone.utc)
        with tenant_admin_tx(t) as conn:
            conn.execute("insert into messages (channel_id, seq, author, body, created_at) "
                         "values (1, 100000, 'x', '미래 메시지', %s)", (future,))
            assert conn.execute("select count(*) as n from messages_default").fetchone()["n"] == 1
            made = conn.execute("select ensure_partitions(24) as n").fetchone()["n"]
            assert made >= 1
            assert conn.execute("select count(*) as n from messages_default").fetchone()["n"] == 0
            part = f"messages_{future:%Y%m}"
            assert conn.execute(sql.SQL("select count(*) as n from {}").format(
                sql.Identifier(part))).fetchone()["n"] == 1
            conn.execute("delete from messages where seq = 100000")

    def test_migrate_all_is_idempotent(self, plat):
        from app.chat import provision
        assert provision.migrate_all() == []
        res = provision.run_maintenance()
        assert all("error" not in r for r in res)


# ── 아카이빙 ────────────────────────────────────────────────────────

def _seed_old(t, svc, name, n_old, private=False, owner="acme_owner"):
    """14개월 전 메시지 n_old개(seq 1..n_old)를 넣고 채널 last_seq를 맞춘다."""
    from app.chat.db import tenant_admin_tx
    ch = svc.create_channel(t, owner, name, visibility="private" if private else "public")
    old = datetime.now(timezone.utc).replace(day=10)
    y, m = old.year, old.month - 14
    while m <= 0:
        m += 12; y -= 1
    old = old.replace(year=y, month=m)
    with tenant_admin_tx(t) as conn:
        conn.execute("select create_month(%s)", (old.date(),))
        for i in range(1, n_old + 1):
            conn.execute("insert into messages (channel_id, seq, author, body, created_at) "
                         "values (%s, %s, %s, %s, %s)", (ch["id"], i, owner, f"old{i}", old))
        conn.execute("update channels set last_seq = %s, last_top_seq = %s where id = %s",
                     (n_old, n_old, ch["id"]))
    return ch, old.date().replace(day=1)


ARCH: dict = {}


@pytest.fixture(scope="module")
def arch_dir(tmp_path_factory):
    """아카이브 저장소는 클래스 전체가 공유한다 — 테스트마다 비우면 앞 시험이
    보관한 객체를 뒤 시험(내보내기·영구 삭제)이 볼 수 없다."""
    d = tmp_path_factory.mktemp("arch")
    mp = pytest.MonkeyPatch()
    mp.setenv("CHAT_ARCHIVE_STORE", "local")
    mp.setenv("CHAT_ARCHIVE_DIR", str(d))
    ARCH["dir"] = d
    yield d
    mp.undo()


@pytest.mark.usefixtures("arch_dir")
class TestArchive:
    def test_roundtrip_and_seamless_pagination(self, plat):
        from app.chat import archive, service as svc
        from app.chat.db import tenant_admin_tx
        t = plat["acme"]
        ch, month = _seed_old(t, svc, "history", 30)
        for i in range(5):
            svc.post_message(t, "acme_owner", ch["id"], f"new{i}")
        res = archive.archive_tenant(t)
        mine = [r for r in res if r["month"] == str(month)]
        assert mine and mine[0]["status"] == "archived" and mine[0]["rows"] == 30
        with tenant_admin_tx(t) as conn:
            assert conn.execute("select to_regclass(%s) as r",
                                (f"messages_{month:%Y%m}",)).fetchone()["r"] is None
            idx = conn.execute("select sum(rows) as rows from archive_index where month = %s",
                               (month,)).fetchone()
            assert idx["rows"] == 30
        page = svc.list_messages(t, "acme_owner", ch["id"], limit=10)
        assert [m["body"] for m in page["messages"]] == \
            [f"old{i}" for i in range(26, 31)] + [f"new{i}" for i in range(5)]
        assert [m["archived"] for m in page["messages"]] == [True] * 5 + [False] * 5
        assert page["has_more"] is True
        seen = [m["seq"] for m in page["messages"]]
        while page["has_more"]:
            page = svc.list_messages(t, "acme_owner", ch["id"], before=seen[0], limit=10)
            seen = [m["seq"] for m in page["messages"]] + seen
        assert seen == list(range(1, 36))                    # 35개 전부, 빈틈·중복 없음

    def test_rearchiving_same_month_never_overwrites(self, plat):
        """회귀: 같은 달에 늦게 들어온 행을 다시 보관해도 앞서 보관한 30건이 남는다."""
        from app.chat import archive, service as svc
        t = plat["acme"]
        late, _ = _seed_old(t, svc, "late-arrivals", 4)
        archive.archive_tenant(t)
        hist = next(c for c in svc.list_channels(t, "acme_owner") if c["name"] == "history")
        allm = svc.list_messages(t, "acme_owner", hist["id"], limit=200)["messages"]
        assert len(allm) == 35
        assert len(svc.list_messages(t, "acme_owner", late["id"], limit=200)["messages"]) == 4

    def test_rerun_is_idempotent(self, plat):
        from app.chat import archive
        assert archive.archive_tenant(plat["acme"]) == []

    def test_archived_private_channel_stays_private(self, plat):
        from app.chat import archive, service as svc
        t = plat["acme"]
        ch, _ = _seed_old(t, svc, "old-secret", 5, private=True, owner="alice")
        archive.archive_tenant(t)
        with pytest.raises(EngineError) as e:
            svc.list_messages(t, "carol", ch["id"])
        assert e.value.http_status == 404
        assert len(svc.list_messages(t, "alice", ch["id"])["messages"]) == 5

    def test_verify_failure_keeps_partition(self, plat, monkeypatch):
        from app.chat import archive, service as svc
        from app.chat.db import tenant_admin_tx
        t = plat["globex"]
        ch, month = _seed_old(t, svc, "fragile", 3, owner="glx_owner")
        real = archive.LocalStore.get
        monkeypatch.setattr(archive.LocalStore, "get", lambda self, k: real(self, k) + b"x")
        with pytest.raises(EngineError) as e:
            archive.archive_month(t, month)
        assert e.value.code == "archive_verify_failed"
        with tenant_admin_tx(t) as conn:
            assert conn.execute("select count(*) as n from messages where channel_id = %s",
                                (ch["id"],)).fetchone()["n"] == 3
            assert conn.execute("select count(*) as n from archive_index").fetchone()["n"] == 0
        monkeypatch.setattr(archive.LocalStore, "get", real)
        assert archive.archive_month(t, month)["status"] == "archived"

    def test_export_requires_permission_and_includes_archive(self, plat):
        from app.chat import archive, service as svc
        t = plat["acme"]
        ch = next(c for c in svc.list_channels(t, "acme_owner") if c["name"] == "history")
        with pytest.raises(EngineError):
            archive.export_channel(t, "bob", ch["id"])
        lines = list(archive.export_channel(t, "acme_owner", ch["id"]))
        assert len(lines) == 35

    def test_purge_respects_legal_hold(self, plat):
        from app.chat import archive
        from app.chat.db import registry
        with registry().control_tx() as c:
            c.execute("update tenants set archive_purge_months = 12, legal_hold = true "
                      "where slug = 'acme'")
        out = [o for o in archive.purge_expired() if o["tenant"] == "acme"]
        assert out == [{"tenant": "acme", "skipped": "legal_hold"}]
        assert any(ARCH["dir"].rglob("*.jsonl.gz"))
        with registry().control_tx() as c:
            c.execute("update tenants set legal_hold = false where slug = 'acme'")
        out = [o for o in archive.purge_expired() if o["tenant"] == "acme"]
        assert len(out[0]["purged"]) >= 1


# ── 컨트롤 플레인 (플랫폼 어드민) ───────────────────────────────────

class TestPlatform:
    @pytest.fixture(autouse=True, scope="class")
    def admins(self, plat):
        from app.chat import platform as pf
        pf.add_admin(None, "root", "root@platform.io", "superadmin")
        pf.add_admin("root", "op", "op@platform.io", "operator")
        pf.add_admin("root", "eye", "eye@platform.io", "viewer")

    def test_role_tiers(self, plat):
        from app.chat import platform as pf
        with pytest.raises(EngineError):
            pf.list_tenants("alice")                          # 테넌트 사용자는 플랫폼 어드민 아님
        assert len(pf.list_tenants("eye")) >= 2               # viewer: 목록 가능
        with pytest.raises(EngineError):
            pf.admin_messages("eye", "acme", 1)               # viewer: 본문 불가
        with pytest.raises(EngineError):
            pf.add_admin("op", "x", "x@p.io", "superadmin")   # operator: 어드민 관리 불가

    def test_content_access_is_audited_on_both_sides(self, plat):
        from app.chat import platform as pf, service as svc
        t = plat["acme"]
        gen = next(c for c in svc.list_channels(t, "alice") if c["name"] == "general")
        svc.post_message(t, "alice", gen["id"], "어드민이 볼 수 있는 글")
        res = pf.admin_messages("op", "acme", gen["id"], reason="고객 요청 #123 조사")
        assert any(m["body"] == "어드민이 볼 수 있는 글" for m in res["messages"])
        aud = pf.platform_audit("eye", slug="acme")
        assert aud[0]["action"] == "content.read" and aud[0]["reason"] == "고객 요청 #123 조사"
        # 테넌트 소유자가 자기 쪽 기록에서 확인할 수 있다
        logs = svc.read_logs(t, "acme_owner", "access_log")
        assert logs[0]["actor"] == "op" and logs[0]["actor_kind"] == "platform_admin"

    def test_admin_reads_private_channels_too(self, plat):
        from app.chat import platform as pf, service as svc
        t = plat["acme"]
        priv = next(c for c in svc.list_channels(t, "alice") if c["name"] == "secret-deal")
        res = pf.admin_messages("op", "acme", priv["id"], reason="감사")
        assert res["messages"][0]["body"] == "기밀 단가 12%"

    def test_suspend_blocks_users(self, plat):
        from app.chat import platform as pf, service as svc
        from app.chat.db import registry
        pf.set_tenant("op", "globex", status="suspended", reason="미납")
        with pytest.raises(EngineError) as e:
            svc.list_channels(registry().tenant("globex"), "u1")
        assert e.value.http_status == 423
        with pytest.raises(EngineError) as e:          # 사유 없는 상태 변경은 거부
            pf.set_tenant("op", "globex", status="active")
        assert e.value.http_status == 400
        pf.set_tenant("op", "globex", status="active", reason="납부 확인")
        assert svc.list_channels(registry().tenant("globex"), "u1")

    def test_announcement_across_cells_with_partial_failure_and_retry(self, plat):
        from app.chat import platform as pf, service as svc
        from app.chat.db import registry, tenant_admin_tx
        # 한 테넌트의 공지 채널을 망가뜨려 그 셀 전달이 실패하게 한다
        t = registry().tenant("initech")
        with tenant_admin_tx(t) as conn:
            conn.execute("update channels set archived_at = now() where kind = 'announcements'")
        res = pf.announce("op", "정기 점검 공지: 토 02:00")
        assert "initech" in res["failed"] and res["delivered"] >= 2
        with tenant_admin_tx(t) as conn:
            conn.execute("update channels set archived_at = null where kind = 'announcements'")
        again = pf.deliver_announcement(res["announcement_id"])
        assert again == {"announcement_id": res["announcement_id"], "delivered": 1, "failed": []}
        # 재전달이 이미 받은 테넌트에 중복 게시하지 않았다
        for slug, uid in (("acme", "alice"), ("globex", "u1"), ("initech", "ini")):
            tt = registry().tenant(slug)
            ann = next(c for c in svc.list_channels(tt, uid) if c["kind"] == "announcements")
            msgs = svc.list_messages(tt, uid, ann["id"])["messages"]
            assert [m["body"] for m in msgs].count("정기 점검 공지: 토 02:00") == 1, slug

    def test_metrics_and_alert_lifecycle(self, plat):
        from app.chat import metrics, platform as pf
        for _ in range(80):
            metrics.record("acme", "post", 3.0, error=False)
        for _ in range(20):
            metrics.record("acme", "post", 900.0, error=True)
        assert metrics.flush() >= 1
        m = pf.tenant_metrics("eye", "acme", 10)
        assert m["summary"]["post"]["requests"] >= 100
        assert m["summary"]["post"]["p99"] >= 500
        res = pf.evaluate_alerts()
        assert any("acme" in o and "오류율" in o for o in res["opened"])
        assert pf.evaluate_alerts()["opened"] == [] or \
            not any("acme: 오류율" in o for o in pf.evaluate_alerts()["opened"])   # 한 번만 열린다
        inc = pf.ops_messages("op", "incidents")
        assert any("acme" in x["body"] for x in inc)
        assert any(a["kind"] == "error_rate" and a["slug"] == "acme" for a in pf.list_alerts("eye"))

    def test_unreachable_cell_raises_critical_alert(self, plat, monkeypatch):
        from app.chat import platform as pf, provision
        monkeypatch.setenv("T_CELL_DEAD", "postgresql://postgres@127.0.0.1:1/nope?connect_timeout=1")
        provision_cells = provision.register_cell
        try:
            provision_cells("dead", "T_CELL_DEAD", dedicated=True)
        except Exception:  # noqa: BLE001 — 역할 준비 단계에서 실패하는 것이 정상
            pass
        cells = {c["id"]: c for c in pf.list_cells("eye")}
        assert cells["dead"]["reachable"] is False and "dsn_env" not in cells["dead"]
        res = pf.evaluate_alerts()
        assert any("셀 dead" in o for o in res["opened"])
        pf.set_cell("root", "dead", "down")

    def test_ops_channel(self, plat):
        from app.chat import platform as pf
        pf.ops_post("op", "ops", "배포 시작")
        assert pf.ops_messages("op", "ops")[-1]["body"] == "배포 시작"
        with pytest.raises(EngineError):
            pf.ops_post("eye", "ops", "viewer는 못 쓴다")

    def test_cron_requires_secret(self, plat, monkeypatch):
        from fastapi.testclient import TestClient
        from app.main import app
        c = TestClient(app)
        monkeypatch.delenv("CHAT_CRON_SECRET", raising=False)
        assert c.post("/platform/v1/jobs/tick").status_code == 403       # 비밀 미설정 = 거부
        monkeypatch.setenv("CHAT_CRON_SECRET", "s3cret-value-xyz")
        assert c.post("/platform/v1/jobs/tick", headers={"x-cron-secret": "nope"}).status_code == 403
        r = c.post("/platform/v1/jobs/tick", headers={"x-cron-secret": "s3cret-value-xyz"})
        assert r.status_code == 200 and "alerts" in r.json()


# ── 셀 이동 (전용 셀 승격) ──────────────────────────────────────────

@pytest.mark.usefixtures("arch_dir")
class TestMove:
    @pytest.fixture(autouse=True, scope="class")
    def ded(self, plat):
        from app.chat import provision
        provision.register_cell("ded", "T_CELL_DED", dedicated=True, max_tenants=5)

    def test_rejects_same_physical_database(self, plat):
        from app.chat import move
        # 'ent' 셀은 셀 b와 같은 DB를 가리킨다(배치 시험에서 등록)
        with pytest.raises(EngineError) as e:
            move.move_tenant("globex", "ent", "root")
        assert e.value.http_status == 409
        from app.chat.db import registry
        assert registry().tenant("globex").cell_id == "b"

    def test_rejects_concurrent_move_and_pauses_jobs(self, plat):
        from app.chat import move, provision
        from app.chat.db import registry
        t = registry().tenant("initech")
        with registry().control_tx() as c:
            mid = c.execute("insert into tenant_moves (tenant_id, from_cell, to_cell, actor) "
                            "values (%s, %s, 'ded', 'x') returning id", (t.id, t.cell_id)).fetchone()["id"]
        try:
            with pytest.raises(EngineError) as e:
                move.move_tenant("initech", "ded", "root")
            assert e.value.http_status == 409
            assert "initech" not in [r["tenant"] for r in provision.run_maintenance()]
        finally:
            with registry().control_tx() as c:
                c.execute("delete from tenant_moves where id = %s", (mid,))

    def test_failed_verify_rolls_back_cleanly(self, plat, monkeypatch):
        from app.chat import move, service as svc
        from app.chat.db import registry
        real = move._checksums
        calls = {"n": 0}

        def broken(c):
            calls["n"] += 1
            out = real(c)
            if calls["n"] == 2:                    # 대상 쪽 체크섬만 어긋나게
                out["messages"] = (out["messages"][0] + 1, "0")
            return out
        monkeypatch.setattr(move, "_checksums", broken)
        with pytest.raises(EngineError) as e:
            move.move_tenant("globex", "ded", "root")
        assert e.value.code == "move_verify_failed"
        registry().forget("globex")
        t = registry().tenant("globex")
        assert (t.cell_id, t.status) == ("b", "active")
        ch = next(c for c in svc.list_channels(t, "u1") if c["name"] == "load")
        assert svc.post_message(t, "u1", ch["id"], "롤백 뒤에도 쓴다")["seq"] > 0
        with psycopg.connect(os.environ["T_CELL_DED"]) as c:
            assert c.execute("select to_regnamespace('t_globex')").fetchone()[0] is None
        with registry().control_tx() as c:
            assert c.execute("select state from tenant_moves where tenant_id = %s order by id desc",
                             (t.id,)).fetchone()["state"] == "rolled_back"

    def test_move_under_live_writes_loses_nothing(self, plat):
        from app.chat import move, service as svc
        from app.chat.db import registry, tenant_admin_tx
        slug = "acme"
        t_old = registry().tenant(slug)
        assert t_old.cell_id == "a"
        chans = [c for c in svc.list_channels(t_old, "alice")
                 if c["kind"] == "channel" and c["name"] in ("general", "history")]
        before_view = {c["name"]: (c["last_seq"], c["mentions"], c["unread"])
                       for c in svc.list_channels(t_old, "alice")}
        with tenant_admin_tx(t_old) as conn:
            TestMove.idx_before = conn.execute("select month, object_key, sha256, rows "
                                               "from archive_index order by id").fetchall()
        ok, fails, lock = [], [], threading.Lock()
        stop = threading.Event()

        def writer(i):
            k = 0
            while not stop.is_set():
                ch = chans[k % len(chans)]
                body = f"w{i}-{k}"
                try:
                    r = svc.post_message(registry().tenant(slug), "alice", ch["id"], body)
                    with lock:
                        ok.append((ch["id"], r["seq"], body))
                except EngineError as e:
                    with lock:
                        fails.append(e.code)
                    time.sleep(0.02)
                k += 1
        ws = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
        for w in ws:
            w.start()
        time.sleep(0.5)                               # 복사 전부터 쓰기가 흐르고 있게
        rep = move.move_tenant(slug, "ded", "root")
        time.sleep(0.5)                               # 전환 뒤에도 쓰기가 이어지게
        stop.set()
        for w in ws:
            w.join()

        t_new = registry().tenant(slug)
        assert t_new.cell_id == "ded" and t_new.status == "active"
        assert ok and set(fails) <= {"tenant_moving"}, set(fails)
        # 확인 응답을 받은 모든 게시가 새 셀에 같은 seq·본문으로 있다
        with tenant_admin_tx(t_new) as conn:
            rows = {(r["channel_id"], r["seq"]): r["body"] for r in conn.execute(
                "select channel_id, seq, body from messages where channel_id = any(%s)",
                ([c["id"] for c in chans],)).fetchall()}
            for ch in chans:
                # 핫 구간이 빈틈없이 이어진다(history는 1~30이 아카이브에 있어 핫은 31부터)
                n, lo, mx, last = conn.execute(
                    "select count(*) as n, min(seq) as lo, max(seq) as mx, (select last_seq "
                    "from channels where id = %s) as last from messages where channel_id = %s",
                    (ch["id"], ch["id"])).fetchone().values()
                assert n == mx - lo + 1 and mx == last, ch["name"]
        missing = [(c, s, b) for c, s, b in ok if rows.get((c, s)) != b]
        assert missing == [], missing[:5]
        print(f"\nMOVE-REPORT copy={rep['copy_sec']}s pause={rep['write_pause_sec']}s "
              f"delta={rep['delta_messages']} acked={len(ok)} retried={len(fails)} "
              f"rows={sum(rep['rows'].values())}")
        assert rep["write_pause_sec"] < 5 and "delta_messages" in rep
        # 캐시가 낡은 인스턴스(옛 셀)로 오는 쓰기는 DB 펜스가 거부한다
        with pytest.raises(EngineError) as e:
            svc.post_message(t_old, "alice", chans[0]["id"], "옛 셀로 가는 글")
        assert e.value.code == "tenant_moving"
        # 권한·읽음·멘션·아카이브가 그대로다
        after_view = {c["name"]: c for c in svc.list_channels(t_new, "alice")}
        for name, (seq, mentions, unread) in before_view.items():
            if name not in ("general", "history"):
                assert (after_view[name]["last_seq"], after_view[name]["mentions"]) == (seq, mentions)
        priv = next(c for c in svc.list_channels(t_new, "alice") if c["name"] == "secret-deal")
        with pytest.raises(EngineError):
            svc.list_messages(t_new, "carol", priv["id"])
        # 아카이브 색인(객체 키·sha)이 그대로 옮겨졌다 — 객체는 slug 기준 키라 셀과 무관
        with tenant_admin_tx(t_new) as conn:
            idx_after = conn.execute("select month, object_key, sha256, rows "
                                     "from archive_index order by id").fetchall()
        assert idx_after == TestMove.idx_before
        TestMove.move_id = rep["move_id"]

    def test_finalize_drops_source(self, plat):
        from app.chat import move, service as svc
        from app.chat.db import registry
        res = move.finalize(TestMove.move_id, "root")
        assert res["dropped"] == "a:t_acme"
        with psycopg.connect(os.environ["T_CELL_A"]) as c:
            assert c.execute("select to_regnamespace('t_acme')").fetchone()[0] is None
        t = registry().tenant("acme")
        gen = next(c for c in svc.list_channels(t, "alice") if c["name"] == "general")
        assert svc.post_message(t, "alice", gen["id"], "전용 셀에서 계속")["seq"] > 0
        with pytest.raises(EngineError):
            move.finalize(TestMove.move_id, "root")       # 두 번은 안 된다


class TestUiQueries:
    def test_me_reports_role_and_capabilities(self, plat):
        from app.chat import service as svc
        from app.chat.db import registry
        t = registry().tenant("globex")
        owner = svc.me(t, "glx_owner")
        assert owner["role"] == "owner" and owner["can"]["role.manage"] is True
        member = svc.me(t, "u1")
        assert member["role"] == "member" and member["can"]["role.manage"] is False
        assert member["can"]["channel.create"] is True

    def test_member_list_respects_guest_visibility(self, plat):
        from app.chat import service as svc
        from app.chat.db import registry
        t = registry().tenant("globex")
        svc.add_member(t, "glx_owner", "g_guest", "g@out.side", role="guest")
        assert len(svc.list_members(t, "u1")) > 3
        assert [m["user_id"] for m in svc.list_members(t, "g_guest")] == ["g_guest"]

    def test_roles_listing_has_catalog(self, plat):
        from app.chat import service as svc
        from app.chat.db import registry
        r = svc.list_roles(registry().tenant("globex"), "u1")
        assert {x["name"] for x in r["roles"]} >= {"owner", "admin", "member", "guest"}
        assert "message.post" in r["catalog"]


def test_thread_root_has_message_shape(plat):
    """회귀: 스레드 루트에 reactions가 빠져 웹 화면이 TypeError로 터졌다."""
    from app.chat import service as svc
    from app.chat.db import registry
    t = registry().tenant("globex")
    ch = svc.create_channel(t, "u6", "root-shape")
    root = svc.post_message(t, "u6", ch["id"], "루트")
    svc.react(t, "u6", ch["id"], root["seq"], "👍", True)
    svc.post_message(t, "u6", ch["id"], "답", parent_seq=root["seq"])
    th = svc.list_messages(t, "u6", ch["id"], thread=root["seq"])
    assert th["root"]["reactions"] == [{"emoji": "👍", "count": 1, "mine": True}]
    assert "client_msg_id" in th["messages"][0]



class TestAdminConsoleFixes:
    """어드민 콘솔을 만들며 드러난 서버 결함들 — 회귀 시험."""

    def test_metrics_with_over_max_latency_is_json_safe(self, plat):
        import json as _json
        from app.chat import metrics, platform as pf
        metrics.record("initech", "post", 9000.0, error=False)   # 2.5초 초과 → 맨 위 칸
        metrics.flush()
        m = pf.tenant_metrics("eye", "initech", 10)
        _json.dumps(m, default=str, allow_nan=False)             # starlette와 같은 조건
        assert m["summary"]["post"]["p99"] is None and m["summary"]["post"]["over_max"] >= 1
        _json.dumps(pf.overview("eye"), default=str, allow_nan=False)

    def test_content_access_requires_reason(self, plat):
        from app.chat import platform as pf
        with pytest.raises(EngineError) as e:
            pf.admin_channels("op", "globex", reason=" ")
        assert e.value.http_status == 400

    def test_unknown_ops_channel_is_not_created(self, plat):
        from app.chat import platform as pf
        with pytest.raises(EngineError) as e:
            pf.ops_messages("op", "opps")
        assert e.value.http_status == 404
        assert {c["name"] for c in pf.ops_channels("op")} == {"ops", "incidents"}

    def test_archive_purge_can_be_cleared(self, plat):
        from app.chat import platform as pf
        from app.chat.db import registry
        pf.set_tenant("op", "initech", archive_purge_months=24)
        pf.set_tenant("op", "initech", clear_archive_purge=True)
        with registry().control_tx() as c:
            assert c.execute("select archive_purge_months from tenants where slug = 'initech'"
                             ).fetchone()["archive_purge_months"] is None

    def test_audit_rows_carry_slug_and_retry_is_audited(self, plat):
        from app.chat import platform as pf
        rows = pf.platform_audit("eye", limit=50)
        assert any(r.get("slug") == "globex" for r in rows)
        aid = pf.announce("op", "재시도 감사 확인")["announcement_id"]
        pf.retry_announcement("op", aid)
        assert any(r["action"] == "announce.retry" and r["target"] == str(aid)
                   for r in pf.platform_audit("eye", limit=20))


# ── 셀 이동 — 배경 작업자 ───────────────────────────────────────────

def _can_write(slug, uid, cname):
    from app.chat import service as svc
    from app.chat.db import registry
    registry().forget(slug)
    t = registry().tenant(slug)
    ch = next(c for c in svc.list_channels(t, uid) if c["name"] == cname)
    return svc.post_message(t, uid, ch["id"], "쓰기 확인")["seq"] > 0


@pytest.mark.usefixtures("arch_dir")
class TestMoveWorker:
    def _state(self, mid):
        from app.chat.db import registry
        with registry().control_tx() as c:
            return c.execute("select state, phase, error from tenant_moves where id = %s",
                             (mid,)).fetchone()

    def test_cancel_while_queued(self, plat):
        from app.chat import move
        mid = move.enqueue("globex", "ded", "root")
        assert self._state(mid)["state"] == "queued"
        with pytest.raises(EngineError):                    # 대기 중엔 두 번째 이동 불가
            move.enqueue("globex", "ded", "root")
        assert move.cancel(mid, "root")["result"] == "cancelled"
        assert self._state(mid)["state"] == "cancelled"
        assert move.run_pending("w") == []                  # 취소된 것은 가져가지 않는다

    def test_cancel_during_copy_rolls_back(self, plat):
        from app.chat import move
        from app.chat.db import registry
        mid = move.enqueue("globex", "ded", "root")
        assert move.claim_next("w1") == mid
        assert move.cancel(mid, "root")["result"] == "cancel_requested"
        assert move.run_move(mid) == {"move_id": mid, "cancelled": True}
        registry().forget("globex")
        t = registry().tenant("globex")
        assert (t.cell_id, t.status) == ("b", "active")
        assert _can_write("globex", "u1", "load")
        with psycopg.connect(os.environ["T_CELL_DED"]) as c:
            assert c.execute("select to_regnamespace('t_globex')").fetchone()[0] is None

    def test_worker_dies_after_fence_and_is_recovered(self, plat, monkeypatch):
        """가장 위험한 경우: 펜스(쓰기 차단) 뒤 작업자가 죽는다 → 하트비트가 끊기고
        recover_stale이 원본 권한·테넌트 상태를 되돌려야 서비스가 살아난다."""
        from app.chat import move
        from app.chat.db import registry
        mid = move.enqueue("globex", "ded", "root")
        monkeypatch.setattr(move, "_CRASH_AT", "delta")      # 펜스 직후 '사망'
        assert move.claim_next("w2") == mid
        with pytest.raises(move._SimulatedCrash):
            move.run_move(mid)
        monkeypatch.setattr(move, "_CRASH_AT", None)
        assert self._state(mid)["state"] == "fencing"
        with pytest.raises(EngineError) as e:               # 갇힌 상태: 쓰기 불가
            _can_write("globex", "u1", "load")
        assert e.value.http_status == 503
        assert move.recover_stale(stale_sec=3600) == []      # 아직 신선하면 손대지 않는다
        out = move.recover_stale(stale_sec=0)
        assert out == [{"move_id": mid, "was": "fencing", "action": "rolled_back"}]
        st = self._state(mid)
        assert st["state"] == "rolled_back" and "하트비트" in st["error"]
        assert _can_write("globex", "u1", "load")            # 원본으로 다시 쓴다
        with psycopg.connect(os.environ["T_CELL_DED"]) as c:
            assert c.execute("select to_regnamespace('t_globex')").fetchone()[0] is None

    def test_api_returns_202_and_worker_completes(self, plat, monkeypatch, tmp_path):
        import app.config as config_mod
        from fastapi.testclient import TestClient
        from app.chat import worker
        from app.main import app
        monkeypatch.setattr(config_mod, "_load_dotenv", lambda: None)
        monkeypatch.setenv("SAAS_AUTH", "dev")
        monkeypatch.setenv("SAAS_ALLOWED_USERS", "root")
        monkeypatch.setenv("SAAS_DB_PATH", str(tmp_path / "s.db"))
        monkeypatch.setenv("CHAT_MOVE_INLINE", "0")          # 서버리스처럼 — 작업자만 실행
        c = TestClient(app)
        h = {"X-Dev-User": "root"}
        r = c.post("/platform/v1/tenants/globex/move", json={"to_cell": "ded"}, headers=h)
        assert r.status_code == 202 and r.json()["state"] == "queued"
        mid = r.json()["move_id"]
        assert c.get(f"/platform/v1/moves/{mid}", headers=h).json()["state"] == "queued"
        res = worker.tick("test-worker")
        assert res["ran"][0]["move_id"] == mid
        m = c.get(f"/platform/v1/moves/{mid}", headers=h).json()
        assert m["state"] == "switched" and m["phase"] == "switched" and m["worker"] == "test-worker"
        assert m["report"]["rows"]["channels"] >= 1 and "write_pause_sec" in m["report"]
        assert _can_write("globex", "u1", "load")
        from app.chat.db import registry
        assert registry().tenant("globex").cell_id == "ded"
