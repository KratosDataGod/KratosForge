// Time limits on engine calls. Only std, so `rustc --test src/watchdog.rs` checks it without
// building the whole app.

use std::io::BufRead;
use std::process::Child;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

/// Calls that only read local state or the kernel, which answer in well under a second. One
/// that takes longer than this means the engine is stuck, and every later call would queue
/// behind it with the app frozen. Everything else (runs, publishing, Fabric calls) can
/// legitimately take hours and has no limit; Stop ends those.
pub const QUICK: &[&str] = &[
    "app_settings", "interrupt_cell", "kernel_complete", "kernel_info", "kernel_variables",
    "list_notebooks", "new_notebook", "notebook_debug_get", "notebook_dependencies", "notebook_environment",
    "open_notebook", "poll_cell", "prove_list", "prove_rules_get", "prove_status",
    "receipt_get", "receipts_list", "rename_notebook", "save_notebook", "side_by_side_list",
    "side_by_side_status", "wizard_preview", "wizard_templates",
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

/// Reads one line from `reader`. If none comes within `limit`, ends `child`, which closes the
/// pipe and frees the read, and says so.
pub fn read_reply(reader: &mut impl BufRead, child: &Arc<Mutex<Child>>, limit: Option<Duration>)
    -> std::io::Result<Reply> {
    let timed_out = Arc::new(AtomicBool::new(false));
    let (done, wait) = mpsc::channel::<()>();
    if let Some(limit) = limit {
        let (child, timed_out) = (child.clone(), timed_out.clone());
        thread::spawn(move || {
            if wait.recv_timeout(limit) == Err(mpsc::RecvTimeoutError::Timeout) {
                timed_out.store(true, Ordering::SeqCst);
                if let Ok(mut child) = child.lock() {
                    let _ = child.kill();
                }
            }
        });
    }
    let mut line = String::new();
    let read = reader.read_line(&mut line);
    let _ = done.send(());
    if timed_out.load(Ordering::SeqCst) {
        return Ok(Reply::TimedOut);
    }
    Ok(if read? == 0 { Reply::Closed } else { Reply::Line(line) })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::BufReader;
    use std::process::{Command, Stdio};
    use std::time::Instant;

    fn spawn(script: &str) -> (Arc<Mutex<Child>>, BufReader<std::process::ChildStdout>) {
        let mut child = if cfg!(windows) {
            Command::new("powershell").args(["-NoProfile", "-Command", script]).stdout(Stdio::piped()).spawn()
        } else {
            Command::new("sh").args(["-c", script]).stdout(Stdio::piped()).spawn()
        }.expect("spawn");
        let out = BufReader::new(child.stdout.take().unwrap());
        (Arc::new(Mutex::new(child)), out)
    }

    #[test]
    fn a_call_that_answers_in_time_returns_its_line() {
        let (child, mut out) = spawn("echo ok");
        match read_reply(&mut out, &child, Some(Duration::from_secs(30))).unwrap() {
            Reply::Line(line) => assert_eq!(line.trim(), "ok"),
            _ => panic!("expected a line"),
        }
    }

    #[test]
    fn a_call_that_does_not_answer_ends_the_process() {
        let script = if cfg!(windows) { "Start-Sleep 30" } else { "exec sleep 30" };
        let (child, mut out) = spawn(script);
        let start = Instant::now();
        assert!(matches!(read_reply(&mut out, &child, Some(Duration::from_millis(300))).unwrap(), Reply::TimedOut));
        assert!(start.elapsed() < Duration::from_secs(10));
        assert!(child.lock().unwrap().wait().is_ok());
    }

    #[test]
    fn a_process_that_exits_is_closed_not_timed_out() {
        let (child, mut out) = spawn("exit 0");
        assert!(matches!(read_reply(&mut out, &child, Some(Duration::from_secs(30))).unwrap(), Reply::Closed));
    }

    #[test]
    fn every_quick_call_is_an_engine_method() {
        let server = include_str!("../../../sidecar/fabric_desktop/server.py");
        for method in QUICK {
            assert!(server.contains(&format!("\"{method}\"")), "{method} is not in server.py METHODS");
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
