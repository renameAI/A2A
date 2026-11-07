// 앱 상태 — 레지스트리(셀 풀)·이벤트(발행/허브)·속도 제한·업스트림 프록시 설정.
use std::sync::Arc;

use once_cell::sync::OnceCell;

use crate::db::Registry;
use crate::events::{Events, Hub};
use crate::ratelimit::Limiter;

// pubsub 수신 태스크가 허브보다 먼저 떠서 전역으로 찾는다 (events.rs).
pub static HUB: OnceCell<Arc<Hub>> = OnceCell::new();

#[derive(Clone)]
pub struct AppState {
    pub reg: Arc<Registry>,
    pub events: Arc<Events>,
    pub limiter: Arc<Limiter>,
    pub upstream: Option<String>,          // A2A_UPSTREAM — 나머지 경로를 Python 엔진으로
    pub http: reqwest::Client,
}

impl AppState {
    pub async fn new() -> crate::errors::Result<Self> {
        let reg = Arc::new(Registry::new()?);
        let events = Arc::new(Events::new().await);
        let _ = HUB.set(events.hub.clone());
        let limiter = Arc::new(Limiter::new().await);
        let upstream = std::env::var("A2A_UPSTREAM").ok().filter(|v| !v.is_empty());
        let http = reqwest::Client::builder().build().expect("reqwest client");
        Ok(AppState { reg, events, limiter, upstream, http })
    }
}
