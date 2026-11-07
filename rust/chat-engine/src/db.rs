// 셀 라우팅과 연결 — Python db.py와 같은 규칙: 셀마다 풀, 트랜잭션마다 SET LOCAL 역할·search_path,
// prepared statement 캐시 없음(트랜잭션 풀러 안전), 라우팅 캐시 TTL, 셀 이동 펜스.
// 펜스 SQL은 app/chat/sql/tx_fence.sql 한 곳에만 있다 — Python과 같은 파일을 읽는다.
// 트랜잭션은 tokio_postgres::Transaction을 쓴다: 커밋 전에 떨어지면 Drop이 롤백을 보내
// 풀로 더러운 연결이 돌아가지 않는다.
use std::collections::HashMap;
use std::str::FromStr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use deadpool_postgres::{Manager, ManagerConfig, Object, Pool, RecyclingMethod};
use once_cell::sync::Lazy;
use tokio_postgres::{GenericClient, NoTls};

use crate::errors::{EngineError, Result};

static SLUG: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"^[a-z][a-z0-9_]{1,40}$").unwrap());
// %s(psycopg) → $n(tokio-postgres) — 같은 파일, 기계적 변환만.
static FENCE_SQL: Lazy<String> = Lazy::new(|| {
    let raw = include_str!("../../../app/chat/sql/tx_fence.sql");
    let sql = raw.lines().filter(|l| !l.trim_start().starts_with("--")).collect::<Vec<_>>().join("\n");
    let mut out = String::new();
    let mut n = 0;
    let mut rest = sql.as_str();
    while let Some(i) = rest.find("%s") {
        n += 1;
        out.push_str(&rest[..i]);
        out.push_str(&format!("${n}"));
        rest = &rest[i + 2..];
    }
    out.push_str(rest);
    out.trim().to_string()
});

fn env(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.is_empty())
}
fn route_ttl() -> Duration {
    Duration::from_secs_f64(env("CHAT_ROUTE_TTL").and_then(|v| v.parse().ok()).unwrap_or(15.0))
}

#[derive(Debug, Clone)]
pub struct Tenant {
    pub id: String,
    pub slug: String,
    pub name: String,
    pub cell_id: String,
    pub schema: String,
    pub status: String,
    pub plan: String,
}
impl Tenant {
    pub fn app_role(&self) -> String { format!("{}_app", self.schema) }
    pub fn owner_role(&self) -> String { format!("{}_owner", self.schema) }
}

fn mk_pool(dsn: &str, max_size: usize) -> Result<Pool> {
    let cfg = tokio_postgres::Config::from_str(dsn)
        .map_err(|e| EngineError::config(format!("DSN 해석 실패: {e}")))?;
    let mgr = Manager::from_config(cfg, NoTls, ManagerConfig { recycling_method: RecyclingMethod::Fast });
    Pool::builder(mgr).max_size(max_size).build()
        .map_err(|e| EngineError::config(format!("풀 생성 실패: {e}")))
}

pub struct Registry {
    control: Pool,
    cells: Mutex<HashMap<String, Pool>>,
    admin_cells: Mutex<HashMap<String, Pool>>,
    routes: Mutex<HashMap<String, (Tenant, Instant)>>,
}

impl Registry {
    pub fn new() -> Result<Self> {
        let dsn = env("CHAT_CONTROL_DSN").ok_or_else(|| {
            EngineError::config("CHAT_CONTROL_DSN이 없습니다 — 메시징 플랫폼은 컨트롤 플레인 DB가 필요합니다")
        })?;
        let size = env("CHAT_CONTROL_POOL").and_then(|v| v.parse().ok()).unwrap_or(10);
        Ok(Self {
            control: mk_pool(&dsn, size)?,
            cells: Mutex::new(HashMap::new()),
            admin_cells: Mutex::new(HashMap::new()),
            routes: Mutex::new(HashMap::new()),
        })
    }

    pub async fn control(&self) -> Result<Object> {
        Ok(self.control.get().await?)
    }

    async fn cell_dsn(&self, cell_id: &str, app: bool) -> Result<String> {
        let c = self.control().await?;
        let row = c.query_opt("select dsn_env, status from cells where id = $1", &[&cell_id]).await?;
        let row = row.ok_or_else(|| EngineError::config(format!("셀 {cell_id} 미등록")))?;
        let dsn_env: String = row.get("dsn_env");
        let dsn = if app { env(&format!("{dsn_env}_APP")) } else { None }
            .or_else(|| env(&dsn_env))
            .ok_or_else(|| EngineError::config(format!("셀 {cell_id}의 DSN 환경변수 {dsn_env} 없음")))?;
        Ok(dsn)
    }

    pub async fn cell_pool(&self, cell_id: &str, admin: bool) -> Result<Pool> {
        {
            let book = if admin { &self.admin_cells } else { &self.cells };
            if let Some(p) = book.lock().unwrap().get(cell_id) {
                return Ok(p.clone());
            }
        }
        let dsn = self.cell_dsn(cell_id, !admin).await?;
        let size = if admin { 4 } else { env("CHAT_CELL_POOL").and_then(|v| v.parse().ok()).unwrap_or(20) };
        let pool = mk_pool(&dsn, size)?;
        let book = if admin { &self.admin_cells } else { &self.cells };
        let mut m = book.lock().unwrap();
        Ok(m.entry(cell_id.to_string()).or_insert(pool).clone())
    }

    pub async fn tenant(&self, slug: &str) -> Result<Tenant> {
        if !SLUG.is_match(slug) {
            return Err(EngineError::not_found("테넌트 없음"));
        }
        if let Some((t, at)) = self.routes.lock().unwrap().get(slug) {
            if at.elapsed() < route_ttl() {
                return Ok(t.clone());
            }
        }
        let c = self.control().await?;
        let row = c.query_opt(
            "select id::text, slug, name, cell_id, schema_name, status, plan \
             from tenants where slug = $1 and status <> 'deleted'", &[&slug]).await?;
        let row = row.ok_or_else(|| EngineError::not_found("테넌트 없음"))?;
        let t = Tenant {
            id: row.get(0), slug: row.get(1), name: row.get(2), cell_id: row.get(3),
            schema: row.get(4), status: row.get(5), plan: row.get(6),
        };
        self.routes.lock().unwrap().insert(slug.to_string(), (t.clone(), Instant::now()));
        Ok(t)
    }

    pub fn forget(&self, slug: &str) {
        self.routes.lock().unwrap().remove(slug);
    }

    /// 사용자 경로의 연결 — 상태 확인 뒤 앱 풀에서 꺼낸다. 트랜잭션은 호출자가 연다.
    pub async fn app_conn(&self, t: &Tenant) -> Result<Object> {
        if t.status == "migrating" {
            return Err(EngineError::new(503, "tenant_moving", "워크스페이스를 옮기는 중입니다 — 잠시 후 다시"));
        }
        if t.status != "active" {
            return Err(EngineError::new(423, "tenant_unavailable",
                                        format!("테넌트가 사용 가능한 상태가 아닙니다 ({})", t.status)));
        }
        Ok(self.cell_pool(&t.cell_id, false).await?.get().await?)
    }

    pub async fn admin_conn(&self, t: &Tenant) -> Result<Object> {
        Ok(self.cell_pool(&t.cell_id, true).await?.get().await?)
    }

    /// 트랜잭션을 테넌트 앱 역할로 내리고 펜스를 확인한다 (tx_fence.sql).
    pub async fn enter_app<C: GenericClient>(&self, tx: &C, t: &Tenant, uid: &str) -> Result<()> {
        tx.batch_execute(&format!("set local role \"{}\"", t.app_role())).await?;
        let row = tx.query_one(FENCE_SQL.as_str(), &[&t.schema, &uid, &t.schema]).await?;
        let ok: bool = row.get("ok");
        if !ok {
            self.forget(&t.slug);
            return Err(EngineError::new(503, "tenant_moving",
                                        "워크스페이스가 다른 셀로 옮겨졌습니다 — 다시 시도하세요"));
        }
        Ok(())
    }

    /// 관리 경로 — 테넌트 owner 역할(RLS 우회). 권한 판정을 마친 뒤에만 쓴다.
    pub async fn enter_admin<C: GenericClient>(&self, tx: &C, t: &Tenant) -> Result<()> {
        tx.batch_execute(&format!("set local role \"{}\"", t.owner_role())).await?;
        tx.execute("select set_config('search_path', $1, true)", &[&t.schema]).await?;
        Ok(())
    }
}
