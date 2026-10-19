use serde_json::{json, Value};

use crate::context::EngineContext;
use crate::fabric::FabricClient;
use crate::handlers::catalog::catalog as build_catalog;
use crate::handlers::lakehouse::list_tables as lakehouse_tables;
use crate::reply::{err, ok};

mod catalog;
mod lakehouse;

const PHASE: u32 = 4;

pub fn engine_phase() -> u32 {
    PHASE
}

pub fn is_rust_method(method: &str) -> bool {
    matches!(
        method,
        "engine_health"
            | "sql_start"
            | "sql_status"
            | "sql_executions"
            | "sql_page"
            | "sql_cancel"
            | "sql_export"
            | "sql_export_status"
            | "sql_export_cancel"
            | "list_tables"
            | "table_columns"
            | "preview_table"
            | "sign_in"
            | "sign_out"
            | "environment"
            | "list_workspaces"
            | "list_lakehouses"
            | "list_items"
            | "catalog"
            | "catalog_tables"
    )
}

pub fn handle(id: Option<Value>, method: &str, params: &Value, ctx: &mut EngineContext) -> Value {
    match method {
        "engine_health" => ok(
            id,
            json!({
                "engine": "rust",
                "phase": engine_phase(),
                "version": env!("CARGO_PKG_VERSION"),
            }),
        ),
        "sql_start" => match ctx.sql.start(
            params.get("query").and_then(Value::as_str).unwrap_or(""),
            params.get("lakehouse_ref").unwrap_or(&Value::Null),
            params.get("dialect").and_then(Value::as_str).unwrap_or("spark"),
            params.get("allow"),
        ) {
            Ok(result) => ok(id, result),
            Err(message) => err(id, message),
        },
        "sql_status" => match ctx.sql.status(
            params.get("execution").and_then(Value::as_str).unwrap_or(""),
        ) {
            Ok(result) => ok(id, result),
            Err(message) => err(id, message),
        },
        "sql_executions" => ok(id, Value::Array(ctx.sql.recent())),
        "sql_page" => match ctx.sql.page(
            params.get("execution").and_then(Value::as_str).unwrap_or(""),
            params.get("offset").and_then(Value::as_i64).unwrap_or(0),
            params.get("limit").and_then(Value::as_i64),
        ) {
            Ok(result) => ok(id, result),
            Err(message) => err(id, message),
        },
        "sql_cancel" => match ctx.sql.cancel(params.get("execution").and_then(Value::as_str).unwrap_or("")) {
            Ok(result) => ok(id, result),
            Err(message) => err(id, message),
        },
        "sql_export" => match ctx.sql.export(
            params.get("execution").and_then(Value::as_str).unwrap_or(""),
            params.get("format").and_then(Value::as_str).unwrap_or("csv"),
            params.get("name").and_then(Value::as_str).unwrap_or(""),
        ) {
            Ok(result) => ok(id, result),
            Err(message) => err(id, message),
        },
        "sql_export_status" => match ctx.sql.export_status(params.get("export").and_then(Value::as_str).unwrap_or("")) {
            Ok(result) => ok(id, result),
            Err(message) => err(id, message),
        },
        "sql_export_cancel" => match ctx.sql.export_cancel(params.get("export").and_then(Value::as_str).unwrap_or("")) {
            Ok(result) => ok(id, result),
            Err(message) => err(id, message),
        },
        "list_tables" => match lakehouse_tables(
            ctx,
            params.get("workspace_id").and_then(Value::as_str).unwrap_or(""),
            params.get("lakehouse_id").and_then(Value::as_str).unwrap_or(""),
        ) {
            Ok(result) => ok(id, result),
            Err(message) => err(id, message),
        },
        "table_columns" | "preview_table" => forward_invoke(id, ctx, method, params),
        "sign_in" | "sign_out" | "environment" | "catalog_tables" => forward_invoke(id, ctx, method, params),
        "catalog" => match build_catalog(
            ctx,
            params.get("all_types").and_then(Value::as_bool).unwrap_or(false),
        ) {
            Ok(result) => ok(id, result),
            Err(message) => err(id, message),
        },
        "list_workspaces" => match list_workspaces_rust(ctx) {
            Ok(result) => ok(id, result),
            Err(message) => err(id, message),
        },
        "list_lakehouses" => {
            let ws = params.get("workspace_id").and_then(Value::as_str).unwrap_or("");
            match list_lakehouses_rust(ctx, ws) {
                Ok(result) => ok(id, result),
                Err(message) => err(id, message),
            }
        }
        "list_items" => {
            let ws = params.get("workspace_id").and_then(Value::as_str).unwrap_or("");
            match list_items_rust(ctx, ws) {
                Ok(result) => ok(id, result),
                Err(message) => err(id, message),
            }
        }
        _ => err(id, format!("unknown method {method:?}")),
    }
}

fn forward_invoke(id: Option<Value>, ctx: &mut EngineContext, method: &str, params: &Value) -> Value {
    match ctx.python.lock().unwrap().invoke(method, params.clone()) {
        Ok(reply) => {
            if let Some(e) = reply.get("error") {
                err(id, e.as_str().unwrap_or("error"))
            } else {
                ok(id, reply.get("result").cloned().unwrap_or(Value::Null))
            }
        }
        Err(message) => err(id, message),
    }
}

fn list_workspaces_rust(ctx: &EngineContext) -> Result<Value, String> {
    let token = fabric_token(ctx)?;
    FabricClient::new(token).list_workspaces()
}

fn list_lakehouses_rust(ctx: &EngineContext, workspace_id: &str) -> Result<Value, String> {
    let token = fabric_token(ctx)?;
    FabricClient::new(token).list_lakehouses(workspace_id)
}

fn list_items_rust(ctx: &EngineContext, workspace_id: &str) -> Result<Value, String> {
    let token = fabric_token(ctx)?;
    FabricClient::new(token).list_items(workspace_id)
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
