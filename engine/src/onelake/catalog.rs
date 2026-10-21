use serde_json::{json, Value};

use crate::fabric::http_get_json;

use super::TABLE_API;

pub struct TableCatalog {
    token: String,
}

impl TableCatalog {
    pub fn new(token: String) -> Self {
        Self { token }
    }

    pub fn list_schemas(&self, workspace_id: &str, lakehouse_id: &str) -> Result<Vec<String>, String> {
        let url = format!("{TABLE_API}/{workspace_id}/{lakehouse_id}/api/2.1/unity-catalog/schemas");
        let body = http_get_json(&self.token, &url, &[("catalog_name", lakehouse_id)])?;
        Ok(body
            .get("schemas")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(|s| s.get("name").and_then(Value::as_str))
                    .map(String::from)
                    .collect()
            })
            .unwrap_or_default())
    }

    pub fn list_tables(&self, workspace_id: &str, lakehouse_id: &str, schema: &str) -> Result<Vec<String>, String> {
        let url = format!("{TABLE_API}/{workspace_id}/{lakehouse_id}/api/2.1/unity-catalog/tables");
        let body = http_get_json(
            &self.token,
            &url,
            &[("catalog_name", lakehouse_id), ("schema_name", schema)],
        )?;
        Ok(body
            .get("tables")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(|t| t.get("name").and_then(Value::as_str))
                    .map(String::from)
                    .collect()
            })
            .unwrap_or_default())
    }

    pub fn list_all_tables(&self, workspace_id: &str, lakehouse_id: &str) -> Result<Value, String> {
        let mut out = serde_json::Map::new();
        for schema in self.list_schemas(workspace_id, lakehouse_id)? {
            let tables = self.list_tables(workspace_id, lakehouse_id, &schema)?;
            out.insert(schema, json!(tables));
        }
        Ok(Value::Object(out))
    }
}
