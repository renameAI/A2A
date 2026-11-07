// 업무 이벤트 받기 — app/chat/collect.py의 단일 소스 이식. 브라우저를 믿지 않고 다시 확인한다:
// 올린 사람 = 이벤트의 user, mode에 맞지 않는 필드는 버리고, 원문은 sha256을 다시 계산해 맞을 때만.
use axum::extract::{Path, State};
use axum::Json;
use once_cell::sync::Lazy;
use regex::Regex;
use serde::Deserialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::auth::User;
use crate::authz;
use crate::errors::{EngineError, Result};
use crate::service::{admin_tx, app_tx, principal};
use crate::state::AppState;

const MAX_EVENTS: usize = 500;
const MAX_BLOBS: usize = 200;
const MAX_BLOB_CHARS: usize = 200_000;
static EVENT_ID: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z0-9_.:-]{1,100}$").unwrap());
static KIND: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[a-z][a-z0-9_.:-]{1,60}$").unwrap());
static HASH: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[0-9a-f]{64}$").unwrap());
static HOST: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)").unwrap());

// signals에서 남기는 것 — 행동의 모양만(원문·주소·입력 내용 없음)
const SIGNAL_KEYS: [&str; 16] = ["schema_version", "event_id", "session_id", "kind", "recorded_at",
    "actor_kind", "content_origin", "platform", "doc_type", "approval_status", "task_id",
    "task_bound", "label_kind", "observation", "evidence_candidate", "data"];
const HASH_EXTRA: [&str; 7] = ["content_hash", "content_bytes", "span", "first_event",
    "derived_from", "derived_from_unknown", "title"];
const SIG_DATA: [&str; 6] = ["tool", "ok", "decision", "simulated", "harness", "dropped"];
const HASH_DATA: [&str; 7] = ["tool", "ok", "decision", "simulated", "harness", "dropped", "how"];

fn shape(ev: &Map<String, Value>, mode: &str) -> Value {
    let mut out = Map::new();
    match mode {
        "full" => { out = ev.clone(); }
        _ => {
            for k in SIGNAL_KEYS {
                if k == "data" { continue; }
                if let Some(v) = ev.get(k) { out.insert(k.into(), v.clone()); }
            }
            if mode == "hashes" {
                for k in HASH_EXTRA {
                    if let Some(v) = ev.get(k) { out.insert(k.into(), v.clone()); }
                }
            }
        }
    }
    let data = ev.get("data").and_then(|d| d.as_object()).cloned().unwrap_or_default();
    if mode == "signals" {
        out.insert("data".into(), Value::Object(
            SIG_DATA.iter().filter_map(|k| data.get(*k).map(|v| ((*k).to_string(), v.clone()))).collect()));
    } else if mode == "hashes" {
        out.insert("data".into(), Value::Object(
            HASH_DATA.iter().filter_map(|k| data.get(*k).map(|v| ((*k).to_string(), v.clone()))).collect()));
        let host = ev.get("source_url").and_then(|u| u.as_str())
            .and_then(|u| HOST.captures(u)).map(|c| c[1].to_string());
        out.insert("source_host".into(), host.map(Value::String).unwrap_or(Value::Null));
    }
    Value::Object(out)
}

#[derive(Deserialize)]
pub struct CollectIn {
    pub mode: String,
    #[serde(default)] pub events: Vec<Value>,
    #[serde(default)] pub blobs: Map<String, Value>,
}

pub async fn ingest(State(st): State<AppState>, Path(slug): Path<String>, user: User,
                    Json(body): Json<CollectIn>) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    if !["signals", "hashes", "full"].contains(&body.mode.as_str()) {
        return Err(EngineError::invalid("mode는 signals·hashes·full 중 하나"));
    }
    if body.events.len() > MAX_EVENTS {
        return Err(EngineError::invalid(format!("이벤트는 한 번에 {MAX_EVENTS}건까지")));
    }
    if !body.blobs.is_empty() && body.mode != "full" {
        return Err(EngineError::invalid("원문은 full 범위에서만 올립니다"));
    }
    if body.blobs.len() > MAX_BLOBS {
        return Err(EngineError::invalid(format!("원문은 한 번에 {MAX_BLOBS}개까지")));
    }
    {
        app_tx!(st, t, &user.uid, conn, tx);
        let (p, _) = principal(&tx, &user.uid).await?;
        tx.commit().await.map_err(EngineError::from)?;
        if !p.active {
            return Err(EngineError::forbidden("비활성화된 멤버입니다"));
        }
    }
    let mut rows: Vec<(String, chrono::DateTime<chrono::Utc>, String, Option<String>, Value)> = Vec::new();
    for ev in &body.events {
        let Some(ev) = ev.as_object() else { return Err(EngineError::invalid("이벤트 모양이 아닙니다")) };
        let eid = ev.get("event_id").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let kind = ev.get("kind").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if !EVENT_ID.is_match(&eid) || !KIND.is_match(&kind) {
            return Err(EngineError::invalid(format!("event_id·kind 형식: {}", &eid.chars().take(40).collect::<String>())));
        }
        if let Some(u) = ev.get("user") {
            if !u.is_null() && u.as_str() != Some(user.uid.as_str()) {
                return Err(EngineError::forbidden("다른 사람의 이벤트는 올릴 수 없습니다"));
            }
        }
        let at_raw = ev.get("recorded_at").and_then(|v| v.as_str()).unwrap_or("").replace('Z', "+00:00");
        let at = chrono::DateTime::parse_from_rfc3339(&at_raw)
            .map_err(|_| EngineError::invalid(format!("recorded_at 형식: {}", &eid.chars().take(40).collect::<String>())))?
            .with_timezone(&chrono::Utc);
        let task = ev.get("task_id").and_then(|v| v.as_str())
            .map(|s| s.chars().take(100).collect::<String>());
        rows.push((eid, at, kind, task, shape(ev, &body.mode)));
    }
    let mut good: Vec<(String, String)> = Vec::new();
    for (h, textv) in &body.blobs {
        let Some(text) = textv.as_str() else { return Err(EngineError::invalid("원문 이름·크기")) };
        if !HASH.is_match(h) || text.chars().count() > MAX_BLOB_CHARS {
            return Err(EngineError::invalid("원문 이름·크기"));
        }
        let digest = hex::encode(Sha256::digest(text.as_bytes()));
        if digest != *h {
            return Err(EngineError::invalid(format!("원문 해시가 맞지 않습니다: {}", &h[..12])));
        }
        good.push((h.clone(), text.to_string()));
    }
    let (n, b) = {
        admin_tx!(st, t, conn, tx);
        let mut n: u64 = 0;
        for (eid, at, kind, task, bodyv) in &rows {
            n += tx.execute(
                "insert into work_events (event_id, user_id, recorded_at, kind, task_id, mode, body) \
                 values ($1, $2, $3, $4, $5, $6, $7) on conflict (event_id) do nothing",
                &[eid, &user.uid, at, kind, task, &body.mode, bodyv]).await?;
        }
        let mut b: u64 = 0;
        for (h, text) in &good {
            b += tx.execute("insert into work_blobs (hash, body, first_user) values ($1, $2, $3) \
                             on conflict (hash) do nothing", &[h, text, &user.uid]).await?;
        }
        tx.commit().await.map_err(EngineError::from)?;
        (n, b)
    };
    Ok(Json(json!({"accepted": n, "duplicates": rows.len() as u64 - n, "blobs": b})))
}

pub async fn summary(State(st): State<AppState>, Path(slug): Path<String>, user: User) -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let (mine, kinds, p, pols) = {
        app_tx!(st, t, &user.uid, conn, tx);
        let (p, pols) = principal(&tx, &user.uid).await?;
        let mine = tx.query_one(
            "select count(*) as n, max(recorded_at) as last, count(distinct task_id) as tasks from work_events",
            &[]).await?;
        let kinds = tx.query("select kind, count(*) as n from work_events group by kind order by n desc limit 30",
                             &[]).await?;
        tx.commit().await.map_err(EngineError::from)?;
        (mine, kinds, p, pols)
    };
    let last: Option<chrono::DateTime<chrono::Utc>> = mine.get(1);
    let mut out = json!({
        "mine": {"events": mine.get::<_, i64>(0), "tasks": mine.get::<_, i64>(2),
                 "last": last.map(|d| d.to_rfc3339_opts(chrono::SecondsFormat::Micros, false))},
        "kinds": kinds.iter().map(|r| (r.get::<_, String>(0), json!(r.get::<_, i64>(1))))
            .collect::<Map<String, Value>>(),
    });
    if authz::check(&p, "audit.read", None, None, &pols).allowed {
        admin_tx!(st, t, conn, tx);
        let a = tx.query_one("select count(*) as n, count(distinct user_id) as users from work_events", &[]).await?;
        let blobs: i64 = tx.query_one("select count(*) as n from work_blobs", &[]).await?.get(0);
        tx.commit().await.map_err(EngineError::from)?;
        out["workspace"] = json!({"events": a.get::<_, i64>(0), "users": a.get::<_, i64>(1), "blobs": blobs});
    }
    Ok(Json(out))
}
