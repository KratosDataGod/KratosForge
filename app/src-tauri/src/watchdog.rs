// Time limits on engine calls. Only std, so `rustc --test src/watchdog.rs` checks it without
// building the whole app.

use std::process::Child;
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Calls that only read local state or the kernel, which answer in well under a second. One
/// that takes longer than this means the engine is stuck, and every later quick call would
/// queue behind it with the app frozen. Everything else (runs, publishing, Fabric calls) can
/// legitimately take hours and has no limit; Stop ends those. The engine runs these in their
/// own lane (QUICK in sidecar/fabric_desktop/server.py, kept the same as this list), so they
/// never wait behind a long call. Calls that write notebook files (new, save, rename) are left
/// out: they must stay in order with publish and delete, which save the notebook too, or a
/// publish queued behind a long call would write older cells over a newer autosave.
pub const QUICK: &[&str] = &[
    "app_settings", "cached_schemas", "debug_cell", "editor_pending", "editor_status", "interrupt_cell", "kernel_complete",
    "kernel_variables",
    "list_notebooks", "notebook_comments_list", "notebook_debug_get", "notebook_dependencies",
    "notebook_folders", "notebook_practices", "notebook_resources", "notebook_version", "notebook_versions",
    "open_notebook", "poll_cell", "prove_list", "prove_rules_get", "prove_status",
    "receipt_get", "side_by_side_list",
    "side_by_side_status", "side_by_side_writes", "sql_analyze", "sql_cancel",
    "sql_export", "sql_export_cancel", "sql_export_status",
    "sql_file_info", "sql_file_read", "sql_file_write", "sql_page", "sql_pin", "sql_practices", "sql_start", "sql_status",
    "wizard_preview", "wizard_templates",
    "engine_state", "engine_poll_cell", "engine_cancel",
];

pub const QUICK_SECONDS: u64 = 60;

/// How long `method` may take before the engine is ended; None for no limit.
pub fn limit(method: &str) -> Option<Duration> {
    QUICK.contains(&method).then(|| Duration::from_secs(QUICK_SECONDS))
}

pub enum Reply {
    Line(String),
    /// The process closed its output: it exited or was ended.
    Closed,
    /// No answer within the limit, so the process was ended.
    TimedOut,
}

/// How often the wait looks at the clock.
const STEP: Duration = Duration::from_secs(1);

/// How much of a wait step that took `took` counts toward the limit. On Windows the clock behind
/// Instant keeps counting while the PC sleeps, so a call in flight when the lid closed would look
/// hours late on resume, and the engine, asleep too, would be ended with every run in it. Where
/// Windows gives the step's awake time (`awake`, from QueryUnbiasedInterruptTime, which leaves out
/// only sleep and hibernation) that is what counts, so a suspended or starved engine still uses up
/// its limit. Without it, a step that took far longer than asked is taken as the PC asleep, not the
/// engine stuck: none of it counts.
fn counted(took: Duration, awake: Option<Duration>) -> Duration {
    match awake {
        Some(awake) => awake,
        None if took > STEP * 5 => Duration::ZERO,
        None => took,
    }
}

/// Time the PC has been awake, on a clock that stops only while it sleeps or hibernates; None off
/// Windows (where Instant already stops during suspend) or if Windows doesn't answer.
#[cfg(windows)]
fn awake_now() -> Option<Duration> {
    #[link(name = "kernel32")]
    extern "system" {
        fn QueryUnbiasedInterruptTime(unbiased: *mut u64) -> i32;
    }
    let mut ticks = 0u64;
    // SAFETY: the call writes one u64 through a pointer to a live local.
    let ok = unsafe { QueryUnbiasedInterruptTime(&mut ticks) } != 0;
    ok.then(|| Duration::from_nanos(ticks.saturating_mul(100)))
}

#[cfg(not(windows))]
fn awake_now() -> Option<Duration> {
    None
}

/// Waits for this call's reply line (the engine's reader thread sends it, or drops the sender
/// when the engine's output closes). If none comes within `limit` of awake time, ends `child`,
/// which ends every other call too, and says so.
pub fn wait_reply(reply: &Receiver<String>, child: &Arc<Mutex<Child>>, limit: Option<Duration>) -> Reply {
    let Some(limit) = limit else {
        return match reply.recv() {
            Ok(line) => Reply::Line(line),
            Err(_) => Reply::Closed,
        };
    };
    let mut waited = Duration::ZERO;
    loop {
        let step = STEP.min(limit.saturating_sub(waited)).max(Duration::from_millis(1));
        let before = Instant::now();
        let awake_before = awake_now();
        match reply.recv_timeout(step) {
            Ok(line) => return Reply::Line(line),
            Err(RecvTimeoutError::Disconnected) => return Reply::Closed,
            Err(RecvTimeoutError::Timeout) => {}
        }
        let awake = awake_before.zip(awake_now()).map(|(start, end)| end.saturating_sub(start));
        waited += counted(before.elapsed(), awake);
        if waited >= limit {
            if let Ok(mut child) = child.lock() {
                let _ = child.kill();
            }
            return Reply::TimedOut;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::{Command, Stdio};
    use std::sync::mpsc::channel;
    use std::time::Instant;

    fn spawn(script: &str) -> Arc<Mutex<Child>> {
        let child = if cfg!(windows) {
            Command::new("powershell").args(["-NoProfile", "-Command", script]).stdout(Stdio::null()).spawn()
        } else {
            Command::new("sh").args(["-c", script]).stdout(Stdio::null()).spawn()
        }.expect("spawn");
        Arc::new(Mutex::new(child))
    }

    fn sleeper() -> Arc<Mutex<Child>> {
        spawn(if cfg!(windows) { "Start-Sleep 30" } else { "exec sleep 30" })
    }

    #[test]
    fn a_call_that_answers_in_time_returns_its_line() {
        let child = sleeper();
        let (send, reply) = channel();
        send.send("ok".to_string()).unwrap();
        match wait_reply(&reply, &child, Some(Duration::from_secs(30))) {
            Reply::Line(line) => assert_eq!(line, "ok"),
            _ => panic!("expected a line"),
        }
        assert!(child.lock().unwrap().try_wait().unwrap().is_none(), "the engine was left running");
        let _ = child.lock().unwrap().kill();
    }

    #[test]
    fn a_call_that_does_not_answer_ends_the_process() {
        let child = sleeper();
        let (_send, reply) = channel::<String>();
        let start = Instant::now();
        assert!(matches!(wait_reply(&reply, &child, Some(Duration::from_millis(300))), Reply::TimedOut));
        assert!(start.elapsed() < Duration::from_secs(10));
        assert!(child.lock().unwrap().wait().is_ok());
    }

    #[test]
    fn time_the_pc_spent_asleep_does_not_count_toward_the_limit() {
        assert_eq!(counted(Duration::from_millis(1000), None), Duration::from_millis(1000));
        assert_eq!(counted(STEP * 5, None), STEP * 5); // a busy PC that ran a step late still counts it
        assert_eq!(counted(Duration::from_secs(3 * 3600), None), Duration::ZERO); // the lid was closed for hours
        // With Windows' awake clock: the lid closed for hours counts only the awake part, and an engine
        // suspended for an hour while the PC stayed awake counts the whole hour.
        assert_eq!(counted(Duration::from_secs(3 * 3600), Some(Duration::from_millis(900))), Duration::from_millis(900));
        assert_eq!(counted(Duration::from_secs(3600), Some(Duration::from_secs(3600))), Duration::from_secs(3600));
    }

    #[test]
    fn an_engine_whose_output_closed_is_closed_not_timed_out() {
        let child = sleeper();
        let (send, reply) = channel::<String>();
        drop(send);
        assert!(matches!(wait_reply(&reply, &child, Some(Duration::from_secs(30))), Reply::Closed));
        assert!(matches!(wait_reply(&reply, &child, None), Reply::Closed));
        let _ = child.lock().unwrap().kill();
    }

    #[test]
    fn every_quick_call_is_an_engine_method() {
        // server.py, plus the services whose METHODS it merges in.
        let engine = [
            include_str!("../../../sidecar/fabric_desktop/server.py"),
            include_str!("../../../sidecar/fabric_desktop/comments_service.py"),
            include_str!("../../../sidecar/fabric_desktop/notebook_folders_service.py"),
            include_str!("../../../sidecar/fabric_desktop/notebook_resources_service.py"),
            include_str!("../../../sidecar/fabric_desktop/notebook_versions_service.py"),
        ]
        .concat();
        for method in QUICK {
            assert!(engine.contains(&format!("\"{method}\"")), "{method} is not an engine method");
        }
    }

    #[test]
    fn only_quick_calls_have_a_limit() {
        assert_eq!(limit("poll_cell"), Some(Duration::from_secs(QUICK_SECONDS)));
        for slow in ["start_cell", "meta_run", "publish_notebook", "prove_start", "sign_in"] {
            assert_eq!(limit(slow), None, "{slow}");
        }
    }
}
