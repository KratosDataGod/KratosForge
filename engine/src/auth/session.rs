use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};

use serde_json::{json, Value};

use crate::bridge::python_bin;

/// Interactive MSAL (Python ``Authenticator``) invoked from Rust so the engine owns auth RPCs.
pub fn auth_bridge(op: &str) -> Result<Value, String> {
    let python = python_bin();
    let mut child = Command::new(&python)
        .args(["-m", "fabric_desktop.auth_bridge", op])
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("Could not run auth_bridge: {e}"))?;
    let stdout = child.stdout.take().expect("stdout");
    let reader = BufReader::new(stdout);
    let line = reader
        .lines()
        .next()
        .transpose()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "auth_bridge produced no output".to_string())?;
    let out: Value = serde_json::from_str(&line).map_err(|e| format!("auth_bridge JSON: {e}"))?;
    if let Some(err) = out.get("error").and_then(Value::as_str) {
        return Err(err.to_string());
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    if !status.success() {
        return Err(format!("auth_bridge exited with {status}"));
    }
    Ok(out)
}

pub fn sign_in() -> Result<Value, String> {
    auth_bridge("sign_in")
}

pub fn sign_out() -> Result<Value, String> {
    auth_bridge("sign_out")
}

pub fn environment() -> Result<Value, String> {
    let env = auth_bridge("environment")?;
    Ok(json!({
        "python": env.get("python"),
        "user": env.get("user"),
        "tenant": env.get("tenant"),
        "engine": "rust",
        "version": env!("CARGO_PKG_VERSION"),
    }))
}
