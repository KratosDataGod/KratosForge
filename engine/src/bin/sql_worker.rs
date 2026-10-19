//! SQL-003 worker: native DuckDB for ``local_root`` jobs; Python worker for OneLake.

use std::io::{self, BufRead};
use std::process::{Command, Stdio};

use fabric_desktop_engine::worker::run_local_job;
use serde_json::Value;

fn main() {
    let line = io::stdin()
        .lock()
        .lines()
        .next()
        .transpose()
        .unwrap_or_else(|e| {
            eprintln!("fabric-desktop-sql-worker: {e}");
            std::process::exit(1);
        })
        .unwrap_or_default();
    let job: Value = serde_json::from_str(line.trim()).unwrap_or(Value::Null);
    if job.get("local_root").is_some() {
        if run_local_job(&job).is_ok() {
            return;
        }
    }
    run_python_worker();
}

fn run_python_worker() {
    let python = std::env::var("FABRIC_DESKTOP_PYTHON").unwrap_or_else(|_| {
        if cfg!(windows) {
            "python".into()
        } else {
            "python3".into()
        }
    });
    let status = Command::new(&python)
        .args(["-m", "fabric_desktop.sql_worker"])
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status();
    match status {
        Ok(s) => std::process::exit(s.code().unwrap_or(1)),
        Err(e) => {
            eprintln!("fabric-desktop-sql-worker: {e}");
            std::process::exit(1);
        }
    }
}
