"""The SQL page's runs (sql_exec.py), the paths the main tests don't reach: a run asked for wrongly, a
worker that can't start, says something that isn't an event, asks a question the engine doesn't know,
or stops without a result; a local project run whose engine is busy, fails or went away; and exports
and results asked for before they exist. Workers here are short scripts; nothing reaches Fabric."""

from __future__ import annotations

import contextlib
import sys
from types import SimpleNamespace

import pyarrow as pa
import pytest

from fabric_desktop import sql_exec
from fabric_desktop.localdata import EngineBusy
from test_sql_exec import limits, wait

LAKE = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Test"}


def manager(tmp_path, command=None, **kw):
    audit = []
    m = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "SECRET-TOKEN", audit=lambda *a: audit.append(a),
                               limits=limits(), downloads=lambda: tmp_path / "Downloads", command=command, **kw)
    m.audit_lines = audit
    return m


def worker(script):
    """A worker process that reads its job and then runs ``script`` (with json and sys imported)."""
    return [sys.executable, "-c", "import json, sys\njob = sys.stdin.readline()\n" + script]


@pytest.mark.parametrize("kwargs, says", [
    ({"query": "  "}, "The query is empty"),
    ({"query": "SELECT 1", "target": "fabric"}, "target must be one of"),
    ({"query": "SELECT 1", "target": "warehouse"}, "runs T-SQL"),
    ({"query": "SELECT 1", "target": "warehouse", "dialect": "tsql", "warehouse_ref": {"kind": "warehouse"}},
     "Pick a warehouse"),
    ({"query": "SELECT 1", "lakehouse_ref": {"workspace_id": "ws"}}, "Pick a lakehouse"),
    ({"query": "SELECT 1", "target": "local"}, "Pick a local data project"),
    ({"query": "SELECT 1", "target": "local", "project": "p", "dialect": "kql"}, "not 'kql'"),
    ({"query": "SELECT 1", "target": "local", "project": "p", "dialect": "duckdb"}, "aren't available here"),
    ({"query": "SELECT 1", "target": "warehouse", "dialect": "tsql",
      "warehouse_ref": {"kind": "warehouse", "workspace_id": "ws", "item_id": "wh"}}, "Warehouse queries aren't available"),
])
def test_a_run_asked_for_wrongly_is_refused_before_anything_starts(tmp_path, kwargs, says):
    m = manager(tmp_path)
    with pytest.raises(ValueError, match=says):
        m.start(**{"lakehouse_ref": LAKE, **kwargs})
    assert m.recent() == []


def test_a_worker_that_cant_start_fails_the_run_and_says_why(tmp_path):
    m = manager(tmp_path, command=[str(tmp_path / "no-such-python")])
    status = m.start("SELECT 1", LAKE)
    assert status["state"] == "failed" and status["error_kind"] == "engine"
    assert m.audit_lines[-1][1] == "error"


def test_a_worker_that_stops_without_a_result_reads_as_out_of_memory(tmp_path):
    m = manager(tmp_path, command=worker("print('this line is not an event', flush=True)\nsys.exit(3)"))
    try:
        status = wait(m, m.start("SELECT 1", LAKE)["id"])
    finally:
        m.shutdown()
    assert status["state"] == "failed" and status["error_kind"] == "engine" and "exit code 3" in status["error"]


def test_a_question_the_engine_doesnt_know_gets_an_error_and_a_refusal_keeps_its_permission(tmp_path):
    script = ("print(json.dumps({'event': 'call', 'name': 'favourite_colour'}), flush=True)\n"
              "reply = json.loads(sys.stdin.readline())\n"
              "print(json.dumps({'event': 'error', 'kind': 'permission', 'permission': 'onelake_write',\n"
              "                  'statement': 1, 'message': reply['error']}), flush=True)\n")
    m = manager(tmp_path, command=worker(script))
    try:
        status = wait(m, m.start("SELECT 1", LAKE)["id"])
    finally:
        m.shutdown()
    assert status["state"] == "failed" and status["error"] == "Unknown question 'favourite_colour'"
    assert status["refused"]["permission"] == "onelake_write" and status["refused"]["statement"] == 1


def test_a_sign_in_that_fails_while_the_worker_waits_fails_the_run_with_why(tmp_path):
    script = ("print(json.dumps({'event': 'call', 'name': 'token'}), flush=True)\n"
              "reply = json.loads(sys.stdin.readline())\n"
              "print(json.dumps({'event': 'error', 'kind': 'auth', 'message': reply['error']}), flush=True)\n")
    m = manager(tmp_path, command=worker(script))

    def signed_out():
        raise PermissionError("the sign-in window was closed")
    m._token = signed_out
    try:
        status = wait(m, m.start("SELECT 1", LAKE)["id"])
    finally:
        m.shutdown()
    assert status["error"] == "Sign in to read lakehouse tables (PermissionError: the sign-in window was closed)"


def test_results_and_exports_asked_for_before_they_exist(tmp_path):
    m = manager(tmp_path, command=[str(tmp_path / "no-such-python")])
    run_id = m.start("SELECT 1", LAKE)["id"]
    with pytest.raises(ValueError, match="no rows to compare"):
        m.table(run_id)
    with pytest.raises(ValueError, match="no result to export"):
        m.export(run_id, "csv")
    with pytest.raises(ValueError, match="Export as"):
        m.export(run_id, "xml")
    with pytest.raises(KeyError, match="No SQL run"):
        m.status("nope")
    with pytest.raises(KeyError, match="No export"):
        m.export_status("nope")
    assert m.query_of(run_id) == ""  # the SQL isn't kept once the run is over


def test_a_cleaned_up_result_cant_be_compared(tmp_path):
    m = manager(tmp_path, command=[str(tmp_path / "no-such-python")])
    run = m._get(m.start("SELECT 1", LAKE)["id"])
    run.state = "completed"
    with pytest.raises(sql_exec.RerunNeeded, match="Run the query again to compare"):
        m.table(run.id)


# ---- local data project runs, with the project's engine stood in


class Project:
    id, name = "p1", "Sales"
    settings = SimpleNamespace(timeout_seconds=0)


class Engine:
    project = Project()

    def __init__(self, fail=None, batches=()):
        self.fail, self.batches = fail, batches

    @contextlib.contextmanager
    def execute_reader(self, query, **kw):
        if self.fail:
            raise self.fail
        schema = pa.schema([("n", pa.int64())])
        yield _Stream(schema, self.batches)

    def cancel(self, run_id, not_started=False):
        pass


class _Stream:
    def __init__(self, schema, batches):
        self.schema, self.batches = schema, batches

    def __iter__(self):
        for n in self.batches:
            yield pa.record_batch([pa.array(list(range(n)), pa.int64())], schema=self.schema)


def _local(tmp_path, engine_for):
    m = manager(tmp_path, local_engine=engine_for)
    status = wait(m, m.start("SELECT 1 AS n", target="local", project="p1", dialect="duckdb")["id"])
    return m, status


def test_empty_batches_are_skipped_in_a_local_result(tmp_path):
    m, status = _local(tmp_path, lambda p: Engine(batches=(0, 2, 0, 3)))
    assert status["state"] == "completed" and status["rows"] == 5
    assert m.page(status["id"], 0, 10)["rows"] == [[0], [1], [0], [1], [2]]


@pytest.mark.parametrize("fail, kind, says", [
    (EngineBusy("The project is busy with another run."), "engine", "busy with another run"),
    (RuntimeError("Binder Error: column n not found"), "sql", "Binder Error"),
    (RuntimeError(""), "sql", "RuntimeError"),
])
def test_a_local_run_that_fails_says_why(tmp_path, fail, kind, says):
    _m, status = _local(tmp_path, lambda p: Engine(fail=fail))
    assert status["state"] == "failed" and status["error_kind"] == kind and says in status["error"]


def test_a_project_that_goes_away_before_its_run_starts_fails_the_run(tmp_path):
    calls = []

    def engine_for(project):
        calls.append(project)
        if len(calls) > 1:  # found when the run was asked for, gone when it starts
            raise KeyError(project)
        return Engine()
    m = manager(tmp_path, local_engine=engine_for)
    m._limits = limits(sql_time_limit_minutes=0)  # no SQL page limit, so the project's own is looked up
    status = wait(m, m.start("SELECT 1 AS n", target="local", project="p1", dialect="duckdb")["id"])
    assert len(calls) == 3 and status["state"] == "failed" and status["error_kind"] == "sql"
    assert status["time_limit_minutes"] == 0 and status["time_limit_from"] == ""


def test_an_audit_line_that_cant_be_written_never_fails_the_run(tmp_path, capsys):
    def broken(*a):
        raise OSError("audit folder is read-only")
    m = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "t", audit=broken, limits=limits(),
                               local_engine=lambda p: Engine(batches=(1,)))
    status = wait(m, m.start("SELECT 1 AS n", target="local", project="p1", dialect="duckdb")["id"])
    assert status["state"] == "completed"
    assert "audit: could not record sql_execution: OSError" in capsys.readouterr().err


# ---- file names in Downloads


def test_no_free_name_in_downloads_is_said_plainly(tmp_path, monkeypatch):
    def taken(path, mode="r", *a, **k):
        raise FileExistsError(path)
    monkeypatch.setattr(sql_exec, "open", taken, raising=False)
    with pytest.raises(ValueError, match="Couldn't find a free file name"):
        sql_exec._reserve(tmp_path, "orders", ".csv")


def test_discarding_keeps_a_file_that_has_rows_and_ignores_one_already_gone(tmp_path):
    kept = tmp_path / "orders.csv"
    kept.write_text("id\n1\n")
    sql_exec._discard(tmp_path / "orders.csv.partial", kept)  # no partial file: nothing to remove
    assert kept.exists()
