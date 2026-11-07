// 멤버·역할·정책 관리 — app/chat/service.py 관리 동작의 단일 소스 이식.
// 판정은 사용자 경로(RLS)에서, 실행은 판정 뒤 owner 경로에서 — Python과 같은 순서.
use axum::extract::{Path, State};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::auth::User;
use crate::authz;
use crate::errors::{EngineError, Result};
use crate::service::{admin_tx, app_tx, principal, require};
use crate::state::AppState;

async fn audit(tx: &tokio_postgres::Transaction<'_>, actor: &str, action: &str, target: &str,
               detail: Option<Value>) -> Result<()> {
    match detail {
        Some(d) => tx.execute("insert into audit_log (actor, action, target, detail) values ($1, $2, $3, $4)",
                              &[&actor, &action, &target, &d]).await?,
        None => tx.execute("insert into audit_log (actor, action, target) values ($1, $2, $3)",
                           &[&actor, &action, &target]).await?,
    };
    Ok(())
}

pub async fn members_doc(st: &AppState, t: &crate::db::Tenant, uid: &str) -> Result<Vec<Value>> {
    app_tx!(st, t, uid, conn, tx);
    require(&tx, uid, "channel.read", None, None).await?;
    // 활성 멤버 — RLS가 게스트에게는 자기 자신만 보여 준다.
    let rows = tx.query(
        "select to_jsonb(m) from (select user_id, email, display_name, avatar_hue, role, status, joined_at \
           from members where status = 'active' order by display_name) m", &[]).await?;
    tx.commit().await.map_err(EngineError::from)?;
    Ok(rows.iter().map(|r| r.get::<_, Value>(0)).collect())
}

pub async fn list_members(State(st): State<AppState>, Path(slug): Path<String>, user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    Ok(Json(json!({"members": members_doc(&st, &t, &user.uid).await?})))
}

pub async fn list_roles(State(st): State<AppState>, Path(slug): Path<String>, user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    app_tx!(st, t, &user.uid, conn, tx);
    require(&tx, &user.uid, "channel.read", None, None).await?;
    let roles = tx.query("select to_jsonb(r) from (select name, permissions, builtin, description \
                           from roles order by builtin desc, name) r", &[]).await?;
    let pols = tx.query("select to_jsonb(p) from (select id, effect, actions, condition, description, \
                          created_by, created_at from policies order by id) p", &[]).await?;
    tx.commit().await.map_err(EngineError::from)?;
    Ok(Json(json!({
        "roles": roles.iter().map(|r| r.get::<_, Value>(0)).collect::<Vec<_>>(),
        "policies": pols.iter().map(|r| r.get::<_, Value>(0)).collect::<Vec<_>>(),
        "catalog": authz::permissions_catalog(),
    })))
}

#[derive(Deserialize)]
pub struct InviteIn {
    pub user_id: String,
    pub email: String,
    #[serde(default = "default_member")] pub role: String,
    #[serde(default)] pub attrs: Map<String, Value>,
}
fn default_member() -> String { "member".into() }

pub async fn invite(State(st): State<AppState>, Path(slug): Path<String>, user: User,
                    Json(body): Json<InviteIn>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    if body.user_id.is_empty() || body.user_id.chars().count() > 128 {
        return Err(EngineError::invalid("user_id는 1~128자"));
    }
    if body.email.chars().count() < 3 || body.email.chars().count() > 200 {
        return Err(EngineError::invalid("email은 3~200자"));
    }
    {
        app_tx!(st, t, &user.uid, conn, tx);
        require(&tx, &user.uid, "member.invite", None, None).await?;
        if body.role == "owner" {
            let (p, _) = principal(&tx, &user.uid).await?;
            if p.role != "owner" {
                return Err(EngineError::forbidden("소유자만 소유자를 지정할 수 있습니다"));
            }
        }
        if tx.query_opt("select 1 from roles where name = $1", &[&body.role]).await?.is_none() {
            return Err(EngineError::invalid(format!("없는 역할: {}", body.role)));
        }
        tx.commit().await.map_err(EngineError::from)?;
    }
    // 멤버 upsert + 기본 채널 자동 가입 규칙은 SQL 함수 add_member(tenant_v5.sql)가 단일 소스다.
    {
        admin_tx!(st, t, conn, tx);
        let attrs = Value::Object(body.attrs.clone());
        tx.execute("select add_member($1, $2, $3, $4)", &[&body.user_id, &body.email, &body.role, &attrs]).await?;
        audit(&tx, &user.uid, "member.invite", &body.user_id, Some(json!({"role": body.role}))).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    let c = st.reg.control().await?;
    c.execute("insert into tenant_users (tenant_id, user_id, email) values ($1::text::uuid, $2, $3) \
               on conflict (tenant_id, user_id) do update set email = excluded.email",
              &[&t.id, &body.user_id, &body.email]).await?;
    Ok(Json(json!({"ok": true})))
}

#[derive(Deserialize)]
pub struct MemberPatch {
    #[serde(default)] pub role: Option<String>,
    #[serde(default)] pub attrs: Option<Map<String, Value>>,
    #[serde(default)] pub active: Option<bool>,
}

pub async fn update_member(State(st): State<AppState>, Path((slug, uid)): Path<(String, String)>,
                           user: User, Json(body): Json<MemberPatch>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    {
        app_tx!(st, t, &user.uid, conn, tx);
        let action = if body.active == Some(false) { "member.remove" } else { "member.update" };
        require(&tx, &user.uid, action, None, None).await?;
        let target = tx.query_opt("select role from members where user_id = $1", &[&uid]).await?;
        let target_role: String = target.ok_or_else(|| EngineError::not_found("멤버 없음"))?.get(0);
        let (me, _) = principal(&tx, &user.uid).await?;
        if (target_role == "owner" || body.role.as_deref() == Some("owner")) && me.role != "owner" {
            return Err(EngineError::forbidden("소유자 변경은 소유자만"));
        }
        tx.commit().await.map_err(EngineError::from)?;
    }
    {
        admin_tx!(st, t, conn, tx);
        if let Some(role) = &body.role {
            tx.execute("update members set role = $1 where user_id = $2", &[role, &uid]).await?;
        }
        if let Some(attrs) = &body.attrs {
            let v = Value::Object(attrs.clone());
            tx.execute("update members set attrs = $1 where user_id = $2", &[&v, &uid]).await?;
        }
        if let Some(active) = body.active {
            let status = if active { "active" } else { "deactivated" };
            tx.execute("update members set status = $1 where user_id = $2", &[&status, &uid]).await?;
            if !active {
                tx.execute("delete from channel_members where user_id = $1", &[&uid]).await?;
            }
        }
        // 소유자가 0명이 되면 테넌트를 아무도 관리할 수 없다 — 같은 트랜잭션에서 검사하고 어기면 되돌린다.
        let n: i64 = tx.query_one(
            "select count(*) as n from members where role = 'owner' and status = 'active'", &[]).await?.get(0);
        if n == 0 {
            return Err(EngineError::new(409, "invalid_state", "마지막 소유자를 강등·비활성화할 수 없습니다"));
        }
        audit(&tx, &user.uid, "member.update", &uid,
              Some(json!({"role": body.role, "attrs": body.attrs, "active": body.active}))).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    if body.active == Some(false) {
        let c = st.reg.control().await?;
        c.execute("delete from tenant_users where tenant_id = $1::text::uuid and user_id = $2", &[&t.id, &uid]).await?;
    }
    Ok(Json(json!({"ok": true})))
}

#[derive(Deserialize)]
pub struct RoleIn { pub permissions: Vec<String> }

pub async fn put_role(State(st): State<AppState>, Path((slug, name)): Path<(String, String)>,
                      user: User, Json(body): Json<RoleIn>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let unknown: Vec<&String> = body.permissions.iter().filter(|p| !authz::known_permission(p)).collect();
    if !unknown.is_empty() {
        return Err(EngineError::invalid(format!("모르는 권한: {unknown:?}")));
    }
    {
        app_tx!(st, t, &user.uid, conn, tx);
        require(&tx, &user.uid, "role.manage", None, None).await?;
        if body.permissions.iter().any(|p| p == "*") {
            let (me, _) = principal(&tx, &user.uid).await?;
            if me.role != "owner" {
                return Err(EngineError::forbidden("전권(*) 역할은 소유자만 만들 수 있습니다"));
            }
        }
        tx.commit().await.map_err(EngineError::from)?;
    }
    if name == "owner" {
        return Err(EngineError::new(409, "invalid_state", "owner 역할은 바꿀 수 없습니다"));
    }
    admin_tx!(st, t, conn, tx);
    tx.execute("insert into roles (name, permissions) values ($1, $2) \
                on conflict (name) do update set permissions = excluded.permissions",
               &[&name, &body.permissions]).await?;
    audit(&tx, &user.uid, "role.upsert", &name, Some(json!({"permissions": body.permissions}))).await?;
    tx.commit().await.map_err(EngineError::from)?;
    Ok(Json(json!({"ok": true})))
}

#[derive(Deserialize)]
pub struct PolicyIn {
    pub effect: String,
    pub actions: Vec<String>,
    #[serde(default)] pub condition: Value,
    #[serde(default)] pub description: String,
}

pub async fn add_policy(State(st): State<AppState>, Path(slug): Path<String>, user: User,
                        Json(body): Json<PolicyIn>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    if (body.effect != "allow" && body.effect != "deny") || body.actions.is_empty() {
        return Err(EngineError::invalid("effect는 allow|deny, actions는 1개 이상"));
    }
    let cond = if body.condition.is_null() { json!({}) } else { body.condition.clone() };
    if let Err(e) = authz::validate_condition(&cond) {
        return Err(EngineError::invalid(format!("조건 형식 오류: {e}")));
    }
    {
        app_tx!(st, t, &user.uid, conn, tx);
        require(&tx, &user.uid, "policy.manage", None, None).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    admin_tx!(st, t, conn, tx);
    let pid: i64 = tx.query_one(
        "insert into policies (effect, actions, condition, description, created_by) \
         values ($1, $2, $3, $4, $5) returning id",
        &[&body.effect, &body.actions, &cond, &body.description, &user.uid]).await?.get(0);
    audit(&tx, &user.uid, "policy.add", &pid.to_string(),
          Some(json!({"effect": body.effect, "actions": body.actions, "condition": cond}))).await?;
    tx.commit().await.map_err(EngineError::from)?;
    Ok(Json(json!({"id": pid})))
}

pub async fn remove_policy(State(st): State<AppState>, Path((slug, pid)): Path<(String, i64)>,
                           user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    {
        app_tx!(st, t, &user.uid, conn, tx);
        require(&tx, &user.uid, "policy.manage", None, None).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    admin_tx!(st, t, conn, tx);
    tx.execute("delete from policies where id = $1", &[&pid]).await?;
    audit(&tx, &user.uid, "policy.remove", &pid.to_string(), None).await?;
    tx.commit().await.map_err(EngineError::from)?;
    Ok(Json(json!({"ok": true})))
}

#[derive(Deserialize)]
pub struct LogsQ { pub limit: Option<i64> }

pub async fn logs(State(st): State<AppState>, Path((slug, which)): Path<(String, String)>,
                  axum::extract::Query(q): axum::extract::Query<LogsQ>, user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    if which != "audit_log" && which != "access_log" {
        return Err(EngineError::invalid("audit_log|access_log"));
    }
    app_tx!(st, t, &user.uid, conn, tx);
    require(&tx, &user.uid, "audit.read", None, None).await?;
    let lim = q.limit.unwrap_or(100).clamp(1, 500);
    let rows = tx.query(&format!("select to_jsonb(l) from (select * from {which} order by id desc limit $1) l"),
                        &[&lim]).await?;
    tx.commit().await.map_err(EngineError::from)?;
    Ok(Json(json!({"rows": rows.iter().map(|r| r.get::<_, Value>(0)).collect::<Vec<_>>()})))
}
