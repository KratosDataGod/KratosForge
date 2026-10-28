// Fabric Desktop shell: hosts the UI and forwards calls to the Python sidecar,
// which does all data work on this machine.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::{BTreeMap, VecDeque};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tauri::webview::{NewWindowResponse, WebviewBuilder};
use tauri::{Emitter, LogicalPosition, LogicalSize, Manager, Url, WebviewUrl};

mod deep_link;
mod engine_patch;
mod exe_name;
mod folder_pick;
mod policy_file;
mod policy_trust;
mod replies;
mod update_guard;
mod vscode_link;
mod watchdog;
use watchdog::Reply;

const SIDECAR_EXITED: &str = "The Python sidecar stopped. It will restart on the next action.";
const SIDECAR_STUCK: &str = "The engine stopped answering, so KratosForge restarted it.";

/// Startup check and updater, run with the user's Python before the sidecar starts.
const UPDATER: &str = include_str!("updater.py");

/// The main commit this exe was built from; empty for local builds, which skip updates.
const BUILD_COMMIT: &str = env!("FABRIC_BUILD_COMMIT");

/// The app (client) ID CI built in; the sidecar uses it unless policy or Settings name another.
const BUILTIN_CLIENT_ID: &str = env!("FABRIC_BUILTIN_CLIENT_ID");
/// The SHA-256 of this build's own engine zip and engine update zip, which CI builds before the
/// exe and bakes in (build.rs); empty in local builds. The engine for this build must match them
/// wherever it was downloaded from, so a changed copy on GitHub or in IT's own folder
/// (engine_download_url) is refused even with a version.json made to match it.
const BUILD_ENGINE_SHA256: &str = env!("FABRIC_BUILD_ENGINE_SHA256");
const BUILD_ENGINE_UPDATE_SHA256: &str = env!("FABRIC_BUILD_ENGINE_UPDATE_SHA256");

/// Reads one key of a settings file's text; None when the text isn't JSON or the key is missing.
fn setting(text: &str, key: &str) -> Option<String> {
    let value: Value = serde_json::from_str(text.trim_start_matches('\u{feff}')).ok()?;
    value.get(key)?.as_str().map(str::to_owned)
}

/// The organization's policy file (%ProgramData%\FabricDesktop\policy.json), or when it can't be
/// read, the last copy the sidecar could read (policy_file.rs); empty for no policy.
fn policy_text() -> String {
    let Some(dir) = std::env::var_os("ProgramData") else { return String::new() };
    policy_file::text(&PathBuf::from(dir).join("FabricDesktop").join("policy.json"), &data_dir().join("policy-last-good.json"))
}

/// Whether the app may update itself. An organization that rolls out builds itself sets
/// `"auto_update": false` in its policy file; only that file can turn updates off.
fn auto_update_allowed() -> bool {
    policy_allows_updates(&policy_text())
}

/// Whether the app registers kratosforge:// links: only `"register_link_scheme": false` in the
/// policy file stops it.
fn policy_allows_link_scheme(text: &str) -> bool {
    let value: Value = serde_json::from_str(text.trim_start_matches('\u{feff}')).unwrap_or(Value::Null);
    value.get("register_link_scheme").and_then(Value::as_bool) != Some(false)
}

fn policy_allows_updates(text: &str) -> bool {
    let value: Value = serde_json::from_str(text.trim_start_matches('\u{feff}')).unwrap_or(Value::Null);
    value.get("auto_update").and_then(Value::as_bool) != Some(false)
}

/// Where IT keeps this build's engine, from `"engine_download_url"` in the policy file: an https
/// folder holding the files of this build's `build-*` release, copied unchanged, for networks that
/// can't reach GitHub. Used only with `"auto_update": false`, since only then is the engine one
/// known build; its version.json must still name this build (check_engine_build) and the zip must
/// match its checksum. Those checks come from the same folder, so the folder is trusted only as far
/// as the policy file: it is read from policy.json itself (never the per-user last good copy), and
/// only when a standard user couldn't have written that file or its folder (policy_trust).
fn engine_mirror() -> Option<String> {
    engine_mirror_state().unwrap_or_else(|why| {
        eprintln!("engine_download_url ignored, so the engine comes from GitHub: {why}");
        None
    })
}

/// The engine folder to use, or why a valid one in a managed policy file is ignored. The file's
/// permissions are checked before it is read, so what is read is what only administrators wrote.
fn engine_mirror_state() -> Result<Option<String>, String> {
    let Some(dir) = std::env::var_os("ProgramData") else { return Ok(None) };
    let file = PathBuf::from(dir).join("FabricDesktop").join("policy.json");
    let trusted = policy_trust::admin_only(&file);
    let text = std::fs::read_to_string(&file).unwrap_or_default();
    if policy_allows_updates(&text) {
        return Ok(None);
    }
    match (policy_engine_url(&text), trusted) {
        (Some(url), Ok(())) => Ok(Some(url)),
        (Some(_), Err(why)) => Err(why),
        (None, _) => Ok(None),
    }
}

/// `engine_download_url` when it is an https address with a host name and nothing else that could
/// change where it leads (no user name, query, fragment, backslash or space), without its trailing /.
fn policy_engine_url(text: &str) -> Option<String> {
    let value: Value = serde_json::from_str(text.trim_start_matches('\u{feff}')).ok()?;
    let url = value.get("engine_download_url")?.as_str()?.trim().trim_end_matches('/');
    let rest = url.strip_prefix("https://")?;
    let authority = rest.split('/').next().unwrap_or("");
    let (host, port) = authority.split_once(':').unwrap_or((authority, "443"));
    let port_ok = !port.is_empty() && port.len() <= 5 && port.bytes().all(|b| b.is_ascii_digit());
    let clean = url.is_ascii() && !url.bytes().any(|b| b.is_ascii_control() || b" ?#@\\%".contains(&b));
    (clean && port_ok && dns_name(host)).then(|| url.to_owned())
}

/// Extra hosts the Fabric view may open for sign-in: the organization's own identity provider
/// (AD FS, Okta, Ping, ...), from `"fabric_view_sign_in_hosts"` in the policy file. Only that
/// file can set it; a missing or unreadable file means none (its last good copy isn't used).
fn fabric_view_sign_in_hosts() -> Vec<String> {
    let Some(dir) = std::env::var_os("ProgramData") else { return Vec::new() };
    policy_sign_in_hosts(&std::fs::read_to_string(PathBuf::from(dir).join("FabricDesktop").join("policy.json")).unwrap_or_default())
}

/// The valid entries of `fabric_view_sign_in_hosts`, lower-cased; invalid ones are skipped.
fn policy_sign_in_hosts(text: &str) -> Vec<String> {
    let value: Value = serde_json::from_str(text.trim_start_matches('\u{feff}')).unwrap_or(Value::Null);
    let Some(list) = value.get("fabric_view_sign_in_hosts").and_then(Value::as_array) else { return Vec::new() };
    list.iter().filter_map(Value::as_str).filter(|h| dns_name(h)).map(str::to_ascii_lowercase).collect()
}

/// An exact DNS host name: two or more labels of letters, digits and inner hyphens, each at most
/// 63 long, 253 in all, not ending in a number (so no IP address). No wildcard, scheme, port,
/// path, user name or trailing dot; a non-ASCII name must be given in its xn-- form.
fn dns_name(host: &str) -> bool {
    let labels: Vec<&str> = host.split('.').collect();
    host.len() <= 253
        && labels.len() >= 2
        && labels.iter().all(|l| {
            !l.is_empty() && l.len() <= 63 && !l.starts_with('-') && !l.ends_with('-')
                && l.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
        })
        && !labels.last().is_some_and(|l| l.bytes().all(|b| b.is_ascii_digit()))
}

#[cfg(test)]
mod policy_tests {
    use super::{policy_allows_link_scheme, policy_allows_updates, policy_engine_url};

    #[test]
    fn engine_download_url_is_an_https_folder_or_nothing() {
        let url = |v: &str| policy_engine_url(&format!(r#"{{"engine_download_url": {v}}}"#));
        assert_eq!(url(r#""https://files.contoso.com/kratosforge/build-1/""#).as_deref(),
                   Some("https://files.contoso.com/kratosforge/build-1"));
        assert_eq!(url(r#""https://files.contoso.com:8443/kf""#).as_deref(), Some("https://files.contoso.com:8443/kf"));
        for bad in [r#""http://files.contoso.com/kf""#, r#""\\\\server\\share""#, r#""file:///C:/kf""#,
                    r#""https://user:pw@files.contoso.com/kf""#, r#""https://files.contoso.com/kf?sig=x""#,
                    r#""https://files.contoso.com/kf#x""#, r#""https://10.0.0.5/kf""#, r#""https:///kf""#,
                    r#""https://files.contoso.com:/kf""#, r#""https://files.contoso.com/a b""#,
                    r#""https://files.contoso.com/%2e%2e""#, r#""https://files.contoso.com\\kf""#, "true", "[]"] {
            assert_eq!(url(bad), None, "{bad}");
        }
        assert_eq!(policy_engine_url("{}"), None);
        assert_eq!(policy_engine_url("not json"), None);
    }

    #[test]
    fn only_policy_false_stops_the_link_scheme() {
        assert!(policy_allows_link_scheme(""));
        assert!(policy_allows_link_scheme("{}"));
        assert!(policy_allows_link_scheme(r#"{"register_link_scheme": "false"}"#));
        assert!(!policy_allows_link_scheme(r#"{"register_link_scheme": false}"#));
    }

    #[test]
    fn only_policy_false_stops_updates() {
        assert!(policy_allows_updates(""));
        assert!(policy_allows_updates("{}"));
        assert!(policy_allows_updates("not json"));
        assert!(policy_allows_updates(r#"{"auto_update": "false"}"#));
        assert!(policy_allows_updates(r#"{"auto_update": true}"#));
        assert!(!policy_allows_updates(r#"{"auto_update": false}"#));
        assert!(!policy_allows_updates("\u{feff}{\"auto_update\": false}"));
    }

    #[test]
    fn sign_in_hosts_keep_only_exact_dns_names() {
        let text = r#"{"fabric_view_sign_in_hosts": [
            "adfs.contoso.com", "Contoso.Okta.com", "sso.login-1.example.co.uk", "xn--bcher-kva.example",
            "*.okta.com", "https://adfs.contoso.com", "adfs.contoso.com:443", "adfs.contoso.com/adfs/ls",
            "user@adfs.contoso.com", "adfs.contoso.com.", ".contoso.com", "localhost", "10.0.0.5",
            "-adfs.contoso.com", "adfs-.contoso.com", "adfs..contoso.com", "adfs contoso.com", "bücher.example",
            "", 42, null, ["adfs.contoso.com"]
        ]}"#;
        assert_eq!(
            super::policy_sign_in_hosts(text),
            ["adfs.contoso.com", "contoso.okta.com", "sso.login-1.example.co.uk", "xn--bcher-kva.example"]
        );
        assert!(super::dns_name(&format!("{}.com", "a".repeat(63))));
        assert!(!super::dns_name(&format!("{}.com", "a".repeat(64))));
        assert!(!super::dns_name(&format!("{}com", "a.".repeat(126))));
        assert_eq!(super::policy_sign_in_hosts("\u{feff}{\"fabric_view_sign_in_hosts\": [\"adfs.contoso.com\"]}"), ["adfs.contoso.com"]);
    }

    #[test]
    fn no_sign_in_hosts_from_a_missing_broken_or_wrongly_typed_file() {
        for text in ["", "not json", "{", "[]", "{}", r#"{"fabric_view_sign_in_hosts": "adfs.contoso.com"}"#,
                     r#"{"fabric_view_sign_in_hosts": {"host": "adfs.contoso.com"}}"#,
                     r#"{"fabric_view_sign_in_hosts": null}"#] {
            assert!(super::policy_sign_in_hosts(text).is_empty(), "{text}");
        }
    }
}

/// The release this PC updates from: "stable" or "latest" (the Preview channel). The
/// organization's policy file (or its last good copy) wins over the Settings page, as in the
/// sidecar's app_settings.py.
fn release_tag() -> &'static str {
    let user = std::fs::read_to_string(data_dir().join("app-settings.json")).unwrap_or_default();
    for text in [policy_text(), user] {
        match setting(&text, "channel").as_deref() {
            Some("preview") => return "latest",
            Some("stable") => return "stable",
            _ => {}
        }
    }
    "stable"
}

/// Where release builds get their engine: Python with the sidecar and every package it
/// needs, built and (once a certificate is set up) code-signed by CI, so the PC's own
/// Python and its unsigned packages are not involved. The channel's release, or with
/// `"auto_update": false` only this build's own (update_guard::engine_tags).
fn engine_tags() -> Vec<String> {
    update_guard::engine_tags(auto_update_allowed(), release_tag(), BUILD_COMMIT)
}

/// With `"auto_update": false` the engine must be this exe's own build, never a newer one (#418).
fn check_engine_build(commit: &str) -> Result<(), String> {
    if auto_update_allowed() || commit.eq_ignore_ascii_case(BUILD_COMMIT) {
        return Ok(());
    }
    Err(format!(
        "Your organization rolls out builds itself, and the engine offered is build {}, not this app's build {}, \
         so it wasn't installed. Ask IT for the current KratosForge exe.",
        commit.get(..7).unwrap_or(commit),
        BUILD_COMMIT.get(..7).unwrap_or(BUILD_COMMIT)
    ))
}

/// Whether a failed download means the release has no such file (curl --fail reports HTTP 404),
/// the one case where a Stable PC may fall back to the Preview engine: before anything has been
/// promoted there is no Stable release. A timeout, a stall or a server error is a failure to report,
/// never a reason to install the untested Preview engine instead.
fn missing_release_file(err: &str) -> bool {
    err.contains("returned error: 404")
}

fn release_file(tag: &str, file: &str) -> String {
    match engine_mirror() {
        Some(folder) => format!("{folder}/{file}"),
        None => format!("https://github.com/KratosDataGod/KratosForge/releases/download/{tag}/{file}"),
    }
}

/// One of Windows' own tools (curl.exe, tar.exe, rundll32.exe) by its full path. By bare name,
/// Windows looks in this exe's folder first, so a curl.exe saved next to it (in Downloads, say)
/// would run instead.
fn system_tool(name: &str) -> PathBuf {
    let root = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
    PathBuf::from(root).join("System32").join(name)
}

/// The SHA-256 of a file, as lowercase hex.
fn sha256_file(path: &std::path::Path) -> Result<String, String> {
    use sha2::{Digest, Sha256};
    let mut file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = file.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// Checks a downloaded engine zip against its release's version.json, which CI writes after the
/// files: the zip's SHA-256 must be the one it gives. A release that gives none is refused (every
/// release CI publishes has one, so only a stripped or hand-made version.json lacks it). Returns
/// the build the release names, if any.
fn check_engine_zip(actual_sha256: &str, release: &Value) -> Result<Option<String>, String> {
    let commit = release["commit"].as_str().filter(|c| c.len() == 40 && c.bytes().all(|b| b.is_ascii_hexdigit()));
    match release["engine_sha256"].as_str() {
        Some(expected) if expected.eq_ignore_ascii_case(actual_sha256) => Ok(commit.map(str::to_owned)),
        Some(_) => Err("The engine download doesn't match the checksum its release gives, so it wasn't installed. \
             A new build may be being published; try again in a few minutes."
            .into()),
        None => Err("The engine's release gives no checksum for it, so it wasn't installed.".into()),
    }
}

/// Checks a downloaded zip of `build`'s engine against the checksum baked into this exe for its
/// own engine (BUILD_ENGINE_SHA256 or BUILD_ENGINE_UPDATE_SHA256). An engine of another build
/// (a self-updating PC moving to a newer one) or an exe built without one is checked only
/// against its release, as before.
fn check_pinned_engine(actual_sha256: &str, pinned: &str, build: Option<&str>, own_build: &str) -> Result<(), String> {
    let own = build.is_none_or(|b| b.eq_ignore_ascii_case(own_build));
    if pinned.is_empty() || !own || pinned.eq_ignore_ascii_case(actual_sha256) {
        return Ok(());
    }
    Err("The engine download isn't the engine this app was built with, so it wasn't installed. \
         Ask IT to copy this build's engine files again, unchanged."
        .into())
}

/// Release builds run the bundled engine; local builds, and anyone who sets
/// FABRIC_DESKTOP_PYTHON, use a Python of their own with the sidecar pip-installed.
fn bundled() -> bool {
    !BUILD_COMMIT.is_empty() && std::env::var_os("FABRIC_DESKTOP_PYTHON").is_none()
}

/// One folder per build: engine\<commit>\python.exe.
fn engine_root() -> PathBuf {
    data_dir().join("engine")
}

/// This build's engine, or failing that another build's, which can still run the
/// startup check that then updates this exe or the engine.
fn engine_python() -> Option<PathBuf> {
    if !bundled() {
        return None;
    }
    let exact = engine_root().join(BUILD_COMMIT).join("python.exe");
    if exact.exists() {
        return Some(exact);
    }
    std::fs::read_dir(engine_root())
        .ok()?
        .flatten()
        .filter(|e| !e.file_name().to_string_lossy().starts_with('.'))
        .map(|e| e.path().join("python.exe"))
        .find(|p| p.exists())
}

/// Shown when no interpreter may be used: the engine is missing and this build won't
/// look for a `python` on PATH, which could run whichever one comes first (SH-10).
// Published builds install their engine before starting it, so this is a build with no engine to
// download (one not built from a published commit).
const NO_ENGINE: &str = "The KratosForge engine isn't installed, and this build has none to download. \
    Install a published build of KratosForge, or set FABRIC_DESKTOP_PYTHON to the full path \
    of a Python with the sidecar installed.";

/// Picks the interpreter that runs the sidecar and the startup check, from
/// `env_override` (FABRIC_DESKTOP_PYTHON), `engine` (this or another build's engine) and
/// `dev_fallback` (whether a bare `python` from PATH may be used: debug builds only).
///
/// Outside debug builds a bare name is never returned: the override must be an absolute
/// path, and with no override and no engine the answer is an error, not `python`.
fn choose_python(env_override: Option<&str>, engine: Option<PathBuf>, dev_fallback: bool) -> Result<String, String> {
    if let Some(path) = env_override.map(str::trim).filter(|p| !p.is_empty()) {
        if dev_fallback || std::path::Path::new(path).is_absolute() {
            return Ok(path.to_string());
        }
        return Err(format!(
            "FABRIC_DESKTOP_PYTHON is set to \"{path}\", which isn't a full path. \
             Set it to the full path of python.exe, or remove it to use the app's own engine."
        ));
    }
    if let Some(engine) = engine {
        return Ok(engine.to_string_lossy().into_owned());
    }
    if dev_fallback {
        return Ok("python".into());
    }
    Err(NO_ENGINE.into())
}

/// The interpreter that runs the sidecar and the startup check.
fn python() -> Result<String, String> {
    let env_override = std::env::var("FABRIC_DESKTOP_PYTHON").ok();
    choose_python(env_override.as_deref(), engine_python(), cfg!(debug_assertions))
}

fn hide_window(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    let _ = cmd;
}

/// The app's own data folder, where the sidecar's log and the updater's working folder live.
fn data_dir() -> PathBuf {
    let dir = std::env::var_os("LOCALAPPDATA")
        .map(|d| PathBuf::from(d).join("FabricDesktop"))
        .unwrap_or_else(std::env::temp_dir);
    let _ = std::fs::create_dir_all(&dir);
    dir
}

/// What this PC remembers about each update channel (HD-022, update_guard.rs).
fn update_state() -> PathBuf {
    data_dir().join("update-state.json")
}

/// Written by a new exe once it has started, so the exe that updated to it can exit.
fn start_marker() -> PathBuf {
    data_dir().join("update-started")
}

fn unix_now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

/// The build the exe that started this one updated to (`--updated-to <commit>`).
fn updated_to_arg() -> Option<String> {
    let args: Vec<String> = std::env::args().collect();
    args.iter().position(|a| a == "--updated-to").and_then(|i| args.get(i + 1)).cloned()
}

/// Applies the release rule to the channel's version.json as the startup check reported it,
/// remembers what it accepted, and adds `refused` with the reason when it isn't accepted.
fn apply_release_rule(latest: &mut Value) {
    if BUILD_COMMIT.is_empty() || latest["commit"].as_str().is_none() {
        return;
    }
    let tag = latest["tag"].as_str().unwrap_or(release_tag()).to_string();
    let path = update_state();
    match update_guard::check_manifest(latest, update_guard::load_seen(&path, &tag).as_ref(), unix_now()) {
        Ok(seen) => {
            let _ = update_guard::remember(&path, &tag, Some(&seen));
        }
        Err(reason) => {
            let _ = update_guard::remember(&path, &tag, None);
            latest["refused"] = json!(reason);
        }
    }
}

fn updater(args: &[&str]) -> Result<Command, String> {
    // Run from the app's own data folder so no stray .py file in the current
    // folder can shadow a standard library module.
    let mut cmd = Command::new(python()?);
    cmd.args(["-u", "-c", UPDATER]).args(args).current_dir(data_dir()).stdin(Stdio::null());
    hide_window(&mut cmd);
    Ok(cmd)
}

/// Runs an updater command, sending each output line to the UI as a
/// `component-log` event. Returns the last lines of output when it fails, without the
/// download's "progress <percent>" lines, which only drive the UI's progress label.
fn stream(app: &tauri::AppHandle, mut cmd: Command) -> Result<(), String> {
    let mut child = cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Could not run Python: {e}"))?;
    let mut tail = VecDeque::new();
    for line in BufReader::new(child.stdout.take().expect("updater stdout")).lines() {
        let line = line.unwrap_or_default();
        let _ = app.emit("component-log", &line);
        if line.starts_with("progress ") {
            continue;
        }
        if tail.len() == 12 {
            tail.pop_front();
        }
        tail.push_back(line);
    }
    let mut stderr = String::new();
    let _ = child.stderr.take().expect("updater stderr").read_to_string(&mut stderr);
    let status = child.wait().map_err(|e| e.to_string())?;
    if status.success() {
        return Ok(());
    }
    tail.extend(stderr.lines().map(str::to_string));
    let start = tail.len().saturating_sub(12);
    Err(tail.iter().skip(start).cloned().collect::<Vec<_>>().join("\n"))
}

/// Several calls can be in flight at once: each writes its request line, then waits for the
/// reply carrying its id, which a reader thread hands over (replies::Pending).
struct Sidecar {
    // Shared with SidecarState so Stop can kill the process while a call is blocked on it.
    child: Arc<Mutex<Child>>,
    stdin: Mutex<ChildStdin>,
    pending: Arc<Mutex<replies::Pending<String>>>,
    next_id: AtomicU64,
    log: PathBuf,
}

/// Reads the engine's reply lines and hands each to the call that sent its id. A reply nobody
/// waits for (its call timed out) is dropped. When the output closes, every waiting call hears so.
fn read_replies(stdout: ChildStdout, pending: Arc<Mutex<replies::Pending<String>>>) {
    let mut stdout = BufReader::new(stdout);
    loop {
        let mut line = String::new();
        match stdout.read_line(&mut line) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        let id = replies::reply_id(&line);
        let Ok(mut waiting) = pending.lock() else { break };
        if !waiting.deliver(id, line) {
            eprintln!("engine reply for {id:?} had no call waiting for it; dropped");
        }
    }
    if let Ok(mut waiting) = pending.lock() {
        waiting.close();
    }
}

impl Sidecar {
    fn start() -> std::io::Result<Self> {
        // A windowed exe has no stderr to hand down, and Python started without one
        // exits at once. Send it to a log file instead, so crashes can be explained.
        let log = data_dir().join("sidecar.log");
        let stderr = std::fs::File::create(&log).map(Stdio::from).unwrap_or_else(|_| Stdio::null());
        let python = python().map_err(std::io::Error::other)?;
        let mut cmd = Command::new(python);
        cmd.args(["-m", "fabric_desktop.server"])
            .env("FABRIC_DESKTOP_BUILTIN_CLIENT_ID", BUILTIN_CLIENT_ID)
            // So Settings > Help and about can say why IT's engine folder isn't used (app_settings).
            .env("FABRIC_DESKTOP_ENGINE_URL_IGNORED", engine_mirror_state().err().unwrap_or_default())
            .current_dir(data_dir())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(stderr);
        hide_window(&mut cmd);
        let mut child = cmd.spawn()?;
        let stdin = Mutex::new(child.stdin.take().expect("sidecar stdin"));
        let stdout = child.stdout.take().expect("sidecar stdout");
        let pending = Arc::new(Mutex::new(replies::Pending::default()));
        let reader = pending.clone();
        std::thread::Builder::new().name("engine-replies".into()).spawn(move || read_replies(stdout, reader))?;
        Ok(Self { child: Arc::new(Mutex::new(child)), stdin, pending, next_id: AtomicU64::new(1), log })
    }

    fn forget(&self, id: u64) {
        if let Ok(mut waiting) = self.pending.lock() {
            waiting.forget(id);
        }
    }

    /// The exit message plus the last thing the sidecar wrote to its log, which is
    /// usually the Python error that stopped it.
    fn exited(&self) -> String {
        let text = std::fs::read_to_string(&self.log).unwrap_or_default();
        let tail: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
        let tail = tail[tail.len().saturating_sub(3)..].join("\n");
        if tail.is_empty() {
            SIDECAR_EXITED.to_string()
        } else {
            format!("{SIDECAR_EXITED}\n{tail}\nFull log: {}", self.log.display())
        }
    }

    fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        // Waiting is set up before the request goes out, so even an instant reply finds its call.
        let reply = self.pending.lock().map_err(|e| e.to_string())?.register(id);
        let Some(reply) = reply else { return Err(self.exited()) };
        let request = json!({ "id": id, "method": method, "params": params });
        let sent = {
            let mut stdin = self.stdin.lock().map_err(|e| e.to_string())?;
            writeln!(stdin, "{request}").and_then(|_| stdin.flush())
        };
        if sent.is_err() {
            self.forget(id);
            return Err(self.exited());
        }

        let line = match watchdog::wait_reply(&reply, &self.child, watchdog::limit(method)) {
            Reply::Line(line) => line,
            Reply::Closed => return Err(self.exited()),
            Reply::TimedOut => {
                self.forget(id);
                return Err(format!(
                    "{SIDECAR_STUCK} {method} had no answer after {} seconds. Notebook variables were cleared; \
                     run the notebook's cells again. Details are in {}",
                    watchdog::QUICK_SECONDS, self.log.display()));
            }
        };
        let response: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
        if response.get("id").and_then(Value::as_u64) != Some(id) {
            return Err(format!("The engine's answer to {method} did not match the request. Try again."));
        }
        if let Some(error) = response.get("error") {
            return Err(error.as_str().unwrap_or("Unknown sidecar error").to_string());
        }
        Ok(response.get("result").cloned().unwrap_or(Value::Null))
    }
}

struct SidecarState {
    sidecar: Mutex<Option<Arc<Sidecar>>>,
    child: Mutex<Option<Arc<Mutex<Child>>>>,
    /// Set while update_app hands over to a new exe: the engine is stopped and must not be
    /// started again by a stray call while the new build's engine starts (HD-022).
    updating: std::sync::atomic::AtomicBool,
    /// Set while update_sidecar installs an engine: a call must not start the old engine meanwhile,
    /// since installing removes the old engine's folder and that engine would run on without its
    /// files (its certificates among them, so every sign-in failed).
    installing: std::sync::atomic::AtomicBool,
}

impl SidecarState {
    /// Starts the engine into an empty slot, as the first call after startup does.
    fn start_into(&self, guard: &mut Option<Arc<Sidecar>>) -> Result<(), String> {
        if guard.is_none() {
            let sidecar = Sidecar::start().map_err(|e| format!("Could not start the Python sidecar: {e}"))?;
            *self.child.lock().map_err(|e| e.to_string())? = Some(sidecar.child.clone());
            *guard = Some(Arc::new(sidecar));
        }
        Ok(())
    }

    /// Ends the engine the way quitting the app does: closing its stdin ends its request loop,
    /// which stops the SQL page's query processes, and the local API thread ends with the
    /// process. Calls in flight each hold the engine (an Arc), and its stdin closes only when the
    /// last of them lets go, so with calls in flight the process is ended at once and this waits
    /// for every holder to drop. One that hasn't exited within `grace` is killed. Returns whether
    /// one was running, or an error when a holder still had it at the time limit: the exe it runs
    /// from must not be swapped then.
    fn shut_down(&self, grace: std::time::Duration) -> Result<bool, String> {
        let sidecar = self.sidecar.lock().unwrap_or_else(|p| p.into_inner()).take();
        let child = self.child.lock().unwrap_or_else(|p| p.into_inner()).take();
        let Some(child) = sidecar.as_ref().map(|s| s.child.clone()).or(child) else { return Ok(false) };
        let deadline = std::time::Instant::now() + grace;
        if let Some(mut engine) = sidecar {
            if Arc::strong_count(&engine) > 1 {
                // A call is waiting on it: end the process so the call returns and lets go.
                let _ = child.lock().unwrap_or_else(|p| p.into_inner()).kill();
            }
            loop {
                match Arc::try_unwrap(engine) {
                    Ok(only) => {
                        drop(only); // closes its stdin
                        break;
                    }
                    Err(_) if std::time::Instant::now() >= deadline => {
                        return Err("The engine was still answering a call, so the new build wasn't put in place.".into());
                    }
                    Err(shared) => engine = shared,
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
        }
        loop {
            let mut process = child.lock().unwrap_or_else(|p| p.into_inner());
            match process.try_wait() {
                Ok(None) if std::time::Instant::now() < deadline => {}
                Ok(None) => {
                    let _ = process.kill();
                    let _ = process.wait();
                    return Ok(true);
                }
                _ => return Ok(true),
            }
            drop(process);
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
    }
}

#[tauri::command]
async fn rpc(
    state: tauri::State<'_, SidecarState>,
    method: String,
    params: Option<Value>,
) -> Result<Value, String> {
    // The lock is held only to find or start the engine, not for the call, so a long call
    // doesn't hold up the others.
    let sidecar = {
        let mut guard = state.sidecar.lock().map_err(|e| e.to_string())?;
        // Checked under the lock: update_app sets it before it takes the engine out, so a call
        // can't start a new engine after the hand-over has stopped this one.
        if state.updating.load(Ordering::SeqCst) {
            return Err("KratosForge is starting its new build. Try again in a moment.".into());
        }
        if state.installing.load(Ordering::SeqCst) {
            return Err("KratosForge is updating its engine. Try again in a moment.".into());
        }
        state.start_into(&mut guard)?;
        guard.clone().expect("sidecar started")
    };
    let first_call = sidecar.next_id.load(Ordering::SeqCst) == 1;
    let engine = sidecar.clone();
    let params = params.unwrap_or_else(|| json!({}));
    let result = tauri::async_runtime::spawn_blocking(move || engine.call(&method, params))
        .await
        .map_err(|e| e.to_string())?;
    let ended = matches!(&result, Err(e) if e.starts_with(SIDECAR_STUCK) || e.starts_with(SIDECAR_EXITED));
    if ended {
        // Ended by the time limit, Stop or a crash: the next call starts a fresh one, unless
        // another call already did.
        let mut guard = state.sidecar.lock().map_err(|e| e.to_string())?;
        if guard.as_ref().is_some_and(|current| Arc::ptr_eq(current, &sidecar)) {
            *guard = None;
        }
    }
    if first_call && matches!(&result, Err(e) if e.starts_with(SIDECAR_EXITED)) {
        // It would not even start: have the next startup check load the engine again
        // rather than trusting that this build worked before.
        let _ = std::fs::remove_file(data_dir().join("engine-verified.json"));
    }
    result
}

/// Stops a running notebook cell by ending the sidecar process. The blocked
/// `rpc` call then returns an error and the next call starts a fresh sidecar.
#[tauri::command]
fn stop_kernel(state: tauri::State<'_, SidecarState>) -> Result<(), String> {
    if let Some(child) = state.child.lock().map_err(|e| e.to_string())?.take() {
        let _ = child.lock().map_err(|e| e.to_string())?.kill();
    }
    Ok(())
}

/// Where this exe's file was renamed to while it runs. Windows keeps reporting the name the
/// process started with, so an update later in the same session must use this one.
static RENAMED_TO: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();

/// This exe's file, under its current name.
fn app_exe() -> std::io::Result<PathBuf> {
    RENAMED_TO.get().cloned().map_or_else(std::env::current_exe, Ok)
}

/// Renames a fabric-desktop.exe to kratosforge.exe and points its shortcuts there
/// (exe_name.rs), noting what happened in exe-rename.json for support. Once per process: the
/// startup check can run again (Retry) while a first rename is under way.
fn rename_to_kratosforge(exe: &std::path::Path) {
    static TRIED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    if TRIED.swap(true, Ordering::SeqCst) {
        return;
    }
    #[cfg(windows)]
    {
        let note = match exe_name::migrate(exe, &mut exe_name::Windows) {
            Ok(exe_name::Outcome::Unchanged) => return,
            Ok(exe_name::Outcome::Renamed { to, shortcuts }) => {
                let shortcuts: Vec<String> = shortcuts.iter().map(|l| l.display().to_string()).collect();
                let note = json!({
                    "from": exe.display().to_string(), "to": to.display().to_string(),
                    "shortcuts": shortcuts, "error": null, "at": unix_now(),
                });
                let _ = RENAMED_TO.set(to);
                note
            }
            Err(e) => json!({ "from": exe.display().to_string(), "to": null, "shortcuts": [], "error": e, "at": unix_now() }),
        };
        let _ = std::fs::write(data_dir().join("exe-rename.json"), note.to_string());
    }
    #[cfg(not(windows))]
    let _ = exe;
}

/// Reports this exe's build, the Python and sidecar it will use, and the
/// latest release. Fails only when Python itself cannot run.
#[tauri::command]
async fn check_components() -> Result<Value, String> {
    let updated_to = updated_to_arg();
    // The window is up and the UI has asked for this check, so this exe started. After an update,
    // the exe that started this one waits for this marker, then exits.
    if updated_to.is_some() {
        let _ = update_guard::mark_started(&start_marker(), BUILD_COMMIT);
    }
    // The previous exe (exe.old) is kept until now so it can be put back; it goes once its process
    // has exited. Not when this is the wrong build: the exe that started it is putting itself back.
    if updated_to.as_deref().is_none_or(|to| to == BUILD_COMMIT) {
        let after_update = updated_to.is_some();
        std::thread::spawn(move || {
            let Ok(exe) = app_exe() else { return };
            let old = exe.with_extension("exe.old");
            for _ in 0..120 {
                if !old.exists() || std::fs::remove_file(&old).is_ok() {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(500));
            }
            // The previous exe has exited and gone, so nothing else holds this file's name: a copy
            // still called fabric-desktop.exe becomes kratosforge.exe. Only after an update this PC
            // takes itself; a managed PC's file stays where IT put it.
            if after_update && !old.exists() && bundled() && auto_update_allowed() {
                rename_to_kratosforge(&exe);
            }
        });
    }
    let mut report = if bundled() && engine_python().is_none() {
        // Nothing to run the check with until the engine is installed; the UI installs
        // it and then checks again.
        json!({
            "python": null, "python_ok": true,
            "sidecar": { "version": null, "commit": null, "editable": false },
            "latest": { "commit": null, "error": null },
        })
    } else {
        let out = updater(&["check"])?
            .output()
            .map_err(|e| format!("Python was not found ({e})."))?;
        if !out.status.success() {
            let stderr = String::from_utf8_lossy(&out.stderr);
            return Err(format!("Python could not run the startup check. {}", stderr.trim()));
        }
        serde_json::from_slice(&out.stdout)
            .map_err(|_| "Python gave an unexpected answer to the startup check.".to_string())?
    };
    report["bundled"] = json!(bundled());
    report["auto_update"] = json!(auto_update_allowed());
    apply_release_rule(&mut report["latest"]);
    // Set by the exe that updated us, so the check can tell whether the update took.
    report["app"] = json!({
        "version": env!("CARGO_PKG_VERSION"),
        "commit": if BUILD_COMMIT.is_empty() { Value::Null } else { json!(BUILD_COMMIT) },
        "updated_to": updated_to,
    });
    Ok(report)
}

/// The channel's newest build (updater.py latest), put through the release rule, which also
/// makes it the build update_app may install.
fn channel_latest() -> Result<Value, String> {
    let out = updater(&["latest"])?.output().map_err(|e| format!("Python was not found ({e})."))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!("Python could not run the update check. {}", stderr.trim()));
    }
    let mut latest: Value = serde_json::from_slice(&out.stdout)
        .map_err(|_| "Python gave an unexpected answer to the update check.".to_string())?;
    apply_release_rule(&mut latest);
    Ok(latest)
}

/// The build the channel offers right now, checked again just before a download, since a banner
/// or dialog can show a build the channel has since replaced (a newer Preview was published).
fn offered_now() -> Result<String, String> {
    let latest = channel_latest()?;
    if let Some(reason) = latest["refused"].as_str() {
        return Err(reason.to_string());
    }
    match latest["commit"].as_str() {
        Some(c) if update_guard::is_commit(c) => Ok(c.to_ascii_lowercase()),
        _ => Err(format!(
            "Could not check the update channel ({}). Try again in a few minutes.",
            latest["error"].as_str().unwrap_or("it names no build")
        )),
    }
}

/// "Check for updates" (account menu, Settings): the channel's newest build, put through the same
/// release rule as the startup check, so update_app will install it. Leaves the engine alone: the
/// new build's startup check updates it.
#[tauri::command]
async fn check_for_update() -> Result<Value, String> {
    let latest = channel_latest()?;
    Ok(json!({
        "commit": if BUILD_COMMIT.is_empty() { Value::Null } else { json!(BUILD_COMMIT) },
        "auto_update": auto_update_allowed(),
        "latest": latest,
    }))
}

/// Installs the sidecar from the commit this exe was built from, so the two always match:
/// the bundled engine for release builds, else pip into the user's Python. Returns the
/// engine's build, which can be newer than this exe when the release has moved on (never with
/// `"auto_update": false`, which installs only this build's engine).
#[tauri::command]
async fn update_sidecar(app: tauri::AppHandle, state: tauri::State<'_, SidecarState>) -> Result<Value, String> {
    if BUILD_COMMIT.is_empty() {
        return Err("This is a local build, so it cannot pick a sidecar version. Install it with pip.".into());
    }
    // A running sidecar holds its files open, which blocks pip and the engine swap on Windows.
    // The flag is set under the engine's lock, so no call starts the old engine again until the
    // install is over (a Monitor refresh every 15 s would otherwise).
    {
        let mut guard = state.sidecar.lock().map_err(|e| e.to_string())?;
        state.installing.store(true, Ordering::SeqCst);
        *guard = None;
    }
    struct Installing<'a>(&'a std::sync::atomic::AtomicBool);
    impl Drop for Installing<'_> {
        fn drop(&mut self) {
            self.0.store(false, Ordering::SeqCst);
        }
    }
    let _installing = Installing(&state.installing);
    stop_kernel(state.clone())?;
    if bundled() {
        return install_engine(&app).map(|commit| json!({ "engine": commit }));
    }
    stream(&app, updater(&["install-sidecar", BUILD_COMMIT])?)?;
    Ok(Value::Null)
}

/// Runs one of Windows' own tools (curl, tar), returning its error output when it fails.
fn run_tool(mut cmd: Command, what: &str) -> Result<(), String> {
    hide_window(&mut cmd);
    let out = cmd.stdin(Stdio::null()).output().map_err(|e| format!("Could not {what}: {e}"))?;
    if out.status.success() {
        return Ok(());
    }
    Err(format!("Could not {what}: {}", String::from_utf8_lossy(&out.stderr).trim()))
}

/// Downloads the latest engine (with `"auto_update": false`, this build's) and unpacks it to
/// engine\<its commit>, then removes other builds' engines. Uses curl.exe and tar.exe, which come with Windows. When an installed
/// engine already has the release's Python and packages, only the engine's own code is
/// downloaded (engine_patch.rs); the whole engine is the fallback.
fn install_engine(app: &tauri::AppHandle) -> Result<String, String> {
    let say = |line: &str| {
        let _ = app.emit("component-log", line);
    };
    let root = engine_root();
    std::fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    let zip = root.join("download.zip");
    let release_json = root.join("version.json");
    let staging = root.join(".new");
    let nocache = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);

    match update_engine_code(&root, nocache, &say) {
        Ok(Some(commit)) => return Ok(commit),
        Ok(None) => {}
        Err(e) => {
            let _ = std::fs::remove_dir_all(&staging);
            say(&format!("{e} Downloading the whole engine instead."));
        }
    }

    say("Downloading the engine (Python and its packages, about 150 MB)…");
    let mut result = Err(String::new());
    let mut release = Value::Null;
    let mut release_tag_used = "";
    let tags = engine_tags();
    for tag in &tags {
        release_tag_used = tag.as_str();
        let mut curl = Command::new(system_tool("curl.exe"));
        // Give up on a dead or stalled connection (under 1 KB/s for a minute) instead of
        // leaving "Downloading the engine" up for ever; Retry is then shown.
        curl.args(["-L", "--proto", "=https", "--proto-redir", "=https"]);
        curl.args(["--fail", "-sS", "--connect-timeout", "20", "--speed-limit", "1024", "--speed-time", "60",
                   "--retry", "2", "-H", "Cache-Control: no-cache", "-o"])
            .arg(&zip)
            .arg(format!("{}?nocache={nocache}", release_file(tag, "fabric-desktop-engine.zip")));
        result = run_tool(curl, "download the engine");
        if result.as_ref().is_err_and(|e| !missing_release_file(e)) {
            break;
        }
        if result.is_ok() {
            // Its checksum comes from the same release, fetched after the zip: CI uploads
            // version.json last, so it never names a zip older than the one just downloaded.
            let mut curl = Command::new(system_tool("curl.exe"));
            curl.args(["-L", "--proto", "=https", "--proto-redir", "=https"]);
            curl.args(["--fail", "-sS", "--connect-timeout", "20", "--max-time", "60", "-H",
                       "Cache-Control: no-cache", "-o"])
                .arg(&release_json)
                .arg(format!("{}?nocache={nocache}", release_file(tag, "version.json")));
            result = run_tool(curl, "read the engine's checksum");
            release = std::fs::read_to_string(&release_json)
                .ok()
                .and_then(|text| serde_json::from_str(text.trim_start_matches('\u{feff}')).ok())
                .unwrap_or(Value::Null);
            let _ = std::fs::remove_file(&release_json);
            if result.is_ok() && !release.is_object() {
                result = Err("Could not read the engine's checksum: the release's version.json isn't valid.".into());
            }
            break;
        }
    }
    if let Err(e) = result {
        let _ = std::fs::remove_file(&zip);
        if let (Some(folder), true) = (engine_mirror(), missing_release_file(&e)) {
            return Err(format!(
                "Your organization keeps this app's engine at {folder}, and the engine for this build ({}) isn't \
                 there, so none was installed. Ask IT to copy the files of release build-{} there.",
                BUILD_COMMIT.get(..7).unwrap_or(BUILD_COMMIT),
                BUILD_COMMIT.get(..12).unwrap_or(BUILD_COMMIT)
            ));
        }
        if !auto_update_allowed() && missing_release_file(&e) {
            return Err(format!(
                "Your organization rolls out builds itself, and this app's build ({}) no longer has an engine to \
                 download (release {release_tag_used}), so none was installed. Ask IT for the current KratosForge exe.",
                BUILD_COMMIT.get(..7).unwrap_or(BUILD_COMMIT)
            ));
        }
        return Err(e);
    }
    // An out-of-date version.json would install an older engine than the channel last offered.
    let state = update_state();
    match update_guard::check_manifest(&release, update_guard::load_seen(&state, release_tag_used).as_ref(), unix_now()) {
        Ok(seen) => {
            let _ = update_guard::remember(&state, release_tag_used, Some(&seen));
        }
        Err(e) => {
            let _ = std::fs::remove_file(&zip);
            return Err(e);
        }
    }
    say("Checking the download…");
    let sha = sha256_file(&zip)?;
    let named = match check_engine_zip(&sha, &release)
        .and_then(|named| check_pinned_engine(&sha, BUILD_ENGINE_SHA256, named.as_deref(), BUILD_COMMIT).map(|()| named))
    {
        Ok(named) => named,
        Err(e) => {
            let _ = std::fs::remove_file(&zip);
            return Err(e);
        }
    };

    say("Unpacking the engine…");
    let _ = std::fs::remove_dir_all(&staging);
    std::fs::create_dir_all(&staging).map_err(|e| e.to_string())?;
    let mut tar = Command::new(system_tool("tar.exe"));
    tar.arg("-xf").arg(&zip).arg("-C").arg(&staging);
    run_tool(tar, "unpack the engine")?;
    let _ = std::fs::remove_file(&zip);

    let info: Value = std::fs::read_to_string(staging.join("engine.json"))
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .ok_or("The engine download has no engine.json, so it is not a KratosForge engine.")?;
    let commit = info["commit"].as_str().unwrap_or_default().to_string();
    if commit.len() != 40 || !commit.bytes().all(|b| b.is_ascii_hexdigit()) {
        let _ = std::fs::remove_dir_all(&staging);
        return Err("The engine download does not say which build it is.".into());
    }
    if named.as_deref().is_some_and(|n| n != commit) {
        let _ = std::fs::remove_dir_all(&staging);
        return Err("The engine download is a different build from the one its release names, so it wasn't installed. \
             A new build may be being published; try again in a few minutes."
            .into());
    }
    if let Err(e) = check_engine_build(&commit) {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(e);
    }
    put_engine_in_place(&root, &staging, &commit)?;
    say(&format!("Installed engine build {}.", &commit[..7]));
    Ok(commit)
}

/// Moves a checked engine from `staging` to engine\<commit>, then removes other builds' engines.
fn put_engine_in_place(root: &std::path::Path, staging: &std::path::Path, commit: &str) -> Result<(), String> {
    let dest = root.join(commit);
    let _ = std::fs::remove_dir_all(&dest);
    std::fs::rename(staging, &dest).map_err(|e| format!("Could not put the engine in place: {e}"))?;

    // Engines of other builds are no longer needed; one still in use goes next time.
    engine_patch::prune_engines(root, &[commit, BUILD_COMMIT]);
    Ok(())
}

/// Installs the release's engine from an installed engine with the same Python and packages plus
/// the release's update zip (a few MB). Ok(None) when that can't apply: the release offers no
/// update zip, no installed engine has its runtime, or the release's build is already installed
/// (a repair, which replaces everything). An error means install_engine downloads the whole engine.
fn update_engine_code(root: &std::path::Path, nocache: u64, say: &dyn Fn(&str)) -> Result<Option<String>, String> {
    // version.json first here, to learn the runtime before downloading anything big. If a new
    // build is published in between, the zip won't match its checksum and the whole engine is
    // downloaded instead.
    let release_json = root.join("version.json");
    let mut release = Value::Null;
    let mut tag_used = "";
    let tags = engine_tags();
    for tag in &tags {
        let mut curl = Command::new(system_tool("curl.exe"));
        curl.args(["-L", "--proto", "=https", "--proto-redir", "=https"]);
        curl.args(["--fail", "-sS", "--connect-timeout", "20", "--max-time", "60", "-H",
                   "Cache-Control: no-cache", "-o"])
            .arg(&release_json)
            .arg(format!("{}?nocache={nocache}", release_file(tag, "version.json")));
        match run_tool(curl, "read the engine's release") {
            Ok(()) => {
                tag_used = tag.as_str();
                release = std::fs::read_to_string(&release_json)
                    .ok()
                    .and_then(|text| serde_json::from_str(text.trim_start_matches('\u{feff}')).ok())
                    .unwrap_or(Value::Null);
                break;
            }
            Err(e) if missing_release_file(&e) => continue,
            Err(e) => {
                let _ = std::fs::remove_file(&release_json);
                return Err(e);
            }
        }
    }
    let _ = std::fs::remove_file(&release_json);
    let Some((runtime, update_sha)) = engine_patch::update_of(&release) else { return Ok(None) };
    let Some(commit) = release["commit"].as_str().filter(|c| update_guard::is_commit(c)).map(str::to_ascii_lowercase) else {
        return Ok(None);
    };
    if root.join(&commit).exists() {
        return Ok(None);
    }
    check_engine_build(&commit)?;
    let Some(source) = engine_patch::find_runtime(root, &runtime) else { return Ok(None) };
    let state = update_state();
    let seen = update_guard::check_manifest(&release, update_guard::load_seen(&state, tag_used).as_ref(), unix_now())?;

    say("Python and its packages are unchanged, so only the engine's own code is downloaded (a few MB)…");
    let zip = root.join("update.zip");
    let mut curl = Command::new(system_tool("curl.exe"));
    curl.args(["-L", "--proto", "=https", "--proto-redir", "=https"]);
    curl.args(["--fail", "-sS", "--connect-timeout", "20", "--speed-limit", "1024", "--speed-time", "60",
               "--retry", "2", "-H", "Cache-Control: no-cache", "-o"])
        .arg(&zip)
        .arg(format!("{}?nocache={nocache}", release_file(tag_used, engine_patch::UPDATE_ZIP)));
    let checked = run_tool(curl, "download the engine update")
        .and_then(|()| sha256_file(&zip))
        .and_then(|sha| {
            engine_patch::check_update_zip(&sha, &update_sha)?;
            check_pinned_engine(&sha, BUILD_ENGINE_UPDATE_SHA256, Some(&commit), BUILD_COMMIT)
        });
    if let Err(e) = checked {
        let _ = std::fs::remove_file(&zip);
        return Err(e);
    }

    let staging = root.join(".new");
    let staged = engine_patch::stage_runtime(&source, &staging).and_then(|()| {
        let mut tar = Command::new(system_tool("tar.exe"));
        tar.arg("-xf").arg(&zip).arg("-C").arg(&staging);
        run_tool(tar, "unpack the engine update")
    });
    let _ = std::fs::remove_file(&zip);
    staged.and_then(|()| engine_patch::check_patched(&staging, &commit, &runtime))?;
    let _ = update_guard::remember(&state, tag_used, Some(&seen));
    put_engine_in_place(root, &staging, &commit)?;
    say(&format!("Updated the engine to build {}.", &commit[..7]));
    Ok(Some(commit))
}

/// How long this exe waits for the new one to say it started (window up, startup check asked for).
/// The first start of a new build unpacks WebView2 data and can be slow on a busy PC.
const START_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(90);

/// update_app's hand-over to the downloaded exe (update_guard::hand_over gives the order).
struct ExeHandover<'a> {
    app: &'a tauri::AppHandle,
    state: &'a SidecarState,
    exe: PathBuf,
    new: PathBuf,
    old: PathBuf,
    marker: PathBuf,
    commit: &'a str,
    child: Option<Child>,
}

impl update_guard::Handover for ExeHandover<'_> {
    fn stop_engine(&mut self) -> Result<bool, String> {
        self.state.updating.store(true, Ordering::SeqCst);
        self.state.shut_down(std::time::Duration::from_secs(10))
    }

    fn swap(&mut self) -> Result<(), String> {
        update_guard::swap_in(&self.exe, &self.new, &self.old)
    }

    fn spawn(&mut self) -> Result<(), String> {
        let _ = std::fs::remove_file(&self.marker);
        deep_link::start_handover(&data_dir());
        INSTANCE.release(); // so the new exe claims it straight away
        self.child = Some(Command::new(&self.exe).args(["--updated-to", self.commit]).spawn().map_err(|e| e.to_string())?);
        if let Some(w) = self.app.get_webview_window("main") {
            let _ = w.hide();
        }
        Ok(())
    }

    fn wait(&mut self) -> update_guard::Start {
        let (marker, commit) = (&self.marker, self.commit);
        let child = &mut self.child;
        let outcome = update_guard::wait_for_start(
            || update_guard::started_as(marker, commit),
            || child.as_mut().is_none_or(|c| c.try_wait().map(|s| s.is_some()).unwrap_or(true)),
            START_TIMEOUT,
            std::time::Duration::from_millis(250),
        );
        let _ = std::fs::remove_file(&self.marker);
        outcome
    }

    fn restore(&mut self) -> Result<(), String> {
        update_guard::restore_previous(&self.exe, &self.old)
    }

    fn resume(&mut self, engine_was_running: bool) {
        self.state.updating.store(false, Ordering::SeqCst);
        // The new exe has ended (only then is an update undone); it may take a moment to let go.
        let dir = data_dir();
        for _ in 0..20 {
            if INSTANCE.reclaim(&dir) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(250));
        }
        deep_link::end_handover(&dir); // only once it's ours again, so a link copy can't take it first
        if engine_was_running {
            // So the restored window works straight away; if this fails, the next call retries.
            if let Ok(mut guard) = self.state.sidecar.lock() {
                let _ = self.state.start_into(&mut guard);
            }
        }
        if let Some(w) = self.app.get_webview_window("main") {
            let _ = w.show();
            let _ = w.set_focus();
        }
    }

    fn exit(&mut self) {
        self.app.exit(0);
    }
}

/// Downloads the build the channel offers (refusing any other), swaps it in place of this one
/// and starts it, after ending this exe's engine so the new one can take the local API port and
/// the engine's files. This exe is kept as exe.old and this process waits until the new one says
/// it started; if the new one exits first, this exe is put back, its engine restarted if it was
/// running, and it keeps running (HD-022). One still running at the time limit without saying so
/// is left to run: builds from before HD-022 never say.
///
/// `commit` is the build the UI showed. The channel is checked again first, and when it has moved
/// on (a newer Preview was published since), or moves during the download, the build it names
/// now is installed instead, so an update never chases a build the release no longer holds.
#[tauri::command]
async fn update_app(app: tauri::AppHandle, state: tauri::State<'_, SidecarState>, commit: String) -> Result<(), String> {
    if !update_guard::is_commit(&commit) {
        return Err(format!("{commit} is not a build commit"));
    }
    if !auto_update_allowed() {
        return Err("Your organization rolls out new builds itself, so the app doesn't update itself.".into());
    }
    let exe = app_exe().map_err(|e| e.to_string())?;
    let new = exe.with_extension("exe.new");
    let old = exe.with_extension("exe.old");
    let dest = new.to_string_lossy().into_owned();
    let mut target = commit.to_ascii_lowercase();
    for attempt in 0..2 {
        let offered = offered_now()?;
        if offered != target {
            let _ = app.emit("component-log", format!(
                "Build {} was replaced on the update channel by build {}; installing that.", &target[..7], &offered[..7]));
            target = offered;
        }
        // Only the build the accepted check just now offered (update_guard::check_manifest).
        if update_guard::offered(&update_state()).is_none_or(|c| !c.eq_ignore_ascii_case(&target)) {
            return Err(format!("Build {} isn't the build your update channel offers, so it wasn't installed.", &target[..7]));
        }
        if !update_guard::wants_install(BUILD_COMMIT, &update_guard::Seen { commit: target.clone(), published: None }) {
            return Err(format!("This is already build {}, or a local build that doesn't update itself.", &target[..7]));
        }
        // The download runs the updater with its own Python, not the engine, so the engine keeps
        // running until the file is here and checked.
        match stream(&app, updater(&["download-app", &dest, &target])?) {
            Ok(()) => break,
            Err(out) => {
                let last = out.lines().last().unwrap_or_default().trim().to_string();
                if last.starts_with("moved ") && attempt == 0 {
                    continue; // republished meanwhile: check the channel again
                }
                let _ = std::fs::remove_file(&new);
                return Err(if last.starts_with("moved ") {
                    "The update channel is being republished. Try again in a few minutes.".into()
                } else if last.is_empty() {
                    "The download failed. Try again in a few minutes.".into()
                } else {
                    last
                });
            }
        }
    }
    update_guard::hand_over(&mut ExeHandover {
        app: &app, state: &state, exe, new, old, marker: start_marker(), commit: &target, child: None,
    })
}

// ---- Fabric's own pages (the Monitoring hub, the OneLake catalog), shown in one second webview
// docked into the page. Fabric pages refuse to load in an iframe, but a separate webview is a
// browser of its own. Both pages share this one view, so they share one Fabric sign-in.

const FABRIC_VIEW: &str = "fabric";
/// The Fabric view in the Monitoring hub's own window (pop-out window "fabric"). It is a second
/// view on the same WebView2 profile, so it shares the sign-in, and it has the same filter.
const FABRIC_POP_OUT_VIEW: &str = "fabric-pop-out";

/// The Fabric view a window of the app shows: the main window's, or the Fabric pop-out's.
fn fabric_view_label(window: &str) -> Result<&'static str, String> {
    match window {
        "main" => Ok(FABRIC_VIEW),
        w if w == pop_out_label("fabric") => Ok(FABRIC_POP_OUT_VIEW),
        other => Err(format!("The {other} window has no Fabric view")),
    }
}
const FABRIC_MONITOR: &str = "https://app.fabric.microsoft.com/monitoringhub?experience=fabric-developer";
const FABRIC_CATALOG: &str = "https://app.fabric.microsoft.com/onelake/explore?experience=fabric-developer";

/// Domains whose cookies count as the Fabric view's sign-in (saved, restored, forgotten).
/// Only for cookies; where the view may go is `FABRIC_VIEW_HOSTS`.
const FABRIC_HOSTS: [&str; 6] = ["microsoft.com", "microsoftonline.com", "powerbi.com", "live.com", "msauth.net", "msftauth.net"];

fn fabric_host(host: &str) -> bool {
    let host = host.trim_start_matches('.');
    FABRIC_HOSTS.iter().any(|d| host == *d || host.ends_with(&format!(".{d}")))
}

/// The only hosts the Fabric view itself navigates to (top-level pages and pop-ups), matched
/// exactly, https only. Images, scripts and frames inside a page aren't filtered by this.
/// The list and its reasons are in docs/security.md (SH-12).
const FABRIC_VIEW_HOSTS: [&str; 8] = [
    "app.fabric.microsoft.com",  // Fabric's portal: Monitoring hub, OneLake catalog, item pages
    "app.powerbi.com",           // Fabric's former portal host; older item links redirect through it
    "login.microsoftonline.com", // Entra ID sign-in and sign-out
    "login.microsoft.com",       // Entra ID's other sign-in host, used on some sign-in redirects
    "login.windows.net",         // Entra ID's legacy sign-in host; older links and redirects still use it
    "device.login.microsoftonline.com", // Entra ID's device sign-in step, where Conditional Access checks the PC
    "autologon.microsoftazuread-sso.com", // Entra ID Seamless SSO: domain-joined PCs sign in without a password
    "login.live.com",            // a guest who signs in with a personal Microsoft account
];

/// What the Fabric view does with a page it is about to open.
#[derive(Debug, PartialEq, Eq)]
enum FabricNav {
    /// Open it in the view.
    Stay,
    /// Open it in the default browser instead.
    Browser,
    /// Open it nowhere.
    Refuse,
}

/// Fabric's pages and sign-in stay in the view; other web links open in the default browser;
/// anything else (http to a Microsoft host, file:, javascript:, ...) is refused. `about:blank`,
/// which the view starts on, is allowed in the view but not as a pop-up. `sign_in_hosts` are the
/// organization's own (`fabric_view_sign_in_hosts`, already checked by `policy_sign_in_hosts`)
/// and follow the same rule as the built-in hosts.
fn fabric_nav(url: &Url, new_window: bool, sign_in_hosts: &[String]) -> FabricNav {
    if url.as_str() == "about:blank" {
        return if new_window { FabricNav::Refuse } else { FabricNav::Stay };
    }
    let host = url.host_str().unwrap_or_default();
    let listed = FABRIC_VIEW_HOSTS.contains(&host) || sign_in_hosts.iter().any(|h| h == host);
    match url.scheme() {
        "https" if listed && url.username().is_empty() && url.password().is_none() && url.port().is_none() => FabricNav::Stay,
        "http" if listed => FabricNav::Refuse,
        "https" | "http" => FabricNav::Browser,
        _ => FabricNav::Refuse,
    }
}

/// Applies `fabric_nav`: true keeps the page in the view.
fn fabric_nav_allows(url: &Url, new_window: bool, sign_in_hosts: &[String]) -> bool {
    match fabric_nav(url, new_window, sign_in_hosts) {
        FabricNav::Stay => true,
        FabricNav::Browser => {
            let _ = open_in_browser(url);
            false
        }
        FabricNav::Refuse => {
            eprintln!("Fabric view: refused to open {}", url.scheme());
            false
        }
    }
}

fn open_in_browser(url: &Url) -> Result<(), String> {
    if !matches!(url.scheme(), "https" | "http") {
        return Err(format!("Not opening {url}"));
    }
    let mut cmd = if cfg!(windows) {
        let mut c = Command::new(system_tool("rundll32.exe"));
        c.args(["url.dll,FileProtocolHandler", url.as_str()]);
        c
    } else {
        let mut c = Command::new("xdg-open");
        c.arg(url.as_str());
        c
    };
    hide_window(&mut cmd);
    cmd.spawn().map(|_| ()).map_err(|e| format!("Could not open the browser: {e}"))
}

/// The page the Fabric view shows: "monitor", "catalog", or an https link to a Fabric item.
fn fabric_page(page: &str, tenant: Option<&str>) -> Result<Url, String> {
    let mut url = match page {
        "monitor" => Url::parse(FABRIC_MONITOR),
        "catalog" => Url::parse(FABRIC_CATALOG),
        link => Url::parse(link),
    }
    .map_err(|e| e.to_string())?;
    if url.scheme() != "https" || url.host_str() != Some("app.fabric.microsoft.com") {
        return Err(format!("Not a Fabric page: {page}"));
    }
    // With the tenant of the account signed in to the app, Fabric skips its
    // "Enter your work or school email" page and goes straight to that directory's sign-in.
    if let Some(tenant) = tenant.filter(|t| t.len() == 36 && t.chars().all(|c| c.is_ascii_hexdigit() || c == '-')) {
        url.query_pairs_mut().append_pair("ctid", tenant).append_pair("noSignUpCheck", "1");
    }
    Ok(url)
}

/// Which page each Fabric view was last sent to, so switching between Monitor and OneLake
/// navigates only when the page changes.
static FABRIC_SHOWN: Mutex<BTreeMap<&'static str, String>> = Mutex::new(BTreeMap::new());

/// Shows the calling window's Fabric view over the page's placeholder, creating it the first time.
/// Coordinates are CSS pixels inside the window, which are Tauri's logical pixels.
#[tauri::command]
async fn fabric_view_show(
    app: tauri::AppHandle,
    window: tauri::Window,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    tenant: Option<String>,
    page: Option<String>,
    force: Option<bool>,
) -> Result<(), String> {
    let page = page.unwrap_or_else(|| "monitor".into());
    let target = fabric_page(&page, tenant.as_deref())?;
    let position = LogicalPosition::new(x, y);
    let size = LogicalSize::new(width.max(1.0), height.max(1.0));
    let label = fabric_view_label(window.label())?;
    if let Some(view) = app.get_webview(label) {
        view.set_position(position).map_err(|e| e.to_string())?;
        view.set_size(size).map_err(|e| e.to_string())?;
        // force: someone clicked a link, so go there even if it is the page last sent; they may
        // have moved around inside Fabric since.
        let changed = FABRIC_SHOWN.lock().unwrap().insert(label, page.clone()).as_ref() != Some(&page);
        if changed || force.unwrap_or(false) {
            view.navigate(target).map_err(|e| e.to_string())?;
        }
        return view.show().map_err(|e| e.to_string());
    }
    // Start blank: the saved sign-in goes in before the first Fabric request.
    let blank = Url::parse("about:blank").map_err(|e| e.to_string())?;
    // The organization's identity provider hosts, read once for this view.
    let sign_in_hosts = Arc::new(fabric_view_sign_in_hosts());
    let (app_, for_pop_ups) = (app.clone(), sign_in_hosts.clone());
    let builder = WebviewBuilder::new(label, WebviewUrl::External(blank))
        .on_navigation(move |url| fabric_nav_allows(url, false, &sign_in_hosts))
        .on_new_window(move |url, features| fabric_pop_up(&app_, &for_pop_ups, url, features));
    #[cfg(windows)]
    let builder = match sso::environment(&app) {
        Ok(environment) => builder.with_environment(environment),
        Err(e) => {
            eprintln!("Fabric view: Windows sign-in is not available ({e}); Fabric will ask for the account.");
            builder
        }
    };
    let view = window.add_child(builder, position, size).map_err(|e| e.to_string())?;
    for cookie in signin::load() {
        let _ = view.set_cookie(cookie);
    }
    FABRIC_SHOWN.lock().unwrap().insert(label, page);
    view.navigate(target).map_err(|e| e.to_string())?;
    signin::keep_saving(app.clone());
    Ok(())
}

/// Pop-ups the Fabric view opens (sign-in windows, mostly). An allowed one gets a window the app
/// builds itself, with the same navigation and pop-up filter, rather than WebView2's own window,
/// which would share the sign-in but follow any link. The new webview is handed to WebView2 as the
/// pop-up (SetNewWindow), so `window.opener` and postMessage back to the opener keep working.
/// `window_features` puts it in the opener's WebView2 environment, so it shares the sign-in.
fn fabric_pop_up(
    app: &tauri::AppHandle,
    sign_in_hosts: &Arc<Vec<String>>,
    url: Url,
    features: tauri::webview::NewWindowFeatures,
) -> NewWindowResponse<tauri::Wry> {
    use std::sync::atomic::{AtomicUsize, Ordering};
    static NEXT: AtomicUsize = AtomicUsize::new(1);
    if !fabric_nav_allows(&url, true, sign_in_hosts) {
        return NewWindowResponse::Deny;
    }
    let label = format!("{FABRIC_VIEW}-popup-{}", NEXT.fetch_add(1, Ordering::Relaxed));
    let blank = Url::parse("about:blank").expect("about:blank parses");
    let (hosts, app_, hosts_) = (sign_in_hosts.clone(), app.clone(), sign_in_hosts.clone());
    let built = tauri::WebviewWindowBuilder::new(app, label, WebviewUrl::External(blank))
        .window_features(features)
        .title(url.host_str().unwrap_or("Sign in"))
        .on_navigation(move |u| fabric_nav_allows(u, false, &hosts))
        .on_new_window(move |u, f| fabric_pop_up(&app_, &hosts_, u, f))
        .on_document_title_changed(|window, title| {
            let _ = window.set_title(&title);
        })
        .build();
    match built {
        Ok(window) => NewWindowResponse::Create { window },
        Err(e) => {
            // Never fall back to WebView2's own, unfiltered window.
            eprintln!("Fabric view: could not open the pop-up ({e})");
            NewWindowResponse::Deny
        }
    }
}

/// Keeps the Fabric view's sign-in between launches. Microsoft's sign-in cookies are usually
/// session cookies, which WebView2 drops when the app closes, so every launch asked to sign
/// in again. The app saves them every few seconds, encrypted for this Windows user with DPAPI
/// (as Edge and Chrome protect their own cookies), and puts them back when the view opens.
mod signin {
    use std::path::PathBuf;
    use std::sync::Mutex;
    use std::time::Duration;

    use serde_json::{json, Value};
    use tauri::webview::cookie::{time::OffsetDateTime, Cookie, Expiration, SameSite};
    use tauri::Manager;

    fn file() -> PathBuf {
        super::data_dir().join("fabric-view-signin.bin")
    }

    /// Serialises the sign-in and Fabric cookies; other sites' cookies are left out.
    pub fn encode(cookies: &[Cookie<'_>]) -> Vec<u8> {
        let list: Vec<Value> = cookies
            .iter()
            .filter(|c| c.domain().is_some_and(super::fabric_host))
            .map(|c| {
                json!({
                    "name": c.name(), "value": c.value(), "domain": c.domain(), "path": c.path(),
                    "secure": c.secure().unwrap_or(false), "http_only": c.http_only().unwrap_or(false),
                    "same_site": match c.same_site() { Some(SameSite::Strict) => "strict", Some(SameSite::Lax) => "lax", _ => "none" },
                    "expires": c.expires_datetime().map(|t| t.unix_timestamp()),
                })
            })
            .collect();
        serde_json::to_vec(&list).unwrap_or_default()
    }

    /// The saved cookies that have not expired. Session cookies come back as session cookies.
    pub fn decode(bytes: &[u8], now: i64) -> Vec<Cookie<'static>> {
        let list: Vec<Value> = serde_json::from_slice(bytes).unwrap_or_default();
        list.iter()
            .filter_map(|c| {
                let text = |k: &str| c.get(k).and_then(Value::as_str).map(str::to_owned);
                let expires = c.get("expires").and_then(Value::as_i64);
                if expires.is_some_and(|t| t <= now) {
                    return None;
                }
                let mut cookie = Cookie::build((text("name")?, text("value")?))
                    .domain(text("domain")?)
                    .path(text("path").unwrap_or_else(|| "/".into()))
                    .secure(c.get("secure").and_then(Value::as_bool).unwrap_or(false))
                    .http_only(c.get("http_only").and_then(Value::as_bool).unwrap_or(false))
                    .same_site(match c.get("same_site").and_then(Value::as_str) {
                        Some("strict") => SameSite::Strict,
                        Some("lax") => SameSite::Lax,
                        _ => SameSite::None,
                    })
                    .build();
                cookie.set_expires(match expires.and_then(|t| OffsetDateTime::from_unix_timestamp(t).ok()) {
                    Some(t) => Expiration::DateTime(t),
                    None => Expiration::Session,
                });
                Some(cookie)
            })
            .collect()
    }

    /// Forgets the saved sign-in and signs the open view out (Sign out in the account menu).
    pub fn forget(app: &tauri::AppHandle) {
        let _ = std::fs::remove_file(file());
        *LAST.lock().unwrap() = Vec::new();
        // The pop-out's view shares the profile, so this signs it out too; its window closes.
        if let Some(window) = app.get_window(&super::pop_out_label("fabric")) {
            let _ = window.close();
        }
        if let Some(view) = app.get_webview(super::FABRIC_VIEW) {
            for cookie in view.cookies().unwrap_or_default() {
                if cookie.domain().is_some_and(super::fabric_host) {
                    let _ = view.delete_cookie(cookie);
                }
            }
            let _ = view.hide();
        }
    }

    pub fn load() -> Vec<Cookie<'static>> {
        let Ok(sealed) = std::fs::read(file()) else { return Vec::new() };
        match dpapi::unprotect(&sealed) {
            Ok(bytes) => decode(&bytes, OffsetDateTime::now_utc().unix_timestamp()),
            Err(e) => {
                eprintln!("Fabric view: could not read the saved sign-in ({e})");
                Vec::new()
            }
        }
    }

    static LAST: Mutex<Vec<u8>> = Mutex::new(Vec::new());
    static STARTED: Mutex<bool> = Mutex::new(false);

    fn save(cookies: &[Cookie<'_>]) {
        let bytes = encode(cookies);
        let mut last = LAST.lock().unwrap();
        if *last == bytes {
            return;
        }
        match dpapi::protect(&bytes) {
            Ok(sealed) => {
                let tmp = file().with_extension("tmp");
                if std::fs::write(&tmp, sealed).and_then(|_| std::fs::rename(&tmp, file())).is_ok() {
                    *last = bytes;
                }
            }
            Err(e) => eprintln!("Fabric view: could not save the sign-in ({e})"),
        }
    }

    /// Reads the view's cookies on a thread of its own: on Windows, reading them from a
    /// command or an event handler deadlocks (WebView2 answers on the main thread).
    pub fn keep_saving(app: tauri::AppHandle) {
        let mut started = STARTED.lock().unwrap();
        if *started || !dpapi::AVAILABLE {
            return;
        }
        *started = true;
        std::thread::spawn(move || loop {
            std::thread::sleep(Duration::from_secs(5));
            // Either view will do: they share one profile.
            let view = app.get_webview(super::FABRIC_VIEW).or_else(|| app.get_webview(super::FABRIC_POP_OUT_VIEW));
            if let Some(view) = view {
                if let Ok(cookies) = view.cookies() {
                    save(&cookies);
                }
            }
        });
    }

    #[cfg(windows)]
    mod dpapi {
        use windows::Win32::Foundation::{LocalFree, HLOCAL};
        use windows::Win32::Security::Cryptography::{
            CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
        };

        pub const AVAILABLE: bool = true;

        fn run(data: &[u8], seal: bool) -> Result<Vec<u8>, String> {
            let input = CRYPT_INTEGER_BLOB { cbData: data.len() as u32, pbData: data.as_ptr() as *mut u8 };
            let mut output = CRYPT_INTEGER_BLOB::default();
            unsafe {
                if seal {
                    CryptProtectData(&input, windows::core::w!("Fabric Desktop sign-in"), None, None, None,
                                     CRYPTPROTECT_UI_FORBIDDEN, &mut output)
                } else {
                    CryptUnprotectData(&input, None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, &mut output)
                }
                .map_err(|e| e.to_string())?;
                let bytes = std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
                LocalFree(Some(HLOCAL(output.pbData as _)));
                Ok(bytes)
            }
        }

        pub fn protect(data: &[u8]) -> Result<Vec<u8>, String> {
            run(data, true)
        }

        pub fn unprotect(data: &[u8]) -> Result<Vec<u8>, String> {
            run(data, false)
        }
    }

    /// Only Windows has DPAPI; elsewhere (development builds) nothing is saved.
    #[cfg(not(windows))]
    mod dpapi {
        pub const AVAILABLE: bool = false;

        pub fn protect(_: &[u8]) -> Result<Vec<u8>, String> {
            Err("not supported on this platform".into())
        }

        pub fn unprotect(_: &[u8]) -> Result<Vec<u8>, String> {
            Err("not supported on this platform".into())
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn keeps_sign_in_cookies_and_drops_expired_and_foreign_ones() {
            let session = Cookie::build(("ESTSAUTH", "abc")).domain("login.microsoftonline.com").path("/")
                .secure(true).http_only(true).same_site(SameSite::None).build();
            let mut expired = Cookie::build(("old", "1")).domain(".powerbi.com").path("/").build();
            expired.set_expires(OffsetDateTime::from_unix_timestamp(100).unwrap());
            let foreign = Cookie::build(("ad", "x")).domain("example.com").path("/").build();
            let back = decode(&encode(&[session, expired, foreign]), 1_000);
            assert_eq!(back.len(), 1);
            assert_eq!((back[0].name(), back[0].value(), back[0].domain()), ("ESTSAUTH", "abc", Some("login.microsoftonline.com")));
            assert_eq!(back[0].expires(), Some(Expiration::Session));
            assert_eq!((back[0].http_only(), back[0].secure(), back[0].same_site()), (Some(true), Some(true), Some(SameSite::None)));
        }
    }
}

/// The Fabric view gets its own WebView2 environment that may sign in with the Windows account
/// (AllowSingleSignOnUsingOSPrimaryAccount), so a work account signed in to Windows needs no
/// password. It uses its own profile folder: WebView2 refuses two option sets on one folder.
#[cfg(windows)]
mod sso {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        CreateCoreWebView2EnvironmentWithOptions, ICoreWebView2Environment, ICoreWebView2EnvironmentOptions,
    };
    use webview2_com::{CoreWebView2EnvironmentOptions, CreateCoreWebView2EnvironmentCompletedHandler};
    use windows::core::{HSTRING, PCWSTR};
    use windows::Win32::Foundation::{E_POINTER, E_UNEXPECTED};

    /// COM objects are not Send; this one is only created and used on the main thread.
    struct OnMainThread(ICoreWebView2Environment);
    unsafe impl Send for OnMainThread {}

    pub fn environment(app: &tauri::AppHandle) -> Result<ICoreWebView2Environment, String> {
        let folder = super::data_dir().join("fabric-view");
        let (tx, rx) = std::sync::mpsc::channel();
        app.run_on_main_thread(move || {
            let _ = tx.send(create(&folder).map(OnMainThread));
        })
        .map_err(|e| e.to_string())?;
        rx.recv().map_err(|e| e.to_string())?.map(|env| env.0)
    }

    fn create(folder: &std::path::Path) -> Result<ICoreWebView2Environment, String> {
        let options = CoreWebView2EnvironmentOptions::default();
        unsafe {
            // The arguments wry uses for the main view, except that SmartScreen stays on here
            // (SH-12): this view shows web pages, and it only visits Microsoft's own hosts.
            options.set_additional_browser_arguments("--disable-features=msWebOOUI,msPdfOOUI".to_string());
            options.set_allow_single_sign_on_using_os_primary_account(true);
        }
        let (tx, rx) = std::sync::mpsc::channel();
        unsafe {
            CreateCoreWebView2EnvironmentWithOptions(
                PCWSTR::null(),
                &HSTRING::from(folder.as_os_str()),
                &ICoreWebView2EnvironmentOptions::from(options),
                &CreateCoreWebView2EnvironmentCompletedHandler::create(Box::new(move |error_code, environment| {
                    let result = (|| {
                        error_code?;
                        environment.ok_or_else(|| windows::core::Error::from(E_POINTER))
                    })();
                    tx.send(result).map_err(|_| windows::core::Error::from(E_UNEXPECTED))
                })),
            )
        }
        .map_err(|e| e.to_string())?;
        webview2_com::wait_with_pump(rx).map_err(|e| e.to_string())?.map_err(|e| e.to_string())
    }
}

#[tauri::command]
async fn fabric_view_hide(app: tauri::AppHandle, window: tauri::Window) -> Result<(), String> {
    match app.get_webview(fabric_view_label(window.label())?) {
        Some(view) => view.hide().map_err(|e| e.to_string()),
        None => Ok(()),
    }
}

/// back, reload, home (the page's own start: page is "monitor" or "catalog"),
/// or browser (open the current page in the default browser).
#[tauri::command]
async fn fabric_view_action(app: tauri::AppHandle, window: tauri::Window, action: String, page: Option<String>) -> Result<(), String> {
    let label = fabric_view_label(window.label())?;
    let view = app.get_webview(label).ok_or("The Fabric view is not open")?;
    match action.as_str() {
        "back" => view.eval("history.back()").map_err(|e| e.to_string()),
        "reload" => view.reload().map_err(|e| e.to_string()),
        "home" => {
            let page = page.unwrap_or_else(|| "monitor".into());
            let url = fabric_page(&page, None)?;
            FABRIC_SHOWN.lock().unwrap().insert(label, page);
            view.navigate(url).map_err(|e| e.to_string())
        }
        "browser" => open_in_browser(&view.url().map_err(|e| e.to_string())?),
        _ => Err(format!("unknown action {action}")),
    }
}

/// The Fabric page the calling window's view shows, without its query or fragment, so the page can
/// offer to open the same item itself (Open in KratosForge). None when it isn't on Fabric.
#[tauri::command]
async fn fabric_view_url(app: tauri::AppHandle, window: tauri::Window) -> Result<Option<String>, String> {
    let label = fabric_view_label(window.label())?;
    let Some(view) = app.get_webview(label) else { return Ok(None) };
    Ok(fabric_page_address(&view.url().map_err(|e| e.to_string())?))
}

fn fabric_page_address(url: &Url) -> Option<String> {
    if url.scheme() != "https" || url.host_str() != Some("app.fabric.microsoft.com") {
        return None;
    }
    let mut page = url.clone();
    page.set_query(None);
    page.set_fragment(None);
    let _ = page.set_username("");
    let _ = page.set_password(None);
    let _ = page.set_port(None);
    Some(page.to_string())
}

/// Signs the Fabric view out and forgets its saved sign-in; the sidecar forgets its own.
#[tauri::command]
async fn fabric_view_sign_out(app: tauri::AppHandle) -> Result<(), String> {
    signin::forget(&app);
    Ok(())
}

// ---- Pop-out windows (Preview): a section of a page in a window of its own, to keep on another
// screen. Each is the app's own page (index.html?popout=<section>) in a second window, with the same
// policy as the main window; the UI draws only that section there. The Fabric one shows its own
// Fabric view (FABRIC_POP_OUT_VIEW). A window closes with the main window. docs/user-guide.md#pop-out-windows-preview

/// The sections that pop out, with their window titles: Monitor's From KratosForge list and
/// Fabric's Monitoring hub.
const POP_OUTS: [(&str, &str); 2] = [("runs", "From KratosForge"), ("fabric", "Fabric Monitoring hub")];
const POP_OUT_PREFIX: &str = "pop-out-";

fn pop_out_label(section: &str) -> String {
    format!("{POP_OUT_PREFIX}{section}")
}

fn pop_out_title(section: &str) -> Result<&'static str, String> {
    POP_OUTS.iter().find(|(s, _)| *s == section).map(|(_, t)| *t).ok_or_else(|| format!("Not a section that pops out: {section}"))
}

/// Where each pop-out window was when it last closed, in logical pixels, kept on this PC.
fn pop_out_places_file() -> PathBuf {
    data_dir().join("pop-out-windows.json")
}

fn pop_out_places() -> Value {
    std::fs::read_to_string(pop_out_places_file()).ok().and_then(|t| serde_json::from_str(t.trim_start_matches('\u{feff}')).ok()).unwrap_or(json!({}))
}

/// A saved place, if it is a sensible size: [x, y, width, height].
fn saved_place(places: &Value, section: &str) -> Option<[f64; 4]> {
    let p = places.get(section)?;
    let n = |k: &str| p.get(k).and_then(Value::as_f64).filter(|v| v.is_finite());
    let place = [n("x")?, n("y")?, n("width")?, n("height")?];
    (place[2] >= 200.0 && place[3] >= 150.0 && place[2] <= 20000.0 && place[3] <= 20000.0).then_some(place)
}

/// True when the window's top-left corner, plus a bit of its title bar, is on one of the screens,
/// so a window last left on a screen that has since been unplugged opens centered instead.
fn place_on_screen(place: [f64; 4], screens: &[(f64, f64, f64, f64, f64)]) -> bool {
    screens.iter().any(|&(x, y, w, h, scale)| {
        let (px, py) = ((place[0] + 40.0) * scale, (place[1] + 10.0) * scale);
        px >= x && py >= y && px < x + w && py < y + h
    })
}

fn save_pop_out_place(window: &tauri::Window) {
    let Some(section) = window.label().strip_prefix(POP_OUT_PREFIX) else { return };
    let (Ok(scale), Ok(pos), Ok(size)) = (window.scale_factor(), window.outer_position(), window.inner_size()) else { return };
    if window.is_minimized().unwrap_or(false) || window.is_maximized().unwrap_or(false) {
        return;
    }
    let (pos, size) = (pos.to_logical::<f64>(scale), size.to_logical::<f64>(scale));
    let mut places = pop_out_places();
    if !places.is_object() {
        places = json!({});
    }
    places[section] = json!({"x": pos.x, "y": pos.y, "width": size.width, "height": size.height});
    let file = pop_out_places_file();
    let tmp = file.with_extension("tmp");
    if std::fs::write(&tmp, places.to_string()).and_then(|_| std::fs::rename(&tmp, &file)).is_err() {
        eprintln!("Pop-out window: could not remember where {section} was");
    }
}

/// The app's own pages, which are all a pop-out window shows (tauri://localhost elsewhere,
/// http://tauri.localhost on Windows).
fn is_app_page(url: &Url) -> bool {
    url.username().is_empty() && url.password().is_none() && url.port().is_none()
        && matches!((url.scheme(), url.host_str()), ("tauri", Some("localhost")) | ("http" | "https", Some("tauri.localhost")))
}

/// A pop-out window stays on the app's own pages; a web link opens in the default browser.
fn app_page(url: &Url) -> bool {
    if is_app_page(url) {
        return true;
    }
    if matches!(url.scheme(), "https" | "http") {
        let _ = open_in_browser(url);
    }
    false
}

/// Opens a section in its own window, or brings its window forward if it is open already.
#[tauri::command]
async fn pop_out_open(app: tauri::AppHandle, section: String, on_top: Option<bool>) -> Result<(), String> {
    let title = pop_out_title(&section)?;
    let label = pop_out_label(&section);
    if let Some(window) = app.get_window(&label) {
        let _ = window.unminimize();
        window.show().map_err(|e| e.to_string())?;
        return window.set_focus().map_err(|e| e.to_string());
    }
    let url = WebviewUrl::App(format!("index.html?popout={section}").into());
    let builder = tauri::WebviewWindowBuilder::new(&app, &label, url)
        .title(format!("KratosForge · {title}"))
        .min_inner_size(360.0, 260.0)
        .always_on_top(on_top.unwrap_or(false))
        .on_navigation(app_page);
    let screens: Vec<_> = app.available_monitors().unwrap_or_default().iter()
        .map(|m| (m.position().x as f64, m.position().y as f64, m.size().width as f64, m.size().height as f64, m.scale_factor()))
        .collect();
    let builder = match saved_place(&pop_out_places(), &section).filter(|p| place_on_screen(*p, &screens)) {
        Some([x, y, w, h]) => builder.position(x, y).inner_size(w, h),
        None => builder.inner_size(if section == "fabric" { 1100.0 } else { 720.0 }, 640.0).center(),
    };
    builder.build().map_err(|e| e.to_string())?;
    let _ = app.emit("pop-out", json!({"section": section, "open": true}));
    Ok(())
}

/// Closes a section's window, which puts the section back in the main window (Dock back).
#[tauri::command]
async fn pop_out_close(app: tauri::AppHandle, section: String) -> Result<(), String> {
    pop_out_title(&section)?;
    match app.get_window(&pop_out_label(&section)) {
        Some(window) => window.close().map_err(|e| e.to_string()),
        None => Ok(()),
    }
}

/// Keep on top, for the window that asks.
#[tauri::command]
async fn pop_out_on_top(window: tauri::Window, on: bool) -> Result<(), String> {
    if !window.label().starts_with(POP_OUT_PREFIX) {
        return Err("Only a pop-out window stays on top".into());
    }
    window.set_always_on_top(on).map_err(|e| e.to_string())
}

/// The sections open in their own windows right now.
#[tauri::command]
fn pop_out_list(app: tauri::AppHandle) -> Vec<String> {
    POP_OUTS.iter().filter(|(s, _)| app.get_window(&pop_out_label(s)).is_some()).map(|(s, _)| s.to_string()).collect()
}

/// A Fabric link clicked in a pop-out window opens in the main window's Fabric view, which brings
/// the main window forward. Only Fabric pages (fabric_page) are passed on.
#[tauri::command]
async fn pop_out_open_in_fabric(app: tauri::AppHandle, page: String) -> Result<(), String> {
    fabric_page(&page, None)?;
    let main = app.get_webview_window("main").ok_or("The main window is not open")?;
    main.emit_to("main", "pop-out-open-in-fabric", page).map_err(|e| e.to_string())?;
    let _ = main.unminimize();
    main.set_focus().map_err(|e| e.to_string())
}

fn on_window_event(window: &tauri::Window, event: &tauri::WindowEvent) {
    match event {
        tauri::WindowEvent::CloseRequested { .. } => save_pop_out_place(window),
        tauri::WindowEvent::Destroyed => {
            let app = window.app_handle();
            if let Some(section) = window.label().strip_prefix(POP_OUT_PREFIX) {
                let _ = app.emit("pop-out", json!({"section": section, "open": false}));
            } else if window.label() == "main" {
                // Pop-out windows don't outlive the app: closing the main window closes them.
                for (label, other) in app.windows() {
                    if label.starts_with(POP_OUT_PREFIX) {
                        save_pop_out_place(&other);
                        let _ = other.destroy();
                    }
                }
            }
        }
        _ => {}
    }
}

/// Opens a link from Settings or Help in the default browser: https only, or a mailto: address
/// for an organization's support contact.
#[tauri::command]
fn open_link(url: String) -> Result<(), String> {
    let url = Url::parse(&url).map_err(|e| e.to_string())?;
    match url.scheme() {
        "https" => open_in_browser(&url),
        "mailto" => open_with_handler(url.as_str(), "the mail app"),
        other => Err(format!("Not opening a {other} link")),
    }
}

/// Hands a link to the app Windows has registered for its scheme (mailto:, vscode:).
fn open_with_handler(link: &str, what: &str) -> Result<(), String> {
    let mut cmd = if cfg!(windows) {
        let mut c = Command::new(system_tool("rundll32.exe"));
        c.args(["url.dll,FileProtocolHandler", link]);
        c
    } else {
        let mut c = Command::new("xdg-open");
        c.arg(link);
        c
    };
    hide_window(&mut cmd);
    cmd.spawn().map(|_| ()).map_err(|e| format!("Could not open {what}: {e}"))
}

/// "Open in VS Code" on a notebook: opens a notebook file, or a folder of notebooks, in VS Code
/// (or VS Code Insiders). Only an existing .ipynb file or folder inside a notebooks folder the app
/// knows, so a page can't open anything else.
#[tauri::command]
fn open_in_vscode(path: String, insiders: bool) -> Result<(), String> {
    let p = PathBuf::from(&path);
    let notebook = p.is_file() && p.extension().is_some_and(|e| e.eq_ignore_ascii_case("ipynb"));
    if !(notebook || p.is_dir()) || !p.is_absolute() {
        return Err(format!("Not a notebook or folder: {path}"));
    }
    if !vscode_link::inside(&p, &notebook_folders()) {
        return Err(format!("Not in the notebooks folder or a repository opened in Source control: {path}"));
    }
    let link = vscode_link::open_link(&p, insiders)?;
    open_with_handler(&link, if insiders { "VS Code Insiders. Is it installed?" } else { "VS Code. Is it installed?" })
}

/// The folders Open in VS Code may open: the app's notebooks folder and the repositories opened
/// in Source control (git_repos.json, as git_service.py keeps it).
fn notebook_folders() -> Vec<PathBuf> {
    let dir = data_dir();
    let repos: Value = std::fs::read_to_string(dir.join("git_repos.json")).ok()
        .and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Value::Null);
    let mut out = vec![dir.join("notebooks")];
    out.extend(repos["repos"].as_array().into_iter().flatten().filter_map(|r| r["path"].as_str()).map(PathBuf::from));
    out
}

/// "Add to VS Code" (Settings > Integrations): asks VS Code, or VS Code Insiders, to add the
/// KratosForge MCP server for a connected tool. VS Code shows the entry and asks before adding it.
/// The link is built here from the id and the launcher's fixed path (vscode_link.rs).
#[tauri::command]
fn add_to_vscode(client_id: String, insiders: bool) -> Result<(), String> {
    let launcher = data_dir().join("bin").join(if cfg!(windows) { "fabric-desktop-mcp.cmd" } else { "fabric-desktop-mcp" });
    if !launcher.is_file() {
        return Err("Turn on Let AI tools connect first: that sets up the command VS Code runs.".into());
    }
    let link = vscode_link::install_link(&launcher, &client_id, insiders)?;
    open_with_handler(&link, if insiders { "VS Code Insiders. Is it installed?" } else { "VS Code. Is it installed?" })
}

/// Browse… beside a folder box (#971): the Windows folder dialog, owned by the window that asked.
/// Returns the chosen folder, or None when the person cancels. The dialog gets a thread of its
/// own, so it never shares a COM apartment with anything else.
#[tauri::command]
async fn pick_folder(window: tauri::Window, start: Option<String>) -> Result<Option<String>, String> {
    #[cfg(windows)]
    let owner = window.hwnd().map_err(|e| e.to_string())?.0 as isize;
    #[cfg(not(windows))]
    let owner = { let _ = &window; 0 };
    tauri::async_runtime::spawn_blocking(move || std::thread::spawn(move || folder_pick::pick(owner, start)).join())
        .await.map_err(|e| e.to_string())?
        .map_err(|_| "The folder dialog stopped unexpectedly".to_string())?
}

/// Opens one of this repository's docs pages in the default browser, such as
/// "xray-fixes.md#small-files". Only pages under docs/ on GitHub.
#[tauri::command]
fn open_doc(page: String) -> Result<(), String> {
    let ok = !page.is_empty()
        && !page.contains("..")
        && page.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_.#/".contains(&b));
    if !ok {
        return Err(format!("Not a docs page: {page}"));
    }
    let url = Url::parse(&format!("https://github.com/KratosDataGod/KratosForge/blob/main/docs/{page}")).map_err(|e| e.to_string())?;
    open_in_browser(&url)
}

/// Notebook HTML output (pandas, Plotly, a %%html cell) is untrusted: anyone can write a notebook.
/// The UI shows it in a sandboxed frame loaded from this scheme (notebook.js, htmlFrame), an origin
/// that isn't the app's, so the app's policy and the Tauri API don't apply to it. The frame gets the
/// HTML from the page by postMessage and writes it into itself, keeping this page's policy: inline
/// scripts and styles (some charting libraries need eval), images and fonts only as data: or blob:, no network.
const OUTPUT_SCHEME: &str = "nbout";
const OUTPUT_CSP: &str = "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; \
    img-src data: blob:; font-src data:; media-src data: blob:; base-uri 'none'; form-action 'none'; \
    frame-ancestors http://tauri.localhost https://tauri.localhost tauri://localhost";
const OUTPUT_FRAME: &str = r#"<!doctype html><meta charset="utf-8"><script>
addEventListener("message", function take(e) {
  if (e.source !== parent || !e.data || typeof e.data.fdHtml !== "string") return;
  removeEventListener("message", take);
  document.open();
  document.write(e.data.fdHtml + "<script>(function(){function s(){parent.postMessage({fdFrameHeight:document.documentElement.scrollHeight},'*')}" +
    "addEventListener('load',s);new ResizeObserver(s).observe(document.documentElement);})();<\/script>");
  document.close();
});
</script>"#;

/// The frame page, for the app's own window only: the Fabric view can't load it.
fn output_frame(webview: &str) -> tauri::http::Response<Vec<u8>> {
    let builder = tauri::http::Response::builder();
    if webview != "main" {
        return builder.status(403).body(Vec::new()).unwrap();
    }
    builder
        .header("Content-Type", "text/html; charset=utf-8")
        .header("Content-Security-Policy", OUTPUT_CSP)
        .header("X-Content-Type-Options", "nosniff")
        .body(OUTPUT_FRAME.as_bytes().to_vec())
        .unwrap()
}

/// A kratosforge:// link the app was started with, waiting for the page to ask for it (the page
/// isn't listening yet when the app starts).
struct PendingLink(Mutex<Option<String>>);

#[tauri::command]
fn take_pending_link(state: tauri::State<'_, PendingLink>) -> Option<String> {
    state.0.lock().ok().and_then(|mut l| l.take())
}

/// Shows a link from another copy of the app (deep_link.rs): brings the window forward and tells
/// the page what to show. Nothing the link says is run.
fn show_link(app: &tauri::AppHandle, link: deep_link::Link) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
    let _ = app.emit_to("main", "deep-link", link.to_json());
}

/// Makes Windows open kratosforge:// links with this exe, for this Windows user. Release builds
/// only, so a developer build never takes the links over. An organization that doesn't want the
/// links sets `"register_link_scheme": false` in its policy file; the app then removes its own
/// registration instead.
fn register_link_scheme() {
    if !cfg!(windows) || cfg!(debug_assertions) {
        return;
    }
    let Ok(exe) = std::env::current_exe() else { return };
    let allowed = policy_allows_link_scheme(&policy_text());
    std::thread::spawn(move || {
        let steps = if allowed { deep_link::register_args(&exe.to_string_lossy()) } else { vec![deep_link::unregister_args()] };
        for args in steps {
            let mut cmd = Command::new(system_tool("reg.exe"));
            cmd.args(&args);
            hide_window(&mut cmd);
            if let Err(e) = cmd.status() {
                eprintln!("kratosforge:// links not registered: {e}");
                return;
            }
        }
    });
}

/// Why a link isn't opened when the policy file turns links off.
const LINKS_OFF: &str = "Your organization turned off kratosforge:// links.";

/// This copy's instance lock, kept for the life of the process except while an update hands over.
static INSTANCE: deep_link::InstanceLock = deep_link::InstanceLock::new();

/// Makes this the one running copy of the app (deep_link::claim_instance), so two never share the
/// sign-in cache and data. When another copy runs, hands it `url` (or just brings it to the front)
/// and returns None: this copy stops. A copy started by an update waits for the old one to let go;
/// any other copy leaves the lock alone while an update hands it over (deep_link::handing_over).
fn claim_single_instance(url: Option<&str>, updating: bool) -> Option<Option<std::fs::File>> {
    // 60 s for a copy an update started (the old copy lets go as it starts this one), or 5 s while
    // another copy starts listening or an update hands over.
    let tries = if updating { 240 } else { 20 };
    match deep_link::claim_or_forward(&data_dir(), url.unwrap_or("kratosforge://"), updating, tries, std::time::Duration::from_millis(250)) {
        deep_link::Claim::Run(lock) => Some(lock),
        deep_link::Claim::Stop => None,
    }
}

fn main() {
    // A link is read before anything else. One that doesn't read as a link is never handed on:
    // the app only says it wasn't opened (and why, when this copy is the one that runs).
    // An organization that turned links off (register_link_scheme: false) gets none: no listener on
    // 127.0.0.1, and a link given on the command line is only refused.
    let links_on = policy_allows_link_scheme(&policy_text());
    let link = deep_link::link_arg(&std::env::args().collect::<Vec<_>>()).map(|url| match deep_link::parse(&url) {
        Ok(_) if !links_on => (deep_link::REFUSED.to_string(), deep_link::Link::Refused(LINKS_OFF.into())),
        Ok(parsed) => (url, parsed),
        Err(e) => (deep_link::REFUSED.to_string(), deep_link::Link::Refused(e)),
    });
    let Some(instance) = claim_single_instance(link.as_ref().map(|(url, _)| url.as_str()), updated_to_arg().is_some()) else {
        return;
    };
    INSTANCE.hold(instance);
    let pending = link.map(|(_, parsed)| parsed.to_json());
    if let Ok(exe) = std::env::current_exe() {
        // Left behind by an update. exe.old is not removed here: it is kept until this exe has
        // shown its window (check_components), so a build that can't start can be put back.
        let _ = std::fs::remove_file(exe.with_extension("exe.new"));
        let _ = std::fs::remove_file(exe.with_extension("exe.failed"));
    }
    tauri::Builder::default()
        .manage(PendingLink(Mutex::new(pending)))
        .setup(move |app| {
            let handle = app.handle().clone();
            if !links_on {
                deep_link::forget_port(&data_dir());
            } else if let Err(e) = deep_link::listen(&data_dir(), move |link| show_link(&handle, link)) {
                eprintln!("kratosforge:// links from other copies won't arrive: {e}");
            }
            register_link_scheme();
            Ok(())
        })
        .manage(SidecarState { sidecar: Mutex::new(None), child: Mutex::new(None), updating: Default::default(), installing: Default::default() })
        .register_uri_scheme_protocol(OUTPUT_SCHEME, |ctx, _| output_frame(ctx.webview_label()))
        .on_window_event(on_window_event)
        .invoke_handler(tauri::generate_handler![
            rpc, stop_kernel, check_components, check_for_update, update_sidecar, update_app,
            fabric_view_show, fabric_view_hide, fabric_view_action, fabric_view_url, open_doc, fabric_view_sign_out, open_link, add_to_vscode, open_in_vscode, take_pending_link, pick_folder,
            pop_out_open, pop_out_close, pop_out_on_top, pop_out_list, pop_out_open_in_fabric
        ])
        .run(tauri::generate_context!())
        .expect("error while running KratosForge");
}

#[cfg(test)]
mod python_choice_tests {
    use super::*;

    fn engine() -> Option<PathBuf> {
        Some(PathBuf::from(r"C:\Users\a\AppData\Local\FabricDesktop\engine\abc\python.exe"))
    }

    fn absolute() -> &'static str {
        if cfg!(windows) { r"C:\Python312\python.exe" } else { "/usr/bin/python3" }
    }

    #[test]
    fn release_never_picks_bare_python() {
        // Every kind of input a release build can see.
        let engine_path = engine().unwrap().to_string_lossy().into_owned();
        for env in [None, Some(""), Some("  "), Some("python"), Some("python.exe"), Some(r".\python.exe"), Some("py"), Some(absolute())] {
            for eng in [None, engine()] {
                if let Ok(chosen) = choose_python(env, eng.clone(), false) {
                    assert!(
                        std::path::Path::new(&chosen).is_absolute() || chosen == engine_path,
                        "release picked {chosen:?} for env={env:?} engine={eng:?}"
                    );
                    assert_ne!(chosen, "python");
                }
            }
        }
    }

    #[test]
    fn release_with_no_engine_and_no_override_is_a_clear_error() {
        assert_eq!(choose_python(None, None, false).unwrap_err(), NO_ENGINE);
        assert_eq!(choose_python(Some(""), None, false).unwrap_err(), NO_ENGINE);
        assert!(NO_ENGINE.contains("engine isn't installed"));
    }

    #[test]
    fn release_uses_the_engine() {
        assert_eq!(choose_python(None, engine(), false).unwrap(), engine().unwrap().to_string_lossy());
    }

    #[test]
    fn release_honours_an_absolute_override_only() {
        assert_eq!(choose_python(Some(absolute()), None, false).unwrap(), absolute());
        assert_eq!(choose_python(Some(absolute()), engine(), false).unwrap(), absolute());
        for bare in ["python", "python3", r".\python.exe"] {
            let err = choose_python(Some(bare), engine(), false).unwrap_err();
            assert!(err.contains("isn't a full path"), "{err}");
        }
    }

    #[test]
    fn debug_keeps_the_developer_fallbacks() {
        assert_eq!(choose_python(None, None, true).unwrap(), "python");
        assert_eq!(choose_python(Some("python3"), None, true).unwrap(), "python3");
        assert_eq!(choose_python(None, engine(), true).unwrap(), engine().unwrap().to_string_lossy());
    }
}

#[cfg(test)]
mod integrity_tests {
    use super::*;

    const BUILD: &str = "13cd5dfa54ee4be7c066919bd3828eda3e5c1326";

    #[test]
    fn hashes_a_file_as_lowercase_hex() {
        let path = std::env::temp_dir().join(format!("fd-sha-{}", std::process::id()));
        std::fs::write(&path, b"abc").unwrap();
        let sha = sha256_file(&path).unwrap();
        let _ = std::fs::remove_file(&path);
        assert_eq!(sha, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn an_engine_matching_its_release_checksum_is_accepted() {
        let release = json!({ "commit": BUILD, "engine_sha256": "AB".repeat(32) });
        assert_eq!(check_engine_zip(&"ab".repeat(32), &release), Ok(Some(BUILD.to_string())));
    }

    #[test]
    fn an_engine_not_matching_its_release_checksum_is_refused() {
        let release = json!({ "commit": BUILD, "engine_sha256": "ab".repeat(32) });
        let err = check_engine_zip(&"cd".repeat(32), &release).unwrap_err();
        assert!(err.contains("doesn't match the checksum"), "{err}");
    }

    #[test]
    fn this_builds_engine_must_match_the_checksum_baked_into_the_exe() {
        let (pin, other) = ("ab".repeat(32), "cd".repeat(32));
        assert_eq!(check_pinned_engine(&pin.to_uppercase(), &pin, Some(BUILD), BUILD), Ok(()));
        assert_eq!(check_pinned_engine(&pin, &pin, None, BUILD), Ok(()));
        let err = check_pinned_engine(&other, &pin, Some(&BUILD.to_uppercase()), BUILD).unwrap_err();
        assert!(err.contains("isn't the engine this app was built with"), "{err}");
        assert!(check_pinned_engine(&other, &pin, None, BUILD).is_err());  // no build named: treated as this one
        // Another build's engine (self-updating to a newer one) and local builds with no pin.
        assert_eq!(check_pinned_engine(&other, &pin, Some(&"1".repeat(40)), BUILD), Ok(()));
        assert_eq!(check_pinned_engine(&other, "", Some(BUILD), BUILD), Ok(()));
    }

    #[test]
    fn a_release_without_an_engine_checksum_is_refused() {
        let release = json!({ "commit": BUILD, "notes": "first stable" });
        let err = check_engine_zip(&"cd".repeat(32), &release).unwrap_err();
        assert!(err.contains("gives no checksum"), "{err}");
        assert!(check_engine_zip(&"cd".repeat(32), &json!({ "commit": "not-a-commit" })).is_err());
    }

    #[test]
    fn only_a_missing_release_file_falls_back_to_another_channel() {
        assert!(missing_release_file("Could not download the engine: curl: (22) The requested URL returned error: 404"));
        for err in [
            "Could not download the engine: curl: (22) The requested URL returned error: 503",
            "Could not download the engine: curl: (28) Operation too slow. Less than 1024 bytes/sec transferred the last 60 seconds",
            "Could not download the engine: curl: (6) Could not resolve host: github.com",
            "Could not download the engine: curl: (7) Failed to connect to github.com port 443",
        ] {
            assert!(!missing_release_file(err), "{err}");
        }
    }

    #[test]
    fn notebook_output_frame_has_no_network_and_only_opens_in_the_app() {
        let page = output_frame("main");
        assert_eq!(page.status(), 200);
        let csp = page.headers()["Content-Security-Policy"].to_str().unwrap();
        assert!(csp.starts_with("default-src 'none';"), "{csp}");
        let sources = csp.split("frame-ancestors").next().unwrap();
        assert!(!sources.contains("connect-src") && !sources.contains("http") && !sources.contains("*"), "{csp}");
        assert_eq!(output_frame(FABRIC_VIEW).status(), 403);
    }

    #[test]
    fn windows_tools_run_from_system32() {
        let tool = system_tool("curl.exe");
        assert!(tool.ends_with("System32/curl.exe") || tool.ends_with("System32\\curl.exe"), "{}", tool.display());
        assert!(tool.is_absolute() || std::env::var_os("SystemRoot").is_none());
    }
}

#[cfg(test)]
mod fabric_nav_tests {
    use super::*;

    #[test]
    fn the_page_address_is_fabric_only_and_drops_the_query() {
        let page = |u: &str| fabric_page_address(&Url::parse(u).unwrap());
        assert_eq!(page("https://app.fabric.microsoft.com/groups/ws/datawarehouses/wh?experience=fabric-developer&ctid=t#x"),
                   Some("https://app.fabric.microsoft.com/groups/ws/datawarehouses/wh".into()));
        assert_eq!(page("https://login.microsoftonline.com/common/oauth2"), None);
        assert_eq!(page("http://app.fabric.microsoft.com/groups/ws"), None);
        assert_eq!(page("https://u:p@app.fabric.microsoft.com:8443/groups/ws"),
                   Some("https://app.fabric.microsoft.com/groups/ws".into()));
    }

    fn nav(url: &str) -> FabricNav {
        fabric_nav(&Url::parse(url).unwrap(), false, &[])
    }

    #[test]
    fn fabric_pages_and_sign_in_stay_in_the_view() {
        for url in [
            FABRIC_MONITOR,
            FABRIC_CATALOG,
            "https://app.fabric.microsoft.com/groups/me/list?experience=fabric-developer",
            "https://APP.Fabric.Microsoft.com:443/home",
            "https://app.powerbi.com/groups/me/reports/1",
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id=x",
            "https://login.microsoft.com/common/oauth2/authorize",
            "https://login.windows.net/common/oauth2/authorize",
            "https://device.login.microsoftonline.com/?request=x",
            "https://autologon.microsoftazuread-sso.com/contoso.com/winauth/sso?client-request-id=x",
            "https://login.live.com/oauth20_authorize.srf?client_id=x",
            "about:blank",
        ] {
            assert_eq!(nav(url), FabricNav::Stay, "{url}");
        }
        let item = fabric_page("https://app.fabric.microsoft.com/groups/1/lakehouses/2", Some("72f988bf-86f1-41af-91ab-2d7cd011db47")).unwrap();
        assert_eq!(fabric_nav(&item, false, &[]), FabricNav::Stay);
    }

    #[test]
    fn look_alike_and_other_hosts_open_in_the_browser() {
        for url in [
            "https://app.fabric.microsoft.com.evil.com/",
            "https://evil.com/app.fabric.microsoft.com",
            "https://app-fabric.microsoft.com/",
            "https://appfabric.microsoft.com/",
            "https://evil.app.fabric.microsoft.com/",
            "https://fabric.microsoft.com/",
            "https://microsoft.com/",
            "https://learn.microsoft.com/fabric/",
            "https://login.microsoftonline.com.evil.com/",
            "https://login.windows.net.evil.com/",
            "https://login.live.com.evil.com/",
            "https://evil-microsoftazuread-sso.com/",
            "https://x.autologon.microsoftazuread-sso.com/",
            "https://windows.net/",
            "https://account.live.com/",  // managing a personal account isn't signing in
            "https://app.fabric.microsoft.com./",
            "https://app.fabric.microsoft.com:8443/",
            "https://\u{0430}pp.fabric.microsoft.com/", // Cyrillic a
            "http://example.com/",
        ] {
            assert_eq!(nav(url), FabricNav::Browser, "{url}");
        }
    }

    #[test]
    fn user_info_never_counts_as_the_host() {
        // The host is evil.com; the allowed name is only the user name.
        assert_eq!(nav("https://app.fabric.microsoft.com@evil.com/"), FabricNav::Browser);
        assert_eq!(nav("https://app.fabric.microsoft.com:pw@evil.com/"), FabricNav::Browser);
        assert_eq!(nav("https://login.microsoftonline.com%2F@evil.com/"), FabricNav::Browser);
        // Allowed host, but with credentials in the link: not kept in the view.
        assert_eq!(nav("https://user@app.fabric.microsoft.com/"), FabricNav::Browser);
        assert_eq!(nav("https://user:pw@login.microsoftonline.com/"), FabricNav::Browser);
    }

    #[test]
    fn plain_http_and_other_schemes_are_refused() {
        for url in [
            "http://app.fabric.microsoft.com/monitoringhub",
            "http://login.microsoftonline.com/common",
            "javascript:alert(1)",
            "file:///C:/Windows/System32/calc.exe",
            "data:text/html,<script>alert(1)</script>",
            "ms-settings:privacy",
            "about:srcdoc",
            "blob:https://evil.com/0b0b",
        ] {
            assert_eq!(nav(url), FabricNav::Refuse, "{url}");
        }
    }

    #[test]
    fn pop_ups_follow_the_same_rule_but_not_blank_ones() {
        let popup = |u: &str| fabric_nav(&Url::parse(u).unwrap(), true, &[]);
        assert_eq!(popup("https://login.microsoftonline.com/common/oauth2/authorize"), FabricNav::Stay);
        assert_eq!(popup("https://example.com/"), FabricNav::Browser);
        assert_eq!(popup("about:blank"), FabricNav::Refuse);
        assert_eq!(popup("javascript:alert(1)"), FabricNav::Refuse);
    }

    #[test]
    fn pop_ups_never_get_webview2s_own_unfiltered_window() {
        // Allow hands the pop-up to WebView2's own window, which has no navigation filter;
        // allowed pop-ups must be Create with fabric_pop_up's filtered window instead.
        let allow = ["NewWindowResponse", "::Allow"].concat();
        assert!(!include_str!("main.rs").contains(&allow));
    }

    #[test]
    fn each_window_has_its_own_fabric_view() {
        assert_eq!(fabric_view_label("main"), Ok(FABRIC_VIEW));
        assert_eq!(fabric_view_label("pop-out-fabric"), Ok(FABRIC_POP_OUT_VIEW));
        assert!(fabric_view_label("pop-out-runs").is_err());
        assert!(fabric_view_label(FABRIC_VIEW).is_err());
        // The pop-out's view is not one of the Fabric view's pop-up windows, nor a pop-out window.
        assert!(!FABRIC_POP_OUT_VIEW.starts_with(&format!("{FABRIC_VIEW}-popup-")));
        assert!(!FABRIC_POP_OUT_VIEW.starts_with(POP_OUT_PREFIX));
    }

    #[test]
    fn only_listed_sections_pop_out() {
        assert_eq!(pop_out_title("runs"), Ok("From KratosForge"));
        assert_eq!(pop_out_title("fabric"), Ok("Fabric Monitoring hub"));
        for bad in ["", "main", "../runs", "runs?x=1", "Fabric"] {
            assert!(pop_out_title(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn pop_out_windows_stay_on_the_apps_own_pages() {
        let page = |u: &str| is_app_page(&Url::parse(u).unwrap());
        assert!(page("tauri://localhost/index.html?popout=runs"));
        assert!(page("http://tauri.localhost/index.html?popout=fabric"));
        assert!(page("https://tauri.localhost/index.html"));
        for other in ["https://app.fabric.microsoft.com/", "http://tauri.localhost.evil.com/", "https://x@tauri.localhost/",
                      "http://tauri.localhost:8080/", "file:///C:/index.html", "javascript:alert(1)", "about:blank"] {
            assert!(!page(other), "{other}");
        }
    }

    #[test]
    fn pop_out_windows_reopen_where_they_were_if_that_is_on_a_screen() {
        let places = json!({"runs": {"x": 100.0, "y": 50.0, "width": 700.0, "height": 600.0},
                            "fabric": {"x": 1.0, "y": 1.0, "width": 10.0, "height": 600.0},
                            "odd": {"x": "1", "y": 1, "width": 700, "height": 600}});
        assert_eq!(saved_place(&places, "runs"), Some([100.0, 50.0, 700.0, 600.0]));
        assert_eq!(saved_place(&places, "fabric"), None); // too small to use
        assert_eq!(saved_place(&places, "odd"), None);
        assert_eq!(saved_place(&json!([]), "runs"), None);
        let one_screen = [(0.0, 0.0, 1920.0, 1080.0, 1.0)];
        assert!(place_on_screen([100.0, 50.0, 700.0, 600.0], &one_screen));
        assert!(!place_on_screen([2500.0, 50.0, 700.0, 600.0], &one_screen)); // a screen since unplugged
        // A second screen to the right at 150%: logical 1400 is physical 2100.
        let two = [(0.0, 0.0, 1920.0, 1080.0, 1.0), (1920.0, 0.0, 2880.0, 1620.0, 1.5)];
        assert!(place_on_screen([1400.0, 100.0, 700.0, 600.0], &two));
    }

    #[test]
    fn the_organizations_sign_in_hosts_follow_the_same_rule() {
        let hosts = policy_sign_in_hosts(r#"{"fabric_view_sign_in_hosts": ["adfs.contoso.com", "Contoso.Okta.com", "*.ping.com"]}"#);
        let with = |u: &str| fabric_nav(&Url::parse(u).unwrap(), false, &hosts);
        assert_eq!(with("https://adfs.contoso.com/adfs/ls/?SAMLRequest=x"), FabricNav::Stay);
        assert_eq!(with("https://ADFS.contoso.com:443/adfs/ls/"), FabricNav::Stay);
        assert_eq!(with("https://contoso.okta.com/app/office365/sso/wsfed/passive"), FabricNav::Stay);
        assert_eq!(fabric_nav(&Url::parse("https://contoso.okta.com/").unwrap(), true, &hosts), FabricNav::Stay);
        assert_eq!(with(FABRIC_MONITOR), FabricNav::Stay); // the built-in hosts still apply
        for url in [
            "https://adfs.contoso.com.evil.com/",
            "https://evil.adfs.contoso.com/",
            "https://contoso.com/",
            "https://adfs-contoso.com/",
            "https://adfs.contoso.com@evil.com/",
            "https://user@adfs.contoso.com/",
            "https://adfs.contoso.com:8443/",
            "https://adfs.contoso.com./",
            "https://x.ping.com/",
            "https://evil-okta.com/",
        ] {
            assert_eq!(with(url), FabricNav::Browser, "{url}");
        }
        assert_eq!(with("http://adfs.contoso.com/adfs/ls/"), FabricNav::Refuse);
        // Without the policy, the same host leaves the view.
        assert_eq!(nav("https://adfs.contoso.com/adfs/ls/"), FabricNav::Browser);
    }
}
