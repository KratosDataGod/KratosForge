// VS Code links the shell opens. "Add to VS Code" in Settings > Integrations: the link VS Code's own MCP installer opens,
// vscode:mcp/install?<URL-encoded JSON server entry> (vscode-insiders: for Insiders). VS Code
// shows the entry and asks before it adds it. The shell builds the whole link itself from a
// client id and the launcher's fixed path, so the page can't make it open any other link.
// "Open in VS Code" on a notebook: vscode://file/<path> (open_link). Only std, so it is tested
// without a window or Windows.

use std::path::Path;

/// The server name VS Code lists, the same as the snippets' (integrations/config.py SERVER_NAME).
pub const SERVER_NAME: &str = "fabric-desktop";

/// A connected tool's id as the engine makes them: letters, digits, "-" and "_", at most 64.
pub fn valid_client_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

fn json_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// encodeURIComponent: everything but A-Z a-z 0-9 - _ . ! ~ * ' ( ) as UTF-8 %XX.
fn encode_component(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 3);
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || b"-_.!~*'()".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// The install link for the launcher at `launcher`, for the connected tool `client_id`.
pub fn install_link(launcher: &Path, client_id: &str, insiders: bool) -> Result<String, String> {
    if !valid_client_id(client_id) {
        return Err("That isn't a connected tool's id.".into());
    }
    let command = launcher.to_str().ok_or("The launcher's path isn't valid text.")?;
    let entry = format!(
        "{{\"name\":{},\"type\":\"stdio\",\"command\":{},\"args\":[\"--client\",{}]}}",
        json_string(SERVER_NAME),
        json_string(command),
        json_string(client_id)
    );
    let scheme = if insiders { "vscode-insiders" } else { "vscode" };
    Ok(format!("{scheme}:mcp/install?{}", encode_component(&entry)))
}

/// The link that opens a notebook file, or a folder, in VS Code: vscode://file/<path>, with "\\"
/// as "/" and everything but letters, digits, "/", ":" and "-_.~" percent-encoded.
pub fn open_link(path: &Path, insiders: bool) -> Result<String, String> {
    let text = path.to_str().ok_or("The path isn't valid text.")?.replace('\\', "/");
    if !(text.starts_with('/') || text.as_bytes().get(1) == Some(&b':')) {
        return Err("Only a full path can be opened in VS Code.".into());
    }
    let mut out = String::new();
    for b in text.bytes() {
        if b.is_ascii_alphanumeric() || b"/:-_.~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    let scheme = if insiders { "vscode-insiders" } else { "vscode" };
    Ok(format!("{scheme}://file/{}", out.trim_start_matches('/')))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn decode(s: &str) -> String {
        let b = s.as_bytes();
        let mut out = Vec::new();
        let mut i = 0;
        while i < b.len() {
            if b[i] == b'%' {
                out.push(u8::from_str_radix(&s[i + 1..i + 3], 16).unwrap());
                i += 3;
            } else {
                out.push(b[i]);
                i += 1;
            }
        }
        String::from_utf8(out).unwrap()
    }

    #[test]
    fn link_carries_the_launcher_and_client_as_vscode_expects() {
        let launcher = PathBuf::from(r"C:\Users\Chris Smith\AppData\Local\FabricDesktop\bin\fabric-desktop-mcp.cmd");
        let link = install_link(&launcher, "cl_7f3a-B", false).unwrap();
        let (scheme, query) = link.split_once('?').unwrap();
        assert_eq!(scheme, "vscode:mcp/install");
        assert!(!query.contains(' ') && !query.contains('"') && !query.contains('\\') && !query.contains('+'));
        assert_eq!(
            decode(query),
            r#"{"name":"fabric-desktop","type":"stdio","command":"C:\\Users\\Chris Smith\\AppData\\Local\\FabricDesktop\\bin\\fabric-desktop-mcp.cmd","args":["--client","cl_7f3a-B"]}"#
        );
        assert!(install_link(&launcher, "cl_1", true).unwrap().starts_with("vscode-insiders:mcp/install?"));
    }

    #[test]
    fn only_a_client_id_can_go_in_the_link() {
        let launcher = PathBuf::from("/tmp/fabric-desktop-mcp");
        for bad in ["", "a\"b", "a b", "a&b", "x/../y", &"a".repeat(65)] {
            assert!(install_link(&launcher, bad, false).is_err(), "{bad}");
        }
    }

    #[test]
    fn file_links_use_forward_slashes_and_encode_the_rest() {
        let link = open_link(&PathBuf::from(r"C:\Users\Chris Smith\notebooks\Sales #1.ipynb"), false).unwrap();
        assert_eq!(link, "vscode://file/C:/Users/Chris%20Smith/notebooks/Sales%20%231.ipynb");
        assert_eq!(open_link(&PathBuf::from("/home/c/nb"), true).unwrap(), "vscode-insiders://file/home/c/nb");
        assert!(open_link(&PathBuf::from("notebooks/x.ipynb"), false).is_err());
    }

    #[test]
    fn non_ascii_paths_are_encoded_as_utf8() {
        let link = install_link(&PathBuf::from(r"C:\Users\José\bin\x.cmd"), "c", false).unwrap();
        assert!(link.contains("Jos%C3%A9"));
    }
}
