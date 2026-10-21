//! Auth for the Rust engine (phase 4).

mod session;

pub use session::{environment, sign_in, sign_out};

use crate::context::EngineContext;
use serde_json::{json, Value};

/// Interactive sign-in uses MSAL via ``fabric_desktop.auth_bridge`` (shared cache with the sidecar).
/// Silent tokens for workers and Fabric REST use ``engine_storage_token`` / ``engine_fabric_token``.

pub fn storage_token(ctx: &EngineContext) -> Result<String, String> {
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

pub fn fabric_token(ctx: &EngineContext) -> Result<String, String> {
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
