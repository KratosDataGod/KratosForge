"""SQL page runs (sql_exec.py), the paths the other sql_exec tests leave out: the checks start() makes
before anything is queued, a warehouse that can't be found or is named differently, a warehouse run its
policy refuses, the worker's questions, how a worker's events end a run (a statement refused part-way,
the warehouse's own timeout, a Stop during a commit, a crash), a worker that can't start, why a local run
failed, and values JSON can't hold. No worker process is started."""

import datetime as dt
import decimal
import errno
import io
import json
import time
import types

import pyarrow as pa
import pytest

from fabric_desktop import sql_exec as se
from fabric_desktop import warehouse

LIMITS = {"sql_page_rows": 1000, "sql_time_limit_minutes": 0, "sql_memory_limit_percent": 60, "sql_max_concurrent": 2,
          "sql_result_retention_hours": 24, "sql_temp_dir": "", "sql_time_limit_by_policy": False}
LH = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "Sales"}
WH = {"workspace_id": "ws", "item_id": "wh", "kind": "warehouse"}


def _manager(tmp_path, **kw):
    kw.setdefault("limits", lambda: dict(LIMITS))
    return se.SqlExecutions(tmp_path / "data", token=lambda: "", **kw)


class Proc:
    """A worker that already wrote ``lines`` and exits with ``code``."""

    def __init__(self, events=(), code=0):
        self.stdout = iter([json.dumps(e) + "\n" for e in events] + ["not json\n"])
        self.stdin = io.StringIO()
        self.code = code

    def wait(self, timeout=None):
        return self.code

    def poll(self):
        return self.code


# ---- small helpers


def test_threads_share_the_cores_and_a_change_says_what_a_stop_kept(monkeypatch):
    monkeypatch.setattr(se.os, "cpu_count", lambda: 8)
    assert se.run_threads({"sql_max_concurrent": 3}) == 2
    monkeypatch.setattr(se.os, "cpu_count", lambda: None)
    assert se.run_threads({"sql_max_concurrent": 4}) == 1
    assert se._change_kept(False, True) == ""
    assert "may or may not have been kept" in se._change_kept(True, True)
    assert se._change_kept(True, False) == " The change was rolled back, so nothing was kept."
    assert se.time_limit_message("caller", 60) == ""


def test_a_snapshot_with_fewer_rows_than_written_needs_a_rerun(tmp_path):
    path = tmp_path / "r.arrow"
    table = pa.table({"a": [1, 2]})
    with pa.OSFile(str(path), "wb") as sink, pa.ipc.new_file(sink, table.schema) as w:
        w.write_table(table)
    assert se._whole(path, 2).num_rows == 2
    with pytest.raises(se.RerunNeeded, match="holds 2 of the 5 rows"):
        se._whole(path, 5)


def test_a_part_keeps_its_offsets_and_what_it_changed():
    assert se._part({"statement": 2, "rows": 5, "batches": [2, 3], "affected": 4, "seconds": 1.5, "columns": [{"name": "a"}]}) == {
        "statement": 2, "rows": 5, "columns": [{"name": "a"}], "batches": [2, 3], "offsets": [0, 2], "affected": 4, "seconds": 1.5}
    assert se._part({}) == {"statement": 0, "rows": 0, "columns": [], "batches": [], "offsets": []}


def test_values_json_cant_hold_become_ones_it_can():
    cells = [float("nan"), float("inf"), b"\x00\x01", dt.date(2026, 1, 2), dt.datetime(2026, 1, 2, 3, 4), dt.time(5, 6),
             decimal.Decimal("1.10"), {1: decimal.Decimal("2")}, (("k", b"v"),), dt.timedelta(minutes=1, seconds=30), 7]
    assert [se._json_cell(v) for v in cells] == [None, None, "AAE=", "2026-01-02", "2026-01-02T03:04:00", "05:06:00",
                                               "1.10", {"1": "2"}, [["k", "dg=="]], 90.0, 7]


def test_a_run_never_started_has_no_seconds(tmp_path):
    run = se.Execution(id="a", query="q", dialect="spark", lakehouse={}, allow=("read_only",), folder=tmp_path)
    object.__delattr__(run, "_mono_created")
    assert run.seconds() == 0.0
    run.units, run.parts = ["SELECT 1", "SELECT 2"], [{"statement": 2}]
    assert run.snapshot == tmp_path / "result-2.arrow"


# ---- start(): refused before anything is queued


@pytest.mark.parametrize("kw, error, match", [
    ({"query": "SELECT :a", "target": "local", "project": "p", "params": [{"name": "a", "type": "number", "value": "1"}]},
     ValueError, "Query parameters are for a lakehouse or a warehouse"),
    ({"query": "", "statements": ["SELECT 1", " "], "lakehouse_ref": LH}, ValueError, "statements must be 1 to"),
    ({"query": "", "statements": "SELECT 1", "lakehouse_ref": LH}, ValueError, "statements must be 1 to"),
    ({"query": "", "statements": ["SELECT 1"], "lakehouse_ref": LH, "profile": True}, ValueError, "Several statements"),
    ({"query": "SELECT 1", "target": "cloud"}, ValueError, "target must be one of"),
    ({"query": "SELECT 1", "target": "warehouse", "dialect": "spark", "warehouse_ref": WH}, ValueError, "A warehouse runs T-SQL"),
    ({"query": "SELECT 1", "target": "warehouse", "dialect": "tsql", "warehouse_ref": {**WH, "kind": "lake"}}, ValueError,
     "Pick a warehouse or SQL analytics endpoint"),
    ({"query": "SELECT 1", "lakehouse_ref": {"workspace_id": "ws"}}, ValueError, "Pick a lakehouse"),
    ({"query": "SELECT 1", "target": "local", "project": ""}, ValueError, "Pick a local data project"),
    ({"query": "SELECT 1", "target": "local", "project": "p", "dialect": "mysql"}, ValueError, "runs duckdb or spark or tsql SQL"),
    ({"query": "SELECT 1", "target": "local", "project": "p"}, ValueError, "Local data projects aren't available here"),
    ({"query": "SELECT 1", "lakehouse_ref": LH, "allow": ["onelake_write"]}, PermissionError, "The SQL page can only read"),
    ({"query": "SELECT 1", "target": "warehouse", "dialect": "tsql", "warehouse_ref": WH, "allow": ["local_mutation"]},
     PermissionError, "A warehouse run reads, or changes"),
    ({"query": "SELECT 1", "target": "warehouse", "dialect": "tsql", "warehouse_ref": WH, "allow": ["warehouse_write"],
      "estimated_plan": True}, ValueError, "An estimated plan is for"),
    ({"query": "SELECT 1", "target": "warehouse", "dialect": "tsql", "warehouse_ref": WH, "profile": True}, ValueError,
     "A query profile is for a lakehouse query"),
    ({"query": "SELECT 1", "lakehouse_ref": LH, "allow": ["local_mutation"], "estimated_plan": True}, ValueError,
     "A plan or profile is for a query that only reads"),
    ({"query": "SELECT 1", "lakehouse_ref": LH, "precondition": {}}, ValueError, "A precondition is for a warehouse change"),
    ({"query": "DROP VIEW v", "target": "warehouse", "dialect": "tsql", "warehouse_ref": WH, "allow": ["warehouse_write"],
      "precondition": {"kind": "view"}}, ValueError, "The change wasn't sent: a precondition names"),
    ({"query": "UPDATE t SET a = 1", "target": "warehouse", "dialect": "tsql", "warehouse_ref": WH,
      "allow": ["warehouse_write"]}, PermissionError, "only after you confirm it, naming the warehouse"),
])
def test_start_says_why_a_run_cant_be_queued(tmp_path, kw, error, match):
    m = _manager(tmp_path)
    with pytest.raises(error, match=match):
        m.start(**kw)
    assert m.recent() == []


def test_a_local_run_asking_for_more_than_the_project_is_refused(tmp_path):
    m = _manager(tmp_path, local_engine=lambda p: types.SimpleNamespace(project=types.SimpleNamespace(id=p, name=p)))
    with pytest.raises(PermissionError, match="never files elsewhere on this PC"):
        m.start("SELECT 1", target="local", project="p", allow=["warehouse_write"])


@pytest.mark.parametrize("source, tail", [("policy", "by your organization."), ("user", "Settings › SQL.")])
def test_warehouse_changes_turned_off_say_who_turned_them_off(tmp_path, monkeypatch, source, tail):
    monkeypatch.setattr(se.app_settings, "load", lambda: {"sql_warehouse_writes": {"value": False, "source": source}})
    with pytest.raises(PermissionError, match="Warehouse changes are turned off") as e:
        _manager(tmp_path).start("UPDATE t SET a = 1 WHERE b = 2", target="warehouse", dialect="tsql", warehouse_ref=WH,
                                 allow=["warehouse_write"], confirm=True, confirm_name="WH")
    assert str(e.value).endswith(tail)


def test_a_warehouse_dry_run_classes_each_statement_and_runs_nothing(tmp_path):
    m = _manager(tmp_path)
    plan = m.start("SELECT 1; DROP TABLE t", target="warehouse", dialect="tsql", warehouse_ref=WH, dry_run=True)
    assert plan["dry_run"] is True and plan["target"] == "warehouse"
    assert [s["statement"] for s in plan["statements"]] == [1, 2]
    assert plan["refused"]["statement"] == 2
    assert m.recent() == []


def test_parameters_and_statements_are_bound_and_split_before_a_dry_run(tmp_path, monkeypatch):
    m = _manager(tmp_path)
    plan = m.start("", lakehouse_ref=LH, statements=["SELECT :a AS x;", "SELECT 2"], dry_run=True,
                   params=[{"name": "a", "type": "number", "value": "1"}])
    assert [s["statement"] for s in plan["statements"]] == [1, 2] and plan["refused"] is None
    monkeypatch.setattr(se.app_settings, "load", lambda: {"sql_warehouse_writes": {"value": True, "source": "user"}})
    plan = m.start("", target="warehouse", dialect="tsql", warehouse_ref=WH, allow=["warehouse_write"], dry_run=True,
                   statements=["UPDATE t SET a = @a WHERE b = 1", "SELECT 1"], params=[{"name": "a", "type": "text", "value": "it's"}])
    assert [s["statement"] for s in plan["statements"]] == [1, 2]
    assert plan["permission"] == "warehouse_write" and plan["refused"] is None
    assert m.recent() == []


def test_a_warehouse_run_its_policy_refuses_is_kept_as_failed_and_fabric_is_never_asked(tmp_path):
    asked = []
    m = _manager(tmp_path, resolve_warehouse=lambda ref: asked.append(ref))
    s = m.start("DROP TABLE dbo.t", target="warehouse", dialect="tsql", warehouse_ref=WH)
    assert s["state"] == "failed" and s["error_kind"] == "permission"
    assert s["error"].startswith("Statement 1 was refused (") and s["error"].endswith("A warehouse run only reads.")
    assert s["refused"]["statement"] == 1
    assert asked == [] and m.status(s["id"])["state"] == "failed"


def test_a_warehouse_query_with_nothing_to_find_it_isnt_available(tmp_path):
    with pytest.raises(ValueError, match="Warehouse queries aren't available here"):
        _manager(tmp_path).start("SELECT 1", target="warehouse", dialect="tsql", warehouse_ref=WH)


# ---- finding the warehouse


def _queued_warehouse(m, tmp_path, confirm_name=""):
    run = se.Execution(id="w1", query="UPDATE t SET a = 1", dialect="tsql", lakehouse={}, folder=tmp_path / "w1",
                       allow=("read_only", "warehouse_write") if confirm_name else ("read_only",), target="warehouse")
    run.warehouse, run.confirm_name = dict(WH), confirm_name
    m._runs[run.id] = run
    return run


def test_a_warehouse_fabric_cant_find_fails_with_why(tmp_path):
    def resolve(ref):
        raise RuntimeError("You're signed out.")
    m = _manager(tmp_path, resolve_warehouse=resolve)
    run = _queued_warehouse(m, tmp_path)
    m._find_warehouse(run)
    assert (run.state, run.error_kind) == ("failed", "fabric")
    assert run.error == "Couldn't find the warehouse's SQL connection in Fabric: You're signed out."


def test_a_change_to_a_warehouse_fabric_names_differently_sends_nothing(tmp_path):
    m = _manager(tmp_path, resolve_warehouse=lambda ref: warehouse.Endpoint("srv", "Finance"))
    run = _queued_warehouse(m, tmp_path, confirm_name="Sales")
    m._find_warehouse(run)
    assert (run.state, run.error_kind, run.permission) == ("failed", "permission", "warehouse_write")
    assert "You confirmed a change to Sales, but Fabric names this warehouse Finance" in run.error
    assert "server" not in run.warehouse


# ---- the worker's questions


def test_the_worker_is_answered_its_questions_or_told_why_not(tmp_path):
    def broken():
        raise RuntimeError("cancelled")
    m = _manager(tmp_path, warehouse_token=lambda: "wh-token", schemas_enabled=lambda ref: ref["name"] == "Sales")  # pragma: allowlist secret (test value)
    wh = se.Execution(id="a", query="q", dialect="tsql", lakehouse={}, allow=("read_only",), folder=tmp_path, target="warehouse")
    lh = se.Execution(id="b", query="q", dialect="spark", lakehouse=dict(LH), allow=("read_only",), folder=tmp_path)

    def ask(run, name):
        proc = Proc()
        m._answer(run, proc, name)
        return json.loads(proc.stdin.getvalue())
    assert ask(wh, "warehouse_token") == {"value": "wh-token"}  # pragma: allowlist secret (test value)
    assert ask(lh, "schemas_enabled") == {"value": True}
    assert ask(lh, "warehouse_token") == {"error": "Unknown question 'warehouse_token'"}
    m._warehouse_token = broken
    assert ask(wh, "warehouse_token") == {"error": "Sign in to query the warehouse (RuntimeError: cancelled)"}
    m._token = broken
    assert ask(lh, "token") == {"error": "Sign in to read lakehouse tables (RuntimeError: cancelled)"}
    closed = Proc()
    closed.stdin.close()
    m._answer(lh, closed, "schemas_enabled")  # the run already ended: nothing to answer


# ---- how the worker's events end a run


def _run(m, tmp_path, **kw):
    run = se.Execution(id=kw.pop("id", "r1"), query="q", dialect="tsql", lakehouse={}, folder=tmp_path / "r1",
                       allow=kw.pop("allow", ("read_only", "warehouse_write")), target="warehouse", **kw)
    run.folder.mkdir(parents=True, exist_ok=True)
    run.state, run.started = "running", time.time()
    m._runs[run.id] = run
    return run


def test_a_statement_refused_part_way_keeps_the_ones_before_it(tmp_path):
    m = _manager(tmp_path)
    run = _run(m, tmp_path, units=["UPDATE a SET x = 1 WHERE y = 2", "DROP TABLE b"])
    (run.folder / "result-1.arrow").write_bytes(b"part 1")
    (run.folder / "result-2.arrow").write_bytes(b"half")
    (run.folder / "spill").mkdir()
    m._read(run, Proc([{"event": "started", "permission": "warehouse_write"}, {"event": "statement", "statement": 1},
                       {"event": "progress", "rows": 3}, {"event": "part", "statement": 1, "rows": 0, "affected": 3},
                       {"event": "statement", "statement": 2}, {"event": "call", "name": "nothing"},
                       {"event": "error", "kind": "permission", "permission": "warehouse_write", "statement": 2,
                        "message": "Statement 2 drops a table.", "batch": 1}], code=1))
    s = run.status()
    assert (s["state"], s["error_kind"], s["failed_statement"], s["failed_batch"], s["rows"]) == ("failed", "permission", 2, 1, 0)
    assert s["refused"]["statement"] == 2 and s["refused"]["permission"] == "warehouse_write"
    assert [p["affected"] for p in s["parts"]] == [3]
    assert sorted(f.name for f in run.folder.iterdir()) == ["result-1.arrow"]


def test_a_finished_script_is_read_as_its_last_statement(tmp_path):
    m = _manager(tmp_path)
    run = _run(m, tmp_path, units=["SELECT 1", "SELECT 2"], allow=("read_only",))
    m._read(run, Proc([{"event": "part", "statement": 1, "rows": 1, "batches": [1]},
                       {"event": "part", "statement": 2, "rows": 5, "batches": [2, 3], "columns": [{"name": "n"}]},
                       {"event": "done", "rows": 99}]))
    assert (run.state, run.rows, run.offsets, run.columns) == ("completed", 5, [0, 2], [{"name": "n"}])


def test_the_warehouse_own_timeout_is_told_in_its_words(tmp_path):
    m = _manager(tmp_path)
    run = _run(m, tmp_path, allow=("read_only",))
    m._read(run, Proc([{"event": "error", "kind": "timeout", "message": "Query timeout expired.", "batch": 2}], code=1))
    assert (run.state, run.error_kind, run.error, run.failed_batch) == ("timed_out", "timeout", "Query timeout expired.", 2)
    assert not run.folder.exists()


def test_a_stop_during_a_commit_says_the_change_may_have_been_kept(tmp_path):
    m = _manager(tmp_path)
    run = _run(m, tmp_path, stop_reason="cancel")
    m._read(run, Proc([{"event": "committing"}], code=-9))
    assert run.state == "cancelled" and run.committing
    assert run.error.startswith("Stopped. The change was being committed, so it may or may not have been kept.")


def test_a_worker_that_crashed_says_it_may_have_run_out_of_memory(tmp_path):
    m = _manager(tmp_path)
    run = _run(m, tmp_path, allow=("read_only",))
    m._read(run, Proc([], code=-9))
    assert (run.state, run.error_kind) == ("failed", "engine")
    assert run.error.startswith("The query's process stopped unexpectedly (exit code -9).")


# ---- a worker that can't start


@pytest.mark.parametrize("target", ["lakehouse", "warehouse"])
def test_a_worker_that_cant_start_fails_the_run_and_keeps_no_values(tmp_path, target):
    m = _manager(tmp_path, command=[str(tmp_path / "no-such-python")])
    m._memory_budget = lambda percent: {"duckdb_mb": 512, "cap_mb": 1024, "bound": "percent", "free_mb": 4096}
    run = se.Execution(id="s1", query="SELECT :a", dialect="tsql" if target == "warehouse" else "spark",
                       lakehouse=dict(LH) if target == "lakehouse" else {}, allow=("read_only",), folder=tmp_path / "s1",
                       target=target, units=["SELECT 1", "SELECT 2"], params=[{"name": "a", "type": "int", "value": 1}],
                       plan=target == "lakehouse")
    if target == "warehouse":
        run.warehouse = {**WH, "server": "srv", "database": "Sales"}
        run.precondition = {"kind": "table", "schema": "dbo", "name": "t", "exists": False}
    m._runs[run.id] = run
    m._launch(run, dict(LIMITS))
    assert (run.state, run.error_kind) == ("failed", "engine")
    assert run.error and run.params == [] and run.proc is None


# ---- what a result can be read from


def test_a_result_that_isnt_there_says_why(tmp_path):
    m = _manager(tmp_path)
    run = _run(m, tmp_path, allow=("read_only",))
    run.state = "timed_out"
    with pytest.raises(ValueError, match="The query is timed out, so there are no rows"):
        m._result(run, None, "are no rows")
    run.parts = [se._part({"statement": 1, "rows": 2, "batches": [2]})]
    assert m._result(run, 1, "x").path == run.folder / "result-1.arrow"
    with pytest.raises(ValueError, match="Statement 3 has no result, so there are no rows"):
        m._result(run, 3, "are no rows")


# ---- why a local run failed


def test_a_local_run_that_ran_out_of_room_or_memory_names_the_projects_settings(tmp_path):
    limited = types.SimpleNamespace(settings=types.SimpleNamespace(max_temp_size="10GB", memory_limit="4GB"))
    plain = types.SimpleNamespace(settings=types.SimpleNamespace(max_temp_size="", memory_limit=""), temp_dir=str(tmp_path),
                                  root=str(tmp_path))
    spill = RuntimeError("IO Error: Failed to offload data block of size 256 KiB")
    kind, msg = se.local_error(spill, limited, None)
    assert kind == "disk" and "reached this project's limit (10GB" in msg
    kind, msg = se.local_error(spill, plain, None)
    assert kind == "disk" and msg.startswith("Stopped: the query ran out of room for its temp files, as the temp folder's drive is full (")
    oom = RuntimeError("Out of Memory Error: could not allocate block")
    assert se.local_error(oom, limited, None)[0] == "memory"
    assert "this project's memory limit (4GB)" in se.local_error(oom, limited, None)[1]
    assert "DuckDB's default share of this PC's memory (80%)" in se.local_error(oom, plain, None)[1]
    full = OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "db.duckdb"))
    kind, msg = se.local_error(full, plain, None)
    assert kind == "disk" and msg.startswith("The disk is full") and "The statement that ran out of room didn't finish." in msg
    assert se.local_error(RuntimeError(""), plain, None) == ("sql", "RuntimeError")


def test_shutdown_ends_a_run_whose_process_is_gone_without_waiting(tmp_path):
    m = _manager(tmp_path)
    run = _run(m, tmp_path, allow=("read_only",))
    run.proc = None
    m.shutdown()
    assert run.stop_reason == "cancel"
