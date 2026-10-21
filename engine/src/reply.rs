use serde_json::{json, Value};

pub fn ok(id: Option<Value>, result: Value) -> Value {
    json!({ "id": id, "result": result })
}

pub fn err(id: Option<Value>, message: impl AsRef<str>) -> Value {
    json!({ "id": id, "error": message.as_ref() })
}

pub fn from_python_reply(id: Option<Value>, reply: &Value) -> Value {
    if reply.get("error").is_some() {
        json!({ "id": id, "error": reply.get("error").cloned().unwrap_or(json!("unknown error")) })
    } else {
        json!({
            "id": id,
            "result": reply.get("result").cloned().unwrap_or(Value::Null),
        })
    }
}
