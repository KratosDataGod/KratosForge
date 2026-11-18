// HD-022 (SH-5): which channel release this PC may install, and keeping the previous exe until
// the new one has started. Only std and serde_json, so the rules are tested without a window.
//
// The rule for releases. A build commit has no order, so "older" is decided by the channel's
// version.json, not by comparing builds:
//
// 1. The channel's version.json must name a build (40 hex characters) and give the exe's SHA-256.
//    The download must then match that checksum (updater.py), so an asset that isn't the
//    manifest's build is never installed.
// 2. CI and Promote stamp version.json with `published`, the Unix time it was written. This PC
//    remembers, per channel, the newest stamp it has accepted. A version.json stamped earlier than
//    that, or not stamped once a stamped one has been seen, is out of date (a stale cache or a
//    replayed file) and is refused. One with the same stamp must name the same build.
// 3. Whatever the channel names is installed when it differs from the running build, even an
//    earlier build. That is how a deliberate rollback works: Promote republishes an earlier build
//    with a new stamp, so it is newer as a release even though its build is older.
//
// Switching channel starts from that channel's own memory, so moving from Preview to Stable can
// install an earlier build, as choosing Stable means.

use std::path::Path;
use std::time::Duration;

use serde_json::{json, Value};

/// A stamp more than this far in the future is a mistake; accepting it would block every
/// later release on this PC.
const FUTURE_SLACK_SECONDS: i64 = 24 * 60 * 60;

pub fn is_commit(s: &str) -> bool {
    s.len() == 40 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

fn is_sha256(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// What this PC last accepted from one channel's version.json.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Seen {
    pub commit: String,
    /// None for a version.json published before stamps.
    pub published: Option<i64>,
}

/// Checks a channel's version.json (or the updater's report of it, which uses the same keys)
/// against what this PC last accepted from that channel. Returns what to remember on success.
pub fn check_manifest(manifest: &Value, seen: Option<&Seen>, now: i64) -> Result<Seen, String> {
    let commit = manifest["commit"]
        .as_str()
        .filter(|c| is_commit(c))
        .ok_or("The update channel's version.json doesn't name a build, so nothing was installed.")?
        .to_ascii_lowercase();
    if !manifest["exe_sha256"].as_str().is_some_and(is_sha256) {
        return Err(format!(
            "The update channel's version.json gives no checksum for build {}, so it wasn't installed.",
            &commit[..7]
        ));
    }
    let published = match &manifest["published"] {
        Value::Null => None,
        stamp => Some(stamp.as_i64().filter(|s| *s > 0).ok_or_else(|| {
            format!("The update channel's version.json for build {} has an invalid publish time, so it wasn't installed.", &commit[..7])
        })?),
    };
    if published.is_some_and(|p| p > now + FUTURE_SLACK_SECONDS) {
        return Err(format!(
            "The update channel's version.json for build {} says it was published in the future, so it wasn't installed. \
             Check this PC's clock.",
            &commit[..7]
        ));
    }
    if let Some(Seen { commit: last_commit, published: Some(last) }) = seen {
        let stale = match published {
            None => true,
            Some(p) if p < *last => true,
            Some(p) if p == *last && commit != *last_commit => {
                return Err(format!(
                    "The update channel names build {} with the same publish time as build {}, which this PC already saw, \
                     so nothing was installed.",
                    &commit[..7],
                    &last_commit[..7.min(last_commit.len())]
                ));
            }
            _ => false,
        };
        if stale {
            return Err(format!(
                "The update channel is showing an older release (build {}) than one this PC already saw (build {}), \
                 so nothing was installed. This is usually an out-of-date cached copy; try again later.",
                &commit[..7],
                &last_commit[..7.min(last_commit.len())]
            ));
        }
    }
    Ok(Seen { commit, published })
}

/// Whether an accepted release should replace the running build. Any other build does, older or
/// newer: the channel decides (rule 3 above).
pub fn wants_install(running: &str, accepted: &Seen) -> bool {
    is_commit(running) && !accepted.commit.eq_ignore_ascii_case(running)
}

/// The releases the engine may come from, in order, for a PC on `channel` ("stable" or "latest")
/// running `build`. With `"auto_update": false` only that build's own release (build-<first 12
/// characters of its commit>, which CI keeps for each published build), so the app never installs
/// an engine newer than the exe the organization deployed (#418). Otherwise the channel's: Stable
/// falls back to Preview ("latest") while no build has been promoted, as the updater does.
pub fn engine_tags(auto_update: bool, channel: &str, build: &str) -> Vec<String> {
    if !auto_update {
        return vec![format!("build-{}", build.get(..12).unwrap_or(build).to_ascii_lowercase())];
    }
    if channel == "stable" { vec!["stable".into(), "latest".into()] } else { vec!["latest".into()] }
}

// ---- What this PC remembers: update-state.json in the app's data folder.
// { "channels": { "<release tag>": { "commit": "...", "published": 1760000000 } },
//   "offered": "<the build the last accepted check offered, which update_app may install>" }

/// A missing or corrupt file reads as empty: the app starts over and accepts the channel's next
/// release, so one older release can get through once. Deliberate, so a damaged file never
/// blocks updates (docs/security.md).
fn read_state(path: &Path) -> Value {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(t.trim_start_matches('\u{feff}')).ok())
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}))
}

fn write_state(path: &Path, state: &Value) -> Result<(), String> {
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(state).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, path).map_err(|e| e.to_string())
}

pub fn load_seen(path: &Path, tag: &str) -> Option<Seen> {
    let entry = &read_state(path)["channels"][tag];
    let commit = entry["commit"].as_str().filter(|c| is_commit(c))?.to_string();
    Some(Seen { commit, published: entry["published"].as_i64() })
}

/// Remembers an accepted release of `tag` and offers its build to update_app; with None, offers
/// nothing (the last check was refused).
pub fn remember(path: &Path, tag: &str, accepted: Option<&Seen>) -> Result<(), String> {
    let mut state = read_state(path);
    match accepted {
        Some(seen) => {
            if !state["channels"].is_object() {
                state["channels"] = json!({});
            }
            state["channels"][tag] = json!({ "commit": seen.commit, "published": seen.published });
            state["offered"] = json!(seen.commit);
        }
        None => state["offered"] = Value::Null,
    }
    write_state(path, &state)
}

/// Builds that this PC rolled back because they never finished starting (EA-024), newest last.
pub fn failed_builds(path: &Path) -> Vec<String> {
    read_state(path)["failed"]
        .as_array()
        .map(|a| a.iter().filter_map(|c| c.as_str().filter(|c| is_commit(c)).map(str::to_ascii_lowercase)).collect())
        .unwrap_or_default()
}

/// Remembers that `commit` was rolled back, so this PC doesn't install it again. Keeps the last ten.
pub fn record_failed(path: &Path, commit: &str) -> Result<(), String> {
    let mut state = read_state(path);
    let mut failed = failed_builds(path);
    let commit = commit.to_ascii_lowercase();
    failed.retain(|c| *c != commit);
    failed.push(commit);
    let keep = failed.len().saturating_sub(10);
    state["failed"] = json!(failed[keep..]);
    write_state(path, &state)
}

/// Why an accepted release isn't installed after all: its build was rolled back on this PC before.
pub fn refused_as_failed(commit: &str, failed: &[String]) -> Option<String> {
    failed.iter().any(|c| c.eq_ignore_ascii_case(commit)).then(|| {
        format!(
            "Build {} didn't finish starting on this PC, so the previous build was put back and it won't be \
             installed again. A newer build will be.",
            &commit[..7.min(commit.len())]
        )
    })
}

/// The time a new build has used of its limit to confirm its start. Time while the engine is being
/// downloaded or installed doesn't count, so a slow connection can't roll a good build back, until
/// INSTALL_GRACE has passed in all: an install that hangs or starts over can't hold the rollback off.
#[derive(Debug, Default)]
pub struct StartClock {
    used: Duration,
    since_start: Duration,
}

/// How long engine installs can pause a new build's start limit, all told.
pub const INSTALL_GRACE: Duration = Duration::from_secs(2 * 60 * 60);

impl StartClock {
    /// Adds `elapsed` unless an engine install was running (within INSTALL_GRACE); says whether
    /// `limit` is now used up.
    pub fn tick(&mut self, elapsed: Duration, installing: bool, limit: Duration) -> bool {
        self.since_start += elapsed;
        if !installing || self.since_start > INSTALL_GRACE {
            self.used += elapsed;
        }
        self.used >= limit
    }
}

/// Whether a new build that hasn't confirmed its start (its engine never answered) puts the previous
/// build back. Only a start that an update made (`--updated-to` this build), and only once per build:
/// if a build from before EA-024 installs it again, it stays, so the two can't hand over forever.
pub fn rolls_back(updated_to: Option<&str>, build: &str, confirmed: bool, failed_before: bool) -> bool {
    !confirmed && !failed_before && is_commit(build) && updated_to.is_some_and(|to| to.eq_ignore_ascii_case(build))
}

/// The build the last accepted check offered, the only one update_app installs.
pub fn offered(path: &Path) -> Option<String> {
    read_state(path)["offered"].as_str().filter(|c| is_commit(c)).map(str::to_owned)
}

// ---- Keeping the previous exe. The running exe is renamed to exe.old (Windows lets a running exe
// be renamed but not overwritten), the new one takes its name and is started with
// --updated-to. The old process waits until the new one says it started (a marker file the new
// exe writes once its window has loaded the UI and asked for the startup check). If the new exe
// exits first, the old process puts itself back. One still running at the time limit is left to
// run, because builds from before HD-022 (which a rollback can install) never write the marker.
// exe.old is removed only by an exe that has got that far, never at the top of main().

/// Renames `from` to `to`, retrying for about 15 seconds while the file is locked. OneDrive (an
/// exe on a synced Desktop), antivirus and the search indexer open a file that was just written
/// or renamed for a few seconds, and Windows then refuses with "Access is denied" (5) or a
/// sharing violation (32). Any other error, such as a missing file, fails at once.
pub fn rename_retrying(from: &Path, to: &Path) -> std::io::Result<()> {
    let mut attempt = 0;
    loop {
        match std::fs::rename(from, to) {
            Err(e) if attempt < 60 && is_lock(&e) => {
                attempt += 1;
                std::thread::sleep(Duration::from_millis(250));
            }
            done => return done,
        }
    }
}

fn is_lock(e: &std::io::Error) -> bool {
    e.kind() == std::io::ErrorKind::PermissionDenied || matches!(e.raw_os_error(), Some(5 | 32 | 33))
}

/// Waits, for about as long as rename_retrying, until nothing else has `path` open. Antivirus,
/// the search indexer and OneDrive open a download that was just written to look at it, and while
/// they do Windows refuses to rename it. Elsewhere there's nothing to wait for. A file that can't
/// be opened for another reason (missing) returns at once: the rename that follows reports it.
pub fn wait_until_unused(path: &Path) {
    wait_until_unused_with(path, 60, Duration::from_millis(250));
}

fn wait_until_unused_with(path: &Path, attempts: u32, pause: Duration) {
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        for attempt in 0..=attempts {
            // Share mode 0: this open fails with a sharing violation while any other handle is open.
            match std::fs::OpenOptions::new().read(true).share_mode(0).open(path) {
                Err(e) if attempt < attempts && is_lock(&e) => std::thread::sleep(pause),
                _ => return,
            }
        }
    }
    #[cfg(not(windows))]
    let _ = (path, attempts, pause);
}

/// Whether this exe can replace itself: an update renames it and puts the new build in its folder,
/// so the folder must be one this account can change. An exe IT copied into `C:\Program Files`
/// can't; finding that out before the download saves the download and the 15 s of retries
/// rename_retrying would spend on "Access is denied". Any other trouble with the probe lets the
/// update go ahead and report what goes wrong.
pub fn can_update_in_place(exe: &Path) -> Result<(), String> {
    let Some(dir) = exe.parent() else { return Ok(()) };
    match probe_writable(dir) {
        Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => Err(format!(
            "KratosForge is in {}, a folder your account can't change, so it can't update itself. \
             Ask IT to deploy the new build, or install KratosForge for your account with the MSI.",
            dir.display()
        )),
        _ => Ok(()),
    }
}

/// Creates and removes an empty file in `dir`.
fn probe_writable(dir: &Path) -> std::io::Result<()> {
    let probe = dir.join(format!(".kratosforge-update-probe-{}", std::process::id()));
    std::fs::OpenOptions::new().write(true).create_new(true).open(&probe)?;
    let _ = std::fs::remove_file(&probe);
    Ok(())
}

/// Puts `new` in place of `exe`, keeping the running exe as `old`.
///
/// The download is waited on first, while the running exe is still in place: moving the exe aside
/// and then waiting up to 15 s for a scanner to let go of the download would leave no exe at its
/// path for that long, and a PC shut down or a power cut then leaves the Start menu shortcut
/// pointing at nothing. Waiting first keeps the two renames back to back.
#[cfg(test)] // the app swaps with swap_in_checked; tests use this for the renames alone
pub fn swap_in(exe: &Path, new: &Path, old: &Path) -> Result<(), String> {
    swap_in_checked(exe, new, old, |_| Ok(()))
}

/// Opens `path` once nothing else has it open, so that until the handle is dropped nobody can
/// write to it. On Windows the handle shares read and delete only, so a scanner can still read it
/// and this process can still rename it. Elsewhere it is a plain read handle.
pub fn hold_unchanged(path: &Path) -> std::io::Result<std::fs::File> {
    wait_until_unused(path);
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_SHARE_READ: u32 = 1;
        const FILE_SHARE_DELETE: u32 = 4;
        options.share_mode(FILE_SHARE_READ | FILE_SHARE_DELETE);
    }
    options.open(path)
}

/// swap_in, after `check` has read the download through a handle that keeps it from being written
/// until it is in place. The download was checked before the wait (its checksum and signer), and
/// something could have written to it while it was waited on; `check` confirms it is still that
/// file. A failed check leaves the running exe where it is.
///
/// The held handle has to share delete, or this process couldn't rename the file, so another
/// program could still move it away and put a different exe.new in its place. `check` therefore
/// runs again on what is at `exe` once the renames are done, and a file that fails it is swapped
/// back out for the previous build.
pub fn swap_in_checked(
    exe: &Path,
    new: &Path,
    old: &Path,
    mut check: impl FnMut(&mut dyn std::io::Read) -> Result<(), String>,
) -> Result<(), String> {
    let mut held = hold_unchanged(new).map_err(|e| format!("Could not open the new build {}: {e}", new.display()))?;
    check(&mut held)?;
    swap_renames(exe, new, old)?;
    let placed = std::fs::File::open(exe)
        .map_err(|e| format!("Could not read the new build to check it again: {e}"))
        .and_then(|mut f| check(&mut f));
    drop(held);
    if let Err(why) = placed {
        restore_previous(exe, old)?;
        return Err(why);
    }
    Ok(())
}

fn swap_renames(exe: &Path, new: &Path, old: &Path) -> Result<(), String> {
    let _ = std::fs::remove_file(old);
    rename_retrying(exe, old).map_err(|e| format!("Could not move {} aside: {e}", exe.display()))?;
    if let Err(e) = rename_retrying(new, exe) {
        if let Err(back) = rename_retrying(old, exe) {
            return Err(format!(
                "Could not put the new build in place of {}: {e}, and the old build couldn't be put back ({back}). \
                 It is kept as {}: rename it to {} to start KratosForge again.",
                exe.display(),
                old.display(),
                exe.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
            ));
        }
        return Err(format!("Could not put the new build in place of {}: {e}", exe.display()));
    }
    Ok(())
}

/// Puts the previous exe (`old`) back in place of a new one that didn't start. Retries for a
/// few seconds while Windows still holds the ended process's file.
pub fn restore_previous(exe: &Path, old: &Path) -> Result<(), String> {
    restore_with(exe, old, |from, to| std::fs::rename(from, to), Duration::from_millis(250))
}

/// restore_previous with the rename and the pause between attempts given, so tests can make a move fail.
fn restore_with(
    exe: &Path,
    old: &Path,
    mut rename: impl FnMut(&Path, &Path) -> std::io::Result<()>,
    pause: Duration,
) -> Result<(), String> {
    if !old.exists() {
        return Err(format!("The previous version ({}) is missing, so it could not be put back.", old.display()));
    }
    let failed = exe.with_extension("exe.failed");
    // Only a copy left by an earlier update: once the new exe is moved there, it is the only copy of
    // that build, and the fallback below puts it back if the previous one can't be.
    let _ = std::fs::remove_file(&failed);
    let mut last = String::new();
    for attempt in 0..20 {
        if attempt > 0 {
            std::thread::sleep(pause);
        }
        // Move the new exe aside first (it may still be held open), then the old one back.
        if exe.exists() {
            if let Err(e) = rename(exe, &failed) {
                last = e.to_string();
                continue;
            }
        }
        match rename(old, exe) {
            Ok(()) => {
                let _ = std::fs::remove_file(&failed);
                return Ok(());
            }
            Err(e) => last = e.to_string(),
        }
    }
    // Never leave no exe at all: put the new one back where it was.
    if !exe.exists() {
        let _ = rename(&failed, exe);
    }
    Err(format!("Could not put the previous version back in place of {}: {last}", exe.display()))
}

/// Written by the new exe once it has started: the build it is.
pub fn mark_started(marker: &Path, commit: &str) -> Result<(), String> {
    std::fs::write(marker, commit).map_err(|e| e.to_string())
}

/// Whether the new exe has said it started as `commit`.
pub fn started_as(marker: &Path, commit: &str) -> bool {
    std::fs::read_to_string(marker).is_ok_and(|t| t.trim().eq_ignore_ascii_case(commit))
}

#[derive(Debug, PartialEq, Eq)]
pub enum Start {
    Started,
    Exited,
    TimedOut,
}

/// Whether the update is undone: only when the new exe ended without saying it started. One that
/// timed out is still running, and may be a build from before HD-022 that never says.
pub fn failed_start(outcome: &Start) -> bool {
    *outcome == Start::Exited
}

/// Waits for the new exe: `started` says it got far enough, `exited` that it ended.
pub fn wait_for_start(
    mut started: impl FnMut() -> bool,
    mut exited: impl FnMut() -> bool,
    timeout: Duration,
    poll: Duration,
) -> Start {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        // Checked first: an exe that started and then closed straight away still started.
        if started() {
            return Start::Started;
        }
        if exited() {
            return if started() { Start::Started } else { Start::Exited };
        }
        if std::time::Instant::now() >= deadline {
            return Start::TimedOut;
        }
        std::thread::sleep(poll);
    }
}

/// The steps of handing over to a new exe once it is downloaded, as update_app does them.
pub trait Handover {
    /// Ends this exe's engine the way quitting does, so the new build's engine can bind the local
    /// API port and open the DuckDB files, kernels and token cache. Says whether one was running.
    fn stop_engine(&mut self) -> Result<bool, String>;
    fn swap(&mut self) -> Result<(), String>;
    /// Starts the new exe (and hides this window while it starts).
    fn spawn(&mut self) -> Result<(), String>;
    fn wait(&mut self) -> Start;
    fn restore(&mut self) -> Result<(), String>;
    /// Back to this build: starts the engine again if it was running, and shows the window.
    fn resume(&mut self, engine_was_running: bool);
    fn exit(&mut self);
}

/// Hands over to the new exe. The engine stops before the new exe exists on disk, so the two
/// builds' engines never run at once; on any failure this build is back in place and resumed.
pub fn hand_over(h: &mut impl Handover) -> Result<(), String> {
    let engine = match h.stop_engine() {
        Ok(running) => running,
        Err(e) => {
            // Nothing was swapped; the next engine call starts one if it is needed.
            h.resume(false);
            return Err(format!("Could not stop the local engine before updating: {e}"));
        }
    };
    if let Err(e) = h.swap() {
        h.resume(engine);
        return Err(e);
    }
    if let Err(e) = h.spawn() {
        let restored = h.restore();
        h.resume(engine);
        restored?;
        return Err(format!("The new build could not be started ({e}), so this build was put back."));
    }
    let outcome = h.wait();
    if !failed_start(&outcome) {
        h.exit();
        return Ok(());
    }
    let restored = h.restore();
    h.resume(engine);
    restored?;
    Err("The new build closed before it finished starting, so this build was put back.".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_build_rolls_back_only_after_its_own_update_and_only_once() {
        assert!(rolls_back(Some(A), A, false, false));
        assert!(!rolls_back(Some(A), A, true, false), "its engine answered");
        assert!(!rolls_back(Some(A), A, false, true), "rolled back once already: it stays");
        assert!(!rolls_back(None, A, false, false), "an ordinary start never rolls back");
        assert!(!rolls_back(Some(B), A, false, false), "the wrong build: the exe that started it handles that");
        assert!(!rolls_back(Some(""), "", false, false), "a local build");
    }

    #[test]
    fn an_engine_install_doesnt_count_toward_the_start_limit() {
        let (sec, limit) = (Duration::from_secs(1), Duration::from_secs(3));
        let mut clock = StartClock::default();
        assert!(!clock.tick(sec, false, limit));
        for _ in 0..100 {
            assert!(!clock.tick(sec, true, limit), "a long download");
        }
        assert!(!clock.tick(sec, false, limit));
        assert!(clock.tick(sec, false, limit), "three seconds outside the download");
    }

    #[test]
    fn an_install_that_never_ends_stops_pausing_the_limit_after_the_grace() {
        let (min, limit) = (Duration::from_secs(60), Duration::from_secs(20 * 60));
        let mut clock = StartClock::default();
        let grace = INSTALL_GRACE.as_secs() / 60;
        for i in 0..grace {
            assert!(!clock.tick(min, true, limit), "minute {i} of a hung download is paused");
        }
        for i in 0..19 {
            assert!(!clock.tick(min, true, limit), "minute {i} past the grace counts, short of the limit");
        }
        assert!(clock.tick(min, true, limit), "twenty minutes past the grace: it rolls back");
    }

    #[test]
    fn a_rolled_back_build_is_refused_and_remembered() {
        let dir = std::env::temp_dir().join(format!("kf-failed-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("update-state.json");
        remember(&path, "latest", Some(&Seen { commit: B.into(), published: Some(NOW) })).unwrap();
        assert!(failed_builds(&path).is_empty());
        record_failed(&path, &A.to_ascii_uppercase()).unwrap();
        record_failed(&path, A).unwrap();
        assert_eq!(failed_builds(&path), [A]);
        assert_eq!(load_seen(&path, "latest").unwrap().commit, B, "what the channel showed is kept");
        assert!(refused_as_failed(A, &failed_builds(&path)).unwrap().contains("won't be installed again"));
        assert!(refused_as_failed(B, &failed_builds(&path)).is_none());
        for i in 0..12 {
            record_failed(&path, &format!("{i:040x}")).unwrap();
        }
        assert_eq!(failed_builds(&path).len(), 10);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    const A: &str = "13cd5dfa54ee4be7c066919bd3828eda3e5c1326";
    const B: &str = "2c9d1f23eee5857a79a72be77d4c8f4f913febcd";
    const NOW: i64 = 1_760_800_000;

    #[test]
    fn managed_pcs_take_the_engine_only_from_their_own_builds_release() {
        assert_eq!(engine_tags(true, "stable", A), ["stable", "latest"]);
        assert_eq!(engine_tags(true, "latest", A), ["latest"]);
        assert_eq!(engine_tags(false, "stable", A), ["build-13cd5dfa54ee"]);
        assert_eq!(engine_tags(false, "latest", &A.to_ascii_uppercase()), ["build-13cd5dfa54ee"]);
    }

    fn manifest(commit: &str, published: Option<i64>) -> Value {
        let mut m = json!({ "commit": commit, "exe_sha256": "ab".repeat(32), "engine_sha256": "cd".repeat(32) });
        if let Some(p) = published {
            m["published"] = json!(p);
        }
        m
    }

    fn seen(commit: &str, published: Option<i64>) -> Seen {
        Seen { commit: commit.into(), published }
    }

    #[test]
    fn a_first_release_on_a_channel_is_accepted() {
        assert_eq!(check_manifest(&manifest(A, Some(NOW)), None, NOW), Ok(seen(A, Some(NOW))));
        assert_eq!(check_manifest(&manifest(A, None), None, NOW), Ok(seen(A, None)));
    }

    #[test]
    fn a_newer_release_is_accepted_and_installed() {
        let accepted = check_manifest(&manifest(B, Some(NOW)), Some(&seen(A, Some(NOW - 60))), NOW).unwrap();
        assert!(wants_install(A, &accepted));
    }

    #[test]
    fn a_release_older_than_one_already_seen_is_refused() {
        let err = check_manifest(&manifest(A, Some(NOW - 3600)), Some(&seen(B, Some(NOW))), NOW).unwrap_err();
        assert!(err.contains("older release (build 13cd5df)"), "{err}");
        assert!(err.contains("build 2c9d1f2"), "{err}");
    }

    #[test]
    fn an_unstamped_release_after_a_stamped_one_is_refused() {
        let err = check_manifest(&manifest(A, None), Some(&seen(B, Some(NOW))), NOW).unwrap_err();
        assert!(err.contains("older release"), "{err}");
    }

    #[test]
    fn the_same_stamp_must_name_the_same_build() {
        assert_eq!(check_manifest(&manifest(B, Some(NOW)), Some(&seen(B, Some(NOW))), NOW), Ok(seen(B, Some(NOW))));
        let err = check_manifest(&manifest(A, Some(NOW)), Some(&seen(B, Some(NOW))), NOW).unwrap_err();
        assert!(err.contains("same publish time"), "{err}");
    }

    #[test]
    fn a_deliberate_rollback_installs_the_earlier_build() {
        // Running B, which the channel offered at NOW - 600. Promote republishes the earlier build A
        // with a new stamp: it is accepted and installed although A is the older build.
        let accepted = check_manifest(&manifest(A, Some(NOW)), Some(&seen(B, Some(NOW - 600))), NOW).unwrap();
        assert_eq!(accepted, seen(A, Some(NOW)));
        assert!(wants_install(B, &accepted));
        // A cached copy of the release it replaced is then refused.
        assert!(check_manifest(&manifest(B, Some(NOW - 600)), Some(&accepted), NOW).is_err());
    }

    #[test]
    fn releases_from_before_stamps_are_not_ordered() {
        // Transitional: until CI stamps version.json there is nothing to order by.
        assert!(check_manifest(&manifest(A, None), Some(&seen(B, None)), NOW).is_ok());
        assert!(check_manifest(&manifest(A, Some(NOW)), Some(&seen(B, None)), NOW).is_ok());
    }

    #[test]
    fn a_release_must_name_a_build_and_give_the_exe_checksum() {
        assert!(check_manifest(&json!({ "commit": "main", "exe_sha256": "ab".repeat(32) }), None, NOW)
            .unwrap_err()
            .contains("doesn't name a build"));
        assert!(check_manifest(&json!({ "commit": A }), None, NOW).unwrap_err().contains("no checksum"));
        assert!(check_manifest(&json!({ "commit": A, "exe_sha256": "xyz" }), None, NOW).unwrap_err().contains("no checksum"));
        assert!(check_manifest(&Value::Null, None, NOW).is_err());
    }

    #[test]
    fn a_bad_or_future_stamp_is_refused() {
        let mut m = manifest(A, None);
        m["published"] = json!("2026-10-18");
        assert!(check_manifest(&m, None, NOW).unwrap_err().contains("invalid publish time"));
        m["published"] = json!(-5);
        assert!(check_manifest(&m, None, NOW).is_err());
        assert!(check_manifest(&manifest(A, Some(NOW + 2 * FUTURE_SLACK_SECONDS)), None, NOW)
            .unwrap_err()
            .contains("in the future"));
        assert!(check_manifest(&manifest(A, Some(NOW + 60)), None, NOW).is_ok());
    }

    #[test]
    fn only_another_build_is_installed() {
        assert!(!wants_install(A, &seen(A, Some(NOW))));
        assert!(!wants_install(&A.to_uppercase(), &seen(A, None)));
        assert!(wants_install(A, &seen(B, None)));
        assert!(!wants_install("", &seen(B, None)), "a local build never updates");
    }

    fn scratch(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("fd-guard-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn remembers_each_channel_and_the_offered_build() {
        let dir = scratch("state");
        let path = dir.join("update-state.json");
        assert_eq!(load_seen(&path, "stable"), None);
        assert_eq!(offered(&path), None);
        remember(&path, "stable", Some(&seen(A, Some(NOW)))).unwrap();
        remember(&path, "latest", Some(&seen(B, None))).unwrap();
        assert_eq!(load_seen(&path, "stable"), Some(seen(A, Some(NOW))));
        assert_eq!(load_seen(&path, "latest"), Some(seen(B, None)));
        assert_eq!(offered(&path).as_deref(), Some(B));
        // A refused check offers nothing but keeps what was seen.
        remember(&path, "stable", None).unwrap();
        assert_eq!(offered(&path), None);
        assert_eq!(load_seen(&path, "stable"), Some(seen(A, Some(NOW))));
        std::fs::write(&path, "not json").unwrap();
        assert_eq!(load_seen(&path, "stable"), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn swap_keeps_the_previous_exe_and_restore_puts_it_back() {
        let dir = scratch("swap");
        let (exe, new, old) = (dir.join("app.exe"), dir.join("app.exe.new"), dir.join("app.exe.old"));
        std::fs::write(&exe, "previous").unwrap();
        std::fs::write(&new, "next").unwrap();
        swap_in(&exe, &new, &old).unwrap();
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "next");
        assert_eq!(std::fs::read_to_string(&old).unwrap(), "previous");
        assert!(!new.exists());
        restore_previous(&exe, &old).unwrap();
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "previous");
        assert!(!old.exists() && !exe.with_extension("exe.failed").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn swap_waits_for_a_scanner_to_let_go_of_the_download_before_moving_the_exe_aside() {
        use std::os::windows::fs::OpenOptionsExt;
        let dir = scratch("swapbusy");
        let (exe, new, old) = (dir.join("app.exe"), dir.join("app.exe.new"), dir.join("app.exe.old"));
        std::fs::write(&exe, "previous").unwrap();
        std::fs::write(&new, "next").unwrap();
        // A virus scanner reading the download: it shares read only, so the download can't be renamed.
        let scanner = std::fs::OpenOptions::new().read(true).share_mode(1).open(&new).unwrap();
        let swap = {
            let (exe, new, old) = (exe.clone(), new.clone(), old.clone());
            std::thread::spawn(move || swap_in(&exe, &new, &old))
        };
        std::thread::sleep(Duration::from_millis(600));
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "previous", "the exe was moved aside while the download was busy");
        assert!(!old.exists());
        drop(scanner);
        swap.join().unwrap().unwrap();
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "next");
        assert_eq!(std::fs::read_to_string(&old).unwrap(), "previous");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_failed_check_leaves_the_running_exe_in_place() {
        let dir = scratch("swapcheck");
        let (exe, new, old) = (dir.join("app.exe"), dir.join("app.exe.new"), dir.join("app.exe.old"));
        std::fs::write(&exe, "previous").unwrap();
        std::fs::write(&new, "changed").unwrap();
        let err = swap_in_checked(&exe, &new, &old, |r| {
            let mut seen = String::new();
            r.read_to_string(&mut seen).map_err(|e| e.to_string())?;
            if seen == "next" { Ok(()) } else { Err("The new build changed after it was checked.".into()) }
        })
        .unwrap_err();
        assert!(err.contains("changed"));
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "previous");
        assert!(!old.exists() && new.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_check_reads_the_download_that_is_then_put_in_place() {
        let dir = scratch("swapchecked");
        let (exe, new, old) = (dir.join("app.exe"), dir.join("app.exe.new"), dir.join("app.exe.old"));
        std::fs::write(&exe, "previous").unwrap();
        std::fs::write(&new, "next").unwrap();
        let mut seen = Vec::new();
        swap_in_checked(&exe, &new, &old, |r| {
            let mut s = String::new();
            r.read_to_string(&mut s).map_err(|e| e.to_string())?;
            seen.push(s);
            Ok(())
        })
        .unwrap();
        assert_eq!(seen, ["next", "next"], "checked when held, and again once in place");
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "next");
        assert_eq!(std::fs::read_to_string(&old).unwrap(), "previous");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_different_file_swapped_in_under_the_held_one_is_put_back_out() {
        let dir = scratch("swapreplaced");
        let (exe, new, old) = (dir.join("app.exe"), dir.join("app.exe.new"), dir.join("app.exe.old"));
        std::fs::write(&exe, "previous").unwrap();
        std::fs::write(&new, "next").unwrap();
        let mut calls = 0;
        // The first check passes on the held file; what lands at exe turns out to be another file.
        let err = swap_in_checked(&exe, &new, &old, |r| {
            calls += 1;
            let mut seen = String::new();
            r.read_to_string(&mut seen).map_err(|e| e.to_string())?;
            if calls == 2 { Err("The new build changed after it was checked.".into()) } else { Ok(()) }
        })
        .unwrap_err();
        assert!(err.contains("changed"));
        assert_eq!(calls, 2);
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "previous");
        assert!(!old.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn nothing_can_write_to_the_download_between_the_check_and_the_swap() {
        let dir = scratch("swapheld");
        let (exe, new, old) = (dir.join("app.exe"), dir.join("app.exe.new"), dir.join("app.exe.old"));
        std::fs::write(&exe, "previous").unwrap();
        std::fs::write(&new, "next").unwrap();
        swap_in_checked(&exe, &new, &old, |_| {
            // Another program trying to change the checked download while it is held.
            match std::fs::OpenOptions::new().write(true).open(&new) {
                Ok(_) => Err("the download could be written while held".into()),
                Err(_) => Ok(()),
            }
        })
        .unwrap();
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "next");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn waiting_on_a_missing_download_returns_at_once_and_the_swap_reports_it() {
        let dir = scratch("swapmissing");
        let started = std::time::Instant::now();
        wait_until_unused_with(&dir.join("app.exe.new"), 60, Duration::from_millis(250));
        assert!(started.elapsed() < Duration::from_secs(2));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_exe_in_a_folder_it_can_change_can_update_itself_and_the_probe_is_gone() {
        let dir = scratch("probeok");
        assert_eq!(can_update_in_place(&dir.join("app.exe")), Ok(()));
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 0, "the probe file was left behind");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_probe_that_fails_for_another_reason_lets_the_update_try() {
        let dir = scratch("probemissing");
        assert_eq!(can_update_in_place(&dir.join("no-such-folder").join("app.exe")), Ok(()));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn an_exe_in_a_folder_it_cant_change_says_so_before_downloading() {
        let dir = scratch("probedenied");
        let target = dir.to_string_lossy().into_owned();
        // Everyone may not add files here: the folder an exe copied into Program Files is in.
        let deny = std::process::Command::new("icacls").args([&target, "/deny", "*S-1-1-0:(AD,WD)"]).output().unwrap();
        assert!(deny.status.success(), "{}", String::from_utf8_lossy(&deny.stdout));
        let result = can_update_in_place(&dir.join("app.exe"));
        let _ = std::process::Command::new("icacls").args([&target, "/remove:d", "*S-1-1-0"]).output();
        let err = result.unwrap_err();
        assert!(err.contains("can't update itself") && err.contains(&target), "{err}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_failed_swap_leaves_the_running_exe_in_place() {
        let dir = scratch("swapfail");
        let (exe, new, old) = (dir.join("app.exe"), dir.join("app.exe.new"), dir.join("app.exe.old"));
        std::fs::write(&exe, "previous").unwrap();
        assert!(swap_in(&exe, &new, &old).is_err(), "no download to put in place");
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "previous");
        assert!(!old.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_restore_that_cant_move_the_previous_exe_back_keeps_the_new_one() {
        let dir = scratch("restorefail");
        let (exe, old) = (dir.join("app.exe"), dir.join("app.exe.old"));
        std::fs::write(&exe, "next").unwrap();
        std::fs::write(&old, "previous").unwrap();
        std::fs::write(exe.with_extension("exe.failed"), "stale").unwrap();
        // A virus scanner holds exe.old, so every move of it fails; other moves work.
        let held = old.clone();
        let rename = |from: &Path, to: &Path| {
            if from == held {
                Err(std::io::Error::other("in use"))
            } else {
                std::fs::rename(from, to)
            }
        };
        assert!(restore_with(&exe, &old, rename, Duration::ZERO).unwrap_err().contains("in use"));
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "next", "the new build is still there to start");
        assert_eq!(std::fs::read_to_string(&old).unwrap(), "previous");
        assert!(!exe.with_extension("exe.failed").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_restore_that_cant_move_the_new_exe_aside_leaves_both_builds_where_they_are() {
        let dir = scratch("restoreheld");
        let (exe, old) = (dir.join("app.exe"), dir.join("app.exe.old"));
        std::fs::write(&exe, "next").unwrap();
        std::fs::write(&old, "previous").unwrap();
        // The ended process still holds the new exe, so it can't be moved aside on any attempt.
        let held = exe.clone();
        let mut tries = 0;
        let rename = |from: &Path, to: &Path| {
            if from == held {
                tries += 1;
                Err(std::io::Error::other("still running"))
            } else {
                std::fs::rename(from, to)
            }
        };
        assert!(restore_with(&exe, &old, rename, Duration::ZERO).unwrap_err().contains("still running"));
        assert_eq!(tries, 20);
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "next");
        assert_eq!(std::fs::read_to_string(&old).unwrap(), "previous", "the previous build is kept to try again");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_restore_after_the_new_exe_is_gone_just_puts_the_previous_one_back() {
        let dir = scratch("restoregone");
        let (exe, old) = (dir.join("app.exe"), dir.join("app.exe.old"));
        std::fs::write(&old, "previous").unwrap();
        restore_previous(&exe, &old).unwrap();
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "previous");
        assert!(!old.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn only_a_locked_file_is_retried() {
        use std::io::{Error, ErrorKind};
        for locked in [Error::from_raw_os_error(5), Error::from_raw_os_error(32), Error::from_raw_os_error(33),
                       Error::from(ErrorKind::PermissionDenied)] {
            assert!(super::is_lock(&locked), "{locked:?}");
        }
        for other in [Error::from(ErrorKind::NotFound), Error::from(ErrorKind::AlreadyExists), Error::other("disk full")] {
            assert!(!super::is_lock(&other), "{other:?}");
        }
        // A missing file fails at once, without the 15 seconds of retries.
        let dir = scratch("renamemissing");
        let started = std::time::Instant::now();
        assert!(rename_retrying(&dir.join("gone.exe"), &dir.join("to.exe")).is_err());
        assert!(started.elapsed() < Duration::from_secs(2));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn restore_needs_the_previous_exe() {
        let dir = scratch("restore");
        let (exe, old) = (dir.join("app.exe"), dir.join("app.exe.old"));
        std::fs::write(&exe, "next").unwrap();
        assert!(restore_previous(&exe, &old).unwrap_err().contains("missing"));
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "next", "the new exe is left alone");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_start_marker_names_the_build() {
        let dir = scratch("marker");
        let marker = dir.join("update-started");
        assert!(!started_as(&marker, A));
        mark_started(&marker, B).unwrap();
        assert!(!started_as(&marker, A), "another build started");
        mark_started(&marker, A).unwrap();
        assert!(started_as(&marker, A));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn waits_for_the_new_exe_to_start_exit_or_time_out() {
        let tick = Duration::from_millis(1);
        let mut polls = 0;
        assert_eq!(wait_for_start(|| { polls += 1; polls > 3 }, || false, Duration::from_secs(5), tick), Start::Started);
        assert_eq!(wait_for_start(|| false, || true, Duration::from_secs(5), tick), Start::Exited);
        assert_eq!(wait_for_start(|| false, || false, Duration::from_millis(20), tick), Start::TimedOut);
        // Started and closed between two polls: still a start.
        let mut calls = 0;
        assert_eq!(wait_for_start(|| { calls += 1; calls > 1 }, || true, Duration::from_secs(5), tick), Start::Started);
    }

    /// Records the order update_app's steps run in.
    struct Recorder {
        steps: Vec<String>,
        engine_running: bool,
        fail: &'static str,
        outcome: Start,
    }

    impl Recorder {
        fn new(fail: &'static str, outcome: Start) -> Self {
            Self { steps: vec![], engine_running: true, fail, outcome }
        }
        fn step(&mut self, name: &str) -> Result<(), String> {
            self.steps.push(name.into());
            if self.fail == name { Err(format!("{name} failed")) } else { Ok(()) }
        }
    }

    impl Handover for Recorder {
        fn stop_engine(&mut self) -> Result<bool, String> {
            self.step("stop_engine").map(|_| self.engine_running)
        }
        fn swap(&mut self) -> Result<(), String> {
            self.step("swap")
        }
        fn spawn(&mut self) -> Result<(), String> {
            self.step("spawn")
        }
        fn wait(&mut self) -> Start {
            self.steps.push("wait".into());
            std::mem::replace(&mut self.outcome, Start::Started)
        }
        fn restore(&mut self) -> Result<(), String> {
            self.step("restore")
        }
        fn resume(&mut self, engine_was_running: bool) {
            self.steps.push(format!("resume(engine={engine_was_running})"));
        }
        fn exit(&mut self) {
            self.steps.push("exit".into());
        }
    }

    fn run(fail: &'static str, outcome: Start) -> (Result<(), String>, Vec<String>) {
        let mut r = Recorder::new(fail, outcome);
        let result = hand_over(&mut r);
        (result, r.steps)
    }

    #[test]
    fn the_engine_stops_before_the_new_exe_is_put_in_place() {
        let (result, steps) = run("", Start::Started);
        assert!(result.is_ok());
        assert_eq!(steps, ["stop_engine", "swap", "spawn", "wait", "exit"]);
        // A rollback target from before HD-022 never says it started; it is left to run.
        assert_eq!(run("", Start::TimedOut).1, ["stop_engine", "swap", "spawn", "wait", "exit"]);
    }

    #[test]
    fn a_new_exe_that_exits_is_undone_and_the_engine_restarted() {
        let (result, steps) = run("", Start::Exited);
        assert!(result.unwrap_err().contains("closed before it finished starting"));
        assert_eq!(steps, ["stop_engine", "swap", "spawn", "wait", "restore", "resume(engine=true)"]);
    }

    #[test]
    fn the_engine_is_restarted_only_if_it_was_running() {
        let mut r = Recorder::new("", Start::Exited);
        r.engine_running = false;
        let _ = hand_over(&mut r);
        assert_eq!(r.steps.last().unwrap(), "resume(engine=false)");
    }

    #[test]
    fn every_failure_resumes_this_build() {
        let (result, steps) = run("stop_engine", Start::Started);
        assert!(result.unwrap_err().contains("Could not stop the local engine"));
        assert_eq!(steps, ["stop_engine", "resume(engine=false)"]);
        assert_eq!(run("swap", Start::Started).1, ["stop_engine", "swap", "resume(engine=true)"]);
        let (result, steps) = run("spawn", Start::Started);
        assert!(result.unwrap_err().contains("could not be started"));
        assert_eq!(steps, ["stop_engine", "swap", "spawn", "restore", "resume(engine=true)"]);
        // A restore that fails is reported, and the window still comes back.
        let (result, steps) = run("restore", Start::Exited);
        assert_eq!(result.unwrap_err(), "restore failed");
        assert_eq!(steps, ["stop_engine", "swap", "spawn", "wait", "restore", "resume(engine=true)"]);
    }

    #[test]
    fn only_an_exit_before_starting_undoes_the_update() {
        assert!(failed_start(&Start::Exited));
        assert!(!failed_start(&Start::Started));
        assert!(!failed_start(&Start::TimedOut), "a rollback target from before HD-022 never writes the marker");
    }
}
