"""One SQL page run (sql_worker.py), called in this process so each path is seen: test_sql_exec.py runs it
as the separate process the engine starts, which coverage can't follow. A refused statement, a run over
local Delta folders that writes its Arrow snapshot, the questions it asks the engine, and how the process
ends. Nothing reaches OneLake: the lakehouse is local Delta folders."""

from __future__ import annotations

import io
import json
import queue

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest
from deltalake import write_deltalake

from fabric_desktop import lakehouse, sql_worker


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """The worker's module state, and the lakehouse it attaches, as they were."""
    monkeypatch.setattr(lakehouse, "_attached", lakehouse._attached)
    monkeypatch.setattr(lakehouse, "open_table", lakehouse.open_table)
    monkeypatch.setattr(sql_worker, "_replies", queue.Queue())
    monkeypatch.setattr(sql_worker, "_asked", {})


def said(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def job(tmp_path, query, allow=("read_only",), **extra):
    root = tmp_path / "lake"
    if not (root / "sales").exists():
        write_deltalake(str(root / "sales"), pa.table({"id": [1, 2, 3], "amount": [1.5, 2.5, 3.5]}))
    return {"query": query, "dialect": "duckdb", "allow": list(allow),
            "lakehouse": {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH", "schemas_enabled": False},
            "work_dir": str(tmp_path / "work"), "out": str(tmp_path / "out.arrow"), "temp_dir": str(tmp_path / "spill"),
            "local_root": str(root)} | extra


def test_a_run_writes_its_result_as_an_arrow_snapshot_and_says_what_it_wrote(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sql_worker, "PROGRESS_SECONDS", 0)
    sql_worker.run(job(tmp_path, "SELECT id, amount * 2 AS twice FROM sales ORDER BY id", memory_mb=256, threads=2))
    events = said(capsys)
    assert events[0] == {"event": "started", "permission": "read_only"}
    assert events[-1]["event"] == "done" and events[-1]["rows"] == 3 and events[-1]["batches"] == [3]
    assert [c["name"] for c in events[-1]["columns"]] == ["id", "twice"]
    assert any(e["event"] == "progress" for e in events)
    table = ipc.open_file(str(tmp_path / "out.arrow")).read_all()
    assert table.column("twice").to_pylist() == [3.0, 5.0, 7.0]
    assert (tmp_path / "spill").is_dir()


def test_statements_before_the_last_run_and_only_the_last_one_is_the_result(tmp_path, capsys):
    sql_worker.run(job(tmp_path, "CREATE TEMP TABLE t AS SELECT 1 AS n; SELECT count(*) AS n FROM sales", allow=("read_only", "local_mutation")))
    done = said(capsys)[-1]
    assert done["event"] == "done" and done["rows"] == 1


def test_a_statement_with_no_result_writes_an_empty_snapshot(tmp_path, capsys, monkeypatch):
    from fabric_desktop import duckdb_rows
    monkeypatch.setattr(duckdb_rows, "for_arrow", lambda con, text: None)  # a statement DuckDB gives no result for
    sql_worker.run(job(tmp_path, "SELECT 1", allow=("read_only",)))
    done = said(capsys)[-1]
    assert done == {"event": "done", "rows": 0, "batches": [], "columns": []}


@pytest.mark.parametrize("query, kind, advice", [
    ("INSERT INTO sales SELECT 4, 4.5", "onelake_write", "write a result with Save as table"),
    ("CREATE TEMP TABLE t AS SELECT 1 AS n", "local_mutation", "confirm to let it change this run's own session"),
    ("COPY (SELECT 1) TO 'x.csv'", "fabric_mutation", "never changes Fabric, files or extensions"),
])
def test_a_statement_beyond_what_the_run_allows_is_refused_before_anything_runs(tmp_path, capsys, query, kind, advice):
    sql_worker.run(job(tmp_path, query))
    [error] = said(capsys)
    assert error["event"] == "error" and error["kind"] == "permission" and error["permission"] == kind
    assert advice in error["message"] and not (tmp_path / "out.arrow").exists()


def test_a_warehouse_run_goes_to_the_warehouse_with_a_token_asked_for_once(monkeypatch, capsys):
    from fabric_desktop import warehouse
    seen = []

    def run(job, token, say):
        seen.append((token(), token()))
        say("done", rows=0)
    monkeypatch.setattr(warehouse, "run", run)
    sql_worker._replies.put({"value": "AZ-SQL-TOKEN"})
    sql_worker.run({"target": "warehouse"})
    assert seen == [("AZ-SQL-TOKEN", "AZ-SQL-TOKEN")]
    events = said(capsys)
    assert events == [{"event": "call", "name": "warehouse_token"}, {"event": "done", "rows": 0}]
    assert "AZ-SQL-TOKEN" not in json.dumps(events)  # the token is never printed


def test_a_question_the_engine_answers_with_an_error_or_never_answers_stops_the_run(monkeypatch, capsys):
    sql_worker._replies.put({"error": "Sign-in was cancelled"})
    with pytest.raises(RuntimeError, match="Sign-in was cancelled"):
        sql_worker._storage_token()

    class Silent:
        def get(self, timeout):
            raise queue.Empty
    monkeypatch.setattr(sql_worker, "_replies", Silent())
    with pytest.raises(RuntimeError, match="Signing in took too long"):
        sql_worker._ask("schemas_enabled")


def test_the_engine_closing_its_pipe_ends_the_run(monkeypatch):
    ended = []
    monkeypatch.setattr(sql_worker, "_engine", io.StringIO('{"value": "tok"}\nnot json\n'))
    monkeypatch.setattr(sql_worker, "_end_now", ended.append)
    sql_worker._listen()
    assert sql_worker._replies.get_nowait() == {"value": "tok"} and sql_worker._replies.empty()
    assert ended == [3]


def test_ending_now_on_windows_terminates_without_the_shutdown_code(monkeypatch):
    import ctypes
    calls = []

    class Kernel32:
        class GetCurrentProcess:
            restype = None

            def __new__(cls):
                return "me"

        class TerminateProcess:
            argtypes = None

            def __new__(cls, handle, code):
                calls.append((handle, code))

    monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": Kernel32})(), raising=False)
    monkeypatch.setattr(sql_worker.sys, "platform", "win32")
    monkeypatch.setattr(sql_worker.os, "_exit", lambda code: calls.append(("exit", code)))
    sql_worker._end_now(3)
    assert calls == [("me", 3), ("exit", 3)]


class Exited(Exception):
    pass


@pytest.fixture
def main_with(monkeypatch):
    def start(text):
        monkeypatch.setattr(sql_worker, "_take_stdin", lambda: io.StringIO(text))

        def exit_(code):
            raise Exited(code)
        monkeypatch.setattr(sql_worker.os, "_exit", exit_)
        monkeypatch.setattr(sql_worker, "_listen", lambda: None)
    return start


def test_a_run_with_no_instructions_says_so(main_with, capsys):
    main_with("not json\n")
    sql_worker.main()
    assert said(capsys) == [{"event": "error", "kind": "engine", "message": "The run got no instructions."}]


def test_a_query_that_tries_to_change_the_runs_settings_is_told_where_they_come_from(main_with, capsys, monkeypatch):
    def locked(job):
        raise RuntimeError("Invalid Input Error: Cannot change configuration option - the configuration has been locked")
    monkeypatch.setattr(sql_worker, "run", locked)
    main_with(json.dumps({"query": "SET threads = 64"}) + "\n")
    with pytest.raises(Exited):
        sql_worker.main()
    [error] = said(capsys)
    assert error["kind"] == "sql" and "They come from Settings › SQL" in error["message"]


def test_any_other_failure_goes_back_to_the_engine_as_a_message(main_with, capsys, monkeypatch):
    def boom(job):
        raise KeyboardInterrupt
    monkeypatch.setattr(sql_worker, "run", boom)
    main_with("{}\n")
    with pytest.raises(Exited):
        sql_worker.main()
    assert said(capsys) == [{"event": "error", "kind": "sql", "message": "KeyboardInterrupt"}]
