//! kratosforge:// links (docs/vscode.md): open a notebook or a page in KratosForge from VS Code,
//! a terminal or a browser. A link only ever shows something; nothing it says is run.
//!
//! - `kratosforge://notebook?path=<.ipynb file>` or `kratosforge://notebook?name=<notebook name>`
//! - `kratosforge://page/<page id>`
//!
//! Windows starts a new copy of the app for each link. That copy hands the link to the one that's
//! already running, over 127.0.0.1 on the port it wrote to `link-port` in the data folder, and
//! exits; the running copy answers `kratosforge-ok`, so a stale port that some other program now
//! holds is never taken for it. With no copy running, the new one starts and shows the link itself.
//! Standard library only, so it can be tested without the app (`rustc --test deep_link.rs`).

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::Path;
use std::time::Duration;

pub const SCHEME: &str = "kratosforge";
const OK: &str = "kratosforge-ok";
const MAX_LINK: usize = 4096;

/// What a link asks to show.
#[derive(Debug, PartialEq)]
pub enum Link {
    NotebookPath(String),
    NotebookName(String),
    Page(String),
}

impl Link {
    /// The link as the page receives it (the `deep-link` event).
    pub fn to_json(&self) -> String {
        let (kind, key, value) = match self {
            Link::NotebookPath(p) => ("notebook", "path", p),
            Link::NotebookName(n) => ("notebook", "name", n),
            Link::Page(p) => ("page", "page", p),
        };
        format!("{{\"kind\":\"{kind}\",\"{key}\":{}}}", json_string(value))
    }
}

/// Reads a kratosforge:// link. Anything else, or anything it doesn't know, is refused.
pub fn parse(url: &str) -> Result<Link, String> {
    let url = url.trim();
    if url.len() > MAX_LINK {
        return Err("The link is too long.".into());
    }
    let rest = url
        .get(..SCHEME.len() + 3)
        .filter(|p| p.eq_ignore_ascii_case(&format!("{SCHEME}://")))
        .map(|_| &url[SCHEME.len() + 3..])
        .ok_or("It isn't a kratosforge:// link.")?;
    let rest = rest.split('#').next().unwrap_or("");
    let (target, query) = rest.split_once('?').unwrap_or((rest, ""));
    let target = target.trim_end_matches('/');
    let param = |key: &str| -> Result<Option<String>, String> {
        for pair in query.split('&') {
            let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
            if k == key {
                return decode(v).map(Some);
            }
        }
        Ok(None)
    };
    if target.eq_ignore_ascii_case("notebook") {
        if let Some(path) = param("path")?.filter(|p| !p.is_empty()) {
            if !path.to_ascii_lowercase().ends_with(".ipynb") {
                return Err("A notebook link names an .ipynb file.".into());
            }
            return Ok(Link::NotebookPath(path));
        }
        if let Some(name) = param("name")?.filter(|n| !n.is_empty() && n.len() <= 300) {
            return Ok(Link::NotebookName(name));
        }
        return Err("A notebook link needs a path or a name.".into());
    }
    if let Some(page) = target.strip_prefix("page/") {
        let page = decode(page)?;
        if !page.is_empty() && page.len() <= 40 && page.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-') {
            return Ok(Link::Page(page));
        }
        return Err("That page isn't one KratosForge has.".into());
    }
    Err("KratosForge doesn't know that link.".into())
}

/// The first kratosforge:// link among the arguments Windows started the app with.
pub fn link_arg(args: &[String]) -> Option<String> {
    args.iter().skip(1).find(|a| a.to_ascii_lowercase().starts_with(&format!("{SCHEME}:"))).cloned()
}

/// Hands `url` to the copy of the app that's already running. True when it took it.
pub fn forward(data_dir: &Path, url: &str) -> bool {
    let Some(port) = std::fs::read_to_string(data_dir.join("link-port")).ok().and_then(|s| s.trim().parse::<u16>().ok()) else {
        return false;
    };
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    if writeln!(stream, "{}", url.replace(['\r', '\n'], "")).is_err() {
        return false;
    }
    let mut answer = String::new();
    BufReader::new(stream).read_line(&mut answer).is_ok() && answer.trim() == OK
}

/// Listens for links from other copies of the app, and writes the port they use. Each link that
/// reads as one goes to `on_link`; the rest are dropped.
pub fn listen(data_dir: &Path, on_link: impl Fn(Link) + Send + 'static) -> std::io::Result<()> {
    let listener = TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0)))?;
    std::fs::create_dir_all(data_dir)?;
    std::fs::write(data_dir.join("link-port"), listener.local_addr()?.port().to_string())?;
    std::thread::spawn(move || {
        for mut stream in listener.incoming().flatten() {
            let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
            let mut line = String::new();
            if BufReader::new(Read::take(&mut stream, MAX_LINK as u64 + 2)).read_line(&mut line).is_err() {
                continue;
            }
            if let Ok(link) = parse(&line) {
                let _ = writeln!(stream, "{OK}");
                on_link(link);
            }
        }
    });
    Ok(())
}

/// The `reg.exe add` arguments that make Windows open kratosforge:// links with `exe`, for this
/// Windows user only (HKCU).
pub fn register_args(exe: &str) -> Vec<Vec<String>> {
    let key = format!("HKCU\\Software\\Classes\\{SCHEME}");
    let add = |k: &str, name: Option<&str>, value: &str| {
        let mut a = vec!["add".to_string(), k.to_string()];
        match name {
            Some(n) => a.extend(["/v".to_string(), n.to_string()]),
            None => a.push("/ve".to_string()),
        }
        a.extend(["/d".to_string(), value.to_string(), "/f".to_string()]);
        a
    };
    vec![
        add(&key, None, "URL:KratosForge link"),
        add(&key, Some("URL Protocol"), ""),
        add(&format!("{key}\\shell\\open\\command"), None, &format!("\"{exe}\" \"%1\"")),
    ]
}

fn decode(s: &str) -> Result<String, String> {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' => {
                let hex = s.get(i + 1..i + 3).ok_or("The link has a broken % code.")?;
                out.push(u8::from_str_radix(hex, 16).map_err(|_| "The link has a broken % code.")?);
                i += 3;
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    let text = String::from_utf8(out).map_err(|_| "The link isn't valid text.")?;
    if text.chars().any(|c| c.is_control()) {
        return Err("The link has control characters.".into());
    }
    Ok(text)
}

fn json_string(s: &str) -> String {
    let mut out = String::from("\"");
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn notebook_and_page_links_read_as_what_they_show() {
        assert_eq!(parse("kratosforge://notebook?path=C%3A%2FUsers%2Fa%2FDaily%20sales.ipynb").unwrap(),
                   Link::NotebookPath("C:/Users/a/Daily sales.ipynb".into()));
        assert_eq!(parse("KratosForge://notebook/?name=Sales%2FDaily+load").unwrap(), Link::NotebookName("Sales/Daily load".into()));
        assert_eq!(parse("kratosforge://page/monitor").unwrap(), Link::Page("monitor".into()));
        assert_eq!(Link::NotebookPath("C:\\a \"b\".ipynb".into()).to_json(),
                   r#"{"kind":"notebook","path":"C:\\a \"b\".ipynb"}"#);
    }

    #[test]
    fn anything_else_is_refused() {
        for bad in ["https://example.com", "kratosforge://run?cell=1", "kratosforge://notebook",
                    "kratosforge://notebook?path=C:/x.exe", "kratosforge://page/../settings",
                    "kratosforge://page/Monitor", "kratosforge://notebook?name=a%0Ab", "kratosforge://notebook?name=%zz",
                    "kratosforge:"] {
            assert!(parse(bad).is_err(), "{bad}");
        }
        assert!(parse(&format!("kratosforge://notebook?name={}", "a".repeat(5000))).is_err());
    }

    #[test]
    fn only_a_kratosforge_argument_is_a_link() {
        let args = ["kratosforge.exe".to_string(), "--updated-to".into(), "abc".into(), "kratosforge://page/monitor".into()];
        assert_eq!(link_arg(&args).as_deref(), Some("kratosforge://page/monitor"));
        assert_eq!(link_arg(&args[..3]), None);
    }

    #[test]
    fn a_second_copy_hands_its_link_to_the_running_one() {
        let dir = std::env::temp_dir().join(format!("kf-link-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        assert!(!forward(&dir, "kratosforge://page/monitor"), "nothing running yet");
        let (tx, rx) = std::sync::mpsc::channel();
        listen(&dir, move |link| tx.send(link).unwrap()).unwrap();
        assert!(forward(&dir, "kratosforge://page/monitor"));
        assert_eq!(rx.recv_timeout(Duration::from_secs(5)).unwrap(), Link::Page("monitor".into()));
        assert!(!forward(&dir, "kratosforge://run?x=1"), "a link it can't read isn't taken");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn windows_opens_links_with_this_exe_for_this_user() {
        let args = register_args(r"C:\Users\a\AppData\Local\KratosForge\kratosforge.exe");
        assert_eq!(args[0], ["add", r"HKCU\Software\Classes\kratosforge", "/ve", "/d", "URL:KratosForge link", "/f"]);
        assert_eq!(args[1][3], "URL Protocol");
        assert_eq!(args[2][1], r"HKCU\Software\Classes\kratosforge\shell\open\command");
        assert_eq!(args[2][4], r#""C:\Users\a\AppData\Local\KratosForge\kratosforge.exe" "%1""#);
    }
}
