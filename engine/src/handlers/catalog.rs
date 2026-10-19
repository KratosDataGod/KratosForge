use serde_json::{json, Value};

use crate::context::EngineContext;
use crate::fabric::FabricClient;

pub fn catalog(ctx: &EngineContext, all_types: bool) -> Result<Value, String> {
    let token = fabric_token(ctx)?;
    let client = FabricClient::new(token);
    let workspaces = client
        .list_workspaces()?
        .as_array()
        .cloned()
        .unwrap_or_default();
    let mut items = Vec::new();
    let mut skipped = Vec::new();
    for ws in &workspaces {
        let ws_id = ws.get("id").and_then(Value::as_str).unwrap_or("");
        let ws_name = ws.get("displayName").and_then(Value::as_str).unwrap_or("");
        let found = if all_types {
            client.list_items(ws_id)
        } else {
            client.list_lakehouses(ws_id)
        };
        match found {
            Ok(list) => {
                for it in list.as_array().unwrap_or(&vec![]) {
                    let mut row = json!({
                        "workspace_id": ws_id,
                        "workspace_name": ws_name,
                        "item_id": it.get("id"),
                        "name": it.get("displayName"),
                        "type": it.get("type").cloned().unwrap_or(json!("Lakehouse")),
                        "description": it.get("description"),
                    });
                    if row["type"] == "Lakehouse" || !all_types {
                        row["lakehouse_id"] = it.get("id").cloned().unwrap_or(Value::Null);
                        row["type"] = json!("Lakehouse");
                    }
                    items.push(row);
                }
            }
            Err(_) => skipped.push(json!(ws_name)),
        }
    }
    Ok(json!({
        "workspaces": workspaces,
        "items": items,
        "skipped": skipped,
        "favorites": [],
    }))
}

fn fabric_token(ctx: &EngineContext) -> Result<String, String> {
    let reply = ctx.python.lock().unwrap().invoke("engine_fabric_token", json!({}))?;
    if let Some(err) = reply.get("error") {
        return Err(err.as_str().unwrap_or("sign in required").to_string());
    }
    reply
        .get("result")
        .and_then(Value::as_str)
        .map(|s| s.to_string())
        .ok_or_else(|| "no fabric token".to_string())
}
