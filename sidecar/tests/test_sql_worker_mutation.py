"""Mutation tests for one SQL page run (sql_worker.py): the batches and progress it reports, parameters in
each statement of a split script, the error it sends back and how a fresh worker process starts and ends.
Runs in this process on local Delta folders, and once as the process the engine starts. Found by
tools/mutate.py."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import types
from pathlib import Path

import pyarrow.ipc as ipc
import pytest

from fabric_desktop import lakehouse, sql_worker, warehouse
from tests.test_sql_worker_inprocess import fresh, job, said  # noqa: F401 - fresh is an autouse fixture


def _clock(*values):
    """A monotonic clock that gives ``values`` in turn, then the last one for good."""
    seq = list(values)
    return types.SimpleNamespace(monotonic=lambda: seq.pop(0) if len(seq) > 1 else seq[0], sleep=time.sleep)


def test_a_result_is_written_in_batches_of_65536_rows(tmp_path, capsys):
    sql_worker.run(job(tmp_path, "SELECT range AS n FROM range(65537)"))
    assert said(capsys)[-1]["batches"] == [65536, 1]
    assert ipc.open_file(str(tmp_path / "out.arrow")).num_record_batches == 2


def test_progress_is_said_once_the_interval_has_passed_and_not_before(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sql_worker, "time", _clock(5.0))
    monkeypatch.setattr(sql_worker, "PROGRESS_SECONDS", 0)
    sql_worker.run(job(tmp_path, "SELECT 1 AS n"))
    assert [e["event"] for e in said(capsys)] == ["started", "progress", "done"]
    monkeypatch.setattr(sql_worker, "PROGRESS_SECONDS", 6)
    sql_worker.run(job(tmp_path, "SELECT 1 AS n"))
    assert [e["event"] for e in said(capsys)] == ["started", "done"]


def test_a_run_uses_a_spill_folder_that_is_already_there_and_names_the_workspace(tmp_path, capsys):
    (tmp_path / "spill").mkdir()
    lh = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH", "schemas_enabled": False, "workspace_name": "Sales WS"}
    sql_worker.run(job(tmp_path, "SELECT count(*) AS n FROM sales", lakehouse=lh))
    assert said(capsys)[-1]["rows"] == 1
    assert lakehouse._attached.workspace_name == "Sales WS"


def test_each_statement_of_a_split_script_gets_its_parameters_and_says_its_seconds(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sql_worker, "time", _clock(100.0, 101.23456))
    sql_worker.run(job(tmp_path, "", allow=("read_only", "local_mutation"), parts_dir=str(tmp_path),
                       statements=["CREATE TEMP TABLE t AS SELECT :n AS v; SELECT v FROM t", "SELECT v + :n AS w FROM t"],
                       params=[{"name": "n", "type": "number", "value": "20"}]))
    events = said(capsys)
    parts = [e for e in events if e["event"] == "part"]
    assert [(p["statement"], p["seconds"]) for p in parts] == [(1, 1.23), (2, 0.0)]
    assert ipc.open_file(str(tmp_path / "result-1.arrow")).read_all().column("v").to_pylist() == [20]
    assert ipc.open_file(str(tmp_path / "result-2.arrow")).read_all().column("w").to_pylist() == [40]


def test_a_plan_lists_at_most_200_steps_and_counts_the_rest():
    node = {"name": "leaf"}
    for _ in range(200):
        node = {"name": "op", "children": [node]}
    plan = sql_worker.plan_steps([node])
    assert (len(plan["steps"]), plan["more_steps"]) == (200, 1)


def test_a_statement_event_without_a_number_means_none_is_running(capsys):
    sql_worker._say("statement", statement=None)
    assert sql_worker._statement_now == 0


def test_the_engine_is_given_ten_minutes_to_answer(monkeypatch):
    waited = []

    class Replies:
        def get(self, timeout):
            waited.append(timeout)
            return {"value": "tok"}

    monkeypatch.setattr(sql_worker, "_replies", Replies())
    assert sql_worker._storage_token() == "tok"
    assert waited == [600]


def test_a_warehouse_run_that_ended_part_way_says_how_by_name():
    exc = warehouse.Interrupted("throttled", "Fabric was busy; batch 2 was kept.", batch=3)
    assert sql_worker.error_message(exc, {"target": "warehouse"}) == ("throttled", "Fabric was busy; batch 2 was kept.")
    assert sql_worker.error_message(RuntimeError("configuration has been locked"), {"target": "warehouse"})[0] == "sql"


@pytest.fixture
def main_with(monkeypatch, capsys):
    """main() with its stdin, its listening thread and its exit replaced: the events it said, its exit
    code and the thread it started."""
    def go(job_, fails, statement_now=0):
        threads, codes = [], []

        class Thread:
            def __init__(self, **kw):
                threads.append(kw)

            def start(self):
                pass

        def exit_(code):
            codes.append(code)
            raise SystemExit(code)

        monkeypatch.setattr(sql_worker, "_statement_now", statement_now)
        monkeypatch.setattr(sql_worker, "_take_stdin", lambda: io.StringIO(json.dumps(job_) + "\n"))
        monkeypatch.setattr(sql_worker, "threading", types.SimpleNamespace(Thread=Thread))
        monkeypatch.setattr(sql_worker.os, "_exit", exit_)
        monkeypatch.setattr(sql_worker, "run", fails)
        with pytest.raises(SystemExit):
            sql_worker.main()
        return said(capsys), codes, threads
    return go


def _raise(exc):
    def run(job_):
        raise exc
    return run


def test_a_failed_run_says_why_in_at_most_4000_characters_and_ends_cleanly(main_with):
    events, codes, threads = main_with({"query": "x"}, _raise(RuntimeError("y" * 5000)), statement_now=4)
    assert events == [{"event": "error", "kind": "sql", "message": "y" * 4000}]
    assert codes == [0]
    assert [(t["daemon"], t["name"]) for t in threads] == [(True, "engine-listen")]


def test_only_a_warehouse_run_of_one_query_names_the_batch_that_failed(main_with):
    exc = warehouse.Interrupted("timeout", "Timed out.", batch=3)
    assert main_with({"target": "warehouse"}, _raise(exc))[0] == [{"event": "error", "kind": "timeout", "message": "Timed out.", "batch": 3}]
    other = RuntimeError("bad")
    other.batch = 3
    assert "batch" not in main_with({"query": "x"}, _raise(other))[0][0]
    assert "batch" not in main_with({"target": "warehouse", "statements": ["x"]}, _raise(exc))[0][0]


def test_a_split_script_names_the_statement_that_failed(main_with):
    def fails(job_):
        sql_worker._say("statement", statement=2)
        raise ValueError("no such table")

    events = main_with({"statements": ["a", "b"]}, fails)[0]
    assert events[-1] == {"event": "error", "kind": "sql", "message": "no such table", "statement": 2}


def test_a_fresh_worker_that_fails_before_any_statement_names_none_and_exits_0():
    folder = Path(sql_worker.__file__).resolve().parents[1]
    line = json.dumps({"statements": ["SELECT 1"], "dialect": "duckdb", "allow": ["read_only"]}) + "\n"
    # stdin stays open, as the engine keeps it: closing it ends the run (_listen)
    proc = subprocess.Popen([sys.executable, "-m", "fabric_desktop.sql_worker"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=folder)
    try:
        proc.stdin.write(line)
        proc.stdin.flush()
        out = proc.stdout.read()
        assert proc.wait(timeout=60) == 0
    finally:
        proc.kill()
        proc.stdin.close()
        proc.stdout.close()
    assert [json.loads(x) for x in out.splitlines()] == [{"event": "error", "kind": "sql", "message": "'lakehouse'"}]
