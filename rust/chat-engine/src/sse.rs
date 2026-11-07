// 실시간 SSE + 채널 내보내기 — app/chat/router.py의 stream·export를 이식.
// 이벤트는 {type, seq}뿐, 공백은 클라이언트가 after=로 메운다. 주기 재확인은 연결별 지터로 흩뿌린다.
use std::collections::HashSet;
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::{Path, Query, State};
use axum::response::sse::{Event, Sse};
use axum::response::{IntoResponse, Response};
use futures::StreamExt;
use rand::Rng;
use serde::Deserialize;
use serde_json::json;

use crate::auth::User;
use crate::db::Tenant;
use crate::errors::{EngineError, Result};
use crate::events::{topic, Hub, STATS};
use crate::service::{self, app_tx, get_channel, require};
use crate::state::AppState;

fn sse_max_sec() -> f64 {
    std::env::var("CHAT_SSE_MAX_SEC").ok().and_then(|v| v.parse().ok()).unwrap_or(280.0)
}
fn sse_recheck_sec() -> f64 {
    std::env::var("CHAT_SSE_RECHECK_SEC").ok().and_then(|v| v.parse().ok()).unwrap_or(60.0)
}

// 멤버 자격을 잃으면 빈 집합(스트림이 revoked를 보내고 비워진다), 셀 이동이면 None(다시 붙게 한다).
async fn visible(st: &AppState, t: &Tenant, uid: &str, want: &HashSet<i64>)
                 -> Result<Option<HashSet<i64>>> {
    let ids: Option<Vec<i64>> = if want.is_empty() { None } else {
        let mut v: Vec<i64> = want.iter().copied().collect();
        v.sort();
        Some(v)
    };
    match service::visible_channel_ids(st, t, uid, ids.as_deref()).await {
        Ok(s) => Ok(Some(s)),
        Err(e) if e.code == "tenant_moving" => Ok(None),
        Err(e) if e.status < 500 => Ok(Some(HashSet::new())),
        Err(e) => Err(e),
    }
}

struct Joined {
    hub: Arc<Hub>,
    subs: Vec<(String, u64)>,
}
impl Joined {
    fn leave_one(&mut self, tp: &str) {
        if let Some(i) = self.subs.iter().position(|(x, _)| x == tp) {
            let (tp, id) = self.subs.remove(i);
            self.hub.leave(&tp, id);
        }
    }
}
impl Drop for Joined {
    fn drop(&mut self) {
        for (tp, id) in self.subs.drain(..) {
            self.hub.leave(&tp, id);
        }
        STATS.add("sse", -1);
    }
}

#[derive(Deserialize)]
pub struct StreamQ {
    #[serde(default)] pub channels: String,
}

pub async fn stream(State(st): State<AppState>, Path(slug): Path<String>,
                    Query(q): Query<StreamQ>, user: User) -> Result<Response> {
    let t = st.reg.tenant(&slug).await?;
    let want: HashSet<i64> = q.channels.split(',').filter_map(|x| x.trim().parse().ok()).collect();
    let allowed = visible(&st, &t, &user.uid, &want).await?
        .ok_or_else(|| EngineError::new(503, "tenant_moving", "워크스페이스를 옮기는 중입니다 — 잠시 후 다시"))?;
    if !want.is_empty() && allowed.is_empty() {
        return Err(EngineError::not_found("구독할 수 있는 채널이 없습니다"));
    }
    let (tx, mut rx) = tokio::sync::mpsc::channel::<serde_json::Value>(1000);
    STATS.add("sse", 1);
    let mut guard = Joined { hub: st.events.hub.clone(), subs: Vec::new() };
    let mut joined: HashSet<i64> = HashSet::new();
    for cid in &allowed {
        let tp = topic(&t.id, *cid);
        let id = guard.hub.join(&tp, tx.clone());
        guard.subs.push((tp, id));
        joined.insert(*cid);
    }
    let max_sec = sse_max_sec();
    let recheck = sse_recheck_sec();
    let st2 = st.clone();
    let uid = user.uid.clone();
    let t2 = t.clone();
    let stream = async_stream::stream! {
        let mut guard = guard;           // 스트림이 끊기면 Drop이 구독을 거둔다
        let mut sorted: Vec<i64> = joined.iter().copied().collect();
        sorted.sort();
        yield Ok::<Event, std::convert::Infallible>(Event::default().event("ready")
            .data(json!({"channels": sorted}).to_string()));
        let started = Instant::now();
        // 재확인 시각을 연결마다 흩뿌린다 — 같은 주기로 동시에 재확인하면 수천 연결이 한 순간에 DB를 친다.
        let mut last_check = Instant::now() - Duration::from_secs_f64(rand::thread_rng().gen_range(0.0..recheck));
        loop {
            if started.elapsed().as_secs_f64() > max_sec {
                yield Ok(Event::default().event("reconnect").data(json!({"reason": "max_duration"}).to_string()));
                return;
            }
            if last_check.elapsed().as_secs_f64() > recheck {
                last_check = Instant::now();
                match visible(&st2, &t2, &uid, &joined).await {
                    Ok(None) => {
                        yield Ok(Event::default().event("reconnect").data(json!({"reason": "tenant_moving"}).to_string()));
                        return;
                    }
                    Ok(Some(still)) => {
                        let gone: Vec<i64> = joined.difference(&still).copied().collect();
                        for cid in gone {
                            joined.remove(&cid);
                            guard.leave_one(&topic(&t2.id, cid));
                            yield Ok(Event::default().event("revoked").data(json!({"channel_id": cid}).to_string()));
                        }
                    }
                    Err(_) => {}        // 일시 오류 — 다음 재확인에서 다시
                }
            }
            match tokio::time::timeout(Duration::from_secs(15), rx.recv()).await {
                Err(_) => { yield Ok(Event::default().comment("keepalive")); }
                Ok(None) => return,
                Ok(Some(ev)) => {
                    let cid = ev.get("channel_id").and_then(|v| v.as_i64()).unwrap_or(-1);
                    if joined.contains(&cid) {
                        let kind = ev.get("type").and_then(|v| v.as_str()).unwrap_or("change").to_string();
                        yield Ok(Event::default().event(kind).data(ev.to_string()));
                    }
                }
            }
        }
    };
    let mut res = Sse::new(stream).into_response();
    let h = res.headers_mut();
    // no-transform: 압축 미들웨어·프록시가 스트림을 버퍼에 쥐지 않게 (실측: 개발 프록시 gzip이 ready를 영영 막았다)
    h.insert("cache-control", "no-cache, no-transform".parse().unwrap());
    h.insert("x-accel-buffering", "no".parse().unwrap());
    Ok(res)
}

// ── 채널 전체 내보내기 (아카이브 + 핫, NDJSON 스트림) ──────────────────
pub async fn export(State(st): State<AppState>, Path((slug, cid)): Path<(String, i64)>,
                    user: User) -> Result<Response> {
    let t = st.reg.tenant(&slug).await?;
    {
        app_tx!(st, t, &user.uid, conn, tx);
        let (ch, my_role) = get_channel(&tx, cid, &user.uid).await?;
        let ctx = crate::authz::ctx_channel(&ch);
        require(&tx, &user.uid, "export.run", Some(&ctx), my_role.as_deref()).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    {
        let mut conn = st.reg.admin_conn(&t).await?;
        let tx = conn.transaction().await.map_err(EngineError::from)?;
        st.reg.enter_admin(&*tx, &t).await?;
        tx.execute("insert into audit_log (actor, action, target) values ($1, 'export.channel', $2)",
                   &[&user.uid, &cid.to_string()]).await?;
        tx.commit().await.map_err(EngineError::from)?;
    }
    let st2 = st.clone();
    let t2 = t.clone();
    let body_stream = async_stream::stream! {
        for thread in [None, Some(-1i64)] {
            match crate::archive::read_archived(&st2.reg, &t2, cid, None, 1_000_000_000, thread).await {
                Ok(rows) => for m in rows {
                    yield Ok::<bytes::Bytes, std::io::Error>(bytes::Bytes::from(format!("{m}\n")));
                },
                Err(e) => { tracing::warn!("export archive read failed: {e}"); }
            }
        }
        // 핫 데이터는 서버 측 스트림으로 — 큰 채널도 메모리에 다 올리지 않는다.
        // 트랜잭션으로 감싼다: 중간에 끊겨도 Drop이 롤백을 보내 풀에 역할이 남지 않는다.
        let mut conn = match st2.reg.admin_conn(&t2).await {
            Ok(c) => c, Err(e) => { tracing::warn!("export conn failed: {e}"); return; }
        };
        let tx = match conn.transaction().await {
            Ok(tx) => tx, Err(e) => { tracing::warn!("export tx failed: {e}"); return; }
        };
        if let Err(e) = st2.reg.enter_admin(&*tx, &t2).await {
            tracing::warn!("export role failed: {e}");
            return;
        }
        let params: Vec<i64> = vec![cid];
        match tx.query_raw("select to_jsonb(m) from messages m where channel_id = $1 order by seq", params).await {
            Ok(rows) => {
                futures::pin_mut!(rows);
                while let Some(r) = rows.next().await {
                    match r {
                        Ok(row) => {
                            let v: serde_json::Value = row.get(0);
                            yield Ok(bytes::Bytes::from(format!("{v}\n")));
                        }
                        Err(e) => { tracing::warn!("export row failed: {e}"); break; }
                    }
                }
            }
            Err(e) => { tracing::warn!("export query failed: {e}"); }
        }
    };
    let mut res = Response::new(axum::body::Body::from_stream(body_stream));
    res.headers_mut().insert("content-type", "application/x-ndjson".parse().unwrap());
    res.headers_mut().insert("content-disposition",
                             format!("attachment; filename=\"channel-{cid}.ndjson\"").parse().unwrap());
    Ok(res)
}
