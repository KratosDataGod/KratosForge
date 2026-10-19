use serde_json::{json, Value};

use crate::context::EngineContext;
use crate::onelake::TableCatalog;

pub fn list_tables(ctx: &EngineContext, workspace_id: &str, lakehouse_id: &str) -> Result<Value, String> {
    let token = storage_token(ctx)?;
    TableCatalog::new(token).list_all_tables(workspace_id, lakehouse_id)
}

fn storage_token(ctx: &EngineContext) -> Result<String, String> {
    let reply = ctx.python.lock().unwrap().invoke("engine_storage_token", json!({}))?;
    if let Some(err) = reply.get("error") {
        return Err(err.as_str().unwrap_or("sign in required").to_string());
    }
    reply
        .get("result")
        .and_then(Value::as_str)
        .map(|s| s.to_string())
        .ok_or_else(|| "no storage token".to_string())
}
