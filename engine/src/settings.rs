use std::path::PathBuf;

use serde_json::{json, Value};

/// Mirrors ``service.data_dir()`` and ``app_settings.sql_limits()`` defaults.
pub fn data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("FABRIC_DESKTOP_DATA_DIR") {
        if !dir.is_empty() {
            return PathBuf::from(dir);
        }
    }
    if cfg!(windows) {
        let base = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| {
            std::env::var("USERPROFILE")
                .map(|p| format!(r"{p}\AppData\Local"))
                .unwrap_or_else(|_| ".".into())
        });
        PathBuf::from(base).join("FabricDesktop")
    } else if cfg!(target_os = "macos") {
        dirs_home().join("Library/Application Support/FabricDesktop")
    } else {
        dirs_home().join(".local/share/FabricDesktop")
    }
}

fn dirs_home() -> PathBuf {
    std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

pub fn sql_limits(root: &PathBuf) -> Value {
    let path = root.join("settings.json");
    let defaults = json!({
        "sql_page_rows": 1000,
        "sql_time_limit_minutes": 60,
        "sql_memory_limit_percent": 30,
        "sql_max_concurrent": 2,
        "sql_result_retention_hours": 24,
        "sql_temp_dir": ""
    });
    if !path.exists() {
        return defaults;
    }
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    let file: Value = serde_json::from_str(&text).unwrap_or(json!({}));
    let mut out = defaults;
    if let Some(obj) = out.as_object_mut() {
        for key in [
            "sql_page_rows",
            "sql_time_limit_minutes",
            "sql_memory_limit_percent",
            "sql_max_concurrent",
            "sql_result_retention_hours",
            "sql_temp_dir",
        ] {
            if let Some(v) = file.get(key) {
                obj.insert(key.to_string(), v.clone());
            }
        }
    }
    out
}
