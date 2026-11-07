"""로컬 개발용 메시징 플랫폼 시드 — 셀 2개, 테넌트 2개, 사용자·채널·메시지.

  CHAT_CONTROL_DSN, <셀 DSN 환경변수들>을 설정한 뒤:
  python scripts/chat_dev_seed.py --cells a=DEV_CELL_A,b=DEV_CELL_B

다시 돌려도 안전하다(이미 있으면 건너뛴다). dev 인증(SAAS_AUTH=dev)에서 사용자 uid는
X-Dev-User 값이고 이메일은 <uid>@dev.local 이다 — 웹은 ?as=<uid>로 사용자를 바꾼다.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.chat import platform, provision, service as svc  # noqa: E402
from app.chat.db import registry  # noqa: E402
from app.errors import EngineError  # noqa: E402


def mail(uid: str) -> str:
    return f"{uid}@dev.local"


def ensure_tenant(slug, name, owner, members, guests=()):
    try:
        t = provision.create_tenant(slug, name, owner_uid=owner, owner_email=mail(owner))
        print(f"tenant {slug} → cell {t.cell_id}")
    except EngineError as e:
        if e.code != "conflict":
            raise
        t = registry().tenant(slug)
        print(f"tenant {slug} exists (cell {t.cell_id})")
    for uid, role in members:
        svc.add_member(t, owner, uid, mail(uid), role=role)
    for uid in guests:
        svc.add_member(t, owner, uid, mail(uid), role="guest")
    return t


def ensure_channel(t, uid, name, **kw):
    try:
        return svc.create_channel(t, uid, name, **kw)
    except EngineError as e:
        if e.code != "conflict":
            raise
        return next(c for c in svc.list_channels(t, uid) if c["name"] == name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", default="a=DEV_CELL_A,b=DEV_CELL_B")
    ap.add_argument("--admin", default="boram")
    a = ap.parse_args()
    provision.init_control()
    for spec in a.cells.split(","):
        cid, env = spec.split("=")
        provision.register_cell(cid, env, max_tenants=50)
    platform.add_admin(None, a.admin, mail(a.admin), "superadmin")
    platform.add_admin(a.admin, "ops", mail("ops"), "operator")

    t = ensure_tenant("test", "TEST", a.admin,
                      [("alice", "admin"), ("bob", "member"), ("carol", "member")],
                      guests=["vendor"])
    ensure_tenant("acme", "Acme Corp", "acme_ceo", [("alice", "member")])

    gen = next(c for c in svc.list_channels(t, a.admin) if c["name"] == "general")
    if gen["last_seq"] == 0:
        svc.post_message(t, "alice", gen["id"], "안녕하세요! 이번 주 베트남 리드 같이 봐요 @boram")
        root = svc.post_message(t, a.admin, gen["id"], "좋아요. A 호텔부터 볼게요 @alice")
        svc.post_message(t, "bob", gen["id"], "PMS 교체 시점 확인해 볼게요", parent_seq=root["seq"])
        svc.react(t, "alice", gen["id"], root["seq"], "👍", True)
    sales = ensure_channel(t, a.admin, "영업", topic="해외 영업 파이프라인")
    if sales["last_seq"] == 0:
        svc.post_message(t, a.admin, sales["id"], "이번 분기 목표: 동남아 호텔 체인 20곳 접촉")
    deal = ensure_channel(t, a.admin, "투자-비공개", visibility="private", topic="투자 논의")
    svc.set_channel_member(t, a.admin, deal["id"], "alice", True)
    if deal["last_seq"] == 0:
        svc.post_message(t, a.admin, deal["id"], "시리즈 A 조건 검토 중 — 외부 공유 금지")
    proj = ensure_channel(t, a.admin, "vendor-협업", topic="외부 협력사와 공유")
    svc.set_channel_member(t, a.admin, proj["id"], "vendor", True)
    print("seeded — users: boram(owner, superadmin), alice(admin), bob, carol, vendor(guest)")


if __name__ == "__main__":
    main()
