//! Auth for the Rust engine (phase 4).
//!
//! Interactive sign-in and the MSAL token cache remain on the Python sidecar (`sign_in`,
//! `sign_out`, `environment`). Rust reads delegated tokens through `engine_storage_token` and
//! `engine_fabric_token` so OneLake and Fabric REST calls never duplicate MSAL cache files.

use crate::context::EngineContext;
use serde_json::{json, Value};

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
