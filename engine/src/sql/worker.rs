use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

use serde_json::{json, Value};

use crate::bridge::sql_worker_command;
use crate::python::PythonSidecar;

pub fn spawn(job: &Value) -> Result<Child, String> {
    let cmd = sql_worker_command();
    let mut command = Command::new(&cmd[0]);
    command
        .args(&cmd[1..])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .env("PYTHONUNBUFFERED", "1");
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    if let Some(mut stdin) = child.stdin.take() {
        let line = job.to_string();
        writeln!(stdin, "{line}")
            .and_then(|_| stdin.flush())
            .map_err(|e| e.to_string())?;
    }
    Ok(child)
}

pub fn read_loop(
    python: Arc<Mutex<PythonSidecar>>,
    lakehouse: Value,
    child: Child,
    on_event: impl Fn(Value) + Send + 'static,
) -> Arc<Mutex<Option<Child>>> {
    let shared = Arc::new(Mutex::new(Some(child)));
    let stdout = shared.lock().unwrap().as_mut().and_then(|c| c.stdout.take());
    if stdout.is_none() {
        return shared;
    }
    let reader = BufReader::new(stdout.unwrap());
    let shared_for_thread = shared.clone();
    thread::spawn(move || {
        let mut engine_in = shared_for_thread.lock().unwrap().as_mut().and_then(|c| c.stdin.take());
        for line in reader.lines() {
            let line = match line {
                Ok(l) => l,
                Err(_) => break,
            };
            let event: Value = serde_json::from_str(&line).unwrap_or(json!({}));
            let kind = event.get("event").and_then(Value::as_str).unwrap_or("");
            if kind == "call" {
                let name = event.get("name").and_then(Value::as_str).unwrap_or("");
                let reply = worker_answer(&python, &lakehouse, name);
                if let Some(mut stdin) = engine_in.as_mut() {
                    let _ = writeln!(stdin, "{}", reply.to_string());
                    let _ = stdin.flush();
                }
                continue;
            }
            on_event(event);
        }
        on_event(json!({"event": "_exit"}));
        if let Some(mut child) = shared_for_thread.lock().unwrap().take() {
            let _ = child.wait();
        }
    });
    shared
}

fn worker_answer(python: &Arc<Mutex<PythonSidecar>>, lakehouse: &Value, name: &str) -> Value {
    let result = (|| {
        let mut py = python.lock().map_err(|_| "python sidecar lock")?;
        match name {
            "token" => {
                let reply = py.invoke("engine_storage_token", json!({}))?;
                if reply.get("error").is_some() {
                    return Err(reply["error"].as_str().unwrap_or("sign in required").to_string());
                }
                Ok(json!({
                    "value": reply.get("result").cloned().unwrap_or(Value::String(String::new()))
                }))
            }
            "schemas_enabled" => {
                let ws = lakehouse.get("workspace_id").and_then(Value::as_str).unwrap_or("");
                let lh = lakehouse.get("lakehouse_id").and_then(Value::as_str).unwrap_or("");
                let reply = py.invoke(
                    "engine_schemas_enabled",
                    json!({ "workspace_id": ws, "lakehouse_id": lh }),
                )?;
                if reply.get("error").is_some() {
                    return Err(reply["error"].as_str().unwrap_or("schemas_enabled failed").to_string());
                }
                Ok(json!({
                    "value": reply.get("result").cloned().unwrap_or(Value::Bool(true))
                }))
            }
            other => Err(format!("Unknown question {other:?}")),
        }
    })();
    match result {
        Ok(v) => v,
        Err(message) => json!({ "error": message }),
    }
}
