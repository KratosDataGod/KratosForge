"""Executions a connected tool starts: the limits mutation testing (batch 23) found unchecked. A
displayed table keeps 100 rows, the output cut names 100,000 characters, an execution keeps 2,000 log
lines, 200 finished records are kept on disk and 50 are listed. A stop waits for the kernel to leave
running before ending it, a cancel waits for the work to finish, and a query error that isn't a stop
reaches the caller. The work is a stand-in; no kernel or query runs,
and every record is written under tmp_path."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import notebook_cells
from fabric_desktop.integrations import executions as ex
from fabric_desktop.integrations.core import CallContext, Caller


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def mgr(tmp_path):
    return ex.ExecutionManager(SimpleNamespace(root=tmp_path, _attach=lambda ref: None), folder=tmp_path / "ex")


def run(client="cl_1", timeout=30.0):
    return ex.Execution("python", client, "a run", {"workspace_id": "ws"}, timeout)


def ctx(client="cl_1", timeout=60):
    return CallContext(Caller(client, "test"), timeout=timeout)


def test_a_table_keeps_its_first_100_rows():
    rows = [[i] for i in range(150)]
    [item] = ex.shape_outputs([{"type": "table", "columns": ["n"], "rows": rows, "total_rows": 150}], [10**9])
    assert item["rows"] == rows[:100] and item["total_rows"] == 150


def test_the_cut_names_100000_characters():
    out = ex.shape_outputs([{"type": "text", "text": "x" * 200_000}], [ex.MAX_OUTPUT_CHARS])
    assert out == [{"type": "omitted", "text": "[output cut: more than 100,000 characters]"}]


def test_an_execution_keeps_2000_log_lines():
    r = run()
    for i in range(2_005):
        r.log(f"line {i}")
    assert len(r.logs) == 2_000
    assert r.logs[-1].endswith(" line 1999")


def test_200_finished_records_are_kept(mgr):
    mgr.folder.mkdir(parents=True)
    for i in range(205):
        (mgr.folder / f"ex_{i:04d}.json").write_text("{}", encoding="utf-8")
    r = run()
    r.id = "ex_9999"
    mgr._save(r)
    kept = sorted(p.name for p in mgr.folder.glob("ex_*.json"))
    assert len(kept) == 200
    assert kept[0] == "ex_0006.json" and kept[-1] == "ex_9999.json"


def test_the_list_holds_50_records_unless_told_otherwise(mgr):
    mgr.folder.mkdir(parents=True)
    for i in range(55):
        rid = f"ex_{i:04d}"
        (mgr.folder / f"{rid}.json").write_text(f'{{"id": "{rid}", "client_id": "cl_1", "created_at": {i}}}',
                                                encoding="utf-8")
    listed = mgr.list()
    assert len(listed) == 50 and listed[0]["id"] == "ex_0054" and listed[-1]["id"] == "ex_0005"
    assert len(mgr.list(limit=55)) == 55


class StoppingKernel:
    """Stops the run from inside its first cell, then reports running twice before it goes idle."""

    def __init__(self, r):
        self.r, self.calls, self.polls = r, [], ["running", "running", "idle"]

    def start(self, code):
        self.r.on_cancel()
        return {"status": "ok"}

    def interrupt(self):
        self.calls.append("interrupt")

    def poll(self):
        status = self.polls.pop(0) if self.polls else "idle"
        self.calls.append(f"poll {status}")
        return {"status": status}

    def stop(self):
        self.calls.append("stop")

    def close(self):
        self.calls.append("close")


def test_a_stop_ends_the_kernel_only_once_it_has_left_running(mgr, monkeypatch):
    r = run()
    kernel = StoppingKernel(r)
    mgr._kernel_factory = lambda: kernel
    monkeypatch.setattr(notebook_cells, "wait", lambda k, snap: ({"status": "ok"}, []))
    mgr.run_cells(r, ["x = 1"], None)
    assert kernel.calls == ["interrupt", "poll running", "poll running", "poll idle", "stop", "close"]


def test_a_cancel_waits_for_the_work_to_finish(mgr, monkeypatch):
    monkeypatch.setattr(ex, "STOP_GRACE_SECONDS", 0.5)  # a cancel waits up to twice this
    r = run()
    r.state = "running"
    with mgr._lock:
        mgr._runs[r.id] = r

    def finish():
        time.sleep(0.2)
        r.finished_at = time.time()
        r.done.set()

    worker = threading.Thread(target=finish)
    worker.start()
    try:
        out = mgr.cancel(r.id, "cl_1")
    finally:
        worker.join()
    assert out["cancelled"] is True and out["finished_at"] is not None


def test_a_query_error_that_isnt_a_stop_reaches_the_caller(mgr, monkeypatch):
    import duckdb

    from fabric_desktop import sql

    def broken(con, statement):
        raise duckdb.InvalidInputException("the query failed")

    monkeypatch.setattr(sql, "execute_sandboxed", broken)
    with pytest.raises(duckdb.InvalidInputException, match="the query failed"):
        mgr.run_sql(run(), "SELECT 1 AS n", {"lakehouse_id": "lh"}, 10)
    assert mgr._sql_slot.acquire(blocking=False)  # the slot was given back
    mgr._sql_slot.release()


def test_the_list_reads_the_newest_200_files_and_sorts_by_start_time(mgr):
    mgr.folder.mkdir(parents=True)
    for i in range(205):
        rid = f"ex_{i:04d}"
        created = -i if i < 3 else i  # the first three files say they started earliest... or not at all
        body = {"id": rid, "client_id": "cl_1"} | ({"created_at": created} if i else {})
        (mgr.folder / f"{rid}.json").write_text(json.dumps(body), encoding="utf-8")
    listed = [r["id"] for r in mgr.list(limit=300)]
    assert len(listed) == 200 and "ex_0004" not in listed and listed[0] == "ex_0204"
    small = mgr.folder.parent / "small"
    small.mkdir()
    for rid, created in (("ex_a", None), ("ex_b", 0.5), ("ex_c", 3), ("ex_d", 2)):
        body = {"id": rid, "client_id": "cl_1"} | ({"created_at": created} if created is not None else {})
        (small / f"{rid}.json").write_text(json.dumps(body), encoding="utf-8")
    mgr.folder = small
    assert [r["id"] for r in mgr.list()] == ["ex_c", "ex_d", "ex_b", "ex_a"]  # no start time sorts as 0


def test_a_running_execution_is_listed_without_its_result(mgr):
    r = run()
    r.result = {"rows": [[1]]}
    with mgr._lock:
        mgr._runs[r.id] = r
    [listed] = mgr.list()
    assert listed["id"] == r.id and "result" not in listed


def test_a_record_another_save_already_removed_is_no_error(mgr, monkeypatch, tmp_path):
    monkeypatch.setattr(ex, "KEEP_RECORDS", 1)

    class Folder(type(tmp_path)):
        def glob(self, pattern):
            yield self / "ex_0000_gone.json"  # listed, then removed before this save gets to it
            yield from super().glob(pattern)

    mgr.folder = Folder(tmp_path / "ex")
    mgr.folder.mkdir(parents=True)
    r = run()
    r.id = "ex_9999"
    mgr._save(r)
    assert [p.name for p in (tmp_path / "ex").iterdir()] == ["ex_9999.json"]


def test_a_view_counts_seconds_from_the_start_to_three_places():
    r = run()
    r.started_at, r.finished_at = 10.0, 12.345678
    assert r.view()["seconds"] == 2.346
    r.started_at = None
    assert r.view()["seconds"] == 0.0


def test_a_query_timeout_is_retryable_and_a_cancel_is_not(mgr):
    r = run(timeout=12)
    r.state = "timed_out"
    err = mgr._stopped(r)
    assert (err.code, err.retryable) == ("timeout", True) and "(12 s)" in err.message
    r.state = "cancelled"
    assert mgr._stopped(r).retryable is False


class CellKernel:
    def __init__(self, statuses):
        self.statuses = list(statuses)

    def start(self, code):
        return {"status": "starting"}

    def close(self):
        pass


def test_cells_are_numbered_from_1_and_a_failed_one_isnt_retryable(mgr, monkeypatch):
    kernel = CellKernel(["ok", "error"])
    mgr._kernel_factory = lambda: kernel
    monkeypatch.setattr(notebook_cells, "wait", lambda k, snap: ({"status": k.statuses.pop(0), "seconds": 0.1}, []))
    r = run()
    r.state = "running"
    out = mgr.run_cells(r, ["x = 1", "1 / 0", "y = 2"], None)
    assert [c["cell"] for c in out["cells"]] == [1, 2]
    assert (out["cells_total"], out["cells_run"]) == (3, 2)
    assert [line.split(" ", 1)[1] for line in r.logs] == ["cell 1 of 3", "cell 2 of 3", "cell 2 ended: error"]
    assert r.state == "failed"
    assert r.error == {"code": "failed", "message": "Cell 2 failed.", "retryable": False}


def test_an_error_output_keeps_its_traceback():
    [item] = ex.shape_outputs([{"type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero",
                                "traceback": "Traceback: line 1"}], [10**6])
    assert item == {"type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero",
                    "traceback": "Traceback: line 1"}


def test_an_execution_and_its_timer_run_on_daemon_threads(mgr):
    seen = {}

    def work(r):
        seen["work"] = threading.current_thread().daemon
        deadline = time.monotonic() + 5  # the timer starts just after the work
        while not (timers := [t for t in threading.enumerate() if t.name == f"execution-timer-{r.id}"]) \
                and time.monotonic() < deadline:
            time.sleep(0.01)
        seen["timer"] = [t.daemon for t in timers]
        return None

    view = mgr.start(run(), work, ctx(), wait=5)
    assert view["state"] == "succeeded"
    assert seen == {"work": True, "timer": [True]}


def test_the_query_stop_runs_on_a_daemon_thread(mgr, monkeypatch):
    import duckdb

    from fabric_desktop import sql

    r = run()
    seen = []

    def stopped_query(con, statement):
        r.on_cancel()
        seen.extend(t.daemon for t in threading.enumerate() if t.name == f"execution-stop-{r.id}")
        raise duckdb.InterruptException("interrupted")

    monkeypatch.setattr(sql, "execute_sandboxed", stopped_query)
    with pytest.raises(duckdb.InterruptException):
        mgr.run_sql(r, "SELECT 1 AS n", {"lakehouse_id": "lh"}, 10)
    assert seen == [True]


def test_a_call_with_a_time_limit_answers_a_second_before_it(mgr):
    """A call with 2 s left waits 1 s for the work, then answers with it still running."""
    def work(r):
        time.sleep(1.6)
        return {"ok": True}

    r = run()
    view = mgr.start(r, work, ctx(timeout=2), wait=10)
    try:
        assert view["state"] == "running"
    finally:
        r.done.wait(10)


def test_an_image_is_named_by_its_type_when_it_has_one():
    out = ex.shape_outputs([{"type": "image", "mime": "image/png", "data": "AAAA"}, {"type": "image", "data": "AAAA"}],
                           [10**6])
    assert [o["text"] for o in out] == ["[image/png not sent to external tools]", "[image not sent to external tools]"]
