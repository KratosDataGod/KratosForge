"""One SQL page run (sql_worker.py), called in this process: a script split into statements, each a part of
its own; parameters bound by name; a plan or a profile in place of the result; and the plan's steps. Nothing
reaches OneLake: the queries read no lakehouse table."""

from __future__ import annotations

import json
import queue

import pyarrow.ipc as ipc
import pytest

from fabric_desktop import lakehouse, sql_worker


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(lakehouse, "_attached", lakehouse._attached)
    monkeypatch.setattr(lakehouse, "open_table", lakehouse.open_table)
    monkeypatch.setattr(sql_worker, "_replies", queue.Queue())
    monkeypatch.setattr(sql_worker, "_asked", {})
    monkeypatch.setattr(sql_worker, "_statement_now", 0)


def said(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def job(tmp_path, query="", **extra):
    return {"query": query, "dialect": "spark", "allow": ["read_only"],
            "lakehouse": {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH", "schemas_enabled": False},
            "work_dir": str(tmp_path / "work"), "out": str(tmp_path / "out.arrow"),
            "temp_dir": str(tmp_path / "spill"), "parts_dir": str(tmp_path)} | extra


def read(path):
    return ipc.open_file(str(path)).read_all()


def test_each_statement_of_a_split_script_is_a_part_of_its_own(tmp_path, capsys):
    sql_worker.run(job(tmp_path, statements=["SELECT 1 AS a", "SELECT 2 AS skipped; SELECT 3 AS b UNION ALL SELECT 4"]))
    events = said(capsys)
    parts = [e for e in events if e["event"] == "part"]
    assert [(p["statement"], p["rows"], [c["name"] for c in p["columns"]]) for p in parts] == [(1, 1, ["a"]), (2, 2, ["b"])]
    assert read(tmp_path / "result-1.arrow").column("a").to_pylist() == [1]
    assert sorted(read(tmp_path / "result-2.arrow").column("b").to_pylist()) == [3, 4]
    assert events[-1]["event"] == "done" and events[-1]["rows"] == 2 and events[-1]["columns"][0]["name"] == "b"
    assert sql_worker._statement_now == 2  # the statement running now, named if it fails


def test_a_refused_statement_in_a_split_script_is_named_by_its_own_number(tmp_path, capsys):
    sql_worker.run(job(tmp_path, statements=["SELECT 1 AS a", "CREATE TEMP VIEW v AS SELECT 1"]))
    error = said(capsys)[-1]
    assert error["event"] == "error" and error["kind"] == "permission" and error["statement"] == 2
    assert error["message"].startswith("Statement 2 ") and not (tmp_path / "result-1.arrow").exists()


def test_a_statement_that_cannot_be_read_is_named_in_the_error(tmp_path, capsys):
    with pytest.raises(ValueError, match=r"^Statement 2: Could not read the query"):
        sql_worker.run(job(tmp_path, statements=["SELECT 1", "SELEC FROM WHERE ((("]))
    assert [e for e in said(capsys) if e["event"] == "statement"][-1] == {"event": "statement", "statement": 2}


def test_parameters_are_bound_by_name_to_the_statement_that_uses_them(tmp_path, capsys):
    sql_worker.run(job(tmp_path, "SELECT :n + 1 AS v, :who AS who",
                       params=[{"name": "n", "type": "number", "value": "41"},
                               {"name": "who", "type": "text", "value": "x' OR '1'='1"}]))
    assert said(capsys)[-1]["rows"] == 1
    table = read(tmp_path / "out.arrow")
    assert table.column("v").to_pylist() == [42] and table.column("who").to_pylist() == ["x' OR '1'='1"]


def test_a_parameter_with_no_value_stops_the_run_naming_it(tmp_path, capsys):
    with pytest.raises(ValueError, match=r"Give a value for :region before running the query\."):
        sql_worker.run(job(tmp_path, "SELECT :region AS r", params=[]))
    assert [e["event"] for e in said(capsys)] == ["started"]


def test_a_plan_lists_the_steps_and_runs_nothing(tmp_path, capsys):
    sql_worker.run(job(tmp_path, "SELECT 1 AS a", explain="plan"))
    assert said(capsys)[-1] == {"event": "done", "rows": 1, "batches": [1], "columns": [{"name": "plan", "type": "string"}]}
    plan = json.loads(read(tmp_path / "out.arrow").column("plan")[0].as_py())
    assert plan["more_steps"] == 0 and plan["steps"] and plan["steps"][0]["depth"] == 0
    assert all(set(s) == {"depth", "name", "rows", "details"} for s in plan["steps"])


def test_a_profile_runs_the_query_and_writes_its_profile_not_to_stdout(tmp_path, capsys):
    sql_worker.run(job(tmp_path, "SELECT 1 AS a", explain="profile"))
    events = said(capsys)
    assert [e["event"] for e in events] == ["started", "done"] and events[-1]["columns"][0]["name"] == "profile"
    profile = json.loads(read(tmp_path / "out.arrow").column("profile")[0].as_py())
    assert isinstance(profile, dict) and "steps" in profile


@pytest.mark.parametrize("query", ["SELECT 1; SELECT 2", "CREATE TEMP VIEW v AS SELECT 1"])
def test_a_plan_is_for_one_query_that_reads(tmp_path, capsys, query):
    sql_worker.run(job(tmp_path, query, explain="plan", allow=["read_only", "local_mutation"]))
    [error] = said(capsys)
    assert error["event"] == "error" and error["kind"] == "plan" and "one query that reads" in error["message"]
    assert not (tmp_path / "out.arrow").exists()


def test_explain_query_with_no_plan_rows_gives_no_steps():
    class Con:
        def execute(self, text):
            assert text == "EXPLAIN (FORMAT JSON) SELECT 1"
            return self

        def fetchall(self):
            return []

    assert sql_worker.explain_query(Con(), "SELECT 1", "plan") == {"steps": [], "more_steps": 0}


def test_plan_steps_reads_estimates_and_details_and_counts_what_it_left_out(monkeypatch):
    monkeypatch.setattr(sql_worker, "PLAN_NODES", 2)
    plan = sql_worker.plan_steps([{
        "name": "PROJECTION", "extra_info": {"Estimated Cardinality": "7", "Projections": ["a", "b"], "Empty": ""},
        "children": [{"name": "FILTER", "extra_info": {"Estimated Cardinality": "~3", "Expression": "a > 1"},
                      "children": [{"name": "SEQ_SCAN"}]}]}])
    assert plan == {"steps": [
        {"depth": 0, "name": "PROJECTION", "rows": 7, "details": {"Projections": "a, b"}},
        {"depth": 1, "name": "FILTER", "rows": None, "details": {"Expression": "a > 1"}},
    ], "more_steps": 1}
