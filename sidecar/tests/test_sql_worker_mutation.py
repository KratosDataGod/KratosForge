"""Mutation batch 264: sql_worker.py, first score (docs/quality/mutation-testing.md)."""

import json
import subprocess
import sys
from pathlib import Path

import duckdb

from fabric_desktop import duckdb_rows, sql_worker, warehouse
from tests.test_sql_worker_inprocess import fresh, job, said  # noqa: F401  (fresh is an autouse fixture)


def test_a_result_is_written_in_batches_of_at_most_65536_rows(tmp_path):
    result = duckdb_rows.for_arrow(duckdb.connect(), "SELECT * FROM range(140000)")  # as a run makes it
    rows, batches, _ = sql_worker._snapshot(result, tmp_path / "out.arrow")
    assert rows == sum(batches) == 140000 and max(batches) <= 65536  # DuckDB 1.5 fills each batch; 2.0 may not


def test_a_spill_folder_is_made_with_its_parents(tmp_path, capsys):
    spill = tmp_path / "a" / "b" / "spill"
    sql_worker.run(job(tmp_path, "SELECT 1 AS n", temp_dir=str(spill)))
    assert said(capsys)[-1]["event"] == "done" and spill.is_dir()


def test_a_plan_lists_two_hundred_steps_and_counts_the_rest():
    node = {"name": "LEAF"}
    for _ in range(200):
        node = {"name": "STEP", "children": [node]}
    plan = sql_worker.plan_steps([node])
    assert len(plan["steps"]) == 200 and plan["more_steps"] == 1


def test_a_warehouse_stop_keeps_its_own_kind_only_for_a_warehouse_run():
    stop = warehouse.Interrupted("throttled", "Fabric was busy; 2 batches were kept.", batch=3)
    assert sql_worker.error_message(stop, {"target": "warehouse"}) == ("throttled", "Fabric was busy; 2 batches were kept.")
    assert sql_worker.error_message(stop, {}) == ("sql", "Fabric was busy; 2 batches were kept.")


def test_running_out_of_memory_says_to_narrow_the_query_or_raise_the_share():
    kind, message = sql_worker.error_message(RuntimeError("Out of Memory Error: failed to allocate"), {})
    assert kind == "memory" and message.startswith("Stopped: the query needed more memory than the share")


def test_a_failure_before_any_statement_names_no_statement():
    worker = subprocess.Popen([sys.executable, "-m", "fabric_desktop.sql_worker"], cwd=Path(__file__).parents[1],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        worker.stdin.write(json.dumps({"statements": ["SELECT 1"], "dialect": "duckdb", "allow": ["read_only"]}) + "\n")
        worker.stdin.flush()
        events = [json.loads(line) for line in worker.stdout.read().splitlines()]
    finally:
        worker.stdin.close()
        worker.wait(60)
    assert events == [{"event": "error", "kind": "sql", "message": "'lakehouse'"}]


def test_the_lakehouse_keeps_its_workspace_name(tmp_path, capsys):
    from fabric_desktop import lakehouse
    run = job(tmp_path, "SELECT 1 AS n")
    run["lakehouse"]["workspace_name"] = "Sales"
    sql_worker.run(run)
    assert lakehouse._attached.workspace_name == "Sales"


def test_each_statement_of_a_split_script_gets_its_own_values(tmp_path, capsys):
    sql_worker.run(job(tmp_path, "", allow=("read_only", "local_mutation"),
                       statements=["CREATE TEMP TABLE t AS SELECT 1 + :n AS v; SELECT v + 1 AS w FROM t", "SELECT 1 + :n AS x"],
                       params=[{"name": "n", "type": "number", "value": "41"}], parts_dir=str(tmp_path)))
    events = said(capsys)
    parts = [e for e in events if e["event"] == "part"]
    assert [p["rows"] for p in parts] == [1, 1] and events[-1]["event"] == "done"
    assert all(0 <= p["seconds"] < 60 and p["seconds"] == round(p["seconds"], 2) for p in parts)


def test_progress_is_said_no_more_often_than_its_interval(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sql_worker, "PROGRESS_SECONDS", 3600)
    sql_worker.run(job(tmp_path, "SELECT * FROM range(200000)"))
    events = said(capsys)
    assert events[-1]["event"] == "done" and len(events[-1]["batches"]) > 1
    assert not [e for e in events if e["event"] == "progress"]


def _main(monkeypatch, capsys, run_job, fail):
    import io
    import os
    monkeypatch.setattr(sql_worker, "_take_stdin", lambda: io.StringIO(json.dumps(run_job) + "\n"))
    monkeypatch.setattr(sql_worker, "_end_now", lambda code: None)
    monkeypatch.setattr(sql_worker, "run", fail)

    def exit_(code):
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", exit_)
    try:
        sql_worker.main()
    except SystemExit:
        pass
    return [e for e in said(capsys) if e["event"] == "error"][-1]


def test_a_warehouse_run_that_stops_names_its_batch(monkeypatch, capsys):
    def fail(job):
        raise warehouse.Interrupted("timeout", "Stopped at batch 3.", batch=3)

    error = _main(monkeypatch, capsys, {"target": "warehouse"}, fail)
    assert error == {"event": "error", "kind": "timeout", "message": "Stopped at batch 3.", "batch": 3}
    error = _main(monkeypatch, capsys, {"target": "warehouse", "statements": ["SELECT 1"]}, fail)
    assert "batch" not in error


def test_a_failed_statement_of_a_script_is_named_and_a_long_message_is_cut(monkeypatch, capsys):
    def fail(job):
        sql_worker._say("statement", statement=2)
        raise RuntimeError("x" * 5000)

    error = _main(monkeypatch, capsys, {"statements": ["SELECT 1", "SELECT 2"]}, fail)
    assert error["statement"] == 2 and error["message"] == "x" * 4000
    error = _main(monkeypatch, capsys, {"query": "SELECT 2"}, fail)
    assert "statement" not in error


def test_a_spill_folder_already_there_is_used(tmp_path, capsys):
    spill = tmp_path / "spill"
    spill.mkdir()
    sql_worker.run(job(tmp_path, "SELECT 1 AS n", temp_dir=str(spill)))
    assert said(capsys)[-1]["event"] == "done"
