use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::thread;

use serde_json::{json, Value};

use crate::context::EngineContext;
use crate::onelake::TableCatalog;

#[derive(Clone, Default)]
pub struct CatalogTables {
    inner: Arc<Mutex<Option<CatalogTablesState>>>,
}

#[derive(Clone)]
struct CatalogTablesState {
    pending: bool,
    items: HashMap<String, Vec<String>>,
}

impl CatalogTables {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn poll(&self) -> Value {
        let guard = self.inner.lock().unwrap();
        match guard.as_ref() {
            Some(state) => json!({ "pending": state.pending, "items": state.items }),
            None => json!({ "pending": false, "items": {} }),
        }
    }

    pub fn start(ctx: &EngineContext, lakehouses: &[Value]) -> Value {
        let catalog = ctx.catalog.clone();
        {
            let guard = catalog.inner.lock().unwrap();
            if guard.is_some() {
                return catalog.poll();
            }
        }
        let token = match storage_token(ctx) {
            Ok(t) => t,
            Err(message) => return json!({ "pending": false, "items": {}, "error": message }),
        };
        {
            let mut guard = catalog.inner.lock().unwrap();
            *guard = Some(CatalogTablesState {
                pending: true,
                items: HashMap::new(),
            });
        }
        let refs: Vec<(String, String)> = lakehouses
            .iter()
            .filter_map(|lh| {
                let ws = lh.get("workspace_id").and_then(Value::as_str)?;
                let id = lh.get("lakehouse_id").and_then(Value::as_str)?;
                Some((id.to_string(), ws.to_string()))
            })
            .collect();
        thread::spawn(move || {
            let table_catalog = TableCatalog::new(token);
            let mut found: HashMap<String, Vec<String>> = HashMap::new();
            for (lakehouse_id, workspace_id) in refs {
                let names = match table_catalog.list_schemas(&workspace_id, &lakehouse_id) {
                    Ok(schemas) => {
                        let mut list = Vec::new();
                        for schema in schemas {
                            if let Ok(ts) =
                                table_catalog.list_tables(&workspace_id, &lakehouse_id, &schema)
                            {
                                for t in ts {
                                    list.push(format!("{schema}.{t}"));
                                }
                            }
                        }
                        list
                    }
                    Err(_) => Vec::new(),
                };
                found.insert(lakehouse_id, names);
            }
            let mut guard = catalog.inner.lock().unwrap();
            *guard = Some(CatalogTablesState {
                pending: false,
                items: found,
            });
        });
        json!({ "pending": true, "items": {} })
    }
}

fn storage_token(ctx: &EngineContext) -> Result<String, String> {
    let reply = ctx.python.lock().unwrap().invoke("engine_storage_token", json!({}))?;
    if let Some(err) = reply.get("error") {
        return Err(err.as_str().unwrap_or("sign in required").to_string());
    }
    reply
        .get("result")
        .and_then(Value::as_str)
        .map(|s| s.to_string())
        .ok_or_else(|| "no storage token".to_string())
}
