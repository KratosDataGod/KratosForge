use std::sync::{Arc, Mutex};

use crate::handlers::catalog_tables::CatalogTables;
use crate::python::PythonSidecar;
use crate::sql::SqlExecutions;

pub struct EngineContext {
    pub python: Arc<Mutex<PythonSidecar>>,
    pub sql: SqlExecutions,
    pub catalog: CatalogTables,
}

impl EngineContext {
    pub fn new() -> Result<Self, String> {
        let python = Arc::new(Mutex::new(PythonSidecar::start()?));
        let sql = SqlExecutions::new(python.clone());
        Ok(Self {
            python,
            sql,
            catalog: CatalogTables::new(),
        })
    }

    pub fn python_forward(&mut self, line: &str) -> Result<String, String> {
        self.python.lock().expect("python").forward(line)
    }
}
