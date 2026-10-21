//! SQL-003 worker: native DuckDB (local + OneLake); Python fallback when native fails.

use std::io::{self, BufRead, Write};
use std::process::{Command, Stdio};

use fabric_desktop_engine::worker::run_native_job;
use serde_json::Value;

fn main() {
    let stdin = io::stdin();
    let mut handle = stdin.lock();
    let mut line = String::new();
    if handle.read_line(&mut line).is_err() {
        eprintln!("fabric-desktop-sql-worker: no job line");
        std::process::exit(1);
    }
    let job: Value = serde_json::from_str(line.trim()).unwrap_or(Value::Null);
    let engine = if job.get("local_root").is_some() {
        None
    } else {
        Some(&mut handle as &mut dyn BufRead)
    };
    if run_native_job(&job, engine).is_ok() {
        return;
    }
    run_python_worker(&job);
}

fn run_python_worker(job: &Value) {
    let python = std::env::var("FABRIC_DESKTOP_PYTHON").unwrap_or_else(|_| {
        if cfg!(windows) {
            "python".into()
        } else {
            "python3".into()
        }
    });
    let mut child = Command::new(&python)
        .args(["-m", "fabric_desktop.sql_worker"])
        .stdin(Stdio::piped())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap_or_else(|e| {
            eprintln!("fabric-desktop-sql-worker: {e}");
            std::process::exit(1);
        });
    if let Some(mut stdin) = child.stdin.take() {
        let _ = writeln!(stdin, "{}", job);
        let _ = stdin.flush();
    }
    match child.wait() {
        Ok(s) => std::process::exit(s.code().unwrap_or(1)),
        Err(e) => {
            eprintln!("fabric-desktop-sql-worker: {e}");
            std::process::exit(1);
        }
    }
}
