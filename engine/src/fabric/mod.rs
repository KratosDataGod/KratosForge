//! Fabric REST read helpers (phase 4).

mod http;

use reqwest::blocking::Client;
use serde_json::Value;

pub use http::http_get_json;

const API: &str = "https://api.fabric.microsoft.com/v1";

pub struct FabricClient {
    http: Client,
    token: String,
}

impl FabricClient {
    pub fn new(token: String) -> Self {
        Self {
            http: Client::new(),
            token,
        }
    }

    pub fn list_workspaces(&self) -> Result<Value, String> {
        self.paged("/workspaces")
    }

    pub fn list_lakehouses(&self, workspace_id: &str) -> Result<Value, String> {
        self.paged(&format!("/workspaces/{workspace_id}/lakehouses"))
    }

    pub fn list_items(&self, workspace_id: &str) -> Result<Value, String> {
        self.paged(&format!("/workspaces/{workspace_id}/items"))
    }

    fn paged(&self, path: &str) -> Result<Value, String> {
        let mut out: Vec<Value> = Vec::new();
        let mut url = format!("{API}{path}");
        while !url.is_empty() {
            let resp = self
                .http
                .get(&url)
                .header("Authorization", format!("Bearer {}", self.token))
                .send()
                .map_err(|e| e.to_string())?;
            if !resp.status().is_success() {
                return Err(format!("Fabric API {}: {}", resp.status(), resp.text().unwrap_or_default()));
            }
            let body: Value = resp.json().map_err(|e| e.to_string())?;
            if let Some(items) = body.get("value").and_then(Value::as_array) {
                out.extend(items.clone());
            }
            url = body
                .get("continuationUri")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
        }
        Ok(Value::Array(out))
    }
}
