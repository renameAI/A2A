// A2A 채팅 엔진(데이터 플레인) — /chat/v1/*를 직접 서빙하고, 나머지는 A2A_UPSTREAM(Python 엔진)으로 넘긴다.
//   a2a-chat serve [--port N]     기본. A2A_CHAT_BIND(기본 127.0.0.1), A2A_CHAT_PORT(기본 8423)
//   a2a-chat print-config         상수(메트릭 버킷 등)를 JSON으로 — Python 쪽 검사가 대조한다
mod admin;
mod archive;
mod auth;
mod authz;
mod collect;
mod db;
mod errors;
mod events;
mod internal;
mod metrics;
mod ratelimit;
mod realtime;
mod service;
mod sse;
mod state;

use axum::body::Body;
use axum::extract::{Path, Request, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures::TryStreamExt;
use serde_json::{json, Value};

use crate::auth::User;
use crate::errors::{EngineError, Result};
use crate::state::AppState;

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    if let Some(i) = args.iter().position(|a| a == "eval-cond") {
        // a2a-chat eval-cond '<조건 JSON>' ['<컨텍스트 JSON>'] → {"ok": bool} | {"error": "..."}
        let cond: Value = args.get(i + 1).and_then(|s| serde_json::from_str(s).ok())
            .unwrap_or(Value::Null);
        let ctx: Value = args.get(i + 2).and_then(|s| serde_json::from_str(s).ok())
            .unwrap_or_else(|| json!({"user": {}, "channel": {}, "env": {}}));
        match authz::evaluate(&cond, &ctx) {
            Ok(b) => println!("{}", json!({"ok": b})),
            Err(e) => println!("{}", json!({"error": e})),
        }
        return;
    }
    if args.iter().any(|a| a == "print-config") {
        println!("{}", json!({
            "metrics_bounds": metrics::BOUNDS,
            "permissions": authz::PERMISSIONS.iter().map(|(k, _)| *k).collect::<Vec<_>>(),
            "version": env!("CARGO_PKG_VERSION"),
        }));
        return;
    }
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| "info".into()))
        .init();
    let st = match AppState::new().await {
        Ok(s) => s,
        Err(e) => { eprintln!("시작 실패: {e}"); std::process::exit(1); }
    };
    metrics::spawn_flusher(st.clone());

    let chat = Router::new()
        .route("/me/tenants", get(service::my_tenants))
        .route("/t/{slug}/me", get(service::me).patch(service::update_me))
        .route("/t/{slug}/collect/events", post(collect::ingest))
        .route("/t/{slug}/collect/summary", get(collect::summary))
        .route("/t/{slug}/members", get(admin::list_members).post(admin::invite))
        .route("/t/{slug}/members/{uid}", axum::routing::patch(admin::update_member))
        .route("/t/{slug}/roles", get(admin::list_roles))
        .route("/t/{slug}/roles/{name}", axum::routing::put(admin::put_role))
        .route("/t/{slug}/channels", get(service::channels).post(service::create_channel))
        .route("/t/{slug}/channels/{cid}/members", post(service::add_channel_member))
        .route("/t/{slug}/channels/{cid}/members/{target}", axum::routing::delete(service::remove_channel_member))
        .route("/t/{slug}/channels/{cid}/archive", post(service::archive_channel))
        .route("/t/{slug}/channels/{cid}/messages", get(service::messages).post(service::post))
        .route("/t/{slug}/channels/{cid}/messages/{seq}", axum::routing::patch(service::edit).delete(service::delete))
        .route("/t/{slug}/channels/{cid}/messages/{seq}/reactions", post(service::react))
        .route("/t/{slug}/channels/{cid}/read", post(service::mark_read))
        .route("/t/{slug}/channels/{cid}/export", get(sse::export))
        .route("/t/{slug}/search", get(service::search))
        .route("/t/{slug}/policies", post(admin::add_policy))
        .route("/t/{slug}/policies/{pid}", axum::routing::delete(admin::remove_policy))
        .route("/t/{slug}/logs/{which}", get(admin::logs))
        .route("/t/{slug}/realtime-token", get(realtime_token))
        .route("/t/{slug}/stream", get(sse::stream))
        .route("/internal/publish", post(internal::publish))
        .route("/internal/forget-route", post(internal::forget_route))
        .route("/internal/me", get(internal::me))
        .route("/internal/members", get(internal::members))
        .route("/internal/stats", get(internal::stats));

    let app = Router::new()
        .nest("/chat/v1", chat)
        .route("/healthz", get(healthz))
        .fallback(proxy)
        .layer(axum::middleware::from_fn(metrics::middleware))
        .with_state(st);

    let bind = std::env::var("A2A_CHAT_BIND").unwrap_or_else(|_| "127.0.0.1".into());
    let port: u16 = args.iter().position(|a| a == "--port")
        .and_then(|i| args.get(i + 1)).and_then(|p| p.parse().ok())
        .or_else(|| std::env::var("A2A_CHAT_PORT").ok().and_then(|p| p.parse().ok()))
        .unwrap_or(8423);
    let listener = tokio::net::TcpListener::bind((bind.as_str(), port)).await
        .unwrap_or_else(|e| { eprintln!("{bind}:{port} 바인드 실패: {e}"); std::process::exit(1); });
    tracing::info!("a2a-chat listening on {bind}:{port}");
    // 직접 accept 루프 — TCP_NODELAY를 켠다(작은 응답이 Nagle+지연 ACK에 걸려 꼬리가 생기지 않게).
    loop {
        let (stream, _addr) = match listener.accept().await {
            Ok(x) => x,
            Err(e) => { tracing::warn!("accept 실패: {e}"); continue; }
        };
        let _ = stream.set_nodelay(true);
        let app2 = app.clone();
        tokio::spawn(async move {
            let io = hyper_util::rt::TokioIo::new(stream);
            let svc = hyper::service::service_fn(move |req: hyper::Request<hyper::body::Incoming>| {
                use tower::ServiceExt;
                app2.clone().oneshot(req)
            });
            if let Err(e) = hyper_util::server::conn::auto::Builder::new(hyper_util::rt::TokioExecutor::new())
                .serve_connection_with_upgrades(io, svc).await {
                tracing::debug!("conn ended: {e}");
            }
        });
    }
}

async fn realtime_token(State(st): State<AppState>, Path(slug): Path<String>, user: User)
                        -> Result<Json<Value>> {
    let t = st.reg.tenant(&slug).await?;
    let ids = service::visible_channel_ids(&st, &t, &user.uid, None).await?;
    let topics = ids.into_iter().map(|i| events::topic(&t.id, i)).collect();
    Ok(Json(realtime::topic_token(&user.uid, topics)?))
}

async fn healthz(State(st): State<AppState>, req: Request<Body>) -> Response {
    if st.upstream.is_some() {
        return proxy(State(st), req).await;
    }
    Json(json!({"ok": true, "version": env!("CARGO_PKG_VERSION")})).into_response()
}

// 하이픈 단위 홉 헤더 — 프록시가 넘기지 않는다
const HOP: [&str; 9] = ["connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                        "te", "trailer", "transfer-encoding", "upgrade", "host"];

async fn proxy(State(st): State<AppState>, req: Request<Body>) -> Response {
    let Some(up) = st.upstream.clone() else {
        return EngineError::not_found("경로 없음").into_response();
    };
    let path_q = req.uri().path_and_query().map(|p| p.as_str().to_string())
        .unwrap_or_else(|| "/".into());
    let url = format!("{}{}", up.trim_end_matches('/'), path_q);
    let (parts, body) = req.into_parts();
    let method = reqwest::Method::from_bytes(parts.method.as_str().as_bytes())
        .unwrap_or(reqwest::Method::GET);
    let mut rb = st.http.request(method, &url);
    for (k, v) in parts.headers.iter() {
        let name = k.as_str().to_lowercase();
        if HOP.contains(&name.as_str()) || name == "content-length" {
            continue;
        }
        rb = rb.header(k.as_str(), v.as_bytes());
    }
    let stream = body.into_data_stream()
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e));
    rb = rb.body(reqwest::Body::wrap_stream(stream));
    match rb.send().await {
        Ok(resp) => {
            let status = axum::http::StatusCode::from_u16(resp.status().as_u16())
                .unwrap_or(axum::http::StatusCode::BAD_GATEWAY);
            let mut out = Response::builder().status(status);
            for (k, v) in resp.headers().iter() {
                let name = k.as_str().to_lowercase();
                if HOP.contains(&name.as_str()) {
                    continue;
                }
                out = out.header(k.as_str(), v.as_bytes());
            }
            let body = Body::from_stream(resp.bytes_stream()
                .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e)));
            out.body(body).unwrap_or_else(|_| EngineError::internal("프록시 응답 구성 실패").into_response())
        }
        Err(e) => {
            tracing::warn!("upstream proxy failed: {e}");
            EngineError::new(502, "upstream_unreachable", "엔진(업스트림)에 연결하지 못했습니다").into_response()
        }
    }
}
