// 아카이브 읽기 경로 — app/chat/archive.py의 read_archived·export를 이식(쓰기·보존은 Python 관리 플레인에 남는다).
// 객체 형식(JSONL.gz)과 색인(archive_index) 계약은 쓰는 쪽과 같아야 한다 — 바꾸면 양쪽을 함께 바꾼다.
use std::io::Read;
use std::path::PathBuf;

use serde_json::{json, Map, Value};

use crate::db::{Registry, Tenant};
use crate::errors::{EngineError, Result};

enum Store {
    Local(PathBuf),
    Supabase { base: String, key: String, bucket: String },
}

fn store() -> Result<Store> {
    let kind = std::env::var("CHAT_ARCHIVE_STORE").unwrap_or_else(|_| "local".into());
    if kind == "supabase" {
        let url = std::env::var("SUPABASE_URL")
            .map_err(|_| EngineError::config("CHAT_ARCHIVE_STORE=supabase인데 SUPABASE_URL 없음"))?;
        let key = std::env::var("SUPABASE_SERVICE_KEY")
            .map_err(|_| EngineError::config("SUPABASE_SERVICE_KEY 없음"))?;
        let bucket = std::env::var("CHAT_ARCHIVE_BUCKET").unwrap_or_else(|_| "chat-archive".into());
        return Ok(Store::Supabase { base: format!("{}/storage/v1/object", url.trim_end_matches('/')), key, bucket });
    }
    Ok(Store::Local(PathBuf::from(std::env::var("CHAT_ARCHIVE_DIR").unwrap_or_else(|_| "chat_archive".into()))))
}

impl Store {
    async fn get(&self, key: &str) -> Result<Vec<u8>> {
        match self {
            Store::Local(root) => {
                let p = root.join(key);
                tokio::fs::read(&p).await
                    .map_err(|_| EngineError::not_found(format!("아카이브 객체 없음: {key}")))
            }
            Store::Supabase { base, key: k, bucket } => {
                let r = reqwest::Client::new()
                    .get(format!("{base}/{bucket}/{key}"))
                    .header("apikey", k).bearer_auth(k)
                    .send().await
                    .map_err(|e| EngineError::internal(format!("아카이브 읽기 실패: {e}")))?;
                if !r.status().is_success() {
                    return Err(EngineError::not_found(format!("아카이브 객체 없음: {key}")));
                }
                Ok(r.bytes().await.map_err(|e| EngineError::internal(e.to_string()))?.to_vec())
            }
        }
    }
}

const KEEP: [&str; 10] = ["channel_id", "seq", "parent_seq", "author", "body", "kind",
                          "created_at", "edited_at", "reply_count", "last_reply_at"];

/// 핫 데이터보다 오래된 메시지 — 채널 seq 범위가 겹치는 달만 새 것부터 연다.
/// 권한은 호출자(list_messages/export)가 이미 확인했다. thread=-1은 내보내기용(답글 전부).
pub async fn read_archived(reg: &Registry, t: &Tenant, channel_id: i64, before: Option<i64>,
                           limit: usize, thread: Option<i64>) -> Result<Vec<Value>> {
    let idx = {
        let mut conn = reg.admin_conn(t).await?;
        let tx_rows: Result<Vec<(String, Value)>> = async {
            let tx = conn.transaction().await.map_err(EngineError::from)?;
            reg.enter_admin(&*tx, t).await?;
            let rows = tx.query("select object_key, channels from archive_index order by month desc, id desc", &[]).await?;
            let out = rows.iter().map(|r| (r.get::<_, String>(0), r.get::<_, Value>(1))).collect();
            tx.commit().await?;
            Ok(out)
        }.await;
        tx_rows?
    };
    if idx.is_empty() || limit == 0 {
        return Ok(Vec::new());
    }
    let st = store()?;
    let mut out: Vec<Value> = Vec::new();
    for (object_key, channels) in idx {
        let rng = channels.get(channel_id.to_string());
        let Some(rng) = rng.and_then(|r| r.as_array()) else { continue };
        let min_seq = rng.first().and_then(|v| v.as_i64()).unwrap_or(0);
        if let Some(b) = before {
            if min_seq >= b { continue; }
        }
        let gz = st.get(&object_key).await?;
        let mut text = String::new();
        flate2::read::GzDecoder::new(&gz[..]).read_to_string(&mut text)
            .map_err(|e| EngineError::internal(format!("아카이브 압축 해제 실패: {e}")))?;
        let mut msgs: Vec<Value> = Vec::new();
        for line in text.lines() {
            let Ok(m) = serde_json::from_str::<Value>(line) else { continue };
            if m.get("channel_id").and_then(|v| v.as_i64()) != Some(channel_id) { continue; }
            let parent = m.get("parent_seq").cloned().unwrap_or(Value::Null);
            match thread {
                Some(-1) => { if parent.is_null() { continue; } }          // 내보내기: 답글 전부
                Some(th) => { if parent.as_i64() != Some(th) { continue; } }
                None => { if !parent.is_null() { continue; } }
            }
            let seq = m.get("seq").and_then(|v| v.as_i64()).unwrap_or(0);
            if let Some(b) = before {
                if seq >= b { continue; }
            }
            let deleted = m.get("deleted_at").map(|d| !d.is_null()).unwrap_or(false);
            let mut row = Map::new();
            for k in KEEP {
                row.insert(k.into(), if k == "body" && deleted { json!("") } else { m.get(k).cloned().unwrap_or(Value::Null) });
            }
            row.insert("deleted".into(), json!(deleted));
            row.insert("archived".into(), json!(true));
            row.insert("reactions".into(), json!([]));
            msgs.push(Value::Object(row));
        }
        msgs.sort_by_key(|m| -m.get("seq").and_then(|v| v.as_i64()).unwrap_or(0));
        for m in msgs {
            if out.len() >= limit { break; }
            out.push(m);
        }
        if out.len() >= limit { break; }
    }
    out.sort_by_key(|m| m.get("seq").and_then(|v| v.as_i64()).unwrap_or(0));
    Ok(out)
}
