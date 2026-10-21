use std::sync::OnceLock;

use tokio::runtime::Runtime;

static RUNTIME: OnceLock<Runtime> = OnceLock::new();

pub fn block_on<F: std::future::Future>(future: F) -> F::Output {
    let rt = RUNTIME.get_or_init(|| {
        Runtime::new().expect("tokio runtime")
    });
    rt.block_on(future)
}
