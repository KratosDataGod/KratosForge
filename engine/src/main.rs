mod protocol;
mod python;

use std::io::{self, BufRead, Write};

use protocol::{handle_rust, invalid_json_reply, is_rust_method, parse_request};
use python::PythonSidecar;

fn handle_line(line: &str, python: &mut Option<PythonSidecar>) -> String {
    let (id, method, params) = match parse_request(line) {
        Ok(parts) => parts,
        Err(e) => return invalid_json_reply(&e),
    };
    if is_rust_method(&method) {
        return handle_rust(id, &method, &params).to_string();
    }
    let sidecar = match python {
        Some(p) => p,
        None => {
            let started = PythonSidecar::start();
            match started {
                Ok(p) => {
                    *python = Some(p);
                    python.as_mut().expect("just set")
                }
                Err(e) => return serde_json::json!({ "id": id, "error": e }).to_string(),
            }
        }
    };
    match sidecar.forward(line) {
        Ok(reply) => reply.trim_end().to_string(),
        Err(e) => serde_json::json!({ "id": id, "error": e }).to_string(),
    }
}

fn serve() -> io::Result<()> {
    let stdin = io::stdin();
    let mut python: Option<PythonSidecar> = None;
    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let reply = handle_line(&line, &mut python);
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
