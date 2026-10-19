use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

use serde_json::Value;

/// Run ``python -m fabric_desktop.sql_bridge`` for classify or translate.
pub fn sql_bridge(op: &str, payload: &Value) -> Result<Value, String> {
    let python = python_bin();
    let mut child = Command::new(&python)
        .args(["-m", "fabric_desktop.sql_bridge", op])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("Could not run sql_bridge: {e}"))?;
    {
        let stdin = child.stdin.as_mut().expect("stdin");
        let line = serde_json::to_string(payload).map_err(|e| e.to_string())?;
        writeln!(stdin, "{line}").and_then(|_| stdin.flush()).map_err(|e| e.to_string())?;
    }
    let stdout = child.stdout.take().expect("stdout");
    let reader = BufReader::new(stdout);
    let mut lines = reader.lines();
    let first = lines
        .next()
        .transpose()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "sql_bridge produced no output".to_string())?;
    let out: Value = serde_json::from_str(&first).map_err(|e| format!("sql_bridge JSON: {e}"))?;
    if let Some(err) = out.get("error").and_then(Value::as_str) {
        return Err(err.to_string());
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    if !status.success() {
        return Err(format!("sql_bridge exited with {status}"));
    }
    Ok(out)
}

pub fn python_bin() -> String {
    std::env::var("FABRIC_DESKTOP_PYTHON").unwrap_or_else(|_| {
        if cfg!(windows) {
            "python".into()
        } else {
            "python3".into()
        }
    })
}

pub fn sql_worker_command() -> Vec<String> {
    if let Ok(bin) = std::env::var("FABRIC_DESKTOP_SQL_WORKER") {
        return vec![bin];
    }
    if let Ok(engine_bin) = std::env::var("FABRIC_DESKTOP_ENGINE_BIN") {
        let path = std::path::Path::new(&engine_bin);
        if let Some(parent) = path.parent() {
            let name = if cfg!(windows) {
                "fabric-desktop-sql-worker.exe"
            } else {
                "fabric-desktop-sql-worker"
            };
            let candidate = parent.join(name);
            if candidate.exists() {
                return vec![candidate.to_string_lossy().to_string()];
            }
        }
    }
    vec![
        python_bin(),
        "-m".into(),
        "fabric_desktop.sql_worker".into(),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn classify_read_only_select() {
        if std::env::var("FABRIC_DESKTOP_PARITY").ok().as_deref() != Some("1") {
            return;
        }
        let out = sql_bridge(
            "classify",
            &json!({"query": "SELECT 1", "dialect": "spark", "allow": ["read_only"]}),
        )
        .expect("bridge");
        assert_eq!(out["permission"], json!("read_only"));
    }
}
