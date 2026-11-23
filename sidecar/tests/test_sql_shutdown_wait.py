"""Closing the app waits for all of its SQL runs at once, under one deadline, not one wait per run.

No real worker processes: fake processes and reader threads, driven by events.
"""

from __future__ import annotations

import subprocess
import threading

from fabric_desktop import sql_exec


class FakeProc:
    """A worker process: kill() asks it to end, and it ends ``stop_after`` seconds later (never, if None)."""

    def __init__(self, stop_after: float | None):
        self.stop_after = stop_after
        self.killed = threading.Event()
        self.ended = threading.Event()

    def poll(self):
        return 0 if self.ended.is_set() else None

    def kill(self):
        if not self.killed.is_set():
            self.killed.set()
            if self.stop_after is not None:
                threading.Timer(self.stop_after, self.ended.set).start()

    def wait(self, timeout=None):
        if not self.ended.wait(timeout):
            raise subprocess.TimeoutExpired("sql_worker", timeout)
        return 0


def add_run(manager, tmp_path, n, stop_after, audit):
    proc = FakeProc(stop_after)

    def read():  # like _read: once the process has ended, the run's audit line
        proc.ended.wait()
        audit.append(run.id)

    run = sql_exec.Execution(id=f"run{n}", query="SELECT 1", dialect="spark", lakehouse={}, allow=("read_only",),
                             folder=tmp_path / f"run{n}", state="running", proc=proc)  # type: ignore[arg-type]
    run.reader = threading.Thread(target=read, daemon=True)
    run.reader.start()
    manager._runs[run.id] = run
    return proc


def close(manager, seconds):
    closing = threading.Thread(target=manager.shutdown, daemon=True)
    closing.start()
    closing.join(seconds)
    return not closing.is_alive()


def test_closing_waits_for_stuck_workers_under_one_deadline_not_one_each(tmp_path, monkeypatch):
    # Three workers that never end after the kill: each used to get its own wait (10 s apiece, and
    # then 10 s for each reader), so closing took a multiple of the deadline. Now all share one.
    monkeypatch.setattr(sql_exec, "SHUTDOWN_WAIT_SECONDS", 1.0)
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "")
    procs = [add_run(manager, tmp_path, n, None, []) for n in range(3)]
    assert close(manager, 6.0), "closing the app waited past its one deadline"
    assert all(p.killed.is_set() for p in procs)
    for p in procs:
        p.ended.set()  # let the fake readers finish


def test_runs_that_take_a_while_to_stop_all_write_their_audit_line_before_closing_returns(tmp_path, monkeypatch):
    # Three runs that each take ~1 s to end, beside one that never does: the three are waited for
    # together, so each audit line is written before shutdown returns, and the stuck one is killed
    # without holding the close past the deadline.
    monkeypatch.setattr(sql_exec, "SHUTDOWN_WAIT_SECONDS", 3.0)
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "")
    audit: list[str] = []
    slow = [add_run(manager, tmp_path, n, 1.0, audit) for n in range(3)]
    stuck = add_run(manager, tmp_path, 3, None, audit)
    assert close(manager, 8.0), "closing the app waited past its one deadline"
    assert sorted(audit) == ["run0", "run1", "run2"]
    assert all(p.killed.is_set() for p in slow) and stuck.killed.is_set()
    stuck.ended.set()
