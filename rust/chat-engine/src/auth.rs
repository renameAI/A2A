// SaaS 인증 — app/saas/auth.py와 같은 규칙: SAAS_AUTH 하나로 고정(조용한 대체 없음),
// SAAS_ALLOWED_USERS 비면 전원 거부(fail closed). 채팅 경로는 uid·email만 필요하다.
use std::time::{Duration, Instant};

use axum::extract::FromRequestParts;
use axum::http::request::Parts;
use dashmap::DashMap;
use jsonwebtoken::{decode, decode_header, Algorithm, DecodingKey, Validation};
use once_cell::sync::Lazy;
use serde::Deserialize;

use crate::errors::EngineError;
use crate::state::AppState;

#[derive(Debug, Clone)]
pub struct User {
    pub uid: String,
    #[allow(dead_code)]
    pub email: String,
}

fn mode() -> String {
    std::env::var("SAAS_AUTH").unwrap_or_else(|_| "dev".into()).to_lowercase()
}

static ALLOW: Lazy<Vec<String>> = Lazy::new(|| {
    std::env::var("SAAS_ALLOWED_USERS").unwrap_or_default()
        .split(',').map(|x| x.trim().to_lowercase()).filter(|x| !x.is_empty()).collect()
});

fn authorize(uid: &str, email: &str) -> Result<(), EngineError> {
    if ALLOW.iter().any(|x| x == "__all__") {   // 시험 하네스 전용 와일드카드 — auth.py와 같은 철자
        return Ok(());
    }
    if ALLOW.is_empty() {
        return Err(EngineError::new(403, "forbidden",
            "접근이 닫혀 있습니다 — SAAS_ALLOWED_USERS가 비어 있어 아무도 들어올 수 없습니다."));
    }
    if !ALLOW.contains(&uid.to_lowercase()) && !ALLOW.contains(&email.to_lowercase()) {
        return Err(EngineError::new(403, "forbidden",
            format!("허용되지 않은 사용자입니다 ({}) — 관리자에게 접근 요청하세요.",
                    if email.is_empty() { uid } else { email })));
    }
    Ok(())
}

// 토큰 → (uid, email) 캐시. TTL이 짧은 이유: 세션 폐기가 늦게 반영되면 접근 차단이 늦어진다.
static TOKEN_CACHE: Lazy<DashMap<String, (String, String, Instant)>> = Lazy::new(DashMap::new);
const TOKEN_TTL: Duration = Duration::from_secs(60);

async fn verify_supabase(token: &str) -> Result<(String, String), EngineError> {
    if let Some(hit) = TOKEN_CACHE.get(token) {
        if hit.2.elapsed() < TOKEN_TTL {
            return Ok((hit.0.clone(), hit.1.clone()));
        }
    }
    let url = std::env::var("SUPABASE_URL").unwrap_or_default();
    let anon = std::env::var("SUPABASE_ANON_KEY").unwrap_or_default();
    if url.is_empty() || anon.is_empty() {
        return Err(EngineError::config("SAAS_AUTH=supabase인데 SUPABASE_URL 또는 SUPABASE_ANON_KEY가 없습니다"));
    }
    #[derive(Deserialize)]
    struct U { id: Option<String>, email: Option<String> }
    let r = reqwest::Client::new()
        .get(format!("{}/auth/v1/user", url.trim_end_matches('/')))
        .header("apikey", &anon).bearer_auth(token)
        .timeout(Duration::from_secs(10)).send().await
        .map_err(|e| EngineError::new(401, "unauthorized", format!("토큰 검증 실패({e})")))?;
    if !r.status().is_success() {
        return Err(EngineError::new(401, "unauthorized", format!("토큰 검증 실패({})", r.status().as_u16())));
    }
    let u: U = r.json().await.map_err(|_| EngineError::new(401, "unauthorized", "토큰 검증 응답 해석 실패"))?;
    let uid = u.id.unwrap_or_default();
    if uid.is_empty() {
        return Err(EngineError::new(401, "unauthorized", "토큰에 사용자 식별자가 없습니다"));
    }
    let email = u.email.unwrap_or_default();
    if TOKEN_CACHE.len() > 500 { TOKEN_CACHE.clear(); }   // 캐시는 편의지 저장소가 아니다
    TOKEN_CACHE.insert(token.to_string(), (uid.clone(), email.clone(), Instant::now()));
    Ok((uid, email))
}

// Firebase ID 토큰 — Google JWK(kid별 RSA)로 서명 검증. 프로젝트 id가 없으면 즉시 실패(조용한 대체 없음).
static JWKS: Lazy<DashMap<String, (DecodingKey, Instant)>> = Lazy::new(DashMap::new);

async fn firebase_key(kid: &str) -> Result<DecodingKey, EngineError> {
    if let Some(hit) = JWKS.get(kid) {
        if hit.1.elapsed() < Duration::from_secs(3600) {
            return Ok(hit.0.clone());
        }
    }
    #[derive(Deserialize)]
    struct Jwk { kid: String, n: String, e: String }
    #[derive(Deserialize)]
    struct Jwks { keys: Vec<Jwk> }
    let r: Jwks = reqwest::Client::new()
        .get("https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com")
        .timeout(Duration::from_secs(10)).send().await
        .and_then(|r| r.error_for_status())
        .map_err(|e| EngineError::new(401, "unauthorized", format!("Firebase 키 조회 실패: {e}")))?
        .json().await
        .map_err(|_| EngineError::new(401, "unauthorized", "Firebase 키 응답 해석 실패"))?;
    for k in r.keys {
        if let Ok(key) = DecodingKey::from_rsa_components(&k.n, &k.e) {
            JWKS.insert(k.kid, (key, Instant::now()));
        }
    }
    JWKS.get(kid).map(|h| h.0.clone())
        .ok_or_else(|| EngineError::new(401, "unauthorized", "토큰의 서명 키를 찾지 못했습니다"))
}

async fn verify_firebase(token: &str) -> Result<(String, String), EngineError> {
    let project = std::env::var("FIREBASE_PROJECT_ID")
        .or_else(|_| std::env::var("GOOGLE_CLOUD_PROJECT"))
        .map_err(|_| EngineError::config("SAAS_AUTH=firebase인데 FIREBASE_PROJECT_ID가 없습니다"))?;
    let header = decode_header(token).map_err(|_| EngineError::new(401, "unauthorized", "토큰 형식 오류"))?;
    let kid = header.kid.ok_or_else(|| EngineError::new(401, "unauthorized", "토큰에 kid 없음"))?;
    let key = firebase_key(&kid).await?;
    let mut v = Validation::new(Algorithm::RS256);
    v.set_audience(&[&project]);
    v.set_issuer(&[format!("https://securetoken.google.com/{project}")]);
    #[derive(Deserialize)]
    struct Claims { sub: String, #[serde(default)] email: String }
    let data = decode::<Claims>(token, &key, &v)
        .map_err(|e| EngineError::new(401, "unauthorized", format!("토큰 검증 실패: {e}")))?;
    Ok((data.claims.sub, data.claims.email))
}

impl FromRequestParts<AppState> for User {
    type Rejection = EngineError;

    async fn from_request_parts(parts: &mut Parts, _state: &AppState) -> Result<Self, Self::Rejection> {
        let hdr = |n: &str| parts.headers.get(n).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
        match mode().as_str() {
            "dev" => {
                let uid = hdr("x-dev-user");
                if uid.is_empty() {
                    return Err(EngineError::new(401, "unauthorized", "dev 모드 — X-Dev-User 헤더 필요"));
                }
                let email = format!("{uid}@dev.local");
                authorize(&uid, &email)?;
                Ok(User { uid, email })
            }
            "supabase" => {
                let auth = hdr("authorization");
                let token = auth.strip_prefix("Bearer ").map(str::trim)
                    .ok_or_else(|| EngineError::new(401, "unauthorized", "Authorization: Bearer <Supabase 토큰> 필요"))?;
                let (uid, email) = verify_supabase(token).await?;
                authorize(&uid, &email)?;
                Ok(User { uid, email })
            }
            "firebase" => {
                let auth = hdr("authorization");
                let token = auth.strip_prefix("Bearer ").map(str::trim)
                    .ok_or_else(|| EngineError::new(401, "unauthorized", "Authorization: Bearer <Firebase ID 토큰> 필요"))?;
                let (uid, email) = verify_firebase(token).await?;
                authorize(&uid, &email)?;
                Ok(User { uid, email })
            }
            m => Err(EngineError::config(format!("SAAS_AUTH={m} — supabase|firebase|dev만 지원 (조용한 대체 없음)"))),
        }
    }
}
