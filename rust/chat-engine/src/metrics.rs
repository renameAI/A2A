// 테넌트별 메트릭 — app/chat/metrics.py의 기록 쪽 단일 소스 이식(읽기·분위수는 Python platform이 그대로).
// 요청마다 DB에 쓰지 않는다: 메모리 버킷에 더하고 FLUSH_SEC마다 (tenant, minute, route)로 upsert.
// BOUNDS는 Python 쪽과 같아야 한다 — tests가 `a2a-chat print-config`와 metrics.BOUNDS를 대조한다.
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use axum::body::Body;
use axum::http::Request;
use axum::middleware::Next;
use axum::response::Response;
use once_cell::sync::Lazy;
use regex::Regex;

use crate::state::AppState;

pub const BOUNDS: [f64; 9] = [5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0];

static MSG: Lazy<Regex> = Lazy::new(|| Regex::new(r"^/chat/v1/t/[^/]+/channels/\d+/messages(/\d+)?$").unwrap());
static STREAM: Lazy<Regex> = Lazy::new(|| Regex::new(r"^/chat/v1/t/[^/]+/stream$").unwrap());
static ADMIN: Lazy<Regex> = Lazy::new(|| Regex::new(r"^/chat/v1/t/[^/]+/(members|roles|policies|logs)").unwrap());
static SLUG: Lazy<Regex> = Lazy::new(|| Regex::new(r"^/chat/v1/t/([a-z][a-z0-9_]{1,40})/").unwrap());

pub fn route_group(method: &str, path: &str) -> Option<&'static str> {
    if !path.starts_with("/chat/v1/t/") {
        return None;
    }
    if MSG.is_match(path) {
        return Some(match method { "POST" => "post", "GET" => "list", _ => "edit" });
    }
    if STREAM.is_match(path) { return Some("stream"); }
    if ADMIN.is_match(path) { return Some("admin"); }
    Some("other")
}

fn bucket(ms: f64) -> usize {
    for (i, b) in BOUNDS.iter().enumerate() {
        if ms <= *b { return i; }
    }
    BOUNDS.len()
}

type Key = (String, i64, &'static str);
static BUF: Lazy<Mutex<HashMap<Key, (i64, i64, [i64; 10])>>> = Lazy::new(|| Mutex::new(HashMap::new()));

pub fn record(slug: &str, group: &'static str, ms: f64, error: bool) {
    let minute = (SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() / 60 * 60) as i64;
    let mut buf = BUF.lock().unwrap();
    let row = buf.entry((slug.to_string(), minute, group)).or_insert((0, 0, [0; 10]));
    row.0 += 1;
    row.1 += error as i64;
    row.2[bucket(ms)] += 1;
}

pub fn spawn_flusher(state: AppState) {
    let sec: f64 = std::env::var("CHAT_METRICS_FLUSH_SEC").ok().and_then(|v| v.parse().ok()).unwrap_or(10.0);
    if std::env::var("CHAT_METRICS").as_deref() == Ok("off") {
        return;
    }
    tokio::spawn(async move {
        let mut slug_to_id: HashMap<String, String> = HashMap::new();
        loop {
            tokio::time::sleep(std::time::Duration::from_secs_f64(sec)).await;
            if let Err(e) = flush(&state, &mut slug_to_id).await {
                tracing::warn!("metrics flush failed: {e}");   // 메트릭 실패가 서비스를 멈추지 않는다
            }
        }
    });
}

async fn flush(state: &AppState, slug_to_id: &mut HashMap<String, String>) -> crate::errors::Result<usize> {
    let rows: Vec<(Key, (i64, i64, [i64; 10]))> = {
        let mut buf = BUF.lock().unwrap();
        buf.drain().collect()
    };
    if rows.is_empty() {
        return Ok(0);
    }
    let c = state.reg.control().await?;
    let mut out = 0;
    for ((slug, minute, group), (n, err, hist)) in rows {
        let tid = match slug_to_id.get(&slug) {
            Some(t) => t.clone(),
            None => {
                let r = c.query_opt("select id::text from tenants where slug = $1", &[&slug]).await?;
                match r {
                    Some(row) => { let id: String = row.get(0); slug_to_id.insert(slug.clone(), id.clone()); id }
                    None => continue,
                }
            }
        };
        let ts = chrono::DateTime::from_timestamp(minute, 0).unwrap();
        let hist_vec: Vec<i64> = hist.to_vec();
        c.execute(
            "insert into metrics_minute (tenant_id, minute, route, requests, errors, lat_hist) \
             values ($1::text::uuid, $2, $3, $4, $5, $6) \
             on conflict (tenant_id, minute, route) do update set \
               requests = metrics_minute.requests + excluded.requests, \
               errors = metrics_minute.errors + excluded.errors, \
               lat_hist = (select array_agg(a + b order by i) \
                             from unnest(metrics_minute.lat_hist, excluded.lat_hist) \
                                  with ordinality as u(a, b, i))",
            &[&tid, &ts, &group, &n, &err, &hist_vec]).await?;
        out += 1;
    }
    Ok(out)
}

static PID: Lazy<String> = Lazy::new(|| std::process::id().to_string());
static DIAG: Lazy<bool> = Lazy::new(|| std::env::var("CHAT_DIAG_HEADERS").as_deref() == Ok("1"));

// 처리 시간을 재고 X-Server-Ms를 붙인다. SSE는 연결 수명이 길어 지연 히스토그램을 오염시키므로 연결 수로만 센다.
pub async fn middleware(req: Request<Body>, next: Next) -> Response {
    let path = req.uri().path().to_string();
    let method = req.method().as_str().to_string();
    let group = route_group(&method, &path);
    let slug = SLUG.captures(&path).map(|c| c[1].to_string());
    let recv_wall = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64();
    let t0 = Instant::now();
    let mut res = next.run(req).await;
    if let Some(group) = group {
        let ms = t0.elapsed().as_secs_f64() * 1000.0;
        let h = res.headers_mut();
        if let Ok(v) = format!("{ms:.1}").parse() { h.insert("x-server-ms", v); }
        if *DIAG {
            if let Ok(v) = PID.parse() { h.insert("x-worker", v); }
            if let Ok(v) = format!("{recv_wall:.6}").parse() { h.insert("x-server-recv", v); }
        }
        if let Some(slug) = slug {
            let code = res.status().as_u16();
            if group != "stream" {
                record(&slug, group, ms, code >= 500);
            } else {
                record(&slug, "stream", 0.0, code >= 400);
            }
        }
    }
    res
}
