// Time limits on engine calls. Only std, so `rustc --test src/watchdog.rs` checks it without
// building the whole app.

use std::process::Child;
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Calls that only read local state or the kernel, which answer in well under a second. One
/// that takes longer than this means the engine is stuck, and every later quick call would
/// queue behind it with the app frozen. Everything else (runs, publishing, Fabric calls) can
/// legitimately take hours and has no limit; Stop ends those. The engine runs these in their
/// own lane (QUICK in sidecar/fabric_desktop/server.py, kept the same as this list), so they
/// never wait behind a long call. Calls that write notebook files (new, save, rename) are left
/// out: they must stay in order with publish and delete, which save the notebook too, or a
/// publish queued behind a long call would write older cells over a newer autosave.
pub const QUICK: &[&str] = &[
    "app_settings", "debug_cell", "editor_pending", "editor_status", "interrupt_cell", "kernel_complete",
    "kernel_info", "kernel_variables",
    "list_notebooks", "notebook_comments_list", "notebook_debug_get", "notebook_dependencies",
    "notebook_environment", "notebook_folders", "notebook_practices", "notebook_resources", "notebook_version", "notebook_versions",
    "open_notebook", "poll_cell", "prove_list", "prove_rules_get", "prove_status",
    "receipt_get", "receipts_list", "side_by_side_list",
    "side_by_side_status", "side_by_side_writes", "sql_analyze", "sql_cancel",
    "sql_compat_profiles", "sql_executions", "sql_export", "sql_export_cancel", "sql_export_status",
    "sql_page", "sql_practices", "sql_start", "sql_status",
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

/// Waits for this call's reply line (the engine's reader thread sends it, or drops the sender
/// when the engine's output closes). If none comes within `limit`, ends `child`, which ends
/// every other call too, and says so.
pub fn wait_reply(reply: &Receiver<String>, child: &Arc<Mutex<Child>>, limit: Option<Duration>) -> Reply {
    let got = match limit {
        Some(limit) => reply.recv_timeout(limit).map_err(|e| e == RecvTimeoutError::Timeout),
        None => reply.recv().map_err(|_| false),
    };
    match got {
        Ok(line) => Reply::Line(line),
        Err(false) => Reply::Closed,
        Err(true) => {
            if let Ok(mut child) = child.lock() {
                let _ = child.kill();
            }
            Reply::TimedOut
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
        for slow in ["start_cell", "run_cell", "meta_run", "publish_notebook", "prove_start", "sign_in"] {
            assert_eq!(limit(slow), None, "{slow}");
        }
    }
}
