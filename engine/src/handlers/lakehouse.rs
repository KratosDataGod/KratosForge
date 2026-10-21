use serde_json::{json, Value};

use crate::context::EngineContext;
use crate::delta::{preview_table as delta_preview, table_columns_for_ref};
use crate::onelake::TableCatalog;

pub fn list_tables(ctx: &EngineContext, workspace_id: &str, lakehouse_id: &str) -> Result<Value, String> {
    let token = storage_token(ctx)?;
    TableCatalog::new(token).list_all_tables(workspace_id, lakehouse_id)
}

pub fn table_columns(
    ctx: &EngineContext,
    lakehouse_ref: &Value,
    table: &str,
) -> Result<Value, String> {
    let lh = lakehouse_map(lakehouse_ref)?;
    let token = storage_token(ctx)?;
    let schemas = schemas_enabled(ctx, &lh)?;
    let cols = table_columns_for_ref(&lh, table, schemas, &token)?;
    Ok(Value::Array(cols))
}

pub fn preview_table(
    ctx: &EngineContext,
    lakehouse_ref: &Value,
    table: &str,
    limit: i64,
) -> Result<Value, String> {
    let lh = lakehouse_map(lakehouse_ref)?;
    let token = storage_token(ctx)?;
    let schemas = schemas_enabled(ctx, &lh)?;
    delta_preview(&lh, table, schemas, &token, limit)
}

fn lakehouse_map(lakehouse_ref: &Value) -> Result<serde_json::Map<String, Value>, String> {
    lakehouse_ref
        .as_object()
        .cloned()
        .ok_or_else(|| "Pick a lakehouse.".into())
}

fn schemas_enabled(ctx: &EngineContext, lh: &serde_json::Map<String, Value>) -> Result<bool, String> {
    let ws = lh.get("workspace_id").and_then(Value::as_str).unwrap_or("");
    let id = lh.get("lakehouse_id").and_then(Value::as_str).unwrap_or("");
    let reply = ctx.python.lock().unwrap().invoke(
        "engine_schemas_enabled",
        json!({ "workspace_id": ws, "lakehouse_id": id }),
    )?;
    if let Some(err) = reply.get("error") {
        return Err(err.as_str().unwrap_or("schemas_enabled failed").to_string());
    }
    Ok(reply.get("result").and_then(Value::as_bool).unwrap_or(true))
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
