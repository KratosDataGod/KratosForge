//! Native DuckDB worker for jobs with ``local_root`` (tests and local Delta folders).

use std::fs::File;
use std::io::Write;
use std::path::Path;

use arrow::ipc::writer::FileWriter;
use duckdb::Connection;
use serde_json::{json, Value};

use crate::bridge::sql_bridge;

pub fn run_local_job(job: &Value) -> Result<(), String> {
    let local_root = job
        .get("local_root")
        .and_then(Value::as_str)
        .ok_or("local_root is required for the native worker")?;
    let query = job.get("query").and_then(Value::as_str).unwrap_or("");
    let dialect = job.get("dialect").and_then(Value::as_str).unwrap_or("spark");
    let allow = job.get("allow").cloned().unwrap_or(json!(["read_only"]));
    let classified = sql_bridge(
        "classify",
        &json!({ "query": query, "dialect": dialect, "allow": allow }),
    )?;
    if classified.get("refused").is_some() && !classified["refused"].is_null() {
        let msg = classified["refused"]
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Refused.");
        emit(json!({ "event": "error", "kind": "permission", "message": msg }));
        return Err(msg.into());
    }
    let translated = sql_bridge("translate", &json!({ "query": query, "dialect": dialect }))?;
    let statements = translated
        .get("statements")
        .and_then(Value::as_array)
        .ok_or("translate returned no statements")?;
    emit(json!({
        "event": "started",
        "permission": classified.get("permission").cloned().unwrap_or(json!("read_only")),
    }));
    let con = Connection::open_in_memory().map_err(|e| e.to_string())?;
    con.execute_batch("INSTALL delta; LOAD delta; SET TimeZone = 'UTC';")
        .map_err(|e| e.to_string())?;
    if let Some(mb) = job.get("memory_mb").and_then(Value::as_i64) {
        con.execute_batch(&format!("SET memory_limit = '{mb}MB';"))
            .map_err(|e| e.to_string())?;
    }
    if let Some(temp) = job.get("temp_dir").and_then(Value::as_str) {
        let escaped = temp.replace('\'', "''");
        con.execute_batch(&format!("SET temp_directory = '{escaped}';"))
            .map_err(|e| e.to_string())?;
    }
    con.execute_batch("SET enable_external_access = false; SET lock_configuration = true;")
        .map_err(|e| e.to_string())?;
    register_local_tables(&con, Path::new(local_root))?;
    let mut last_sql = String::new();
    for stmt in statements {
        last_sql = stmt.as_str().unwrap_or("").to_string();
        if !last_sql.is_empty() {
            con.execute_batch(&last_sql).map_err(|e| e.to_string())?;
        }
    }
    let out = job.get("out").and_then(Value::as_str).ok_or("missing out")?;
    let mut stmt = con.prepare(&last_sql).map_err(|e| e.to_string())?;
    let mut arrow = stmt.query_arrow([]).map_err(|e| e.to_string())?;
    let schema = arrow.get_schema();
    let file = File::create(out).map_err(|e| e.to_string())?;
    let mut writer = FileWriter::try_new(file, schema.as_ref()).map_err(|e| e.to_string())?;
    let mut rows = 0i64;
    let mut batches = Vec::new();
    while let Some(batch) = arrow.next() {
        writer.write(&batch).map_err(|e| e.to_string())?;
        let n = batch.num_rows();
        rows += n as i64;
        batches.push(n);
    }
    writer.finish().map_err(|e| e.to_string())?;
    let columns = schema
        .fields()
        .iter()
        .map(|f| json!({ "name": f.name(), "type": format!("{}", f.data_type()) }))
        .collect::<Vec<_>>();
    emit(json!({ "event": "done", "rows": rows, "batches": batches, "columns": columns }));
    Ok(())
}

fn register_local_tables(con: &Connection, root: &Path) -> Result<(), String> {
    for entry in std::fs::read_dir(root).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        let path = entry.path().to_string_lossy().replace('\'', "''");
        con.execute_batch(&format!(
            "CREATE OR REPLACE VIEW \"{name}\" AS SELECT * FROM delta_scan('{path}');"
        ))
        .map_err(|e| format!("Could not register table {name}: {e}"))?;
    }
    Ok(())
}

fn emit(value: Value) {
    println!("{}", value);
    let _ = std::io::stdout().flush();
}
