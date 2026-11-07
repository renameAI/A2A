// 메시징 서비스 — app/chat/service.py + router.py의 단일 소스 이식.
// 규칙은 그대로: 판정(authz) → 실행(셀 트랜잭션: 사용자 경로 RLS, 관리 경로는 판정 뒤 owner) → 커밋 뒤 전파.
// 쓰기의 원자 규칙(post_message·edit·delete·react·mark_read·add_member)은 SQL 함수가 단일 소스다.
use std::collections::{HashMap, HashSet};

use axum::extract::{Path, Query, State};
use axum::Json;
use once_cell::sync::Lazy;
use regex::Regex;
use serde::Deserialize;
use serde_json::{json, Map, Value};
use tokio_postgres::Transaction;

use crate::auth::User;
use crate::authz::{self, Principal};
use crate::db::Tenant;
use crate::errors::{EngineError, Result};
use crate::state::AppState;

pub const MAX_BODY: usize = 8000;
const PAGE_MAX: i64 = 200;
static MENTION: Lazy<Regex> = Lazy::new(|| Regex::new(r"@([A-Za-z0-9._%+\-]+(?:@[A-Za-z0-9.\-]+)?)").unwrap());
static CHANNEL_NAME: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[a-z0-9가-힣][a-z0-9가-힣_\-]{0,79}$").unwrap());
static CLIENT_MSG_ID: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z0-9_\-]{8,64}$").unwrap());

const COLS: &str = "channel_id, seq, parent_seq, author, \
    case when deleted_at is null then body else '' end as body, \
    kind, created_at, edited_at, deleted_at is not null as deleted, reply_count, last_reply_at, client_msg_id";

fn jrows(rows: &[tokio_postgres::Row]) -> Vec<Value> {
    rows.iter().map(|r| r.get::<_, Value>(0)).collect()
}

// ── 판정 도우미 ─────────────────────────────────────────────────────
pub async fn principal(tx: &Transaction<'_>, uid: &str) -> Result<(Principal, Vec<Value>)> {
    let row = tx.query_opt(
        "select m.role, m.attrs, m.status, r.permissions \
           from members m join roles r on r.name = m.role where m.user_id = $1", &[&uid]).await?;
    let row = row.ok_or_else(|| EngineError::forbidden("이 테넌트의 멤버가 아닙니다"))?;
    let role: String = row.get(0);
    let attrs: Option<Value> = row.get(1);
    let status: String = row.get(2);
    let perms: Vec<String> = row.get(3);
    let pols = tx.query(
        "select to_jsonb(p) from (select id, effect, actions, condition, description from policies) p", &[]).await?;
    Ok((Principal { uid: uid.to_string(), role, permissions: perms.into_iter().collect(),
                    attrs: attrs.unwrap_or_else(|| json!({})), active: status == "active" },
        jrows(&pols)))
}

pub async fn require(tx: &Transaction<'_>, uid: &str, action: &str,
                     channel: Option<&Value>, channel_role: Option<&str>) -> Result<Principal> {
    let (p, pols) = principal(tx, uid).await?;
    let d = authz::check(&p, action, channel, channel_role, &pols);
    if !d.allowed {
        return Err(EngineError::forbidden(d.reason));
    }
    Ok(p)
}

/// RLS를 통과한 채널만 보인다 — 없으면 404(존재 여부도 새지 않게).
pub async fn get_channel(tx: &Transaction<'_>, channel_id: i64, uid: &str)
                         -> Result<(Map<String, Value>, Option<String>)> {
    let row = tx.query_opt(
        "select to_jsonb(c) from (select id, name, topic, visibility, kind, attrs, archived_at, \
                last_seq, last_top_seq, created_by from channels where id = $1) c", &[&channel_id]).await?;
    let row = row.ok_or_else(|| EngineError::not_found("채널 없음"))?;
    let ch = row.get::<_, Value>(0).as_object().cloned()
        .ok_or_else(|| EngineError::internal("채널 행 해석 실패"))?;
    let cm = tx.query_opt("select role from channel_members where channel_id = $1 and user_id = $2",
                          &[&channel_id, &uid]).await?;
    Ok((ch, cm.map(|r| r.get::<_, String>(0))))
}

// 트랜잭션 열기 패턴 — 사용자 경로.
macro_rules! app_tx {
    ($st:expr, $t:expr, $uid:expr, $conn:ident, $tx:ident) => {
        let mut $conn = $st.reg.app_conn(&$t).await?;
        let $tx = $conn.transaction().await.map_err(EngineError::from)?;
        $st.reg.enter_app(&*$tx, &$t, $uid).await?;
    };
}
macro_rules! admin_tx {
    ($st:expr, $t:expr, $conn:ident, $tx:ident) => {
        let mut $conn = $st.reg.admin_conn(&$t).await?;
        let $tx = $conn.transaction().await.map_err(EngineError::from)?;
        $st.reg.enter_admin(&*$tx, &$t).await?;
    };
}
pub(crate) use {admin_tx, app_tx};

async fn audit(tx: &Transaction<'_>, actor: &str, action: &str, target: &str, detail: Option<Value>) -> Result<()> {
    match detail {
        Some(d) => tx.execute("insert into audit_log (actor, action, target, detail) values ($1, $2, $3, $4)",
                              &[&actor, &action, &target, &d]).await?,
        None => tx.execute("insert into audit_log (actor, action, target) values ($1, $2, $3)",
                           &[&actor, &action, &target]).await?,
    };
    Ok(())
}

// ── /me/tenants ─────────────────────────────────────────────────────
pub async fn my_tenants(State(st): State<AppState>, user: User) -> Result<Json<Value>> {
    let c = st.reg.control().await?;
    let rows = c.query(
        "select to_jsonb(q) from (select t.slug, t.name, t.plan, t.status from tenant_users tu \
           join tenants t on t.id = tu.tenant_id \
          where tu.user_id = $1 and t.status <> 'deleted' order by t.name) q", &[&user.uid]).await?;
    Ok(Json(json!({"tenants": jrows(&rows)})))
}

// ── me ──────────────────────────────────────────────────────────────
const CAN_ACTIONS: [&str; 9] = ["channel.create", "channel.create_private", "member.invite",
    "member.update", "role.manage", "policy.manage", "audit.read", "export.run", "lead.access"];

pub async fn me_doc(st: &AppState, t: &Tenant, uid: &str) -> Result<Value> {
    app_tx!(st, t, uid, conn, tx);
    let (p, pols) = principal(&tx, uid).await?;
    let m = tx.query_opt("select email, display_name, avatar_hue from members where user_id = $1", &[&uid]).await?
        .ok_or_else(|| EngineError::forbidden("이 테넌트의 멤버가 아닙니다"))?;
    tx.commit().await.map_err(EngineError::from)?;
    let can: Map<String, Value> = CAN_ACTIONS.iter()
        .map(|a| ((*a).to_string(), json!(authz::check(&p, a, None, None, &pols).allowed)))
        .collect();
    let hue: Option<i16> = m.get(2);
    Ok(json!({
        "tenant": {"slug": t.slug, "name": t.name, "plan": t.plan},
        "user_id": uid, "email": m.get::<_, String>(0), "display_name": m.get::<_, String>(1),
        "avatar_hue": hue, "role": p.role, "attrs": p.attrs, "can": can,
    }))
}

pub async fn me(State(st): State<AppState>, Path(slug): Path<String>, user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    Ok(Json(me_doc(&st, &t, &user.uid).await?))
}

pub async fn update_me(State(st): State<AppState>, Path(slug): Path<String>, user: User,
                       Json(body): Json<Value>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let obj = body.as_object().cloned().unwrap_or_default();
    let mut changes: Vec<(&str, Value)> = Vec::new();
    if let Some(v) = obj.get("display_name") {
        if !v.is_null() {
            let name = v.as_str().map(|s| s.split_whitespace().collect::<Vec<_>>().join(" "))
                .unwrap_or_default();
            if name.is_empty() || name.chars().count() > 40 {
                return Err(EngineError::invalid("표시 이름은 1~40자"));
            }
            changes.push(("display_name", json!(name)));
        }
    }
    if let Some(v) = obj.get("avatar_hue") {
        if v.is_null() {
            changes.push(("avatar_hue", Value::Null));
        } else {
            let hue = v.as_i64().filter(|h| (0..=359).contains(h) && !v.is_boolean());
            match hue {
                Some(h) => changes.push(("avatar_hue", json!(h))),
                None => return Err(EngineError::invalid("아바타 색은 0~359")),
            }
        }
    }
    if changes.is_empty() {
        return Err(EngineError::invalid("바꿀 항목이 없습니다"));
    }
    {
        app_tx!(st, t, &user.uid, conn, tx);
        let (p, _) = principal(&tx, &user.uid).await?;    // 멤버가 아니면 여기서 막힌다
        tx.commit().await.map_err(EngineError::from)?;
        if !p.active {
            return Err(EngineError::forbidden("비활성화된 멤버입니다"));
        }
    }
    {
        admin_tx!(st, t, conn, tx);
        for (col, val) in &changes {
            // 열 이름은 위에서 정한 두 가지뿐
            match (*col, val) {
                ("display_name", v) => {
                    tx.execute("update members set display_name = $1 where user_id = $2",
                               &[&v.as_str().unwrap_or(""), &user.uid]).await?;
                }
                ("avatar_hue", Value::Null) => {
                    tx.execute("update members set avatar_hue = null where user_id = $1", &[&user.uid]).await?;
                }
                ("avatar_hue", v) => {
                    let h = v.as_i64().unwrap() as i16;
                    tx.execute("update members set avatar_hue = $1 where user_id = $2", &[&h, &user.uid]).await?;
                }
                _ => {}
            }
        }
        let detail: Map<String, Value> = changes.iter().map(|(k, v)| (k.to_string(), v.clone())).collect();
        audit(&tx, &user.uid, "profile.update", &user.uid, Some(Value::Object(detail))).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    Ok(Json(me_doc(&st, &t, &user.uid).await?))
}

// ── 채널 ───────────────────────────────────────────────────────────
pub async fn list_channels_doc(st: &AppState, t: &Tenant, uid: &str) -> Result<Vec<Value>> {
    app_tx!(st, t, uid, conn, tx);
    require(&tx, uid, "channel.read", None, None).await?;
    let rows = tx.query(
        "select to_jsonb(q) from ( \
           select c.id, c.name, c.topic, c.visibility, c.kind, c.archived_at, \
                  c.last_seq, c.last_top_seq, c.last_message_at, \
                  cm.user_id is not null as joined, coalesce(cm.last_read_seq, 0) as last_read_seq, \
                  cm.role as channel_role, coalesce(cm.muted, false) as muted, \
                  (select count(*) from mentions mn \
                    where mn.user_id = $1 and mn.channel_id = c.id \
                      and mn.seq > coalesce(cm.last_read_seq, 0)) as mentions \
             from channels c \
             left join channel_members cm on cm.channel_id = c.id and cm.user_id = $1 \
            where c.archived_at is null \
            order by c.kind = 'announcements' desc, c.name) q", &[&uid]).await?;
    tx.commit().await.map_err(EngineError::from)?;
    Ok(jrows(&rows).into_iter().map(|mut r| {
        let unread = r.get("last_top_seq").and_then(|v| v.as_i64()).unwrap_or(0)
            > r.get("last_read_seq").and_then(|v| v.as_i64()).unwrap_or(0);
        r["unread"] = json!(unread);
        r
    }).collect())
}

pub async fn channels(State(st): State<AppState>, Path(slug): Path<String>, user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    Ok(Json(json!({"channels": list_channels_doc(&st, &t, &user.uid).await?})))
}

/// 구독 권한 확인용 가벼운 질의 — RLS를 통과한 채널 id만 (list_channels는 멘션 집계가 무겁다).
pub async fn visible_channel_ids(st: &AppState, t: &Tenant, uid: &str, ids: Option<&[i64]>)
                                 -> Result<HashSet<i64>> {
    app_tx!(st, t, uid, conn, tx);
    let (p, _) = principal(&tx, uid).await?;
    if !p.active {
        tx.commit().await.map_err(EngineError::from)?;
        return Ok(HashSet::new());
    }
    let rows = match ids {
        Some(list) if !list.is_empty() => {
            let v: Vec<i64> = list.to_vec();
            tx.query("select id from channels where archived_at is null and id = any($1)", &[&v]).await?
        }
        _ => tx.query("select id from channels where archived_at is null", &[]).await?,
    };
    tx.commit().await.map_err(EngineError::from)?;
    Ok(rows.iter().map(|r| r.get::<_, i64>(0)).collect())
}

#[derive(Deserialize)]
pub struct ChannelIn {
    pub name: String,
    #[serde(default = "default_public")] pub visibility: String,
    #[serde(default)] pub topic: String,
    #[serde(default)] pub attrs: Map<String, Value>,
}
fn default_public() -> String { "public".into() }

pub async fn create_channel(State(st): State<AppState>, Path(slug): Path<String>, user: User,
                            Json(body): Json<ChannelIn>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let name = body.name.trim().to_lowercase();
    if !CHANNEL_NAME.is_match(&name) {
        return Err(EngineError::invalid("채널 이름은 소문자·숫자·한글·-·_ 80자 이내"));
    }
    if body.topic.chars().count() > 250 {
        return Err(EngineError::invalid("topic은 250자 이내"));
    }
    if body.visibility != "public" && body.visibility != "private" {
        return Err(EngineError::invalid("visibility는 public|private"));
    }
    let action = if body.visibility == "private" { "channel.create_private" } else { "channel.create" };
    {
        app_tx!(st, t, &user.uid, conn, tx);
        let ctx = json!({"visibility": body.visibility, "attrs": body.attrs});
        require(&tx, &user.uid, action, Some(&ctx), None).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    admin_tx!(st, t, conn, tx);
    let attrs = Value::Object(body.attrs.clone());
    let row = tx.query_one(
        "with c as (insert into channels (name, topic, visibility, attrs, created_by) \
          values ($1, $2, $3, $4, $5) returning id, name, topic, visibility, kind, last_seq) \
         select to_jsonb(c) from c",
        &[&name, &body.topic, &body.visibility, &attrs, &user.uid]).await;
    let row = match row {
        Ok(r) => r,
        Err(e) => {
            if e.as_db_error().map(|d| d.code().code()) == Some("23505") {
                return Err(EngineError::new(409, "conflict", format!("이미 있는 채널: {name}")));
            }
            return Err(e.into());
        }
    };
    let ch: Value = row.get(0);
    let cid = ch.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
    tx.execute("insert into channel_members (channel_id, user_id, role) values ($1, $2, 'channel_admin')",
               &[&cid, &user.uid]).await?;
    audit(&tx, &user.uid, "channel.create", &format!("{cid}:{name}:{}", body.visibility), None).await?;
    tx.commit().await.map_err(EngineError::from)?;
    Ok(Json(ch))
}

#[derive(Deserialize)]
pub struct MemberIn {
    pub user_id: String,
    #[serde(default = "default_member")] pub role: String,
}
fn default_member() -> String { "member".into() }

pub async fn add_channel_member(State(st): State<AppState>, Path((slug, cid)): Path<(String, i64)>,
                                user: User, Json(body): Json<MemberIn>) -> Result<Json<Value>> {
    set_channel_member(&st, &slug, &user, cid, &body.user_id, true, &body.role).await?;
    Ok(Json(json!({"ok": true})))
}
pub async fn remove_channel_member(State(st): State<AppState>, Path((slug, cid, target)): Path<(String, i64, String)>,
                                   user: User) -> Result<Json<Value>> {
    set_channel_member(&st, &slug, &user, cid, &target, false, "member").await?;
    Ok(Json(json!({"ok": true})))
}

async fn set_channel_member(st: &AppState, slug: &str, user: &User, channel_id: i64,
                            target: &str, on: bool, role: &str) -> Result<()> {
    let t = st.reg.tenant(slug).await?;
    {
        app_tx!(st, t, &user.uid, conn, tx);
        let (ch, my_role) = get_channel(&tx, channel_id, &user.uid).await?;
        let self_join = target == user.uid && on
            && ch.get("visibility").and_then(|v| v.as_str()) == Some("public");
        let self_leave = target == user.uid && !on;
        if !(self_join || self_leave) {
            let ctx = authz::ctx_channel(&ch);
            require(&tx, &user.uid, "channel.manage_members", Some(&ctx), my_role.as_deref()).await?;
        }
        tx.commit().await.map_err(EngineError::from)?;
    }
    {
        admin_tx!(st, t, conn, tx);
        if on {
            let ok = tx.query_opt("select 1 from members where user_id = $1 and status = 'active'", &[&target]).await?;
            if ok.is_none() {
                return Err(EngineError::not_found("그런 멤버가 없습니다"));
            }
            tx.execute("insert into channel_members (channel_id, user_id, role) values ($1, $2, $3) \
                        on conflict (channel_id, user_id) do update set role = excluded.role",
                       &[&channel_id, &target, &role]).await?;
        } else {
            tx.execute("delete from channel_members where channel_id = $1 and user_id = $2",
                       &[&channel_id, &target]).await?;
        }
        audit(&tx, &user.uid, if on { "channel.member_add" } else { "channel.member_remove" },
              &format!("{channel_id}:{target}"), None).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    st.events.publish(&t, channel_id, json!({"type": "membership", "user": target, "on": on}));
    Ok(())
}

pub async fn archive_channel(State(st): State<AppState>, Path((slug, cid)): Path<(String, i64)>,
                             user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    {
        app_tx!(st, t, &user.uid, conn, tx);
        let (ch, my_role) = get_channel(&tx, cid, &user.uid).await?;
        if ch.get("kind").and_then(|v| v.as_str()) != Some("channel") {
            return Err(EngineError::new(409, "invalid_state", "기본 채널은 보관할 수 없습니다"));
        }
        let ctx = authz::ctx_channel(&ch);
        require(&tx, &user.uid, "channel.archive", Some(&ctx), my_role.as_deref()).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    admin_tx!(st, t, conn, tx);
    tx.execute("update channels set archived_at = now() where id = $1", &[&cid]).await?;
    audit(&tx, &user.uid, "channel.archive", &cid.to_string(), None).await?;
    tx.commit().await.map_err(EngineError::from)?;
    Ok(Json(json!({"ok": true})))
}

// ── 메시지 ─────────────────────────────────────────────────────────
async fn resolve_mentions(tx: &Transaction<'_>, body: &str) -> Result<Vec<String>> {
    let toks: HashSet<String> = MENTION.captures_iter(body)
        .map(|c| c[1].to_lowercase()).collect();
    if toks.is_empty() {
        return Ok(Vec::new());
    }
    let rows = tx.query("select user_id, email from members where status = 'active'", &[]).await?;
    let mut out = Vec::new();
    for r in rows {
        let uid: String = r.get(0);
        let email: String = r.get::<_, String>(1).to_lowercase();
        let local = email.split('@').next().unwrap_or("").to_string();
        if toks.contains(&uid.to_lowercase()) || toks.contains(&email) || toks.contains(&local) {
            out.push(uid);
        }
    }
    Ok(out)
}

#[derive(Deserialize)]
pub struct PostIn {
    pub body: String,
    #[serde(default)] pub parent_seq: Option<i64>,
    #[serde(default)] pub client_msg_id: Option<String>,
    #[serde(default = "default_text")] pub kind: String,
}
fn default_text() -> String { "text".into() }

pub async fn post(State(st): State<AppState>, Path((slug, cid)): Path<(String, i64)>, user: User,
                  Json(body): Json<PostIn>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let text = body.body.trim().to_string();
    if text.is_empty() {
        return Err(EngineError::invalid("빈 메시지"));
    }
    if text.chars().count() > MAX_BODY {
        return Err(EngineError::invalid(format!("메시지는 {MAX_BODY}자 이내")));
    }
    if body.kind != "text" && body.kind != "activity" {
        return Err(EngineError::invalid("kind는 text|activity"));
    }
    if let Some(cmi) = &body.client_msg_id {
        if !CLIENT_MSG_ID.is_match(cmi) {
            return Err(EngineError::invalid("client_msg_id 형식 오류"));
        }
    }
    st.limiter.check_post(&t.id, &user.uid).await?;
    let (seq, created_at, duplicate, mentions) = {
        app_tx!(st, t, &user.uid, conn, tx);
        let (ch, my_role) = get_channel(&tx, cid, &user.uid).await?;
        if !ch.get("archived_at").map(|v| v.is_null()).unwrap_or(true) {
            return Err(EngineError::new(409, "invalid_state", "보관된 채널입니다"));
        }
        let ctx = authz::ctx_channel(&ch);
        if ch.get("kind").and_then(|v| v.as_str()) == Some("announcements") {
            require(&tx, &user.uid, "channel.manage_members", Some(&ctx), my_role.as_deref()).await?;
        }
        require(&tx, &user.uid, "message.post", Some(&ctx), my_role.as_deref()).await?;
        let mentions = resolve_mentions(&tx, &text).await?;
        let row = tx.query_one("select seq, created_at, duplicate from post_message($1, $2, $3, $4, $5, $6, $7)",
                               &[&cid, &user.uid, &text, &body.kind, &body.parent_seq,
                                 &body.client_msg_id, &mentions]).await;
        let row = match row {
            Ok(r) => r,
            Err(e) => {
                if let Some(db) = e.as_db_error() {
                    if db.code().code() == "P0002" {
                        return Err(EngineError::not_found(
                            db.message().split('\n').next().unwrap_or("없음").to_string()));
                    }
                    if db.code().code() == "42501" {
                        return Err(EngineError::forbidden("이 채널에 쓸 수 없습니다"));
                    }
                }
                return Err(e.into());
            }
        };
        let seq: i64 = row.get(0);
        let at: chrono::DateTime<chrono::Utc> = row.get(1);
        let dup: bool = row.get(2);
        tx.commit().await.map_err(EngineError::from)?;
        (seq, at, dup, mentions)
    };
    if !duplicate {
        st.events.publish(&t, cid, json!({"type": "message", "seq": seq, "parent_seq": body.parent_seq}));
    }
    Ok(Json(json!({
        "channel_id": cid, "seq": seq, "parent_seq": body.parent_seq, "author": user.uid,
        "body": text, "kind": body.kind,
        "created_at": created_at.to_rfc3339_opts(chrono::SecondsFormat::Micros, false),
        "mentions": mentions, "duplicate": duplicate, "client_msg_id": body.client_msg_id,
        "_acked": chrono::Utc::now().timestamp_millis() as f64 / 1000.0,
    })))
}

#[derive(Deserialize)]
pub struct ListQ {
    pub before: Option<i64>,
    pub after: Option<i64>,
    pub thread: Option<i64>,
    pub limit: Option<i64>,
}

async fn reactions_for(tx: &Transaction<'_>, uid: &str, channel_id: i64, seqs: &[i64])
                       -> Result<HashMap<i64, Vec<Value>>> {
    let mut out: HashMap<i64, Vec<Value>> = HashMap::new();
    if seqs.is_empty() {
        return Ok(out);
    }
    let v: Vec<i64> = seqs.to_vec();
    let rows = tx.query(
        "select seq, emoji, count(*) as n, bool_or(user_id = $1) as mine \
           from reactions where channel_id = $2 and seq = any($3) \
          group by seq, emoji order by seq, emoji", &[&uid, &channel_id, &v]).await?;
    for r in rows {
        let seq: i64 = r.get(0);
        out.entry(seq).or_default().push(json!({
            "emoji": r.get::<_, String>(1), "count": r.get::<_, i64>(2), "mine": r.get::<_, bool>(3)}));
    }
    Ok(out)
}

pub async fn messages(State(st): State<AppState>, Path((slug, cid)): Path<(String, i64)>,
                      Query(q): Query<ListQ>, user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let limit = q.limit.unwrap_or(50);
    if !(1..=PAGE_MAX).contains(&limit) {
        return Err(EngineError::invalid("limit은 1~200"));
    }
    let (ch, rows, reacts, root, more_hot) = {
        app_tx!(st, t, &user.uid, conn, tx);
        let (ch, my_role) = get_channel(&tx, cid, &user.uid).await?;
        let ctx = authz::ctx_channel(&ch);
        require(&tx, &user.uid, "channel.read", Some(&ctx), my_role.as_deref()).await?;
        let mut where_parts: Vec<String> = vec!["channel_id = $1".into()];
        let mut params: Vec<Box<dyn tokio_postgres::types::ToSql + Sync + Send>> = vec![Box::new(cid)];
        match q.thread {
            Some(th) => { params.push(Box::new(th)); where_parts.push(format!("parent_seq = ${}", params.len())); }
            None => where_parts.push("parent_seq is null".into()),
        }
        let order;
        if let Some(a) = q.after {
            params.push(Box::new(a));
            where_parts.push(format!("seq > ${}", params.len()));
            order = "asc";
        } else {
            if let Some(b) = q.before {
                params.push(Box::new(b));
                where_parts.push(format!("seq < ${}", params.len()));
            }
            order = "desc";
        }
        params.push(Box::new(limit + 1));
        let sql = format!("select to_jsonb(m) from (select {COLS} from messages where {} \
                           order by seq {order} limit ${}) m",
                          where_parts.join(" and "), params.len());
        let refs: Vec<&(dyn tokio_postgres::types::ToSql + Sync)> =
            params.iter().map(|b| b.as_ref() as &(dyn tokio_postgres::types::ToSql + Sync)).collect();
        let raw = tx.query(&sql, &refs).await?;
        let mut rows = jrows(&raw);
        let more = rows.len() as i64 > limit;
        rows.truncate(limit as usize);
        if order == "desc" {
            rows.reverse();
        }
        let seqs: Vec<i64> = rows.iter().filter_map(|r| r.get("seq").and_then(|v| v.as_i64())).collect();
        let reacts = reactions_for(&tx, &user.uid, cid, &seqs).await?;
        let root = match q.thread {
            Some(th) => {
                let r = tx.query_opt(
                    &format!("select to_jsonb(m) from (select {COLS} from messages \
                              where channel_id = $1 and seq = $2 and parent_seq is null) m"),
                    &[&cid, &th]).await?;
                let r = r.ok_or_else(|| EngineError::not_found("스레드 루트 없음"))?;
                let root: Value = r.get(0);
                let rr = reactions_for(&tx, &user.uid, cid, &[th]).await?;
                let mut root = root;
                root["archived"] = json!(false);
                root["reactions"] = json!(rr.get(&th).cloned().unwrap_or_default());
                Some(root)
            }
            None => None,
        };
        tx.commit().await.map_err(EngineError::from)?;
        (ch, rows, reacts, root, more)
    };
    // 핫 데이터가 바닥나면 아카이브로 이어 읽는다(과거 방향일 때만). 권한은 위에서 이미 확인했다.
    let mut more = more_hot;
    let mut archived: Vec<Value> = Vec::new();
    if q.after.is_none() && !more {
        let floor = rows.first().and_then(|r| r.get("seq")).and_then(|v| v.as_i64()).or(q.before);
        let want = limit as usize - rows.len();
        if want > 0 {
            let got = crate::archive::read_archived(&st.reg, &t, cid, floor, want + 1, q.thread).await?;
            more = got.len() > want;
            archived = got.into_iter().rev().take(want).collect::<Vec<_>>();
            archived.reverse();
        }
    }
    let msgs: Vec<Value> = archived.into_iter().chain(rows.into_iter().map(|mut r| {
        let seq = r.get("seq").and_then(|v| v.as_i64()).unwrap_or(0);
        r["reactions"] = json!(reacts.get(&seq).cloned().unwrap_or_default());
        r["archived"] = json!(false);
        r
    })).collect();
    Ok(Json(json!({
        "channel": {"id": ch.get("id"), "name": ch.get("name"), "last_seq": ch.get("last_seq")},
        "messages": msgs, "has_more": more, "root": root,
    })))
}

#[derive(Deserialize)]
pub struct EditIn { pub body: String }

pub async fn edit(State(st): State<AppState>, Path((slug, cid, seq)): Path<(String, i64, i64)>,
                  user: User, Json(body): Json<EditIn>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let text = body.body.trim().to_string();
    if text.is_empty() || text.chars().count() > MAX_BODY {
        return Err(EngineError::invalid("본문 길이 오류"));
    }
    let ok = {
        app_tx!(st, t, &user.uid, conn, tx);
        let (ch, my_role) = get_channel(&tx, cid, &user.uid).await?;
        let ctx = authz::ctx_channel(&ch);
        require(&tx, &user.uid, "message.edit_own", Some(&ctx), my_role.as_deref()).await?;
        let ok: bool = tx.query_one("select edit_message($1, $2, $3, $4) as ok",
                                    &[&cid, &seq, &user.uid, &text]).await?.get(0);
        tx.commit().await.map_err(EngineError::from)?;
        ok
    };
    if !ok {
        return Err(EngineError::not_found("수정할 수 있는 내 메시지가 아닙니다"));
    }
    st.events.publish(&t, cid, json!({"type": "edit", "seq": seq}));
    Ok(Json(json!({"ok": true})))
}

pub async fn delete(State(st): State<AppState>, Path((slug, cid, seq)): Path<(String, i64, i64)>,
                    user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let ok = {
        app_tx!(st, t, &user.uid, conn, tx);
        let (ch, my_role) = get_channel(&tx, cid, &user.uid).await?;
        let author = tx.query_opt("select author from messages where channel_id = $1 and seq = $2",
                                  &[&cid, &seq]).await?;
        let author: String = author.ok_or_else(|| EngineError::not_found("메시지 없음"))?.get(0);
        let mine = author == user.uid;
        let ctx = authz::ctx_channel(&ch);
        require(&tx, &user.uid, if mine { "message.delete_own" } else { "message.delete_any" },
                Some(&ctx), my_role.as_deref()).await?;
        let ok: bool = tx.query_one("select delete_message($1, $2, $3, $4) as ok",
                                    &[&cid, &seq, &user.uid, &(!mine)]).await?.get(0);
        tx.commit().await.map_err(EngineError::from)?;
        ok
    };
    if !ok {
        return Err(EngineError::not_found("이미 삭제됐거나 없는 메시지"));
    }
    st.events.publish(&t, cid, json!({"type": "delete", "seq": seq}));
    Ok(Json(json!({"ok": true})))
}

#[derive(Deserialize)]
pub struct ReactIn {
    pub emoji: String,
    #[serde(default = "default_true")] pub on: bool,
}
fn default_true() -> bool { true }

pub async fn react(State(st): State<AppState>, Path((slug, cid, seq)): Path<(String, i64, i64)>,
                   user: User, Json(body): Json<ReactIn>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let n = body.emoji.chars().count();
    if n < 1 || n > 32 {
        return Err(EngineError::invalid("emoji는 1~32자"));
    }
    {
        app_tx!(st, t, &user.uid, conn, tx);
        let (ch, my_role) = get_channel(&tx, cid, &user.uid).await?;
        let ctx = authz::ctx_channel(&ch);
        require(&tx, &user.uid, "reaction.add", Some(&ctx), my_role.as_deref()).await?;
        tx.execute("select react($1, $2, $3, $4, $5)", &[&cid, &seq, &user.uid, &body.emoji, &body.on]).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    st.events.publish(&t, cid, json!({"type": "reaction", "seq": seq}));
    Ok(Json(json!({"ok": true})))
}

#[derive(Deserialize)]
pub struct ReadIn { pub seq: i64 }

pub async fn mark_read(State(st): State<AppState>, Path((slug, cid)): Path<(String, i64)>,
                       user: User, Json(body): Json<ReadIn>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    if body.seq < 0 {
        return Err(EngineError::invalid("seq는 0 이상"));
    }
    app_tx!(st, t, &user.uid, conn, tx);
    get_channel(&tx, cid, &user.uid).await?;
    let v: Option<i64> = tx.query_one("select mark_read($1, $2, $3) as v",
                                      &[&cid, &user.uid, &body.seq]).await?.get(0);
    tx.commit().await.map_err(EngineError::from)?;
    Ok(Json(json!({"last_read_seq": v.unwrap_or(0)})))
}

#[derive(Deserialize)]
pub struct SearchQ { pub q: String }

pub async fn search(State(st): State<AppState>, Path(slug): Path<String>,
                    Query(sq): Query<SearchQ>, user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let q = sq.q.trim().to_string();
    if q.chars().count() < 2 {
        return Err(EngineError::invalid("검색어는 2자 이상"));
    }
    app_tx!(st, t, &user.uid, conn, tx);
    require(&tx, &user.uid, "channel.read", None, None).await?;
    let lim: i64 = 30;
    let rows = tx.query(
        &format!("select to_jsonb(m) from (select {COLS} from messages \
                   where deleted_at is null \
                     and to_tsvector('simple', body) @@ plainto_tsquery('simple', $1) \
                   order by created_at desc limit $2) m"), &[&q, &lim]).await?;
    tx.commit().await.map_err(EngineError::from)?;
    Ok(Json(json!({"results": jrows(&rows)})))
}
