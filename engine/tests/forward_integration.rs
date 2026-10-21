use std::io::Write;
use std::process::{Command, Stdio};

#[test]
fn forwards_app_settings_to_python() {
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
    let req = r#"{"id":1,"method":"app_settings","params":{}}"#;
    child.stdin.as_mut().unwrap().write_all(format!("{req}\n").as_bytes()).unwrap();
    let out = std::io::read_to_string(child.stdout.take().unwrap()).unwrap();
    let value: serde_json::Value = serde_json::from_str(out.lines().next().unwrap_or("{}")).unwrap();
    assert!(value.get("result").is_some(), "expected result, got {value}");
    assert!(value.get("error").is_none());
}
