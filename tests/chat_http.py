"""시험용 데이터 플레인 어댑터 — 예전 app/chat/service.py와 같은 함수 모양을,
실제 Rust 엔진(rust/chat-engine)을 띄워 HTTP로 잇는다.

서비스 사본을 남기지 않기 위한 장치다: 시험이 검증하는 규칙은 이제 Rust/SQL 한 곳에만 있고,
이 파일은 호출 모양만 바꾼다. 환경(CHAT_* 변수)이 바뀌면 서버를 다시 띄운다 — 시험 픽스처가
스크래치 DB를 만들고 나서 처음 부르는 순간에 맞는 서버가 뜬다.
"""
import atexit
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.errors import EngineError

_BIN = os.environ.get("A2A_CHAT_BIN") or str(
    Path(__file__).resolve().parent.parent / "rust" / "chat-engine" / "target" / "release" / "a2a-chat")
_proc: "subprocess.Popen | None" = None
_base = ""
_fingerprint = ""


def _env_fingerprint() -> str:
    keys = sorted(k for k in os.environ
                  if k.startswith(("CHAT_", "T_CELL", "DEV_CELL", "LT_CELL"))
                  and k != "CHAT_DATA_PLANE_URL")   # 자기 자신(지난 서버의 주소)은 지문이 아니다
    return json.dumps([(k, os.environ[k]) for k in keys])


def _stop():
    global _proc
    if _proc is not None:
        _proc.terminate()
        try:
            _proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _proc.kill()
        _proc = None


atexit.register(_stop)


_lock = threading.Lock()


def ensure_server() -> str:
    """현재 CHAT_* 환경으로 도는 데이터 플레인을 보장하고 베이스 URL을 돌려준다.
    시험이 스레드로 두들기므로(동시성 시험) 재시작 판단은 잠금 안에서만 한다."""
    global _proc, _base, _fingerprint
    with _lock:
        return _ensure_locked()


def _ensure_locked() -> str:
    global _proc, _base, _fingerprint
    fp = _env_fingerprint()
    if _proc is not None and _proc.poll() is None and fp == _fingerprint:
        return _base
    _stop()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    env.setdefault("SAAS_AUTH", "dev")
    env["SAAS_ALLOWED_USERS"] = "__all__"        # 시험 전용 와일드카드 (auth.py·auth.rs 동일 철자)
    env.setdefault("CHAT_FANOUT", "memory")
    for k in ("CHAT_RL_USER_RATE", "CHAT_RL_USER_BURST", "CHAT_RL_TENANT_RATE", "CHAT_RL_TENANT_BURST"):
        env[k] = "1000000"           # 예전 시험은 서비스 계층을 직접 불러 라우터의 속도 제한이 없었다
    env.setdefault("RUST_LOG", "warn")
    log = open(os.environ.get("A2A_CHAT_TEST_LOG", "/tmp/a2a-chat-test.log"), "ab")
    _proc = subprocess.Popen([_BIN, "serve", "--port", str(port)], env=env,
                             stdout=log, stderr=log)
    _base = f"http://127.0.0.1:{port}"
    _fingerprint = fp
    os.environ["CHAT_DATA_PLANE_URL"] = _base    # 관리 플레인(internal_client)도 같은 서버를 본다
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{_base}/healthz", timeout=1):
                return _base
        except OSError:
            if _proc.poll() is not None:
                raise RuntimeError(f"a2a-chat이 바로 죽었습니다 (bin={_BIN})")
            time.sleep(0.1)
    raise RuntimeError("a2a-chat이 뜨지 않았습니다")


def _req(method: str, path: str, uid: str, body=None, raw=False):
    base = ensure_server()
    req = urllib.request.Request(f"{base}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-Dev-User": uid})
    try:
        r = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", {})
        except Exception:  # noqa: BLE001
            err = {}
        raise EngineError(e.code, err.get("code", "error"), err.get("message", str(e.code))) from None
    if raw:
        return r
    with r:
        return json.load(r)


MAX_BODY = 8000


# ── service.py와 같은 표면 ───────────────────────────────────────────
def me(t, uid):
    return _req("GET", f"/chat/v1/t/{t.slug}/me", uid)


def update_me(t, uid, **kwargs):
    return _req("PATCH", f"/chat/v1/t/{t.slug}/me", uid, kwargs)


def list_channels(t, uid):
    return _req("GET", f"/chat/v1/t/{t.slug}/channels", uid)["channels"]


def create_channel(t, uid, name, *, visibility="public", topic="", attrs=None):
    return _req("POST", f"/chat/v1/t/{t.slug}/channels", uid,
                {"name": name, "visibility": visibility, "topic": topic, "attrs": attrs or {}})


def set_channel_member(t, uid, channel_id, target, on, role="member"):
    if on:
        _req("POST", f"/chat/v1/t/{t.slug}/channels/{channel_id}/members", uid,
             {"user_id": target, "role": role})
    else:
        _req("DELETE", f"/chat/v1/t/{t.slug}/channels/{channel_id}/members/{target}", uid)


def archive_channel(t, uid, channel_id):
    _req("POST", f"/chat/v1/t/{t.slug}/channels/{channel_id}/archive", uid)


def post_message(t, uid, channel_id, body, *, parent_seq=None, client_msg_id=None, kind="text"):
    return _req("POST", f"/chat/v1/t/{t.slug}/channels/{channel_id}/messages", uid,
                {"body": body, "parent_seq": parent_seq, "client_msg_id": client_msg_id, "kind": kind})


def list_messages(t, uid, channel_id, *, before=None, after=None, limit=50, thread=None):
    q = [f"limit={limit}"]
    if before is not None:
        q.append(f"before={before}")
    if after is not None:
        q.append(f"after={after}")
    if thread is not None:
        q.append(f"thread={thread}")
    return _req("GET", f"/chat/v1/t/{t.slug}/channels/{channel_id}/messages?{'&'.join(q)}", uid)


def edit_message(t, uid, channel_id, seq, body):
    _req("PATCH", f"/chat/v1/t/{t.slug}/channels/{channel_id}/messages/{seq}", uid, {"body": body})


def delete_message(t, uid, channel_id, seq):
    _req("DELETE", f"/chat/v1/t/{t.slug}/channels/{channel_id}/messages/{seq}", uid)


def react(t, uid, channel_id, seq, emoji, on):
    _req("POST", f"/chat/v1/t/{t.slug}/channels/{channel_id}/messages/{seq}/reactions", uid,
         {"emoji": emoji, "on": on})


def mark_read(t, uid, channel_id, seq):
    return _req("POST", f"/chat/v1/t/{t.slug}/channels/{channel_id}/read", uid, {"seq": seq})["last_read_seq"]


def search(t, uid, q, limit=30):
    from urllib.parse import quote
    return _req("GET", f"/chat/v1/t/{t.slug}/search?q={quote(q)}", uid)["results"]


def add_member(t, actor, uid, email, role="member", attrs=None):
    _req("POST", f"/chat/v1/t/{t.slug}/members", actor,
         {"user_id": uid, "email": email, "role": role, "attrs": attrs or {}})


def update_member(t, actor, uid, *, role=None, attrs=None, active=None):
    body = {}
    if role is not None:
        body["role"] = role
    if attrs is not None:
        body["attrs"] = attrs
    if active is not None:
        body["active"] = active
    _req("PATCH", f"/chat/v1/t/{t.slug}/members/{uid}", actor, body)


def list_members(t, uid):
    return _req("GET", f"/chat/v1/t/{t.slug}/members", uid)["members"]


def list_roles(t, uid):
    return _req("GET", f"/chat/v1/t/{t.slug}/roles", uid)


def upsert_role(t, actor, name, permissions):
    _req("PUT", f"/chat/v1/t/{t.slug}/roles/{name}", actor, {"permissions": permissions})


def add_policy(t, actor, effect, actions, condition, description=""):
    return _req("POST", f"/chat/v1/t/{t.slug}/policies", actor,
                {"effect": effect, "actions": actions, "condition": condition,
                 "description": description})["id"]


def remove_policy(t, actor, pid):
    _req("DELETE", f"/chat/v1/t/{t.slug}/policies/{pid}", actor)


def read_logs(t, actor, which, limit=100):
    return _req("GET", f"/chat/v1/t/{t.slug}/logs/{which}?limit={limit}", actor)["rows"]


def visible_channel_ids(t, uid, ids=None):
    return {c["id"] for c in list_channels(t, uid)}


def export_channel(t, uid, channel_id):
    # 권한 오류는 호출 즉시 난다(예전 service와 같게) — 본문만 생성기로 흘린다
    r = _req("GET", f"/chat/v1/t/{t.slug}/channels/{channel_id}/export", uid, raw=True)

    def lines():
        with r:
            for line in r:
                line = line.decode().strip()
                if line:
                    yield line
    return lines()


# ── collect.py와 같은 표면 ───────────────────────────────────────────
def ingest(t, uid, mode, events, blobs=None):
    return _req("POST", f"/chat/v1/t/{t.slug}/collect/events", uid,
                {"mode": mode, "events": events, "blobs": blobs or {}})


def summary(t, uid):
    return _req("GET", f"/chat/v1/t/{t.slug}/collect/summary", uid)


# ── authz.py와 같은 표면(평가기는 Rust 단일 소스 — eval-cond 하위 명령) ──
def evaluate(cond, ctx):
    out = json.loads(subprocess.run(
        [_BIN, "eval-cond", json.dumps(cond), json.dumps(ctx)],
        capture_output=True, text=True, check=True).stdout)
    if "error" in out:
        raise ValueError(out["error"])
    return out["ok"]


def validate_condition(cond):
    evaluate(cond, {"user": {}, "channel": {}, "env": {}})
