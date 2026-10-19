use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;

use arrow::csv::Writer as CsvWriter;
use arrow::ipc::reader::FileReader;
use serde_json::{json, Value};

use super::executions::SqlExecutions;

pub fn start_export(sql: &SqlExecutions, run_id: &str, format: &str, name: &str) -> Result<Value, String> {
    if format != "csv" && format != "parquet" {
        return Err("Export as csv or parquet.".into());
    }
    let run = sql.get_run(run_id)?;
    if run.state != "completed" {
        return Err(format!(
            "The query is {}, so there is no result to export.",
            run.state.replace('_', " ")
        ));
    }
    let snapshot = run.folder.join("result.arrow");
    if !snapshot.exists() {
        return Ok(json!({
            "rerun_needed": true,
            "message": "This result was cleaned up. Run the query again to export it.",
        }));
    }
    let downloads = downloads_dir();
    fs::create_dir_all(&downloads).map_err(|e| e.to_string())?;
    let stem = sanitize_name(name, &run.lakehouse);
    let path = reserve_file(&downloads, &stem, format)?;
    let export_id = uuid::Uuid::new_v4().simple().to_string()[..12].to_string();
    let cancel = Arc::new(AtomicBool::new(false));
    let job = ExportHandle {
        id: export_id.clone(),
        execution: run_id.to_string(),
        format: format.to_string(),
        path: path.clone(),
        state: "running".to_string(),
        rows: 0,
        total: run.rows,
        error: String::new(),
        cancel,
    };
    sql.insert_export(job.clone());
    let status = job.status();
    let snapshot_path = snapshot;
    let sql = sql.clone();
    let fmt = format.to_string();
    let cancel_flag = job.cancel.clone();
    thread::spawn(move || {
        write_export(&snapshot_path, &fmt, &path, &cancel_flag, &job, &sql);
    });
    Ok(status)
}

pub fn export_status(sql: &SqlExecutions, export_id: &str) -> Result<Value, String> {
    Ok(sql.get_export(export_id)?.status())
}

pub fn export_cancel(sql: &SqlExecutions, export_id: &str) -> Result<Value, String> {
    let export = sql.get_export(export_id)?;
    export.cancel.store(true, Ordering::Relaxed);
    Ok(export.status())
}

#[derive(Clone)]
pub struct ExportHandle {
    pub id: String,
    pub execution: String,
    pub format: String,
    pub path: PathBuf,
    pub state: String,
    pub rows: i64,
    pub total: i64,
    pub error: String,
    pub cancel: Arc<AtomicBool>,
}

impl ExportHandle {
    pub fn status(&self) -> Value {
        json!({
            "id": self.id,
            "execution": self.execution,
            "format": self.format,
            "path": self.path.to_string_lossy(),
            "state": self.state,
            "rows": self.rows,
            "total": self.total,
            "error": self.error,
        })
    }
}

fn write_export(
    snapshot: &Path,
    format: &str, // csv | parquet
    path: &Path,
    cancel: &Arc<AtomicBool>,
    job: &ExportHandle,
    sql: &SqlExecutions,
) {
    let partial = path.with_extension(format!("{}.partial", path.extension().and_then(|e| e.to_str()).unwrap_or("")));
    let result = (|| -> Result<(), String> {
        let file = File::open(snapshot).map_err(|e| e.to_string())?;
        let mut reader = FileReader::try_new(file, None).map_err(|e| e.to_string())?;
        let out = File::create(&partial).map_err(|e| e.to_string())?;
        if format == "csv" {
            let mut writer = CsvWriter::new(out);
            for batch in &mut reader {
                if cancel.load(Ordering::Relaxed) {
                    return Err("cancelled".into());
                }
                let batch = batch.map_err(|e| e.to_string())?;
                writer.write(&batch).map_err(|e| e.to_string())?;
                sql.update_export_rows(&job.id, batch.num_rows() as i64);
            }
        } else {
            return Err("Parquet export is not implemented in the Rust engine yet; use csv.".into());
        }
        if cancel.load(Ordering::Relaxed) {
            return Err("cancelled".into());
        }
        fs::rename(&partial, path).map_err(|e| e.to_string())?;
        Ok(())
    })();
    match result {
        Ok(()) => sql.finish_export(&job.id, "completed", ""),
        Err(e) if e == "cancelled" => {
            discard(&partial, path);
            sql.finish_export(&job.id, "cancelled", "");
        }
        Err(e) => {
            discard(&partial, path);
            sql.finish_export(&job.id, "failed", &e);
        }
    }
}

fn downloads_dir() -> PathBuf {
    std::env::var("HOME")
        .map(|h| PathBuf::from(h).join("Downloads"))
        .unwrap_or_else(|_| PathBuf::from("."))
}

fn sanitize_name(name: &str, lakehouse: &Value) -> String {
    let fallback = lakehouse
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("SQL");
    let raw = if name.trim().is_empty() { fallback } else { name };
    raw.chars()
        .filter(|c| c.is_alphanumeric() || *c == ' ' || *c == '-' || *c == '_')
        .collect::<String>()
        .trim()
        .chars()
        .take(60)
        .collect()
}

fn reserve_file(folder: &Path, base: &str, format: &str) -> Result<PathBuf, String> {
    let stem = if base.is_empty() { "SQL" } else { base };
    for n in 1..1000 {
        let name = if n == 1 {
            format!("{stem} query.{format}")
        } else {
            format!("{stem} query ({n}).{format}")
        };
        let path = folder.join(name);
        match File::create_new(&path) {
            Ok(_) => return Ok(path),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e.to_string()),
        }
    }
    Err("Couldn't find a free file name in Downloads for the export.".into())
}

fn discard(partial: &Path, reserved: &Path) {
    let _ = fs::remove_file(partial);
    if let Ok(meta) = reserved.metadata() {
        if meta.len() == 0 {
            let _ = fs::remove_file(reserved);
        }
    }
}
