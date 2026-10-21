//! Parse lakehouse table names like the Python ``lakehouse.table_ref``.

use crate::onelake::table_uri;

pub struct TableRef {
    pub workspace_id: String,
    pub lakehouse_id: String,
    pub schema: String,
    pub table: String,
    pub flat: bool,
}

pub fn table_ref(
    lakehouse: &serde_json::Map<String, serde_json::Value>,
    table: &str,
    schemas_enabled: bool,
) -> Result<TableRef, String> {
    let lh_name = lakehouse
        .get("name")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    let workspace_id = lakehouse
        .get("workspace_id")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .to_string();
    let lakehouse_id = lakehouse
        .get("lakehouse_id")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .to_string();
    let mut parts: Vec<&str> = table.split('.').collect();
    if parts.len() >= 2 && parts[0].eq_ignore_ascii_case(lh_name) {
        parts.remove(0);
    }
    if parts.len() > 2 {
        return Err(format!(
            "{table}: name tables as table or schema.table in the attached lakehouse ({lh_name})."
        ));
    }
    if schemas_enabled {
        let (schema, name) = if parts.len() == 2 {
            (parts[0].to_string(), parts[1].to_string())
        } else {
            ("dbo".to_string(), parts[0].to_string())
        };
        return Ok(TableRef {
            workspace_id,
            lakehouse_id,
            schema,
            table: name,
            flat: false,
        });
    }
    if parts.len() == 2 && !parts[0].eq_ignore_ascii_case("dbo") {
        return Err(format!(
            "{lh_name} was created without schemas, so a table can't go in '{}'. Use '{}'.",
            parts[0],
            parts[1]
        ));
    }
    let name = if parts.len() == 2 { parts[1] } else { parts[0] };
    Ok(TableRef {
        workspace_id,
        lakehouse_id,
        schema: "dbo".to_string(),
        table: name.to_string(),
        flat: true,
    })
}

pub fn table_uri_for_ref(ref_: &TableRef) -> Result<String, String> {
    let full = if ref_.flat {
        ref_.table.clone()
    } else {
        format!("{}.{}", ref_.schema, ref_.table)
    };
    table_uri(&ref_.workspace_id, &ref_.lakehouse_id, &full, ref_.flat)
}
