// Supabase Realtime 토픽 한정 토큰 — app/chat/realtime.py의 단일 소스 이식 (HS256, chat_topics 클레임).
use jsonwebtoken::{encode, EncodingKey, Header};
use serde_json::{json, Value};

use crate::errors::{EngineError, Result};

pub fn topic_token(uid: &str, mut topics: Vec<String>) -> Result<Value> {
    let secret = std::env::var("SUPABASE_JWT_SECRET").unwrap_or_default();
    if secret.len() < 32 {
        return Err(EngineError::config("SUPABASE_JWT_SECRET이 없거나 짧습니다"));
    }
    let ttl: i64 = std::env::var("CHAT_RT_TOKEN_TTL").ok().and_then(|v| v.parse().ok()).unwrap_or(300);
    topics.sort();
    let now = chrono::Utc::now().timestamp();
    let claims = json!({"sub": uid, "role": "authenticated", "aud": "authenticated",
                        "iat": now, "exp": now + ttl, "chat_topics": topics});
    let token = encode(&Header::default(), &claims, &EncodingKey::from_secret(secret.as_bytes()))
        .map_err(|e| EngineError::internal(format!("토큰 서명 실패: {e}")))?;
    Ok(json!({"token": token, "expires_at": now + ttl, "topics": claims["chat_topics"],
              "url": std::env::var("SUPABASE_URL").unwrap_or_default()}))
}
