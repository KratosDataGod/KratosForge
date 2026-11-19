"""SQL page runs (sql_exec.py): the remaining edges, with no worker started. A warehouse that answers after
the run was stopped, Stop on a finished run, pinning a result that was cleaned up, the job a warehouse run
sends, a worker that's gone when answered or read, the warehouse's own time limit, cell values JSON can
hold, and cleanup of old results and folders that aren't runs. Processes are stand-ins."""

from __future__ import annotations

import datetime as dt
import io
import json
import threading
import time
import types

import pytest

from fabric_desktop import sql_exec as se


def manager(tmp_path, limits=None):
    return se.SqlExecutions(tmp_path / "data", token=lambda: "tok", limits=lambda: {"sql_result_retention_hours": 0, "sql_max_concurrent": 2} | dict(limits or {}))


def execution(tmp_path, run_id="abcdef123456", **kw):
    folder = tmp_path / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    return se.Execution(id=run_id, query="SELECT 1", dialect="duckdb", lakehouse={}, allow=(), folder=folder, **kw)


class Closed:
    """A worker's stdin after it ended."""

    def __init__(self, error=None):
        self.error, self.written = error or OSError("pipe closed"), []

    def write(self, text):
        self.written.append(text)

    def flush(self):
        raise self.error

    def close(self):
        raise self.error


class Proc:
    def __init__(self, lines=(), stdin=None):
        self.stdout, self.stdin, self.killed = io.StringIO("".join(json.dumps(x) + "\n" for x in lines)), stdin, 0

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed += 1


# ---- the run's life


def test_a_warehouse_found_after_the_run_was_stopped_leaves_it_stopped(tmp_path):
    m = manager(tmp_path)
    run = execution(tmp_path, state="cancelled", warehouse={"id": "w"})

    def missing(item):
        raise LookupError("not found")
    m._resolve_warehouse = missing
    m._find_warehouse(run)
    assert run.state == "cancelled" and run.error == ""
    m._resolve_warehouse = lambda item: types.SimpleNamespace(server="s", database="Other")
    run.confirm_name = "Sales"
    m._find_warehouse(run)
    assert run.state == "cancelled" and run.error == ""


def test_stop_on_a_finished_run_changes_nothing_and_time_up_after_it_neither(tmp_path):
    m = manager(tmp_path)
    run = execution(tmp_path, state="completed")
    m._runs[run.id] = run
    assert m.cancel(run.id)["state"] == "completed"
    m._time_up(run)
    assert run.stop_reason == ""


def test_a_local_run_with_no_engine_has_nothing_to_interrupt(tmp_path):
    m = manager(tmp_path)
    run = execution(tmp_path, target="local", state="running")
    m._end_process(run)  # no local engine yet: nothing to cancel
    assert run.state == "running"


def test_a_result_that_was_cleaned_up_cant_be_pinned_or_profiled(tmp_path):
    m = manager(tmp_path)
    run = execution(tmp_path, state="completed", rows=3)
    m._runs[run.id] = run
    run.folder.rmdir()
    with pytest.raises(se.RerunNeeded, match="cleaned up, so it can't be pinned"):
        m.pin(run.id)
    with pytest.raises(se.RerunNeeded, match="cleaned up. Run the query again to see its statistics"):
        m.column_stats(run.id, 0)


def test_a_warehouse_run_sends_its_statements_values_and_precondition(tmp_path, monkeypatch):
    m = manager(tmp_path)
    stdin = Closed()
    started = []
    monkeypatch.setattr(se.subprocess, "Popen", lambda *a, **k: started.append(a) or Proc([{"event": "done", "rows": 0}], stdin))
    run = execution(tmp_path, target="warehouse", warehouse={"server": "srv", "database": "Sales"},
                    units=["SELECT 1", "SELECT 2"], params=[{"name": "@a", "value": 1}],
                    precondition={"object": "dbo.t"})
    m._runs[run.id] = run
    m._launch(run, {"sql_time_limit_minutes": 0})
    job = json.loads(stdin.written[0])
    assert job["statements"] == ["SELECT 1", "SELECT 2"] and job["parts_dir"] == str(run.folder)
    assert job["params"] == [{"name": "@a", "value": 1}] and job["precondition"] == {"object": "dbo.t"}
    assert job["time_limit_s"] == 0 and run.timer is None and run.params == []
    for t in threading.enumerate():  # the reader thread sees the worker end
        if t.name == f"sql-run-{run.id}":
            t.join(timeout=30)
    assert run.state == "completed"


def test_answering_a_worker_that_ended_does_nothing(tmp_path):
    m = manager(tmp_path)
    run = execution(tmp_path)
    proc = Proc(stdin=Closed(ValueError("I/O operation on closed file")))
    m._answer(run, proc, "token")  # no error
    assert json.loads(proc.stdin.written[0]) == {"value": "tok"}


def test_a_worker_s_odd_events_are_skipped_and_the_warehouse_s_own_time_limit_is_its_message(tmp_path):
    m = manager(tmp_path)
    run = execution(tmp_path, state="running", target="warehouse")
    m._runs[run.id] = run
    lines = [{"event": "statement", "statement": 1}, {"event": "part", "rows": 2},
             {"event": "error", "kind": "timeout", "message": "Query timeout expired"}]
    m._read(run, Proc(lines, stdin=Closed()))
    assert run.state == "timed_out" and run.error == "Query timeout expired" and run.parts == []
    run2 = execution(tmp_path, "bcdefa234567", state="running", target="warehouse")
    m._runs[run2.id] = run2
    m._read(run2, Proc([{"event": "error", "kind": "timeout"}], stdin=Closed()))
    assert run2.error == "The warehouse stopped the run at its time limit."


# ---- values and parts


def test_cells_in_lists_objects_and_durations_are_json_values():
    out = se._json_cell({"when": dt.date(2026, 11, 19), "tags": [b"\x01", float("nan")],
                         "pair": ("k", dt.timedelta(minutes=1, seconds=30))})
    assert out == {"when": "2026-11-19", "tags": ["AQ==", None], "pair": ["k", 90.0]}


def test_a_part_keeps_the_rows_it_changed_and_its_time_only_when_given():
    assert se._part({"statement": 2, "rows": 0, "affected": 5}) == {
        "statement": 2, "rows": 0, "columns": [], "batches": [], "offsets": [], "affected": 5}
    assert se._part({"statement": 1, "batches": [2, 3], "seconds": 0.5})["offsets"] == [0, 2]


# ---- cleanup


def test_an_old_result_is_removed_but_its_run_kept_and_other_folders_left(tmp_path):
    m = manager(tmp_path, {"sql_result_retention_hours": 1})
    old = execution(tmp_path, state="completed")
    old.folder = m.base() / old.id
    old.folder.mkdir(parents=True)
    old.ended = time.time() - 2 * 3600
    m._runs[old.id] = old
    (m.base() / "notes").mkdir()  # not a run's folder: its name isn't 12 characters
    (m.base() / "readme.txt").write_text("x", encoding="utf-8")
    m.cleanup(earlier_sessions=True)
    assert not old.folder.exists() and old.id in m._runs
    assert (m.base() / "notes").is_dir() and (m.base() / "readme.txt").exists()
