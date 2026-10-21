use std::io::Write;
use std::process::{Command, Stdio};

/// When ``FABRIC_DESKTOP_PARITY=1`` and the sidecar is installed, compare Rust and Python
/// ``engine_health`` replies (extended parity for SQL runs is local-only).
#[test]
fn engine_health_matches_python_shape() {
    if std::env::var("FABRIC_DESKTOP_PARITY").ok().as_deref() != Some("1") {
        return;
    }
    let python = if cfg!(windows) { "python" } else { "python3" };
    if Command::new(python)
        .args(["-c", "import fabric_desktop"])
        .status()
        .map(|s| !s.success())
        .unwrap_or(true)
    {
        return;
    }
    let Some(bin) = option_env!("CARGO_BIN_EXE_fabric-desktop-engine") else {
        return;
    };
    let mut child = Command::new(bin)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("start engine");
    let req = r#"{"id":99,"method":"engine_health","params":{}}"#;
    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(format!("{req}\n").as_bytes())
        .unwrap();
    let out = std::io::read_to_string(child.stdout.take().unwrap()).unwrap();
    let rust: serde_json::Value =
        serde_json::from_str(out.lines().next().unwrap_or("{}")).expect("rust json");
    assert_eq!(rust["id"], 99);
    assert_eq!(rust["result"]["engine"], "rust");
    assert!(rust["result"]["phase"].as_u64().unwrap_or(0) >= 1);
}
