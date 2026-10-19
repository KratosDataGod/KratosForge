use std::io::{self, BufRead, Write};

use fabric_desktop_engine::context::EngineContext;
use fabric_desktop_engine::handlers::is_rust_method;
use fabric_desktop_engine::protocol::{handle_rust, invalid_json_reply, parse_request};

fn handle_line(line: &str, ctx: &mut Option<EngineContext>) -> String {
    let (id, method, params) = match parse_request(line) {
        Ok(parts) => parts,
        Err(e) => return invalid_json_reply(&e),
    };
    let engine = match ctx {
        Some(c) => c,
        None => match EngineContext::new() {
            Ok(c) => {
                *ctx = Some(c);
                ctx.as_mut().expect("just set")
            }
            Err(e) => return serde_json::json!({ "id": id, "error": e }).to_string(),
        }
    };
    if is_rust_method(&method) {
        return handle_rust(id, &method, &params, engine).to_string();
    }
    match engine.python_forward(line) {
        Ok(reply) => reply.trim_end().to_string(),
        Err(e) => serde_json::json!({ "id": id, "error": e }).to_string(),
    }
}

fn serve() -> io::Result<()> {
    let stdin = io::stdin();
    let mut ctx: Option<EngineContext> = None;
    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let reply = handle_line(&line, &mut ctx);
        writeln!(io::stdout(), "{reply}")?;
        io::stdout().flush()?;
    }
    Ok(())
}

fn main() {
    if let Err(e) = serve() {
        eprintln!("fabric-desktop-engine: {e}");
        std::process::exit(1);
    }
}
