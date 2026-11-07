// 공통 에러 계약 — Python EngineError와 같은 모양: HTTP {"error": {code, message, details}}.
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

#[derive(Debug, Clone)]
pub struct EngineError {
    pub status: u16,
    pub code: String,
    pub message: String,
    pub details: Option<Value>,
}

impl EngineError {
    pub fn new(status: u16, code: &str, message: impl Into<String>) -> Self {
        Self { status, code: code.into(), message: message.into(), details: None }
    }
    pub fn invalid(message: impl Into<String>) -> Self {
        Self::new(400, "invalid_input", message)
    }
    pub fn forbidden(message: impl Into<String>) -> Self {
        Self::new(403, "forbidden", message)
    }
    pub fn not_found(message: impl Into<String>) -> Self {
        Self::new(404, "not_found", message)
    }
    pub fn config(message: impl Into<String>) -> Self {
        Self::new(500, "config_error", message)
    }
    pub fn internal(message: impl Into<String>) -> Self {
        Self::new(500, "internal", message)
    }
}

impl std::fmt::Display for EngineError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} {}: {}", self.status, self.code, self.message)
    }
}
impl std::error::Error for EngineError {}

// DB·레디스 오류는 원문을 사용자에게 흘리지 않는다 — 로그에만 남긴다.
impl From<tokio_postgres::Error> for EngineError {
    fn from(e: tokio_postgres::Error) -> Self {
        tracing::warn!("postgres error: {e}");
        if let Some(db) = e.as_db_error() {
            let code = db.code().code();
            if code == "42501" {
                return EngineError::forbidden("권한이 없습니다");
            }
            if code == "P0002" {
                return EngineError::not_found(db.message().split('\n').next().unwrap_or("없음").to_string());
            }
            if code == "23505" {
                return EngineError::new(409, "conflict", db.message().to_string());
            }
        }
        EngineError::internal("데이터베이스 오류")
    }
}
impl From<deadpool_postgres::PoolError> for EngineError {
    fn from(e: deadpool_postgres::PoolError) -> Self {
        tracing::warn!("pool error: {e}");
        EngineError::new(503, "db_unavailable", "데이터베이스 연결을 얻지 못했습니다")
    }
}

impl IntoResponse for EngineError {
    fn into_response(self) -> Response {
        let status = StatusCode::from_u16(self.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        let body = json!({"error": {"code": self.code, "message": self.message, "details": self.details}});
        (status, Json(body)).into_response()
    }
}

pub type Result<T> = std::result::Result<T, EngineError>;
