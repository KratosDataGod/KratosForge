//! OneLake path helpers (phase 3).

mod catalog;

pub use catalog::TableCatalog;

pub const DFS_HOST: &str = "onelake.dfs.fabric.microsoft.com";
pub const TABLE_API: &str = "https://onelake.table.fabric.microsoft.com/delta";

pub fn storage_options_map(token: &str) -> std::collections::HashMap<String, String> {
    std::collections::HashMap::from([
        ("bearer_token".to_string(), token.to_string()),
        ("use_fabric_endpoint".to_string(), "true".to_string()),
    ])
}

/// Build a Delta table URI for a lakehouse table name (``schema.table`` or ``table`` when flat).
pub fn table_uri(workspace_id: &str, lakehouse_id: &str, table: &str, flat: bool) -> Result<String, String> {
    let table = table.trim();
    if table.is_empty() {
        return Err("A table name is required.".into());
    }
    let folder = if flat || !table.contains('.') {
        table.to_string()
    } else {
        table.replace('.', "/")
    };
    Ok(format!(
        "abfss://{workspace_id}@{DFS_HOST}/{lakehouse_id}/Tables/{folder}"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schema_enabled_uri() {
        assert_eq!(
            table_uri("ws", "lh", "dbo.sales", false).unwrap(),
            "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Tables/dbo/sales"
        );
    }
}
