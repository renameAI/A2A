"""권한 판정 (ABAC) — 앱의 모든 쓰기·관리 동작은 여기 한 곳을 거친다.

판정 순서 (거부 우선):
  1. 비활성 멤버 → 거부
  2. 정책 중 effect=deny가 하나라도 맞으면 → 거부  (역할 권한보다 우선)
  3. 역할 권한에 동작이 있으면('*' 포함) → 허용
  4. 채널 관리자(channel_admin)는 그 채널 안에서 CHANNEL_ADMIN_GRANTS → 허용
  5. 정책 중 effect=allow가 맞으면 → 허용
  6. 그 외 → 거부

조건 언어(JSON) — 사람이 관리 화면에서 쓰고 읽을 수 있을 만큼 작게:
  {"all": [c, ...]}  {"any": [c, ...]}  {"not": c}
  {"eq": [a, b]}  {"ne": [a, b]}  {"in": [a, [..]]}  {"exists": "user.attrs.x"}
  피연산자: "user.role", "user.attrs.department", "channel.visibility",
            "channel.attrs.department", "env.hour_utc", "env.weekday" — 그 밖의
            문자열·숫자는 리터럴. 경로가 없으면 None(=조건 거짓 쪽으로 기운다).
RLS는 '볼 수 있는가'의 바닥을 DB에서 보장하고, 이 모듈은 그 위의 세부 판정이다.
읽기에도 deny 정책이 걸리면(예: 외부 근무 시간 열람 금지) 앱이 거부한다 —
RLS는 정책 테이블의 임의 조건을 평가하지 않는다(문서화된 한계).
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

PERMISSIONS = {
    "channel.read": "채널 읽기",
    "channel.create": "공개 채널 만들기",
    "channel.create_private": "비공개 채널 만들기",
    "channel.archive": "채널 보관",
    "channel.manage_members": "채널 멤버 관리",
    "message.post": "메시지 쓰기",
    "message.edit_own": "내 메시지 수정",
    "message.delete_own": "내 메시지 삭제",
    "message.delete_any": "남의 메시지 삭제(모더레이션)",
    "reaction.add": "리액션",
    "member.invite": "멤버 초대",
    "member.remove": "멤버 내보내기",
    "member.update": "멤버 역할·속성 변경",
    "role.manage": "역할 관리",
    "policy.manage": "정책 관리",
    "audit.read": "감사·열람 기록 보기",
    "export.run": "내보내기",
    "retention.manage": "보존기간·법적 보존",
    "lead.access": "리드 데이터(요청·후보·초안·발송) 보기·작업",
}
# 읽기는 기본 허용 동작이다(볼 수 있는지는 RLS가 정한다). deny 정책으로만 막는다.
DEFAULT_ALLOW = {"channel.read"}
CHANNEL_ADMIN_GRANTS = {"channel.manage_members", "message.delete_any", "channel.archive"}


@dataclass
class Principal:
    uid: str
    role: str
    permissions: set[str]
    attrs: dict = field(default_factory=dict)
    active: bool = True


@dataclass
class Decision:
    allowed: bool
    reason: str


def _resolve(path, ctx: dict):
    if not isinstance(path, str) or "." not in path:
        return path
    head, *rest = path.split(".")
    if head not in ("user", "channel", "env"):
        return path
    cur = ctx.get(head)
    for k in rest:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def evaluate(cond, ctx: dict) -> bool:
    if not cond:
        return True                      # 빈 조건 = 항상 참
    if not isinstance(cond, dict) or len(cond) != 1:
        raise ValueError(f"조건 형식 오류: {cond!r}")
    op, arg = next(iter(cond.items()))
    if op == "all":
        return all(evaluate(c, ctx) for c in arg)
    if op == "any":
        return any(evaluate(c, ctx) for c in arg)
    if op == "not":
        return not evaluate(arg, ctx)
    if op == "exists":
        return _resolve(arg, ctx) is not None
    if op in ("eq", "ne"):
        a, b = (_resolve(x, ctx) for x in arg)
        if a is None or b is None:
            return op == "ne" and not (a is None and b is None)
        return (a == b) if op == "eq" else (a != b)
    if op == "in":
        a = _resolve(arg[0], ctx)
        pool = _resolve(arg[1], ctx)
        return a is not None and isinstance(pool, list) and a in pool
    raise ValueError(f"모르는 연산자: {op}")


def validate_condition(cond) -> None:
    """저장 전에 형식만 검사한다 — 평가 중에 터지면 판정이 거부로 기울어 조용히
    권한이 사라진다. 저장 시점에 거부하는 편이 정직하다."""
    evaluate(cond, {"user": {}, "channel": {}, "env": {}})


def _matches(action: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p == "*" or p == action or (p.endswith(".*") and action.startswith(p[:-1])):
            return True
    return False


def check(p: Principal, action: str, *, channel: "dict | None" = None,
          channel_role: "str | None" = None, policies: "list[dict] | None" = None,
          now: "datetime | None" = None) -> Decision:
    if not p.active:
        return Decision(False, "비활성 멤버")
    now = now or datetime.now(timezone.utc)
    ctx = {"user": {"uid": p.uid, "role": p.role, "attrs": p.attrs},
           "channel": channel or {},
           "env": {"hour_utc": now.hour, "weekday": now.isoweekday()}}
    pols = policies or []
    for pol in pols:
        if pol["effect"] == "deny" and _matches(action, pol["actions"]) \
                and evaluate(pol.get("condition") or {}, ctx):
            return Decision(False, f"정책 #{pol.get('id')} 거부: {pol.get('description') or ''}".strip())
    if action in DEFAULT_ALLOW or _matches(action, sorted(p.permissions)):
        return Decision(True, f"역할 {p.role}")
    if channel_role == "channel_admin" and action in CHANNEL_ADMIN_GRANTS:
        return Decision(True, "채널 관리자")
    for pol in pols:
        if pol["effect"] == "allow" and _matches(action, pol["actions"]) \
                and evaluate(pol.get("condition") or {}, ctx):
            return Decision(True, f"정책 #{pol.get('id')} 허용")
    return Decision(False, f"역할 {p.role}에 {action} 권한 없음")
