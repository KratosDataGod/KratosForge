use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::PathBuf;
use std::process::Child;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::bridge::sql_bridge;
use crate::python::PythonSidecar;
use crate::settings::{data_dir, sql_limits};

use super::export::{export_cancel as do_export_cancel, export_status as do_export_status, start_export};
use super::page::read_page;
use super::worker::{read_loop, spawn};

const MAX_KEPT: usize = 20;
const FINISHED: &[&str] = &["completed", "failed", "cancelled", "timed_out"];

#[derive(Clone)]
pub struct SqlExecutions {
    inner: Arc<Inner>,
}

struct Inner {
    root: PathBuf,
    python: Arc<Mutex<PythonSidecar>>,
    runs: Mutex<HashMap<String, Run>>,
    exports: Mutex<HashMap<String, super::export::ExportHandle>>,
    cleaned_at_start: Mutex<bool>,
    test_lake: Option<PathBuf>,
    proc_handles: Mutex<HashMap<String, Arc<Mutex<Option<Child>>>>>,
    timer_flags: Mutex<HashMap<String, Arc<std::sync::atomic::AtomicBool>>>,
}

#[derive(Clone)]
pub struct Run {
    pub id: String,
    pub query: String,
    pub dialect: String,
    pub lakehouse: Value,
    pub allow: Vec<String>,
    pub folder: PathBuf,
    pub created: f64,
    pub started: f64,
    pub ended: f64,
    pub state: String,
    pub rows: i64,
    pub batches: Vec<i64>,
    pub offsets: Vec<i64>,
    pub columns: Value,
    pub permission: String,
    pub error: String,
    pub error_kind: String,
    pub refused: Option<Value>,
    pub stop_reason: String,
    pub time_limit_s: f64,
}

impl SqlExecutions {
    pub fn new(python: Arc<Mutex<PythonSidecar>>) -> Self {
        let test_lake = std::env::var("FABRIC_DESKTOP_SQL_TEST_LAKE")
            .ok()
            .filter(|s| !s.is_empty())
            .map(PathBuf::from);
        Self {
            inner: Arc::new(Inner {
                root: data_dir(),
                python,
                runs: Mutex::new(HashMap::new()),
                exports: Mutex::new(HashMap::new()),
                cleaned_at_start: Mutex::new(false),
                test_lake,
                proc_handles: Mutex::new(HashMap::new()),
                timer_flags: Mutex::new(HashMap::new()),
            }),
        }
    }

    pub fn start(
        &self,
        query: &str,
        lakehouse_ref: &Value,
        dialect: &str,
        allow: Option<&Value>,
    ) -> Result<Value, String> {
        if query.trim().is_empty() {
            return Err("The query is empty.".into());
        }
        let lh = lakehouse_ref
            .as_object()
            .ok_or("Pick a lakehouse to run the query against.")?;
        for key in ["workspace_id", "lakehouse_id", "name"] {
            if !lh.get(key).and_then(Value::as_str).map(|s| !s.is_empty()).unwrap_or(false) {
                return Err("Pick a lakehouse to run the query against.".into());
            }
        }
        let mut allowed = vec!["read_only".to_string()];
        if let Some(Value::Array(items)) = allow {
            for item in items {
                if let Some(s) = item.as_str() {
                    if s != "read_only" && s != "local_mutation" {
                        return Err(
                            "The SQL page can only read tables and change a run's own session on this PC.".into(),
                        );
                    }
                    if !allowed.contains(&s.to_string()) {
                        allowed.push(s.to_string());
                    }
                }
            }
        }
        let classified = sql_bridge(
            "classify",
            &json!({ "query": query, "dialect": dialect, "allow": allowed }),
        )?;
        if classified.get("refused").is_some() && !classified["refused"].is_null() {
            return Ok(failed_permission_status(
                dialect,
                lakehouse_ref,
                classified.get("permission").and_then(Value::as_str).unwrap_or(""),
                &classified["refused"],
            ));
        }
        if !*self.inner.cleaned_at_start.lock().unwrap() {
            *self.inner.cleaned_at_start.lock().unwrap() = true;
            self.cleanup(true);
        }
        let id = uuid::Uuid::new_v4().simple().to_string()[..12].to_string();
        let run = Run {
            id: id.clone(),
            query: query.to_string(),
            dialect: dialect.to_string(),
            lakehouse: lakehouse_ref.clone(),
            allow: allowed,
            folder: self.base().join(&id),
            created: now_secs(),
            started: 0.0,
            ended: 0.0,
            state: "queued".into(),
            rows: 0,
            batches: vec![],
            offsets: vec![],
            columns: json!([]),
            permission: classified
                .get("permission")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            error: String::new(),
            error_kind: String::new(),
            refused: None,
            stop_reason: String::new(),
            time_limit_s: 0.0,
        };
        let run_id = id.clone();
        self.inner.runs.lock().unwrap().insert(id, run);
        self.pump();
        self.status(&run_id)
    }

    pub fn status(&self, run_id: &str) -> Result<Value, String> {
        Ok(self.get_run(run_id)?.status())
    }

    pub fn recent(&self) -> Vec<Value> {
        let runs = self.inner.runs.lock().unwrap();
        let mut list: Vec<&Run> = runs.values().collect();
        list.sort_by(|a, b| b.created.partial_cmp(&a.created).unwrap_or(std::cmp::Ordering::Equal));
        list.iter().map(|r| r.status()).collect()
    }

    pub fn cancel(&self, run_id: &str) -> Result<Value, String> {
        let mut runs = self.inner.runs.lock().unwrap();
        let run = runs
            .get_mut(run_id)
            .ok_or_else(|| format!("No SQL run '{run_id}' on this PC."))?;
        if run.state == "queued" {
            run.state = "cancelled".into();
            run.ended = now_secs();
            let snapshot = run.clone();
            drop(runs);
            self.after_finish(&snapshot);
            return self.status(run_id);
        }
        if run.state == "running" {
            run.state = "cancelling".into();
            run.stop_reason = "cancel".into();
            if let Some(proc) = self.inner.proc_handles.lock().unwrap().remove(run_id) {
                if let Some(mut child) = proc.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        }
        Ok(run.status())
    }

    pub fn page(&self, run_id: &str, offset: i64, limit: Option<i64>) -> Result<Value, String> {
        let run = self.get_run(run_id)?;
        if run.state != "completed" {
            return Err(format!(
                "The query is {}, so there are no rows to show.",
                run.state.replace('_', " ")
            ));
        }
        let snapshot = run.folder.join("result.arrow");
        if !snapshot.exists() {
            return Ok(json!({
                "id": run_id,
                "rerun_needed": true,
                "message": "This result was cleaned up. Run the query again to see it.",
            }));
        }
        let limits = self.limits();
        let page_rows = limits.get("sql_page_rows").and_then(Value::as_i64).unwrap_or(1000).clamp(1, 10_000);
        let limit = limit.unwrap_or(page_rows).clamp(1, 10_000);
        let offset = if run.rows > 0 { offset.max(0).min(run.rows - 1) } else { 0 };
        let page = read_page(&snapshot, run.rows, &run.offsets, &run.columns, offset, limit)?;
        Ok(json!({
            "id": run_id,
            "offset": page["offset"],
            "limit": page["limit"],
            "rows": page["rows"],
            "columns": page["columns"],
            "total": page["total"],
            "has_previous": page["has_previous"],
            "has_next": page["has_next"],
        }))
    }

    pub fn export(&self, run_id: &str, format: &str, name: &str) -> Result<Value, String> {
        start_export(self, run_id, format, name)
    }

    pub fn export_status(&self, export_id: &str) -> Result<Value, String> {
        do_export_status(self, export_id)
    }

    pub fn export_cancel(&self, export_id: &str) -> Result<Value, String> {
        do_export_cancel(self, export_id)
    }

    pub(crate) fn get_run(&self, run_id: &str) -> Result<Run, String> {
        self.inner
            .runs
            .lock()
            .unwrap()
            .get(run_id)
            .cloned()
            .ok_or_else(|| format!("No SQL run '{run_id}' on this PC. It may have been cleaned up; run the query again."))
    }

    pub(crate) fn insert_export(&self, job: super::export::ExportHandle) {
        self.inner.exports.lock().unwrap().insert(job.id.clone(), job);
    }

    pub(crate) fn get_export(&self, export_id: &str) -> Result<super::export::ExportHandle, String> {
        self.inner
            .exports
            .lock()
            .unwrap()
            .get(export_id)
            .cloned()
            .ok_or_else(|| format!("No export '{export_id}'."))
    }

    pub(crate) fn update_export_rows(&self, export_id: &str, rows: i64) {
        if let Some(job) = self.inner.exports.lock().unwrap().get_mut(export_id) {
            job.rows += rows;
        }
    }

    pub(crate) fn finish_export(&self, export_id: &str, state: &str, error: &str) {
        if let Some(job) = self.inner.exports.lock().unwrap().get_mut(export_id) {
            job.state = state.to_string();
            job.error = error.to_string();
        }
    }

    fn limits(&self) -> Value {
        sql_limits(&self.inner.root)
    }

    fn base(&self) -> PathBuf {
        let limits = self.limits();
        let configured = limits.get("sql_temp_dir").and_then(Value::as_str).unwrap_or("");
        if !configured.is_empty() {
            PathBuf::from(configured)
        } else {
            self.inner.root.join("sql-runs")
        }
    }

    fn pump(&self) {
        let limits = self.limits();
        let max_concurrent = limits.get("sql_max_concurrent").and_then(Value::as_i64).unwrap_or(2).max(1) as usize;
        let mut runs = self.inner.runs.lock().unwrap();
        let active = runs.values().filter(|r| r.state == "running" || r.state == "cancelling").count();
        let mut waiting: Vec<(f64, String)> = runs
            .values()
            .filter(|r| r.state == "queued")
            .map(|r| (r.created, r.id.clone()))
            .collect();
        waiting.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
        for (_, id) in waiting.into_iter().take(max_concurrent.saturating_sub(active)) {
            if let Some(run) = runs.get_mut(&id) {
                self.launch(run, &limits);
            }
        }
    }

    fn launch(&self, run: &mut Run, limits: &Value) {
        fs::create_dir_all(&run.folder).ok();
        let lh = &run.lakehouse;
        let mut job = json!({
            "query": run.query,
            "dialect": run.dialect,
            "allow": run.allow,
            "lakehouse": {
                "workspace_id": lh.get("workspace_id"),
                "lakehouse_id": lh.get("lakehouse_id"),
                "name": lh.get("name"),
                "workspace_name": lh.get("workspace_name"),
            },
            "work_dir": self.inner.root.to_string_lossy(),
            "out": run.folder.join("result.arrow").to_string_lossy(),
            "temp_dir": run.folder.join("spill").to_string_lossy(),
            "memory_mb": memory_mb(limits),
            "threads": std::thread::available_parallelism()
                .map(|n| (n.get() / max_concurrent(limits)).max(1))
                .unwrap_or(1),
        });
        if let Some(root) = &self.inner.test_lake {
            job["lakehouse"]["schemas_enabled"] = json!(false);
            job["local_root"] = json!(root.to_string_lossy());
        }
        run.state = "running".into();
        run.started = now_secs();
        let minutes = limits.get("sql_time_limit_minutes").and_then(Value::as_f64).unwrap_or(0.0);
        run.time_limit_s = minutes * 60.0;
        let child = match spawn(&job) {
            Ok(c) => c,
            Err(exc) => {
                run.state = "failed".into();
                run.ended = now_secs();
                run.error_kind = "engine".into();
                run.error = exc;
                let snapshot = run.clone();
                self.after_finish(&snapshot);
                return;
            }
        };
        let exec = self.clone();
        let run_id = run.id.clone();
        let proc = read_loop(
            self.inner.python.clone(),
            run.lakehouse.clone(),
            child,
            move |event| exec.inner.on_worker_event(&exec, &run_id, event),
        );
        self.inner.proc_handles.lock().unwrap().insert(run.id.clone(), proc);
        if minutes > 0.0 {
            let flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
            let run_id = run.id.clone();
            let inner = self.inner.clone();
            let seconds = minutes * 60.0;
            let timer_flag = flag.clone();
            thread::spawn(move || {
                thread::sleep(Duration::from_secs_f64(seconds));
                if timer_flag.load(std::sync::atomic::Ordering::Relaxed) {
                    return;
                }
                let mut runs = inner.runs.lock().unwrap();
                if let Some(r) = runs.get_mut(&run_id) {
                    if r.state == "running" {
                        r.stop_reason = "timeout".into();
                        r.state = "cancelling".into();
                        if let Some(proc) = inner.proc_handles.lock().unwrap().remove(&run_id) {
                            if let Some(mut child) = proc.lock().unwrap().take() {
                                let _ = child.kill();
                            }
                        }
                    }
                }
            });
            self.inner.timer_flags.lock().unwrap().insert(run.id.clone(), flag);
        }
    }

    fn after_finish(&self, run: &Run) {
        self.audit(run);
        self.cleanup(false);
        self.pump();
    }

    fn audit(&self, run: &Run) {
        let lh = &run.lakehouse;
        let seconds = (run.ended.max(run.started) - run.started).max(0.0);
        if let Ok(mut py) = self.inner.python.lock() {
            let _ = py.invoke(
                "engine_audit_sql",
                json!({
                    "action": "sql_execution",
                    "outcome": if run.state == "completed" { "ok" } else { "error" },
                    "seconds": seconds,
                    "params": {
                        "execution": run.id,
                        "state": run.state,
                        "dialect": run.dialect,
                        "permission": run.permission,
                        "rows": run.rows,
                        "workspace_id": lh.get("workspace_id").and_then(Value::as_str).unwrap_or(""),
                        "lakehouse_id": lh.get("lakehouse_id").and_then(Value::as_str).unwrap_or(""),
                        "lakehouse": lh.get("name").and_then(Value::as_str).unwrap_or(""),
                    },
                }),
            );
        }
    }

    fn cleanup(&self, earlier_sessions: bool) {
        let limits = self.limits();
        let hours = limits.get("sql_result_retention_hours").and_then(Value::as_f64).unwrap_or(24.0);
        let now = now_secs();
        let mut runs = self.inner.runs.lock().unwrap();
        let exports = self.inner.exports.lock().unwrap();
        let busy: HashSet<String> = exports
            .values()
            .filter(|e| e.state == "running")
            .map(|e| e.execution.clone())
            .collect();
        let mut done: Vec<(f64, String)> = runs
            .values()
            .filter(|r| FINISHED.contains(&r.state.as_str()))
            .map(|r| (r.ended, r.id.clone()))
            .collect();
        done.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        for (n, (_, id)) in done.iter().enumerate() {
            if let Some(run) = runs.get(id) {
                let old = hours > 0.0 && now - run.ended > hours * 3600.0;
                if (old || n >= MAX_KEPT) && !busy.contains(id) {
                    fs::remove_dir_all(&run.folder).ok();
                }
            }
            if n >= MAX_KEPT {
                runs.remove(id);
            }
        }
        if earlier_sessions {
            let known: HashSet<String> = runs.keys().cloned().collect();
            let base = self.base();
            if base.is_dir() {
                for entry in fs::read_dir(&base).into_iter().flatten().flatten() {
                    let name = entry.file_name().to_string_lossy().to_string();
                    if name.len() == 12 && !known.contains(&name) {
                        fs::remove_dir_all(entry.path()).ok();
                    }
                }
            }
        }
    }
}

impl Inner {
    fn on_worker_event(&self, exec: &SqlExecutions, run_id: &str, event: Value) {
        let kind = event.get("event").and_then(Value::as_str).unwrap_or("");
        let mut terminal = false;
        {
            let mut runs = self.runs.lock().unwrap();
            let run = runs.get_mut(run_id);
            if run.is_none() {
                return;
            }
            let run = run.unwrap();
            match kind {
                "started" => {
                    run.permission = event.get("permission").and_then(Value::as_str).unwrap_or("").to_string();
                }
                "progress" => {
                    run.rows = event.get("rows").and_then(Value::as_i64).unwrap_or(0);
                }
                "done" => {
                    run.state = "completed".into();
                    run.rows = event.get("rows").and_then(Value::as_i64).unwrap_or(0);
                    if let Some(b) = event.get("batches").and_then(Value::as_array) {
                        run.batches = b.iter().filter_map(Value::as_i64).collect();
                        let mut off = 0i64;
                        run.offsets = run.batches.iter().map(|n| { let o = off; off += *n; o }).collect();
                    }
                    run.columns = event.get("columns").cloned().unwrap_or(json!([]));
                    run.ended = now_secs();
                    run.query.clear();
                    terminal = true;
                }
                "error" => {
                    run.state = "failed".into();
                    run.error_kind = event.get("kind").and_then(Value::as_str).unwrap_or("sql").to_string();
                    run.error = event.get("message").and_then(Value::as_str).unwrap_or("The query failed.").to_string();
                    run.ended = now_secs();
                    run.query.clear();
                    fs::remove_dir_all(&run.folder).ok();
                    terminal = true;
                }
                "_exit" => {
                    if run.state == "running" {
                        run.state = "failed".into();
                        run.error_kind = "engine".into();
                        run.error = "The query's process stopped unexpectedly.".into();
                        run.ended = now_secs();
                        terminal = true;
                    } else if run.state == "cancelling" {
                        if run.stop_reason == "timeout" {
                            run.state = "timed_out".into();
                            run.error_kind = "timeout".into();
                            run.error = format!(
                                "Stopped after the time limit ({:.2} minutes, Settings › SQL).",
                                run.time_limit_s / 60.0
                            );
                        } else {
                            run.state = "cancelled".into();
                            run.error = "Stopped.".into();
                        }
                        run.ended = now_secs();
                        terminal = true;
                    }
                }
                _ => {}
            }
        }
        if terminal {
            self.proc_handles.lock().unwrap().remove(run_id);
            if let Some(flag) = self.timer_flags.lock().unwrap().remove(run_id) {
                flag.store(true, std::sync::atomic::Ordering::Relaxed);
            }
            if let Some(run) = self.runs.lock().unwrap().get(run_id).cloned() {
                exec.after_finish(&run);
            }
        }
    }
}

impl Run {
    fn status(&self) -> Value {
        let end = if self.ended > 0.0 { self.ended } else { now_secs() };
        let seconds = if self.started > 0.0 { (end - self.started).max(0.0) } else { 0.0 };
        json!({
            "id": self.id,
            "state": self.state,
            "target": "lakehouse",
            "dialect": self.dialect,
            "lakehouse": self.lakehouse.get("name").cloned().unwrap_or(json!("")),
            "created": self.created,
            "started": if self.started > 0.0 { Value::from(self.started) } else { Value::Null },
            "ended": if self.ended > 0.0 { Value::from(self.ended) } else { Value::Null },
            "seconds": (seconds * 100.0).round() / 100.0,
            "rows": self.rows,
            "columns": self.columns,
            "permission": self.permission,
            "error": self.error,
            "error_kind": self.error_kind,
            "refused": self.refused,
            "time_limit_minutes": (self.time_limit_s / 60.0 * 100.0).round() / 100.0,
            "snapshot": self.state == "completed" && self.folder.join("result.arrow").exists(),
        })
    }
}

fn failed_permission_status(dialect: &str, lakehouse: &Value, permission: &str, refused: &Value) -> Value {
    json!({
        "id": "",
        "state": "failed",
        "target": "lakehouse",
        "dialect": dialect,
        "lakehouse": lakehouse.get("name").cloned().unwrap_or(json!("")),
        "created": now_secs(),
        "started": Value::Null,
        "ended": now_secs(),
        "seconds": 0.0,
        "rows": 0,
        "columns": [],
        "permission": permission,
        "error": refused.get("message").and_then(Value::as_str).unwrap_or("Refused."),
        "error_kind": "permission",
        "refused": {
            "permission": refused.get("permission"),
            "statement": refused.get("statement"),
            "label": refused.get("label"),
        },
        "time_limit_minutes": 0.0,
        "snapshot": false,
    })
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn memory_mb(limits: &Value) -> i64 {
    let percent = limits.get("sql_memory_limit_percent").and_then(Value::as_i64).unwrap_or(30).max(1);
    (8 * 1024 * percent / 100).max(256)
}

fn max_concurrent(limits: &Value) -> usize {
    limits.get("sql_max_concurrent").and_then(Value::as_i64).unwrap_or(2).max(1) as usize
}
