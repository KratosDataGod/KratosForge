use std::io::{BufRead, Write};

use serde_json::{json, Value};

pub fn emit(value: Value) {
    println!("{}", value);
    let _ = std::io::stdout().flush();
}

pub fn ask(engine_in: &mut (impl BufRead + ?Sized), name: &str) -> Result<Value, String> {
    emit(json!({ "event": "call", "name": name }));
    let mut line = String::new();
    engine_in
        .read_line(&mut line)
        .map_err(|e| e.to_string())?;
    let reply: Value = serde_json::from_str(line.trim()).map_err(|e| e.to_string())?;
    if let Some(err) = reply.get("error").and_then(Value::as_str) {
        return Err(err.to_string());
    }
    Ok(reply)
}
