// 속도 제한 — app/chat/ratelimit.py의 단일 소스 이식. 토큰 버킷, 사용자·테넌트 단위.
// Redis가 있으면 Lua 하나로 원자 실행(여러 인스턴스 공유), 없으면 프로세스 안 버킷(한도 × 인스턴스 수 경고는 헬스가 맡는다).
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::errors::{EngineError, Result};
use crate::events::STATS;

const LUA: &str = r#"
local tokens_key = KEYS[1]
local rate = tonumber(ARGV[1]); local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3]); local cost = tonumber(ARGV[4])
local b = redis.call('HMGET', tokens_key, 't', 'ts')
local t = tonumber(b[1]); local ts = tonumber(b[2])
if t == nil then t = burst; ts = now end
t = math.min(burst, t + (now - ts) * rate)
local ok = 0
if t >= cost then t = t - cost; ok = 1 end
redis.call('HSET', tokens_key, 't', t, 'ts', now)
redis.call('PEXPIRE', tokens_key, math.ceil(burst / rate * 1000) + 1000)
return {ok, tostring(t)}
"#;

fn envf(name: &str, default: f64) -> f64 {
    std::env::var(name).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}
fn now_s() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64()
}

pub struct Limiter {
    redis: Option<redis::aio::MultiplexedConnection>,
    script: redis::Script,
    local: Mutex<HashMap<String, (f64, f64)>>,
    user_rate: f64, user_burst: f64, tenant_rate: f64, tenant_burst: f64,
}

impl Limiter {
    pub async fn new() -> Self {
        let redis = match std::env::var("CHAT_REDIS_URL") {
            Ok(url) if !url.is_empty() => match redis::Client::open(url) {
                Ok(c) => c.get_multiplexed_async_connection().await.ok(),
                Err(_) => None,
            },
            _ => None,
        };
        Limiter {
            redis,
            script: redis::Script::new(LUA),
            local: Mutex::new(HashMap::new()),
            user_rate: envf("CHAT_RL_USER_RATE", 5.0),
            user_burst: envf("CHAT_RL_USER_BURST", 20.0),
            tenant_rate: envf("CHAT_RL_TENANT_RATE", 300.0),
            tenant_burst: envf("CHAT_RL_TENANT_BURST", 600.0),
        }
    }

    pub fn mode(&self) -> &'static str {
        if self.redis.is_some() { "redis" } else { "local" }
    }

    fn take_local(&self, key: &str, rate: f64, burst: f64, now: f64) -> bool {
        let mut b = self.local.lock().unwrap();
        let (t, ts) = *b.get(key).unwrap_or(&(burst, now));
        let mut t = burst.min(t + (now - ts) * rate);
        let ok = t >= 1.0;
        if ok { t -= 1.0; }
        b.insert(key.to_string(), (t, now));
        ok
    }

    async fn take(&self, key: &str, rate: f64, burst: f64) -> bool {
        let now = now_s();
        if let Some(conn) = &self.redis {
            let mut c = conn.clone();
            let r: redis::RedisResult<(i64, String)> = self.script.key(key)
                .arg(rate).arg(burst).arg(now).arg(1).invoke_async(&mut c).await;
            match r {
                Ok((ok, _)) => return ok == 1,
                Err(_) => { STATS.add("ratelimit_fallback", 1); }   // 장애 시 로컬 강등(가용성 우선, 기록)
            }
        }
        self.take_local(key, rate, burst, now)
    }

    pub async fn check_post(&self, tenant_id: &str, uid: &str) -> Result<()> {
        if !self.take(&format!("rl:u:{tenant_id}:{uid}"), self.user_rate, self.user_burst).await {
            return Err(EngineError::new(429, "rate_limited", "너무 빠르게 보내고 있어요 — 잠시 후 다시"));
        }
        if !self.take(&format!("rl:t:{tenant_id}"), self.tenant_rate, self.tenant_burst).await {
            return Err(EngineError::new(429, "rate_limited", "이 워크스페이스의 전송량 한도에 도달했어요"));
        }
        Ok(())
    }
}
