// 실시간 전파 — app/chat/events.py의 단일 소스 이식. 이벤트는 {type, seq} 알림뿐, 본문을 싣지 않는다.
// 백엔드(CHAT_FANOUT): memory(프로세스 안) | redis(pub/sub) | supabase(배치 브로드캐스트) | none.
// 구독 허브: 프로세스당 Redis 연결 하나(RESP3 push)로 필요한 토픽만 구독하고, 연결별 mpsc로 나눠 준다.
use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use once_cell::sync::Lazy;
use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::db::Tenant;

pub fn topic(tenant_id: &str, channel_id: i64) -> String {
    format!("t:{tenant_id}:c:{channel_id}")
}

// ── 통계 (플랫폼 헬스가 읽는다) ──────────────────────────────────────
pub static STATS: Lazy<Stats> = Lazy::new(Stats::default);

#[derive(Default)]
pub struct Stats(Mutex<HashMap<&'static str, AtomicI64>>);
impl Stats {
    pub fn add(&self, key: &'static str, n: i64) {
        self.0.lock().unwrap().entry(key).or_default().fetch_add(n, Ordering::Relaxed);
    }
    pub fn snapshot(&self) -> Value {
        let m = self.0.lock().unwrap();
        Value::Object(m.iter().map(|(k, v)| ((*k).to_string(), json!(v.load(Ordering::Relaxed)))).collect())
    }
}

// ── 발행자 ──────────────────────────────────────────────────────────
pub enum Publisher {
    Memory(Arc<Hub>),
    Redis(redis::aio::MultiplexedConnection),
    Supabase(mpsc::Sender<(String, Value)>),
    None,
}

pub struct Events {
    pub publisher: Publisher,
    pub hub: Arc<Hub>,
}

impl Events {
    pub async fn new() -> Self {
        let kind = std::env::var("CHAT_FANOUT").unwrap_or_else(|_| "memory".into()).to_lowercase();
        let hub = Arc::new(Hub::new(kind == "redis").await);
        let publisher = match kind.as_str() {
            "redis" => match redis_conn(false).await {
                Ok(c) => Publisher::Redis(c),
                Err(e) => {
                    tracing::error!("redis 연결 실패 — 팬아웃 없이 시작: {e}");
                    Publisher::None
                }
            },
            "supabase" => Publisher::Supabase(spawn_supabase()),
            "none" => Publisher::None,
            _ => Publisher::Memory(hub.clone()),
        };
        Events { publisher, hub }
    }

    /// 커밋 뒤에만 부른다. 전파 실패는 게시를 실패로 만들지 않는다.
    pub fn publish(&self, t: &Tenant, channel_id: i64, event: Value) {
        let mut payload = event;
        if let Some(o) = payload.as_object_mut() {
            o.insert("tenant".into(), json!(t.slug));
            o.insert("channel_id".into(), json!(channel_id));
            o.insert("ts".into(), json!(chrono::Utc::now().timestamp_millis() as f64 / 1000.0));
        }
        let tp = topic(&t.id, channel_id);
        match &self.publisher {
            Publisher::Memory(hub) => {
                hub.deliver(&tp, payload);
                STATS.add("published", 1);
            }
            Publisher::Redis(conn) => {
                let mut c = conn.clone();
                tokio::spawn(async move {
                    let body = payload.to_string();
                    match redis::cmd("PUBLISH").arg(&tp).arg(body).query_async::<i64>(&mut c).await {
                        Ok(_) => STATS.add("published", 1),
                        Err(e) => {
                            STATS.add("publish_errors", 1);
                            tracing::warn!("publish failed: {e}");
                        }
                    }
                });
            }
            Publisher::Supabase(tx) => {
                if tx.try_send((tp, payload)).is_err() {
                    STATS.add("dropped", 1);
                } else {
                    STATS.add("published", 1);
                }
            }
            Publisher::None => {}
        }
    }
}

fn redis_url() -> String {
    std::env::var("CHAT_REDIS_URL").unwrap_or_else(|_| "redis://127.0.0.1:6379/0".into())
}

async fn redis_conn(resp3: bool) -> Result<redis::aio::MultiplexedConnection, redis::RedisError> {
    let mut url = redis_url();
    if resp3 && !url.contains("protocol=") {
        url.push_str(if url.contains('?') { "&protocol=resp3" } else { "?protocol=resp3" });
    }
    let client = redis::Client::open(url)?;
    client.get_multiplexed_async_connection().await
}

// ── 구독 허브 ────────────────────────────────────────────────────────
enum SubCmd { Sub(String), Unsub(String) }

pub struct Hub {
    // 토픽 → (연결 id → 연결별 큐). 느린 소비자는 try_send 실패로 드러나고 공백 보충이 메운다.
    topics: Mutex<HashMap<String, HashMap<u64, mpsc::Sender<Value>>>>,
    next_id: AtomicI64,
    ctl: Option<mpsc::UnboundedSender<SubCmd>>,
}

impl Hub {
    async fn new(redis_mode: bool) -> Self {
        let ctl = if redis_mode { Some(Self::spawn_subscriber().await) } else { None };
        Hub { topics: Mutex::new(HashMap::new()), next_id: AtomicI64::new(1), ctl }
    }

    async fn spawn_subscriber() -> mpsc::UnboundedSender<SubCmd> {
        let (ctl_tx, mut ctl_rx) = mpsc::unbounded_channel::<SubCmd>();
        tokio::spawn(async move {
            // 끊기면 구독 집합을 들고 다시 연결한다 — listen() 종료 같은 조용한 멈춤이 없게.
            let mut wanted: Vec<String> = Vec::new();
            loop {
                let (push_tx, mut push_rx) = mpsc::unbounded_channel::<redis::PushInfo>();
                let mut url = redis_url();
                if !url.contains("protocol=") {
                    url.push_str(if url.contains('?') { "&protocol=resp3" } else { "?protocol=resp3" });
                }
                let conn = match redis::Client::open(url.clone()) {
                    Ok(client) => {
                        let cfg = redis::AsyncConnectionConfig::new().set_push_sender(push_tx);
                        client.get_multiplexed_async_connection_with_config(&cfg).await
                    }
                    Err(e) => Err(e),
                };
                let mut conn = match conn {
                    Ok(c) => c,
                    Err(e) => {
                        tracing::warn!("pubsub 연결 실패, 재시도: {e}");
                        tokio::time::sleep(Duration::from_millis(500)).await;
                        continue;
                    }
                };
                for tp in &wanted {
                    if let Err(e) = conn.subscribe(&[tp.as_str()]).await {
                        tracing::warn!("재구독 실패 {tp}: {e}");
                    }
                }
                loop {
                    tokio::select! {
                        cmd = ctl_rx.recv() => match cmd {
                            Some(SubCmd::Sub(tp)) => {
                                if !wanted.contains(&tp) { wanted.push(tp.clone()); }
                                if let Err(e) = conn.subscribe(&[tp.as_str()]).await {
                                    tracing::warn!("subscribe 실패 {tp}: {e}");
                                    break;      // 연결을 다시 만든다
                                }
                            }
                            Some(SubCmd::Unsub(tp)) => {
                                wanted.retain(|x| x != &tp);
                                let _ = conn.unsubscribe(&[tp.as_str()]).await;
                            }
                            None => return,
                        },
                        push = push_rx.recv() => match push {
                            Some(info) => {
                                if info.kind == redis::PushKind::Message && info.data.len() >= 2 {
                                    let tp = redis::from_redis_value::<String>(&info.data[0]).unwrap_or_default();
                                    let raw = redis::from_redis_value::<Vec<u8>>(&info.data[1]).unwrap_or_default();
                                    if let Ok(v) = serde_json::from_slice::<Value>(&raw) {
                                        crate::state::HUB.get().map(|h| h.deliver(&tp, v));
                                    }
                                }
                            }
                            None => break,       // 연결이 죽었다 — 다시 만든다
                        },
                    }
                }
                tokio::time::sleep(Duration::from_millis(300)).await;
            }
        });
        ctl_tx
    }

    pub fn deliver(&self, tp: &str, payload: Value) {
        let mut dead: Vec<u64> = Vec::new();
        {
            let topics = self.topics.lock().unwrap();
            if let Some(subs) = topics.get(tp) {
                for (id, tx) in subs {
                    match tx.try_send(payload.clone()) {
                        Ok(()) => { STATS.add("delivered", 1); }
                        Err(mpsc::error::TrySendError::Full(_)) => { STATS.add("slow_consumer_drops", 1); }
                        Err(mpsc::error::TrySendError::Closed(_)) => dead.push(*id),
                    }
                }
            }
        }
        if !dead.is_empty() {
            let mut topics = self.topics.lock().unwrap();
            if let Some(subs) = topics.get_mut(tp) {
                for id in dead { subs.remove(&id); }
            }
        }
    }

    pub fn join(&self, tp: &str, tx: mpsc::Sender<Value>) -> u64 {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed) as u64;
        let mut topics = self.topics.lock().unwrap();
        let subs = topics.entry(tp.to_string()).or_default();
        let first = subs.is_empty();
        subs.insert(id, tx);
        if first {
            if let Some(ctl) = &self.ctl { let _ = ctl.send(SubCmd::Sub(tp.to_string())); }
        }
        id
    }

    pub fn leave(&self, tp: &str, id: u64) {
        let mut topics = self.topics.lock().unwrap();
        if let Some(subs) = topics.get_mut(tp) {
            subs.remove(&id);
            if subs.is_empty() {
                topics.remove(tp);
                if let Some(ctl) = &self.ctl { let _ = ctl.send(SubCmd::Unsub(tp.to_string())); }
            }
        }
    }

    pub fn topics_count(&self) -> usize {
        self.topics.lock().unwrap().len()
    }
}

// ── Supabase Realtime 브로드캐스트 — 묶고, 합치고, 속도를 지키고, 재시도한다 (이 저장소에서 실연결 미검증) ──
struct Pending {
    order: VecDeque<String>,
    msgs: HashMap<String, Value>,
    others: VecDeque<(String, Value)>,
}
impl Pending {
    fn new() -> Self { Pending { order: VecDeque::new(), msgs: HashMap::new(), others: VecDeque::new() } }
    fn len(&self) -> usize { self.msgs.len() + self.others.len() }
    fn add(&mut self, tp: String, p: Value, front: bool) {
        if !front { STATS.add("coalesced_in", 1); }
        if p.get("type").and_then(|t| t.as_str()) != Some("message") {
            if front { self.others.push_front((tp, p)); } else { self.others.push_back((tp, p)); }
            return;
        }
        let seq = p.get("seq").and_then(|s| s.as_i64()).unwrap_or(0);
        let merged_in = p.get("merged").and_then(|m| m.as_i64()).unwrap_or(1);
        match self.msgs.get_mut(&tp) {
            None => {
                let mut v = p;
                v["merged"] = json!(merged_in);
                self.msgs.insert(tp.clone(), v);
                if front { self.order.push_front(tp); } else { self.order.push_back(tp); }
            }
            Some(cur) => {
                let cur_seq = cur.get("seq").and_then(|s| s.as_i64()).unwrap_or(0);
                let cur_merged = cur.get("merged").and_then(|m| m.as_i64()).unwrap_or(1);
                if seq > cur_seq {
                    let mut v = p;
                    v["merged"] = json!(cur_merged + merged_in);
                    *cur = v;
                } else {
                    cur["merged"] = json!(cur_merged + merged_in);
                }
            }
        }
    }
    fn take(&mut self, n: usize) -> Vec<(String, Value)> {
        let mut out = Vec::new();
        while out.len() < n {
            if let Some(x) = self.others.pop_front() { out.push(x); continue; }
            match self.order.pop_front() {
                Some(tp) => { if let Some(v) = self.msgs.remove(&tp) { out.push((tp, v)); } }
                None => break,
            }
        }
        STATS.add("coalesced_out", out.len() as i64);
        out
    }
}

fn spawn_supabase() -> mpsc::Sender<(String, Value)> {
    let (tx, mut rx) = mpsc::channel::<(String, Value)>(50000);
    let url = std::env::var("SUPABASE_URL").unwrap_or_default();
    let key = std::env::var("SUPABASE_SERVICE_KEY").unwrap_or_default();
    let max_eps: f64 = std::env::var("CHAT_RT_MAX_EPS").ok().and_then(|v| v.parse().ok()).unwrap_or(80.0);
    tokio::spawn(async move {
        let endpoint = format!("{}/realtime/v1/api/broadcast", url.trim_end_matches('/'));
        let client = reqwest::Client::builder().timeout(Duration::from_secs(5)).build().unwrap();
        let mut pending = Pending::new();
        let mut budget = max_eps;
        let mut last = Instant::now();
        let mut backoff_until = Instant::now();
        let mut delay = 0.2f64;
        loop {
            let wait = if pending.len() > 0 { Duration::from_millis(50) } else { Duration::from_secs(1) };
            match tokio::time::timeout(wait, rx.recv()).await {
                Ok(Some((tp, p))) => {
                    pending.add(tp, p, false);
                    while let Ok((tp, p)) = rx.try_recv() { pending.add(tp, p, false); }
                }
                Ok(None) => return,
                Err(_) => {}
            }
            let now = Instant::now();
            budget = (budget + now.duration_since(last).as_secs_f64() * max_eps).min(max_eps);
            last = now;
            if pending.len() == 0 || now < backoff_until || budget < 1.0 { continue; }
            let chunk = pending.take((budget as usize).min(100));
            let body = json!({"messages": chunk.iter().map(|(t, p)| json!({
                "topic": t, "event": p.get("type").and_then(|x| x.as_str()).unwrap_or("change"),
                "payload": p, "private": true})).collect::<Vec<_>>()});
            let resp = client.post(&endpoint).json(&body)
                .header("apikey", &key).bearer_auth(&key).send().await;
            let code = resp.as_ref().map(|r| r.status().as_u16()).unwrap_or(599);
            if code < 300 {
                budget -= chunk.len() as f64;
                STATS.add("sent", chunk.len() as i64);
                delay = 0.2;
            } else if matches!(code, 429 | 500 | 502 | 503 | 504 | 599) {
                for (t, p) in chunk.into_iter().rev() { pending.add(t, p, true); }
                STATS.add("retries", 1);
                let ra = resp.ok().and_then(|r| r.headers().get("retry-after")
                    .and_then(|v| v.to_str().ok()).and_then(|v| v.parse::<f64>().ok()));
                backoff_until = Instant::now() + Duration::from_secs_f64(ra.unwrap_or(delay));
                delay = (delay * 2.0).min(5.0);
            } else {
                STATS.add("publish_errors", 1);
            }
        }
    });
    tx
}
