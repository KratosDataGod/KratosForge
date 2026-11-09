//! What a crash leaves behind, and how a crash loop is stopped.
//!
//! - A panic in the app is written to `app-crash.log` in the data folder (a windowed exe has no
//!   console to print it to), with the time, the build and where it happened. The support bundle
//!   includes it. Nothing is uploaded.
//! - When the engine ends on its own, the shell adds a line to its log saying when and with which
//!   exit code, so a native crash that printed nothing still leaves a reason.
//! - An engine that keeps ending soon after it starts is not started again and again by every poll
//!   in the page: after [`STRIKES`] such ends in a row the app stops starting it for [`PAUSE`] and
//!   every call gets [`SAFE_MODE`] instead, then it tries once more.

use std::io::Write;
use std::path::Path;
use std::time::{Duration, Instant};

/// An engine that ends within this long of starting counts toward a crash loop.
pub const SOON: Duration = Duration::from_secs(60);
/// Ends in a row, each soon after starting, that stop the app starting the engine.
pub const STRIKES: u32 = 3;
/// How long the app waits before it starts the engine again after a crash loop.
pub const PAUSE: Duration = Duration::from_secs(5 * 60);

pub const SAFE_MODE: &str = "The engine keeps stopping as it starts, so KratosForge has stopped starting it for now. \
    Your files, notebooks and settings are untouched.";

/// Past this, `app-crash.log` starts again, so a panic that repeats can't fill the disk.
pub const CRASH_LOG_CAP: u64 = 256 * 1024;

/// Engine starts that ended soon after starting, in a row, and whether the app is waiting out a loop.
#[derive(Debug, Default)]
pub struct CrashLoop {
    strikes: u32,
    paused_until: Option<Instant>,
    last: String,
}

impl CrashLoop {
    /// Whether the engine may be started now; the safe-mode message when not.
    pub fn may_start(&self, now: Instant, log_dir: &Path) -> Result<(), String> {
        match self.paused_until {
            Some(until) if now < until => {
                let minutes = (until - now).as_secs().div_ceil(60).max(1);
                Err(format!(
                    "{SAFE_MODE} It stopped {} times in a row within a minute of starting. KratosForge tries again in \
                     {minutes} minute{}, or when you restart it. If it keeps stopping, send IT sidecar.log, sidecar.prev.log \
                     and app-crash.log from {}.\nThe last time: {}",
                    self.strikes, if minutes == 1 { "" } else { "s" }, log_dir.display(), self.last))
            }
            _ => Ok(()),
        }
    }

    /// An engine ended on its own (not Stop, not an update) after running for `ran`.
    pub fn ended(&mut self, ran: Duration, now: Instant, why: &str) {
        if ran >= SOON {
            *self = Self::default();
            return;
        }
        self.strikes += 1;
        self.last = why.to_string();
        if self.strikes >= STRIKES {
            self.paused_until = Some(now + PAUSE);
        }
    }

    /// An engine has run past [`SOON`] and answered: whatever went wrong before is over.
    pub fn settled(&mut self) {
        *self = Self::default();
    }

    pub fn strikes(&self) -> u32 {
        self.strikes
    }
}

/// An exit code in words for the log: Windows' crash codes by name, the rest as they are.
pub fn exit_reason(code: Option<i32>) -> String {
    let Some(code) = code else { return "it was ended by a signal".into() };
    let ntstatus = code as u32;
    let name = match ntstatus {
        0 => return "it exited normally (code 0)".into(),
        0xC000_0005 => Some("an access violation in native code"),
        0xC000_00FD => Some("a stack overflow"),
        0xC000_0409 => Some("a failed security check (fail fast)"),
        0xC000_0374 => Some("heap corruption"),
        0xC000_0017 | 0xC000_012D => Some("running out of memory"),
        0xC000_013A => Some("Ctrl+C"),
        0xC000_001D => Some("an illegal instruction"),
        _ => None,
    };
    match name {
        Some(name) => format!("it crashed with {name} (exit code {ntstatus:#010X})"),
        None if !(0..=255).contains(&code) => format!("it exited with code {ntstatus:#010X}"),
        None => format!("it exited with code {code}"),
    }
}

/// A Unix time as `2026-11-09T01:05:03Z`.
pub fn utc_stamp(secs: i64) -> String {
    let (days, rest) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z", rest / 3600, rest % 3600 / 60, rest % 60)
}

/// Adds one entry to a crash log, starting it again when it is past `cap`. Errors are ignored by
/// callers: a crash report must never cause a second failure.
pub fn append(log: &Path, cap: u64, entry: &str) -> std::io::Result<()> {
    let past_cap = std::fs::metadata(log).map(|m| m.len() > cap).unwrap_or(false);
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .append(!past_cap)
        .truncate(past_cap)
        .open(log)?;
    let mut text = entry.to_string();
    if !text.ends_with('\n') {
        text.push('\n');
    }
    file.write_all(text.as_bytes())
}

/// The entry a panic leaves: when, which build, which thread, where, and the panic's own message.
pub fn panic_entry(secs: i64, build: &str, thread: &str, location: &str, message: &str) -> String {
    format!("{} KratosForge {build} panicked in thread '{thread}' at {location}: {message}\n", utc_stamp(secs))
}

/// From now on every panic in the app is also written to `log`. The previous hook still runs.
pub fn log_panics(log: std::path::PathBuf, build: &'static str) {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let message = info
            .payload()
            .downcast_ref::<&str>()
            .map(|s| s.to_string())
            .or_else(|| info.payload().downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "(no message)".into());
        let location = info.location().map(|l| format!("{}:{}", l.file(), l.line())).unwrap_or_else(|| "an unknown place".into());
        let thread = std::thread::current().name().unwrap_or("unnamed").to_string();
        let secs = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0);
        let _ = append(&log, CRASH_LOG_CAP, &panic_entry(secs, build, &thread, &location, &message));
        previous(info);
    }));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("kf-crash-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn three_quick_ends_in_a_row_stop_the_engine_starting_until_the_pause_is_over() {
        let dir = Path::new("C:\\Users\\someone\\AppData\\Local\\FabricDesktop");
        let start = Instant::now();
        let mut lp = CrashLoop::default();
        assert!(lp.may_start(start, dir).is_ok());
        lp.ended(Duration::from_secs(2), start, "ImportError: no module named duckdb");
        lp.ended(Duration::from_secs(2), start, "ImportError: no module named duckdb");
        assert!(lp.may_start(start, dir).is_ok(), "two ends are not a loop yet");
        lp.ended(Duration::from_secs(2), start, "ImportError: no module named duckdb");
        let message = lp.may_start(start, dir).unwrap_err();
        assert!(message.starts_with(SAFE_MODE), "{message}");
        assert!(message.contains("3 times") && message.contains("5 minutes") && message.contains("ImportError"), "{message}");
        assert!(message.contains("sidecar.log") && message.contains("app-crash.log") && message.contains("FabricDesktop"), "{message}");
        assert!(lp.may_start(start + Duration::from_secs(4 * 60 + 30), dir).unwrap_err().contains("1 minute,"));
        // After the pause one more start is allowed; if it ends quickly too, the pause begins again at once.
        let later = start + PAUSE;
        assert!(lp.may_start(later, dir).is_ok());
        lp.ended(Duration::from_secs(1), later, "again");
        assert!(lp.may_start(later, dir).unwrap_err().contains("4 times"));
        assert!(lp.may_start(later + PAUSE, dir).is_ok());
    }

    #[test]
    fn an_engine_that_ran_a_while_or_answered_clears_the_count() {
        let now = Instant::now();
        let dir = Path::new("x");
        let mut lp = CrashLoop::default();
        lp.ended(Duration::from_secs(1), now, "a");
        lp.ended(Duration::from_secs(1), now, "b");
        lp.ended(SOON, now, "ran a minute, then crashed");
        assert_eq!(lp.strikes(), 0);
        assert!(lp.may_start(now, dir).is_ok());
        lp.ended(Duration::from_secs(1), now, "a");
        lp.ended(Duration::from_secs(1), now, "b");
        lp.settled();
        lp.ended(Duration::from_secs(1), now, "c");
        assert!(lp.may_start(now, dir).is_ok());
        assert_eq!(lp.strikes(), 1);
    }

    #[test]
    fn exit_codes_read_as_words() {
        assert_eq!(exit_reason(Some(0xC000_0005_u32 as i32)), "it crashed with an access violation in native code (exit code 0xC0000005)");
        assert!(exit_reason(Some(0xC000_00FD_u32 as i32)).contains("stack overflow"));
        assert!(exit_reason(Some(0xC000_0409_u32 as i32)).contains("fail fast"));
        assert_eq!(exit_reason(Some(1)), "it exited with code 1");
        assert_eq!(exit_reason(Some(0)), "it exited normally (code 0)");
        assert_eq!(exit_reason(Some(0xE000_0001_u32 as i32)), "it exited with code 0xE0000001");
        assert_eq!(exit_reason(None), "it was ended by a signal");
    }

    #[test]
    fn stamps_are_utc_dates() {
        assert_eq!(utc_stamp(0), "1970-01-01T00:00:00Z");
        assert_eq!(utc_stamp(951_782_400), "2000-02-29T00:00:00Z");
        assert_eq!(utc_stamp(1_793_062_503), "2026-10-27T00:55:03Z");
    }

    #[test]
    fn a_crash_log_keeps_entries_and_starts_again_past_its_cap() {
        let dir = scratch("log");
        let log = dir.join("app-crash.log");
        append(&log, 100, "first").unwrap();
        append(&log, 100, "second\n").unwrap();
        assert_eq!(std::fs::read_to_string(&log).unwrap(), "first\nsecond\n");
        append(&log, 100, &"x".repeat(200)).unwrap();
        append(&log, 100, "after the cap").unwrap();
        assert_eq!(std::fs::read_to_string(&log).unwrap(), "after the cap\n");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_panic_entry_names_when_what_build_and_where() {
        let entry = panic_entry(0, "6b631e29", "main", "src/main.rs:10", "boom");
        assert_eq!(entry, "1970-01-01T00:00:00Z KratosForge 6b631e29 panicked in thread 'main' at src/main.rs:10: boom\n");
    }

    #[test]
    fn a_panic_is_written_to_the_crash_log() {
        let dir = scratch("hook");
        let log = dir.join("app-crash.log");
        log_panics(log.clone(), "test-build");
        let _ = std::thread::Builder::new().name("worker".into()).spawn(|| panic!("the test's panic")).unwrap().join();
        let _ = std::panic::take_hook(); // back to the default for the other tests
        let text = std::fs::read_to_string(&log).unwrap();
        assert!(text.contains("KratosForge test-build panicked in thread 'worker' at ") && text.contains(": the test's panic\n"), "{text}");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
