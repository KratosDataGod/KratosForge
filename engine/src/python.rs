use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::{json, Value};

/// One long-lived Python sidecar process for forwarded RPC lines.
pub struct PythonSidecar {
    child: Child,
    stdin: std::process::ChildStdin,
    stdout: BufReader<std::process::ChildStdout>,
    next_id: AtomicU64,
}

impl PythonSidecar {
    pub fn start() -> Result<Self, String> {
        let python = std::env::var("FABRIC_DESKTOP_PYTHON").unwrap_or_else(|_| {
            if cfg!(windows) {
                "python".into()
            } else {
                "python3".into()
            }
        });
        let mut cmd = Command::new(&python);
        cmd.args(["-m", "fabric_desktop.server"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        if let Ok(id) = std::env::var("FABRIC_DESKTOP_BUILTIN_CLIENT_ID") {
            cmd.env("FABRIC_DESKTOP_BUILTIN_CLIENT_ID", id);
        }
        let mut child = cmd.spawn().map_err(|e| format!("Could not start Python sidecar: {e}"))?;
        let stdin = child.stdin.take().expect("python stdin");
        let stdout = BufReader::new(child.stdout.take().expect("python stdout"));
        Ok(Self {
            child,
            stdin,
            stdout,
            next_id: AtomicU64::new(1),
        })
    }

    pub fn forward(&mut self, line: &str) -> Result<String, String> {
        if self.child.try_wait().ok().flatten().is_some() {
            return Err("The Python sidecar stopped.".into());
        }
        let trimmed = line.trim();
        if writeln!(self.stdin, "{trimmed}").and_then(|_| self.stdin.flush()).is_err() {
            return Err("The Python sidecar stopped.".into());
        }
        let mut out = String::new();
        self.stdout
            .read_line(&mut out)
            .map_err(|_| "The Python sidecar stopped.".to_string())?;
        Ok(out)
    }

    pub fn invoke(&mut self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let line = json!({ "id": id, "method": method, "params": params }).to_string();
        let raw = self.forward(&line)?;
        serde_json::from_str(raw.trim()).map_err(|e| format!("invalid sidecar JSON: {e}"))
    }
}
