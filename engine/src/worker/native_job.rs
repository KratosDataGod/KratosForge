//! Native DuckDB SQL-003 worker (local Delta folders and OneLake).

use std::collections::HashMap;
use std::fs::File;
use std::io::BufRead;
use std::path::Path;

use arrow::ipc::writer::FileWriter;
use duckdb::Connection;
use serde_json::{json, Value};

use crate::bridge::sql_bridge;
use crate::lakehouse_ref::{table_ref, table_uri_for_ref};
use crate::worker::engine_io::{ask, emit};

pub fn run_native_job(job: &Value, engine_in: Option<&mut dyn BufRead>) -> Result<(), String> {
    if job.get("local_root").is_some() {
        return run_local(job);
    }
    let engine_in = engine_in.ok_or("OneLake runs need the engine channel.")?;
    run_onelake(job, engine_in)
}

fn run_local(job: &Value) -> Result<(), String> {
    let local_root = job
        .get("local_root")
        .and_then(Value::as_str)
        .ok_or("local_root is required for the native worker")?;
    let (con, statements, classified) = prepare_session(job)?;
    register_local_tables(&con, Path::new(local_root))?;
    finish_query(&con, job, &statements, &classified)
}

fn run_onelake(job: &Value, engine_in: &mut dyn BufRead) -> Result<(), String> {
    let lh = job
        .get("lakehouse")
        .and_then(Value::as_object)
        .ok_or("lakehouse is required")?;
    let (con, statements, classified) = prepare_session(job)?;
    let names = sql_bridge(
        "table_names",
        &json!({
            "query": job.get("query").unwrap_or(&Value::Null),
            "dialect": job.get("dialect").unwrap_or(&json!("spark")),
        }),
    )?;
    let tables = names
        .get("tables")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let schemas_enabled = schemas_enabled_for_job(&lh, engine_in)?;
    let token = ask(engine_in, "token")?
        .get("value")
        .and_then(Value::as_str)
        .ok_or("no storage token")?
        .to_string();
    register_onelake_tables(&con, lh, &tables, schemas_enabled, &token)?;
    finish_query(&con, job, &statements, &classified)
}

fn prepare_session(job: &Value) -> Result<(Connection, Vec<String>, Value), String> {
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
        .ok_or("translate returned no statements")?
        .iter()
        .filter_map(|s| s.as_str().map(String::from))
        .collect::<Vec<_>>();
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
    Ok((con, statements, classified))
}

fn finish_query(
    con: &Connection,
    job: &Value,
    statements: &[String],
    classified: &Value,
) -> Result<(), String> {
    let mut last_sql = String::new();
    for stmt in statements {
        last_sql = stmt.clone();
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
    let _ = classified;
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

fn schemas_enabled_for_job(lh: &serde_json::Map<String, Value>, engine_in: &mut dyn BufRead) -> Result<bool, String> {
    if let Some(v) = lh.get("schemas_enabled") {
        return Ok(v.as_bool().unwrap_or(true));
    }
    let reply = ask(engine_in, "schemas_enabled")?;
    Ok(reply.get("value").and_then(Value::as_bool).unwrap_or(true))
}

fn register_onelake_tables(
    con: &Connection,
    lh: &serde_json::Map<String, Value>,
    tables: &[Value],
    schemas_enabled: bool,
    token: &str,
) -> Result<(), String> {
    let escaped_token = token.replace('\'', "''");
    con.execute_batch(&format!(
        "CREATE OR REPLACE SECRET fabric_onelake (
            TYPE AZURE,
            PROVIDER 'config',
            ACCOUNT_NAME 'onelake.dfs.fabric.microsoft.com',
            KEY_ID 'bearer',
            SECRET '{escaped_token}'
        );"
    ))
    .map_err(|e| format!("Could not configure OneLake auth: {e}"))?;
    let mut views: HashMap<String, String> = HashMap::new();
    for name in tables {
        let name = name.as_str().unwrap_or("");
        if name.is_empty() {
            continue;
        }
        let key = name.to_lowercase();
        if views.contains_key(&key) {
            continue;
        }
        let ref_ = table_ref(lh, name, schemas_enabled)?;
        let uri = table_uri_for_ref(&ref_)?.replace('\'', "''");
        let view = format!("__lakehouse_table_{}", views.len());
        views.insert(key, view.clone());
        let sql = format!(
            "CREATE OR REPLACE VIEW \"{view}\" AS SELECT * FROM delta_scan('{uri}');"
        );
        con.execute_batch(&sql)
            .map_err(|e| format!("Could not register {name}: {e}"))?;
        // Alias the name used in the query.
        con.execute_batch(&format!(
            "CREATE OR REPLACE VIEW \"{name}\" AS SELECT * FROM \"{view}\";"
        ))
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}
