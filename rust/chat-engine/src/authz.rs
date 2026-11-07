// 권한 판정 (ABAC) — app/chat/authz.py를 그대로 옮긴 단일 소스(파이썬 쪽은 삭제됨).
// 판정 순서(거부 우선): 비활성 → deny 정책 → 역할 권한('*') → 채널 관리자 → allow 정책 → 거부.
use std::collections::HashSet;

use chrono::{Datelike, Timelike, Utc};
use serde_json::{json, Map, Value};

pub const PERMISSIONS: &[(&str, &str)] = &[
    ("channel.read", "채널 읽기"),
    ("channel.create", "공개 채널 만들기"),
    ("channel.create_private", "비공개 채널 만들기"),
    ("channel.archive", "채널 보관"),
    ("channel.manage_members", "채널 멤버 관리"),
    ("message.post", "메시지 쓰기"),
    ("message.edit_own", "내 메시지 수정"),
    ("message.delete_own", "내 메시지 삭제"),
    ("message.delete_any", "남의 메시지 삭제(모더레이션)"),
    ("reaction.add", "리액션"),
    ("member.invite", "멤버 초대"),
    ("member.remove", "멤버 내보내기"),
    ("member.update", "멤버 역할·속성 변경"),
    ("role.manage", "역할 관리"),
    ("policy.manage", "정책 관리"),
    ("audit.read", "감사·열람 기록 보기"),
    ("export.run", "내보내기"),
    ("retention.manage", "보존기간·법적 보존"),
    ("lead.access", "리드 데이터(요청·후보·초안·발송) 보기·작업"),
];
// 읽기는 기본 허용 동작이다(볼 수 있는지는 RLS가 정한다). deny 정책으로만 막는다.
const DEFAULT_ALLOW: &[&str] = &["channel.read"];
const CHANNEL_ADMIN_GRANTS: &[&str] = &["channel.manage_members", "message.delete_any", "channel.archive"];

pub fn permissions_catalog() -> Value {
    Value::Object(PERMISSIONS.iter().map(|(k, v)| ((*k).to_string(), json!(v))).collect())
}
pub fn known_permission(p: &str) -> bool {
    p == "*" || PERMISSIONS.iter().any(|(k, _)| *k == p)
        || (p.ends_with(".*") && PERMISSIONS.iter().any(|(k, _)| k.starts_with(&p[..p.len() - 1])))
}

#[derive(Debug, Clone)]
pub struct Principal {
    pub uid: String,
    pub role: String,
    pub permissions: HashSet<String>,
    pub attrs: Value,
    pub active: bool,
}

#[derive(Debug)]
pub struct Decision {
    pub allowed: bool,
    pub reason: String,
}

fn resolve<'a>(path: &'a Value, ctx: &'a Value) -> Value {
    let Some(s) = path.as_str() else { return path.clone() };
    if !s.contains('.') {
        return path.clone();
    }
    let mut parts = s.split('.');
    let head = parts.next().unwrap_or("");
    if !matches!(head, "user" | "channel" | "env") {
        return path.clone();
    }
    let mut cur = ctx.get(head).cloned().unwrap_or(Value::Null);
    for k in parts {
        match cur {
            Value::Object(ref m) => cur = m.get(k).cloned().unwrap_or(Value::Null),
            _ => return Value::Null,
        }
    }
    cur
}

pub fn evaluate(cond: &Value, ctx: &Value) -> Result<bool, String> {
    if cond.is_null() {
        return Ok(true);
    }
    let Some(obj) = cond.as_object() else { return Err(format!("조건 형식 오류: {cond}")) };
    if obj.is_empty() {
        return Ok(true); // 빈 조건 = 항상 참
    }
    if obj.len() != 1 {
        return Err(format!("조건 형식 오류: {cond}"));
    }
    let (op, arg) = obj.iter().next().unwrap();
    match op.as_str() {
        "all" => {
            for c in arg.as_array().ok_or("all 인자는 배열")? {
                if !evaluate(c, ctx)? { return Ok(false); }
            }
            Ok(true)
        }
        "any" => {
            for c in arg.as_array().ok_or("any 인자는 배열")? {
                if evaluate(c, ctx)? { return Ok(true); }
            }
            Ok(false)
        }
        "not" => Ok(!evaluate(arg, ctx)?),
        "exists" => Ok(!resolve(arg, ctx).is_null()),
        "eq" | "ne" => {
            let a = arg.as_array().filter(|a| a.len() == 2).ok_or("eq/ne 인자는 [a, b]")?;
            let (x, y) = (resolve(&a[0], ctx), resolve(&a[1], ctx));
            if x.is_null() || y.is_null() {
                return Ok(op == "ne" && !(x.is_null() && y.is_null()));
            }
            Ok(if op == "eq" { x == y } else { x != y })
        }
        "in" => {
            let a = arg.as_array().filter(|a| a.len() == 2).ok_or("in 인자는 [a, 목록]")?;
            let x = resolve(&a[0], ctx);
            let pool = resolve(&a[1], ctx);
            Ok(!x.is_null() && pool.as_array().map(|p| p.contains(&x)).unwrap_or(false))
        }
        other => Err(format!("모르는 연산자: {other}")),
    }
}

/// 저장 전에 형식만 검사한다 — 평가 중에 터지면 판정이 거부로 기울어 조용히 권한이 사라진다.
pub fn validate_condition(cond: &Value) -> Result<(), String> {
    let ctx = json!({"user": {}, "channel": {}, "env": {}});
    evaluate(cond, &ctx).map(|_| ())
}

fn matches(action: &str, patterns: &[String]) -> bool {
    patterns.iter().any(|p| {
        p == "*" || p == action || (p.ends_with(".*") && action.starts_with(&p[..p.len() - 1]))
    })
}

pub fn check(p: &Principal, action: &str, channel: Option<&Value>, channel_role: Option<&str>,
             policies: &[Value]) -> Decision {
    if !p.active {
        return Decision { allowed: false, reason: "비활성 멤버".into() };
    }
    let now = Utc::now();
    let ctx = json!({
        "user": {"uid": p.uid, "role": p.role, "attrs": p.attrs},
        "channel": channel.cloned().unwrap_or_else(|| json!({})),
        "env": {"hour_utc": now.hour(), "weekday": now.weekday().number_from_monday()},
    });
    let acts = |pol: &Value| -> Vec<String> {
        pol.get("actions").and_then(|a| a.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
            .unwrap_or_default()
    };
    for pol in policies {
        if pol.get("effect").and_then(|e| e.as_str()) == Some("deny") && matches(action, &acts(pol)) {
            let cond = pol.get("condition").cloned().unwrap_or(Value::Null);
            if evaluate(&cond, &ctx).unwrap_or(false) {
                let desc = pol.get("description").and_then(|d| d.as_str()).unwrap_or("");
                let id = pol.get("id").map(|i| i.to_string()).unwrap_or_default();
                return Decision { allowed: false, reason: format!("정책 #{id} 거부: {desc}").trim().to_string() };
            }
        }
    }
    let perms: Vec<String> = p.permissions.iter().cloned().collect();
    if DEFAULT_ALLOW.contains(&action) || matches(action, &perms) {
        return Decision { allowed: true, reason: format!("역할 {}", p.role) };
    }
    if channel_role == Some("channel_admin") && CHANNEL_ADMIN_GRANTS.contains(&action) {
        return Decision { allowed: true, reason: "채널 관리자".into() };
    }
    for pol in policies {
        if pol.get("effect").and_then(|e| e.as_str()) == Some("allow") && matches(action, &acts(pol)) {
            let cond = pol.get("condition").cloned().unwrap_or(Value::Null);
            if evaluate(&cond, &ctx).unwrap_or(false) {
                let id = pol.get("id").map(|i| i.to_string()).unwrap_or_default();
                return Decision { allowed: true, reason: format!("정책 #{id} 허용") };
            }
        }
    }
    Decision { allowed: false, reason: format!("역할 {}에 {} 권한 없음", p.role, action) }
}

pub fn ctx_channel(ch: &Map<String, Value>) -> Value {
    json!({"id": ch.get("id"), "visibility": ch.get("visibility"), "kind": ch.get("kind"),
           "attrs": ch.get("attrs").cloned().unwrap_or_else(|| json!({}))})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn eval_ops() {
        let ctx = json!({"user": {"role": "pm", "attrs": {"department": "sales"}},
                         "channel": {"visibility": "private"}, "env": {"hour_utc": 3}});
        assert!(evaluate(&json!({"eq": ["user.role", "pm"]}), &ctx).unwrap());
        assert!(evaluate(&json!({"in": ["user.attrs.department", ["sales", "cs"]]}), &ctx).unwrap());
        assert!(!evaluate(&json!({"eq": ["user.attrs.missing", "x"]}), &ctx).unwrap());
        assert!(evaluate(&json!({"ne": ["user.attrs.missing", "x"]}), &ctx).unwrap());
        assert!(evaluate(&json!({"all": [{"exists": "user.attrs.department"},
                                          {"not": {"eq": ["channel.visibility", "public"]}}]}), &ctx).unwrap());
        assert!(evaluate(&json!({}), &ctx).unwrap());
        assert!(evaluate(&Value::Null, &ctx).unwrap());
        assert!(evaluate(&json!({"bogus": 1}), &ctx).is_err());
    }

    #[test]
    fn deny_overrides_role() {
        let p = Principal { uid: "u".into(), role: "admin".into(),
                            permissions: ["*".to_string()].into_iter().collect(),
                            attrs: json!({}), active: true };
        let pols = vec![json!({"id": 1, "effect": "deny", "actions": ["message.post"], "condition": {}})];
        assert!(!check(&p, "message.post", None, None, &pols).allowed);
        assert!(check(&p, "channel.create", None, None, &pols).allowed);
    }

    #[test]
    fn channel_admin_grants() {
        let p = Principal { uid: "u".into(), role: "member".into(),
                            permissions: ["message.post".to_string()].into_iter().collect(),
                            attrs: json!({}), active: true };
        assert!(check(&p, "channel.manage_members", None, Some("channel_admin"), &[]).allowed);
        assert!(!check(&p, "channel.manage_members", None, Some("member"), &[]).allowed);
    }
}
