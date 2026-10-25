//! kratosforge:// links (docs/vscode.md): open a notebook or a page in KratosForge from VS Code,
//! a terminal or a browser. A link only ever shows something; nothing it says is run.
//!
//! - `kratosforge://notebook?path=<.ipynb file>` or `kratosforge://notebook?name=<notebook name>`
//! - `kratosforge://page/<page id>`
//! - `kratosforge://` alone brings the open window to the front
//!
//! A path has to be on this PC: a network path (\\host\share) is refused before anything reads
//! it, so a link can't make Windows contact another computer.
//!
//! Windows starts a new copy of the app for each link. That copy hands the link to the one that's
//! already running, over 127.0.0.1 on the port it wrote to `link-port` in the data folder, and
//! exits; the running copy answers `kratosforge-ok`, so a stale port that some other program now
//! holds is never taken for it. With no copy running, the new one starts and shows the link itself.
//! One copy runs at a time (`claim_instance`), so a link never starts a second app on the same data.
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
    Show,
}

impl Link {
    /// The link as the page receives it (the `deep-link` event).
    pub fn to_json(&self) -> String {
        let (kind, key, value) = match self {
            Link::NotebookPath(p) => ("notebook", "path", p),
            Link::NotebookName(n) => ("notebook", "name", n),
            Link::Page(p) => ("page", "page", p),
            Link::Show => return "{\"kind\":\"show\"}".into(),
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
    if target.is_empty() && query.is_empty() {
        return Ok(Link::Show);
    }
    if target.eq_ignore_ascii_case("notebook") {
        if let Some(path) = param("path")?.filter(|p| !p.is_empty()) {
            if !path.to_ascii_lowercase().ends_with(".ipynb") {
                return Err("A notebook link names an .ipynb file.".into());
            }
            if !local_path(&path) {
                return Err("A notebook link names a file on this PC, by its full path.".into());
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

/// A full path on a drive of this PC: `C:\...` or `C:/...`. Nothing else: not a network path
/// (`\\host\share`, `//host/share`, `\\host@SSL\dav`) or a device path (`\\?\`, `\\.\`),
/// which would make Windows contact that host and offer it the person's sign-in.
fn local_path(path: &str) -> bool {
    let b = path.as_bytes();
    b.len() > 2 && b[0].is_ascii_alphabetic() && b[1] == b':' && (b[2] == b'\\' || b[2] == b'/')
}

/// The first kratosforge:// link among the arguments Windows started the app with.
pub fn link_arg(args: &[String]) -> Option<String> {
    args.iter().skip(1).find(|a| a.to_ascii_lowercase().starts_with(&format!("{SCHEME}:"))).cloned()
}

/// Makes this copy the one running KratosForge: an exclusive lock on `instance.lock` in the data
/// folder, held until the returned file is dropped or the process ends. None when another copy
/// holds it.
pub fn claim_instance(data_dir: &Path) -> std::io::Result<Option<std::fs::File>> {
    std::fs::create_dir_all(data_dir)?;
    let file = std::fs::OpenOptions::new().create(true).write(true).truncate(false).open(data_dir.join("instance.lock"))?;
    match file.try_lock() {
        Ok(()) => Ok(Some(file)),
        Err(std::fs::TryLockError::WouldBlock) => Ok(None),
        Err(std::fs::TryLockError::Error(e)) => Err(e),
    }
}

/// Hands `url` to the copy of the app that's already running. True when it took it.
pub fn forward(data_dir: &Path, url: &str) -> bool {
    let Ok(text) = std::fs::read_to_string(data_dir.join("link-port")) else { return false };
    let mut parts = text.split_whitespace();
    let (Some(port), Some(token)) = (parts.next().and_then(|p| p.parse::<u16>().ok()), parts.next()) else {
        return false;
    };
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    if writeln!(stream, "{token} {}", url.replace(['\r', '\n'], "")).is_err() {
        return false;
    }
    let mut answer = String::new();
    BufReader::new(stream).read_line(&mut answer).is_ok() && answer.trim() == OK
}

/// Listens for links from other copies of the app, and writes the port they use with a random
/// token to `link-port` (in this Windows user's own data folder). A line is `<token> <link>`; one
/// without the token, or with a link that doesn't read as one, is dropped unanswered, so another
/// user's programs on the same PC can't send links.
pub fn listen(data_dir: &Path, on_link: impl Fn(Link) + Send + 'static) -> std::io::Result<()> {
    let listener = TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0)))?;
    let token = random_token();
    std::fs::create_dir_all(data_dir)?;
    std::fs::write(data_dir.join("link-port"), format!("{} {token}", listener.local_addr()?.port()))?;
    std::thread::spawn(move || {
        for mut stream in listener.incoming().flatten() {
            let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
            let mut line = String::new();
            if BufReader::new(Read::take(&mut stream, MAX_LINK as u64 + 100)).read_line(&mut line).is_err() {
                continue;
            }
            let Some((sent, url)) = line.trim().split_once(' ') else { continue };
            if sent != token {
                continue;
            }
            if let Ok(link) = parse(url) {
                let _ = writeln!(stream, "{OK}");
                on_link(link);
            }
        }
    });
    Ok(())
}

/// 128 random bits as hex, from the standard library's per-process random hash keys.
fn random_token() -> String {
    use std::hash::{BuildHasher, Hasher};
    let mut out = String::new();
    for i in 0..2u64 {
        let mut h = std::collections::hash_map::RandomState::new().build_hasher();
        h.write_u64(i);
        h.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
        out.push_str(&format!("{:016x}", h.finish()));
    }
    out
}

/// The `reg.exe delete` arguments that take the links back (policy `register_link_scheme: false`).
pub fn unregister_args() -> Vec<String> {
    vec!["delete".into(), format!("HKCU\\Software\\Classes\\{SCHEME}"), "/f".into()]
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
        assert_eq!(parse("kratosforge://").unwrap(), Link::Show);
        assert_eq!(Link::Show.to_json(), r#"{"kind":"show"}"#);
        assert_eq!(Link::NotebookPath("C:\\a \"b\".ipynb".into()).to_json(),
                   r#"{"kind":"notebook","path":"C:\\a \"b\".ipynb"}"#);
    }

    #[test]
    fn anything_else_is_refused() {
        for bad in ["https://example.com", "kratosforge://run?cell=1", "kratosforge://notebook",
                    "kratosforge://notebook?path=C:/x.exe", "kratosforge://page/../settings",
                    "kratosforge://page/Monitor", "kratosforge://notebook?name=a%0Ab", "kratosforge://notebook?name=%zz",
                    "kratosforge:", "kratosforge://?x=1"] {
            assert!(parse(bad).is_err(), "{bad}");
        }
        assert!(parse(&format!("kratosforge://notebook?name={}", "a".repeat(5000))).is_err());
    }

    #[test]
    fn a_path_has_to_be_on_this_pc() {
        for remote in [r"\\host\share\x.ipynb", "//host/share/x.ipynb", r"/\host\share\x.ipynb", r"\/host/x.ipynb",
                       r"\\?\C:\x.ipynb", r"\\.\C:\x.ipynb", r"\\?\UNC\host\share\x.ipynb", r"\x.ipynb",
                       r"\\host@SSL\dav\x.ipynb", r"\\host@SSL@443\DavWWWRoot\x.ipynb", "/home/a/x.ipynb",
                       "x.ipynb", r"notebooks\x.ipynb", "C:x.ipynb", "http://host/x.ipynb", r"1:\x.ipynb"] {
            let url = format!("kratosforge://notebook?path={}", remote.replace('\\', "%5C").replace('/', "%2F").replace(':', "%3A").replace('?', "%3F"));
            assert!(parse(&url).is_err(), "{remote}");
        }
        for raw in ["%5C%5Chost%5Cshare%5Cx.ipynb", "%5c%5chost%5cshare%5cx.ipynb", "%2F%2Fhost%2Fshare%2Fx.ipynb",
                    "//host/share/x.ipynb", r"\\host@SSL\dav\x.ipynb", "%5C%5Chost%40SSL%5Cdav%5Cx.ipynb"] {
            assert!(parse(&format!("kratosforge://notebook?path={raw}")).is_err(), "{raw}");
        }
        assert!(parse("kratosforge://notebook?path=C%3A%5CUsers%5Ca%5Cx.ipynb").is_ok());
        assert!(parse("kratosforge://notebook?path=c:/Users/a/x.ipynb").is_ok());
    }

    #[test]
    fn one_copy_runs_at_a_time() {
        let dir = std::env::temp_dir().join(format!("kf-instance-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let first = claim_instance(&dir).unwrap();
        assert!(first.is_some());
        assert!(claim_instance(&dir).unwrap().is_none(), "a second copy is refused while the first runs");
        drop(first);
        assert!(claim_instance(&dir).unwrap().is_some(), "free again once the first has closed");
        let _ = std::fs::remove_dir_all(&dir);
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
        // Without the token from link-port, nothing is taken.
        let port: u16 = std::fs::read_to_string(dir.join("link-port")).unwrap().split_whitespace().next().unwrap().parse().unwrap();
        let mut s = TcpStream::connect(SocketAddr::from(([127, 0, 0, 1], port))).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        writeln!(s, "wrong kratosforge://page/monitor").unwrap();
        let mut answer = String::new();
        let _ = BufReader::new(s).read_line(&mut answer);
        assert_ne!(answer.trim(), OK);
        assert!(rx.recv_timeout(Duration::from_millis(300)).is_err(), "nothing shown");
        assert_ne!(random_token(), random_token());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn windows_opens_links_with_this_exe_for_this_user() {
        let args = register_args(r"C:\Users\a\AppData\Local\KratosForge\kratosforge.exe");
        assert_eq!(args[0], ["add", r"HKCU\Software\Classes\kratosforge", "/ve", "/d", "URL:KratosForge link", "/f"]);
        assert_eq!(args[1][3], "URL Protocol");
        assert_eq!(args[2][1], r"HKCU\Software\Classes\kratosforge\shell\open\command");
        assert_eq!(args[2][4], r#""C:\Users\a\AppData\Local\KratosForge\kratosforge.exe" "%1""#);
        assert_eq!(unregister_args(), ["delete", r"HKCU\Software\Classes\kratosforge", "/f"]);
    }
}
