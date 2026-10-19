use serde_json::{json, Value};

/// Methods handled in Rust; everything else goes to the Python sidecar.
pub const RUST_METHODS: &[&str] = &["engine_health"];

pub fn parse_request(line: &str) -> Result<(Option<Value>, String, Value), String> {
    let req: Value = serde_json::from_str(line.trim()).map_err(|e| format!("invalid JSON: {e}"))?;
    let id = req.get("id").cloned();
    let method = req
        .get("method")
        .and_then(Value::as_str)
        .ok_or_else(|| "missing method".to_string())?
        .to_string();
    let params = req.get("params").cloned().unwrap_or(json!({}));
    Ok((id, method, params))
}

pub fn handle_rust(id: Option<Value>, method: &str, _params: &Value) -> Value {
    match method {
        "engine_health" => json!({
            "id": id,
            "result": {
                "engine": "rust",
                "phase": 1,
                "version": env!("CARGO_PKG_VERSION"),
            },
        }),
        _ => json!({
            "id": id,
            "error": format!("unknown method {method:?}"),
        }),
    }
}

pub fn is_rust_method(method: &str) -> bool {
    RUST_METHODS.contains(&method)
}

pub fn invalid_json_reply(err: &str) -> String {
    json!({ "id": null, "error": err }).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_request_line() {
        let (id, method, params) =
            parse_request(r#"{"id":1,"method":"engine_health","params":{}}"#).unwrap();
        assert_eq!(id, Some(json!(1)));
        assert_eq!(method, "engine_health");
        assert_eq!(params, json!({}));
    }

    #[test]
    fn engine_health_returns_phase_one() {
        let reply = handle_rust(Some(json!(7)), "engine_health", &json!({}));
        assert_eq!(reply["id"], json!(7));
        assert_eq!(reply["result"]["engine"], "rust");
        assert_eq!(reply["result"]["phase"], 1);
    }

    #[test]
    fn rust_method_list_includes_engine_health() {
        assert!(is_rust_method("engine_health"));
        assert!(!is_rust_method("app_settings"));
    }
}
