// 내부 API — Python 관리 플레인(platform.py 공지 전파, workspace.py 권한 조회)이 부른다.
// 판정·팬아웃 규칙을 여기 한 곳에 두기 위한 입구다(사본 금지). CHAT_INTERNAL_KEY가 있으면 헤더 일치 필수.
use axum::extract::{Query, State};
use axum::http::HeaderMap;
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::errors::{EngineError, Result};
use crate::events::STATS;
use crate::state::AppState;

fn guard(headers: &HeaderMap) -> Result<()> {
    if let Ok(key) = std::env::var("CHAT_INTERNAL_KEY") {
        if !key.is_empty() {
            let got = headers.get("x-internal-key").and_then(|v| v.to_str().ok()).unwrap_or("");
            if got != key {
                return Err(EngineError::forbidden("내부 API 키가 맞지 않습니다"));
            }
        }
    }
    Ok(())
}

#[derive(Deserialize)]
pub struct PublishIn {
    pub slug: String,
    pub channel_id: i64,
    pub event: Value,
}

pub async fn publish(State(st): State<AppState>, headers: HeaderMap,
                     Json(body): Json<PublishIn>) -> Result<Json<Value>> {
    guard(&headers)?;
    let t = st.reg.tenant(&body.slug).await?;
    st.events.publish(&t, body.channel_id, body.event);
    Ok(Json(json!({"ok": true})))
}

#[derive(Deserialize)]
pub struct ForgetIn { pub slug: String }

/// 정지·셀 이동처럼 라우팅 캐시(TTL 15s)가 끼면 안 되는 변경 직후, 관리 플레인이 캐시를 깬다.
pub async fn forget_route(State(st): State<AppState>, headers: HeaderMap,
                          Json(body): Json<ForgetIn>) -> Result<Json<Value>> {
    guard(&headers)?;
    st.reg.forget(&body.slug);
    Ok(Json(json!({"ok": true})))
}

#[derive(Deserialize)]
pub struct WhoQ { pub slug: String, pub uid: String }

pub async fn me(State(st): State<AppState>, headers: HeaderMap, Query(q): Query<WhoQ>) -> Result<Json<Value>> {
    guard(&headers)?;
    let t = st.reg.tenant(&q.slug).await?;
    Ok(Json(crate::service::me_doc(&st, &t, &q.uid).await?))
}

pub async fn members(State(st): State<AppState>, headers: HeaderMap, Query(q): Query<WhoQ>) -> Result<Json<Value>> {
    guard(&headers)?;
    let t = st.reg.tenant(&q.slug).await?;
    Ok(Json(json!({"members": crate::admin::members_doc(&st, &t, &q.uid).await?})))
}

pub async fn stats(State(st): State<AppState>, headers: HeaderMap) -> Result<Json<Value>> {
    guard(&headers)?;
    Ok(Json(json!({
        "fanout": STATS.snapshot(),
        "ratelimit_mode": st.limiter.mode(),
        "hub_topics": st.events.hub.topics_count(),
    })))
}
