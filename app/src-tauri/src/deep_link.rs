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
/// Links being handed over at once; a connection beyond them is closed unread.
const MAX_HANDOVERS: usize = 8;
/// How long a connection has, in all, to send its line: one that trickles or says nothing is closed.
const HANDOVER_TIME: Duration = Duration::from_secs(2);

/// What a link asks to show.
#[derive(Debug, PartialEq)]
pub enum Link {
    NotebookPath(String),
    NotebookName(String),
    Page(String),
    Show,
    /// A link that wasn't opened, with why when this copy read it (none when it was handed over).
    Refused(String),
}

/// What a copy hands the running one for a link it refused: the page says it wasn't opened.
pub const REFUSED: &str = "kratosforge://?refused";

impl Link {
    /// The link as the page receives it (the `deep-link` event).
    pub fn to_json(&self) -> String {
        let (kind, key, value) = match self {
            Link::NotebookPath(p) => ("notebook", "path", p),
            Link::NotebookName(n) => ("notebook", "name", n),
            Link::Page(p) => ("page", "page", p),
            Link::Show => return "{\"kind\":\"show\"}".into(),
            Link::Refused(why) => ("refused", "reason", why),
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
    if target.is_empty() && query == "refused" {
        return Ok(Link::Refused(String::new()));
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
pub fn local_path(path: &str) -> bool {
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

/// The running copy's lock (claim_instance). An update lets go of it just before it starts the new
/// exe, so the new one claims it at once, and takes it back if the new one ends without starting.
pub struct InstanceLock(std::sync::Mutex<Option<std::fs::File>>);

impl InstanceLock {
    pub const fn new() -> Self {
        InstanceLock(std::sync::Mutex::new(None))
    }

    pub fn hold(&self, lock: Option<std::fs::File>) {
        *self.0.lock().unwrap_or_else(|e| e.into_inner()) = lock;
    }

    pub fn release(&self) {
        if let Some(file) = self.0.lock().unwrap_or_else(|e| e.into_inner()).take() {
            let _ = file.unlock();
        }
    }

    /// Takes the lock back (a no-op while it is held). False when another copy has it.
    pub fn reclaim(&self, data_dir: &Path) -> bool {
        let mut held = self.0.lock().unwrap_or_else(|e| e.into_inner());
        if held.is_none() {
            *held = claim_instance(data_dir).ok().flatten();
        }
        held.is_some()
    }
}

const HANDOVER: &str = "instance.handover";

/// Marks that an update is handing the lock to a new exe, so a copy a link starts meanwhile
/// leaves the lock for it. The new exe (or a rollback) clears it; one older than two minutes is
/// stale (the new exe didn't start) and doesn't count.
pub fn start_handover(data_dir: &Path) {
    let _ = std::fs::create_dir_all(data_dir);
    let _ = std::fs::write(data_dir.join(HANDOVER), b"");
}

pub fn end_handover(data_dir: &Path) {
    let _ = std::fs::remove_file(data_dir.join(HANDOVER));
}

pub fn handing_over(data_dir: &Path) -> bool {
    std::fs::metadata(data_dir.join(HANDOVER))
        .and_then(|m| m.modified())
        .is_ok_and(|t| t.elapsed().is_ok_and(|age| age < Duration::from_secs(120))) // one from the future is stale
}

/// What claim_or_forward decided.
pub enum Claim {
    /// This copy runs, with the lock (or without it when the lock can't be checked, or when a
    /// copy an update started gave up waiting for the old one).
    Run(Option<std::fs::File>),
    /// Another copy runs (it took the link, or is still starting and holds the lock): this one stops.
    Stop,
}

/// Makes this the one running copy, or hands `url` to the one that runs. A copy an update started
/// (`updating`) waits for the lock. Any other copy leaves a free lock alone while an update hands
/// it over, and once `tries` run out takes it anyway, so a link or a double-click is never lost.
pub fn claim_or_forward(data_dir: &Path, url: &str, updating: bool, tries: u32, pause: Duration) -> Claim {
    for _ in 0..tries {
        match claim_instance(data_dir) {
            Ok(Some(lock)) if updating => {
                end_handover(data_dir);
                return Claim::Run(Some(lock));
            }
            Ok(Some(lock)) if !handing_over(data_dir) => return Claim::Run(Some(lock)),
            Ok(Some(lock)) => drop(lock), // the build an update is starting takes it
            Ok(None) if !updating && forward(data_dir, url) => return Claim::Stop,
            Ok(None) => {}
            Err(_) => return Claim::Run(None),
        }
        std::thread::sleep(pause);
    }
    match claim_instance(data_dir) {
        Ok(Some(lock)) => Claim::Run(Some(lock)),
        Ok(None) if !updating && forward(data_dir, url) => Claim::Stop,
        // Another copy holds the lock and doesn't answer (it is still starting): this one stops
        // rather than run a second copy on the same data. A copy an update started runs.
        Ok(None) if !updating => Claim::Stop,
        _ => Claim::Run(None),
    }
}

/// Hands `url` to the copy of the app that's already running. True when it took it. The listener
/// first proves it knows the token (it answers a fresh nonce with `proof`), so the token and the
/// link are never sent to some other program that holds the port now, such as another Windows
/// account's on a shared PC, after the copy that wrote link-port stopped listening.
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
    let (Ok(nonce), Ok(answers)) = (random_token(), stream.try_clone()) else { return false };
    let mut answers = BufReader::new(answers);
    let mut answer = String::new();
    if writeln!(stream, "{HELLO} {nonce}").is_err() || answers.read_line(&mut answer).is_err()
        || !same_token(answer.trim(), &proof(token, &nonce)) {
        return false;
    }
    if writeln!(stream, "{token} {}", url.replace(['\r', '\n'], "")).is_err() {
        return false;
    }
    answer.clear();
    answers.read_line(&mut answer).is_ok() && answer.trim() == OK
}

/// The first line a copy handing over a link sends: `hello <nonce>`, 32 hex characters it just made.
const HELLO: &str = "hello";

/// What the listener answers a hello with: HMAC-SHA256 of the nonce, keyed with the token, in hex.
/// It shows the listener knows the token without saying anything about it.
fn proof(token: &str, nonce: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut key = [0u8; 64];
    let token = &token.as_bytes()[..token.len().min(64)];
    key[..token.len()].copy_from_slice(token);
    let pad = |with: u8| key.iter().map(|k| k ^ with).collect::<Vec<u8>>();
    let inner = Sha256::new().chain_update(pad(0x36)).chain_update(b"kratosforge-link-v1 ").chain_update(nonce.as_bytes()).finalize();
    Sha256::new().chain_update(pad(0x5c)).chain_update(inner).finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// Listens for links from other copies of the app, and writes the port they use with a random
/// token to `link-port` (in this Windows user's own data folder). A line is `<token> <link>`; one
/// without the token, or with a link that doesn't read as one, is dropped unanswered, so another
/// user's programs on the same PC can't send links. Each connection is read on its own thread and
/// has HANDOVER_TIME in all, and at most MAX_HANDOVERS are read at once, so a program that holds
/// connections open, or sends a byte at a time, can't keep a link from being handed over.
pub fn listen(data_dir: &Path, on_link: impl Fn(Link) + Send + 'static) -> std::io::Result<()> {
    let listener = TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0)))?;
    let token = std::sync::Arc::new(random_token()?);
    std::fs::create_dir_all(data_dir)?;
    std::fs::write(data_dir.join("link-port"), format!("{} {token}", listener.local_addr()?.port()))?;
    let (links, taken) = std::sync::mpsc::channel::<Link>();
    std::thread::spawn(move || {
        for link in taken {
            on_link(link); // one at a time, in the order they came
        }
    });
    let busy = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            if busy.fetch_add(1, std::sync::atomic::Ordering::SeqCst) >= MAX_HANDOVERS {
                busy.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
                continue; // closed unread
            }
            let (token, links, done) = (token.clone(), links.clone(), busy.clone());
            let started = std::thread::Builder::new().name("link-handover".into()).spawn(move || {
                take_link(stream, &token, &links);
                done.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
            });
            if started.is_err() {
                busy.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
            }
        }
    });
    Ok(())
}

/// One connection, all by HANDOVER_TIME from now: a hello it answers with its proof, then the line
/// with the token and the link; the link is taken only with the token. A copy from before the hello
/// sends the token line straight away, which is taken too.
fn take_link(mut stream: TcpStream, token: &str, links: &std::sync::mpsc::Sender<Link>) {
    let deadline = std::time::Instant::now() + HANDOVER_TIME;
    let Some(mut line) = read_line_by(&mut stream, deadline, MAX_LINK + 100) else { return };
    if let Some(nonce) = line.trim().strip_prefix(HELLO).and_then(|rest| rest.strip_prefix(' ')) {
        if nonce.len() != 32 || !nonce.bytes().all(|b| b.is_ascii_hexdigit()) || writeln!(stream, "{}", proof(token, nonce)).is_err() {
            return;
        }
        let Some(next) = read_line_by(&mut stream, deadline, MAX_LINK + 100) else { return };
        line = next;
    }
    let Some((sent, url)) = line.trim().split_once(' ') else { return };
    if !same_token(sent, token) {
        return;
    }
    if let Ok(link) = parse(url) {
        let _ = writeln!(stream, "{OK}");
        let _ = links.send(link);
    }
}

/// The first line `stream` sends (or all it sends before it closes), if that comes by `deadline`
/// and is at most `limit` bytes of text. Each read waits only for what's left of the time.
fn read_line_by(stream: &mut TcpStream, deadline: std::time::Instant, limit: usize) -> Option<String> {
    let mut line = Vec::new();
    let mut chunk = [0u8; 512];
    loop {
        let left = deadline.checked_duration_since(std::time::Instant::now()).filter(|d| !d.is_zero())?;
        stream.set_read_timeout(Some(left)).ok()?;
        let n = stream.read(&mut chunk).ok()?;
        if n == 0 {
            break;
        }
        line.extend_from_slice(&chunk[..n]);
        if let Some(end) = line.iter().position(|&b| b == b'\n') {
            line.truncate(end);
            break;
        }
        if line.len() > limit {
            return None;
        }
    }
    if line.len() > limit {
        return None;
    }
    String::from_utf8(line).ok()
}

/// For an organization that turned links off (policy `register_link_scheme: false`), or when this
/// copy couldn't listen: the port an earlier run wrote is removed, so a copy started later never sends its token to a
/// port some other program may hold now.
pub fn forget_port(data_dir: &Path) {
    let _ = std::fs::remove_file(data_dir.join("link-port"));
}

/// 128 random bits from the operating system's random source, as 32 hex characters. If the OS
/// can't supply them, nothing listens: links then open a new copy, as when no copy is running.
fn random_token() -> std::io::Result<String> {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).map_err(|e| std::io::Error::other(format!("no random token: {e}")))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// Whether `sent` is the token, taking the same time whichever character differs, so the time an
/// answer takes says nothing about how much of a guess was right.
fn same_token(sent: &str, token: &str) -> bool {
    sent.len() == token.len() && sent.bytes().zip(token.bytes()).fold(0u8, |diff, (a, b)| diff | (a ^ b)) == 0
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
    fn a_refused_link_is_handed_over_without_its_text() {
        assert_eq!(parse(REFUSED).unwrap(), Link::Refused(String::new()));
        assert!(parse("kratosforge://?refused=anything").is_err(), "no text from a link reaches the page");
        assert_eq!(Link::Refused("Too long.".into()).to_json(), r#"{"kind":"refused","reason":"Too long."}"#);
    }

    #[test]
    fn a_link_during_an_update_waits_for_the_new_copy() {
        let dir = std::env::temp_dir().join(format!("kf-updating-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        assert!(!handing_over(&dir));
        start_handover(&dir);
        assert!(handing_over(&dir), "a copy started by a link leaves the lock to the --updated-to copy");
        let marker = std::fs::File::options().write(true).open(dir.join(HANDOVER)).unwrap();
        marker.set_modified(std::time::SystemTime::now() + Duration::from_secs(3600)).unwrap();
        assert!(!handing_over(&dir), "a marker from the future doesn't hold links back");
        marker.set_modified(std::time::SystemTime::now() - Duration::from_secs(300)).unwrap();
        assert!(!handing_over(&dir), "nor one an update left behind");
        drop(marker);
        end_handover(&dir);
        assert!(!handing_over(&dir));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_copy_takes_the_lock_once_a_handover_is_overdue() {
        let dir = std::env::temp_dir().join(format!("kf-overdue-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        start_handover(&dir);
        let started = std::time::Instant::now();
        let Claim::Run(Some(lock)) = claim_or_forward(&dir, "kratosforge://", false, 4, Duration::from_millis(10)) else {
            panic!("a link or double-click during a handover that never finishes still starts the app");
        };
        assert!(started.elapsed() >= Duration::from_millis(40), "it waited for the new build first");
        assert!(matches!(claim_or_forward(&dir, "kratosforge://", false, 2, Duration::from_millis(1)), Claim::Stop));
        drop(lock);
        end_handover(&dir);
        assert!(matches!(claim_or_forward(&dir, "kratosforge://", true, 1, Duration::ZERO), Claim::Run(Some(_))));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_update_hands_the_lock_to_the_new_copy() {
        let dir = std::env::temp_dir().join(format!("kf-handover-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let old = InstanceLock::new();
        old.hold(claim_instance(&dir).unwrap());
        assert!(old.reclaim(&dir), "already held");
        old.release();
        let started = std::time::Instant::now();
        let new = claim_instance(&dir).unwrap();
        assert!(new.is_some() && started.elapsed() < Duration::from_millis(100), "the --updated-to copy doesn't wait");
        assert!(!old.reclaim(&dir), "the old copy can't take it while the new one runs");
        drop(new);
        assert!(old.reclaim(&dir), "back to the old copy when the new one ended without starting");
        assert!(claim_instance(&dir).unwrap().is_none());
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
        let _ = std::fs::remove_dir_all(&dir);
    }

    fn port_and_token(dir: &Path) -> (SocketAddr, String) {
        let text = std::fs::read_to_string(dir.join("link-port")).unwrap();
        let (port, token) = text.split_once(' ').unwrap();
        (SocketAddr::from(([127, 0, 0, 1], port.parse::<u16>().unwrap())), token.trim().to_string())
    }

    #[test]
    fn malformed_links_are_refused_without_a_panic() {
        // A fixed seed, so a failure names the same input every run.
        let mut seed: u64 = 0x5eed_1e55;
        let mut next = move || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        let pieces = ["kratosforge://", "notebook", "page/", "?", "&", "=", "path=", "name=", "%", "%2", "%2e", "%2E%2E", "%5c",
                      "%2f", "%00", "%0a", "%ff", "%c3", "%c3%a9", "é", "\u{202e}", "..", "/", "\\", "#", "+", "C:", ".ipynb",
                      "\\\\?\\", "\\\\.\\", "//host/", "UNC", "@SSL", " ", "\t", "\u{0}"];
        for _ in 0..20_000 {
            let mut url = String::new();
            for _ in 0..(next() % 12) {
                url.push_str(pieces[(next() % pieces.len() as u64) as usize]);
            }
            match parse(&url) {
                Ok(Link::NotebookPath(p)) => assert!(local_path(&p) && p.to_ascii_lowercase().ends_with(".ipynb")
                                                     && !p.chars().any(char::is_control), "{url:?} -> {p:?}"),
                Ok(Link::NotebookName(n)) => assert!(!n.is_empty() && n.len() <= 300 && !n.chars().any(char::is_control), "{url:?}"),
                Ok(Link::Page(p)) => assert!(p.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-'), "{url:?}"),
                Ok(_) | Err(_) => {}
            }
        }
        for bad in ["kratosforge://notebook?path=%", "kratosforge://notebook?path=%2", "kratosforge://notebook?path=%e9.ipynb",
                    "kratosforge://notebook?path=C%3A%5Cx%00.ipynb", "kratosforge://page/%2e%2e%2fsettings",
                    "kratosforge://page/%2E%2E", "kratosforge://notebook?path=%5C%5C%3F%5CC%3A%5Cx.ipynb",
                    "kratosforge://notebook?path=%5C%5C.%5CC%3A%5Cx.ipynb", "kratosforge://notebook?path=%2F%2Fhost%2Fs%2Fx.ipynb",
                    "kratosforge://notebook?path=C%3A%5Cx.ipynb%00", "kratosforge://notebook?name=%e2%80%ae"] {
            assert!(!matches!(parse(bad), Ok(Link::NotebookPath(_)) | Ok(Link::Page(_))), "{bad}");
        }
        assert!(parse(&format!("kratosforge://notebook?path=C:/{}.ipynb", "a".repeat(MAX_LINK))).is_err(), "too long");
        // Encoded ".." still names a full local path; which folder it lands in is the engine's to check.
        assert_eq!(parse("kratosforge://notebook?path=C%3A%5Cn%5C%2e%2e%5Cx.ipynb").unwrap(), Link::NotebookPath(r"C:\n\..\x.ipynb".into()));
    }

    #[test]
    fn a_link_is_handed_over_while_other_programs_hold_connections_open_or_trickle() {
        let dir = std::env::temp_dir().join(format!("kf-flood-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let (tx, rx) = std::sync::mpsc::channel();
        listen(&dir, move |link| tx.send(link).unwrap()).unwrap();
        let (addr, token) = port_and_token(&dir);
        // Idle connections, more than are read at once, and one that sends a byte every half second.
        let idle: Vec<TcpStream> = (0..MAX_HANDOVERS * 4).filter_map(|_| TcpStream::connect(addr).ok()).collect();
        let trickler = std::thread::spawn(move || {
            let Ok(mut s) = TcpStream::connect(addr) else { return 0 };
            let mut sent = 0;
            while sent < 40 && s.write_all(b"a").is_ok() {
                sent += 1;
                std::thread::sleep(Duration::from_millis(500));
            }
            sent
        });
        // Within a few deadlines the held connections are closed and a real link goes through.
        let started = std::time::Instant::now();
        let mut handed = false;
        while !handed && started.elapsed() < HANDOVER_TIME * 5 {
            handed = forward(&dir, "kratosforge://page/monitor");
        }
        assert!(handed, "a link got through in {:?}", started.elapsed());
        assert_eq!(rx.recv_timeout(Duration::from_secs(5)).unwrap(), Link::Page("monitor".into()));
        assert!(trickler.join().unwrap() < 40, "the trickling connection was closed before it finished");
        drop(idle);
        // Wrong, missing and over-long lines are dropped, and nothing reaches the page.
        for line in [format!("{} kratosforge://page/monitor\n", "0".repeat(32)), "kratosforge://page/monitor\n".into(),
                     format!("{token}\n"), format!("{token} kratosforge://page/{}\n", "a".repeat(MAX_LINK * 2)),
                     format!("{token} https://example.com\n"),
                     format!("{token} \u{0}kratosforge://page/monitor\n"), "\u{ff}\u{fe}\n".into()] {
            let mut s = TcpStream::connect(addr).unwrap();
            s.set_read_timeout(Some(HANDOVER_TIME * 2)).unwrap();
            let _ = s.write_all(line.as_bytes());
            let mut answer = String::new();
            let _ = BufReader::new(s).read_line(&mut answer);
            assert_ne!(answer.trim(), OK, "{:?}", &line[..line.len().min(60)]);
        }
        let mut s = TcpStream::connect(addr).unwrap();
        let _ = s.write_all(&[0xff; 64]); // not text
        drop(s);
        assert!(rx.recv_timeout(Duration::from_millis(500)).is_err(), "nothing shown");
        assert!(forward(&dir, "kratosforge://page/monitor"), "and it still takes a real one");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_token_goes_only_to_a_listener_that_proves_it_knows_it() {
        let dir = std::env::temp_dir().join(format!("kf-squat-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        // Another program holds the port link-port names: it reads what comes, and answers anything.
        let squatter = TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0))).unwrap();
        let port = squatter.local_addr().unwrap().port();
        let token = random_token().unwrap();
        std::fs::write(dir.join("link-port"), format!("{port} {token}")).unwrap();
        let heard = std::thread::spawn(move || {
            let mut heard = String::new();
            // Garbage, the nonce echoed back, a proof-shaped guess, nothing at all, and the old protocol's OK.
            for answer in ["wrong", "echo", &"0".repeat(64), "silent", OK] {
                let Ok((s, _)) = squatter.accept() else { break };
                s.set_read_timeout(Some(Duration::from_secs(4))).unwrap();
                let mut reader = BufReader::new(s.try_clone().unwrap());
                let mut line = String::new();
                let _ = reader.read_line(&mut line);
                heard.push_str(&line);
                match answer {
                    "echo" => { let _ = writeln!(&s, "{}", line.trim().trim_start_matches(HELLO).trim()); }
                    "silent" => {}
                    other => { let _ = writeln!(&s, "{other}"); }
                }
                line.clear();
                let _ = reader.read_line(&mut line);
                heard.push_str(&line);
            }
            heard
        });
        for _ in 0..5 {
            let started = std::time::Instant::now();
            assert!(!forward(&dir, "kratosforge://notebook?path=C%3A%5Csecret.ipynb"));
            assert!(started.elapsed() < Duration::from_secs(4), "a silent listener is given up on");
        }
        let heard = heard.join().unwrap();
        assert!(!heard.contains(&token) && !heard.contains("secret"), "{heard}");
        assert_eq!(heard.matches(HELLO).count(), 5, "only hellos: {heard}");
        let nonces: std::collections::BTreeSet<&str> = heard.lines().filter_map(|l| l.strip_prefix("hello ")).collect();
        assert_eq!(nonces.len(), 5, "a new nonce each time");
        assert!(nonces.iter().all(|n| n.len() == 32 && n.bytes().all(|b| b.is_ascii_hexdigit())));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_proof_is_hmac_sha256_and_a_bad_hello_gets_nothing() {
        // RFC 4231 test case 2, through the same construction.
        use sha2::{Digest, Sha256};
        let mut key = [0u8; 64];
        key[..4].copy_from_slice(b"Jefe");
        let pad = |with: u8| key.iter().map(|k| k ^ with).collect::<Vec<u8>>();
        let inner = Sha256::new().chain_update(pad(0x36)).chain_update(b"what do ya want for nothing?").finalize();
        let mac: String = Sha256::new().chain_update(pad(0x5c)).chain_update(inner).finalize().iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(mac, "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
        let (t, n) = (random_token().unwrap(), random_token().unwrap());
        assert_eq!(proof(&t, &n).len(), 64);
        assert_eq!(proof(&t, &n), proof(&t, &n));
        assert_ne!(proof(&t, &n), proof(&random_token().unwrap(), &n));
        assert_ne!(proof(&t, &n), proof(&t, &random_token().unwrap()));
        // The listener answers only a well-formed hello, and still wants the token after it.
        let dir = std::env::temp_dir().join(format!("kf-hello-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let (tx, rx) = std::sync::mpsc::channel();
        listen(&dir, move |link| tx.send(link).unwrap()).unwrap();
        let (addr, token) = port_and_token(&dir);
        for hello in ["hello\n", "hello \n", "hello zz\n", "hello 0123\n", "helloabcdef0123456789abcdef0123456789\n"] {
            let mut s = TcpStream::connect(addr).unwrap();
            s.set_read_timeout(Some(HANDOVER_TIME * 2)).unwrap();
            s.write_all(hello.as_bytes()).unwrap();
            let mut answer = String::new();
            let _ = BufReader::new(s).read_line(&mut answer);
            assert!(answer.trim().is_empty(), "{hello:?} got {answer:?}");
        }
        let nonce = random_token().unwrap();
        let mut s = TcpStream::connect(addr).unwrap();
        s.set_read_timeout(Some(HANDOVER_TIME * 2)).unwrap();
        let mut answers = BufReader::new(s.try_clone().unwrap());
        writeln!(s, "{HELLO} {nonce}").unwrap();
        let mut answer = String::new();
        answers.read_line(&mut answer).unwrap();
        assert_eq!(answer.trim(), proof(&token, &nonce));
        writeln!(s, "{} kratosforge://page/monitor", "0".repeat(32)).unwrap();
        answer.clear();
        let _ = answers.read_line(&mut answer);
        assert_ne!(answer.trim(), OK, "a hello doesn't stand in for the token");
        assert!(rx.recv_timeout(Duration::from_millis(300)).is_err());
        // A copy from before the hello still hands its link straight over.
        let mut s = TcpStream::connect(addr).unwrap();
        s.set_read_timeout(Some(HANDOVER_TIME * 2)).unwrap();
        writeln!(s, "{token} kratosforge://page/monitor").unwrap();
        answer.clear();
        let _ = BufReader::new(s).read_line(&mut answer);
        assert_eq!(answer.trim(), OK);
        assert_eq!(rx.recv_timeout(Duration::from_secs(5)).unwrap(), Link::Page("monitor".into()));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_token_is_32_hex_characters_and_new_each_time() {
        let (a, b) = (random_token().unwrap(), random_token().unwrap());
        assert_eq!(a.len(), 32);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()), "{a}");
        assert_ne!(a, b);
    }

    #[test]
    fn only_the_whole_token_is_taken() {
        let token = random_token().unwrap();
        assert!(same_token(&token, &token));
        let mut wrong = token.clone().into_bytes();
        wrong[31] = if wrong[31] == b'0' { b'1' } else { b'0' };
        assert!(!same_token(std::str::from_utf8(&wrong).unwrap(), &token), "same length, one character off");
        assert!(!same_token(&token[..31], &token), "shorter");
        assert!(!same_token(&format!("{token}0"), &token), "longer");
        assert!(!same_token("", &token));
    }

    #[test]
    fn with_links_turned_off_a_second_copy_has_nothing_to_hand_its_link_to() {
        let dir = std::env::temp_dir().join(format!("kf-nolinks-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        listen(&dir, |_| {}).unwrap();
        assert!(forward(&dir, "kratosforge://page/monitor"), "listening");
        forget_port(&dir);
        assert!(!dir.join("link-port").exists());
        assert!(!forward(&dir, "kratosforge://page/monitor"), "no port, no handover");
        forget_port(&dir); // nothing to remove is fine
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
