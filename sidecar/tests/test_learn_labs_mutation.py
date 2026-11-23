"""learn/labs.py's limits and defaults, read directly (mutation testing, batch 340).

test_learn_labs.py runs every module's checks; these pin the seed a lab starts with, a lab folder that is
already there, the row and hint limits at their edges, the time a run reports, and what _run gives for a
table check whose SQL fails, a practices check and a speed check's defaults.
"""

from __future__ import annotations

import threading

import duckdb
import pytest

from fabric_desktop.learn import labs

MODULE = "LM-01"


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    root = tmp_path_factory.mktemp("labs")
    labs.start(root, MODULE)
    return root


def test_a_lab_starts_and_resets_with_seed_seven(tmp_path):
    assert labs.start(tmp_path, MODULE)["seed"] == 7
    assert labs.reset(tmp_path, MODULE)["seed"] == 7


def test_a_lab_folder_already_there_is_used(tmp_path):
    (labs.lab_dir(tmp_path, MODULE) / "answers").mkdir(parents=True)
    assert labs.start(tmp_path, MODULE)["started"] is True


def test_a_run_gives_at_most_a_thousand_rows_and_at_least_one(lab):
    many = labs.run(lab, MODULE, "SELECT * FROM range(1500)", limit=5000)
    assert len(many["rows"]) == 1000 and many["more"] is True
    one = labs.run(lab, MODULE, "SELECT * FROM range(3)", limit=0)
    assert len(one["rows"]) == 1 and one["more"] is True
    exact = labs.run(lab, MODULE, "SELECT * FROM range(2)", limit=2)
    assert len(exact["rows"]) == 2 and exact["more"] is False


def test_a_run_reports_its_time_in_milliseconds(lab, monkeypatch):
    ticks = iter([10.0, 12.0])
    monkeypatch.setattr(labs.time, "perf_counter", lambda: next(ticks))
    assert labs.run(lab, MODULE, "SELECT 1")["ms"] == 2000


def test_hint_zero_is_the_first_hint():
    ex = labs.content.get(MODULE)["exercises"][0]
    first = labs.hint(MODULE, ex["id"], 0)
    assert first["level"] == 1 and first["text"] == ex["hints"][0]


def test_the_time_limit_timer_never_keeps_the_process_alive():
    con = duckdb.connect()
    try:
        with labs._time_limit(con):
            timers = [t for t in threading.enumerate() if isinstance(t, threading.Timer)]
            assert timers and all(t.daemon for t in timers)
    finally:
        con.close()


def test_a_table_check_whose_sql_fails_does_not_pass():
    con = duckdb.connect()
    try:
        out = labs._run(con, {"kind": "table_shape", "table": "t"}, "CREATE TABLE t AS SELECT * FROM nope", "", {},
                        MODULE, [])
    finally:
        con.close()
    assert out["passed"] is False and out["detail"].startswith("Your SQL didn't run: ")


def test_a_practices_check_looks_for_the_rules_it_names():
    con = duckdb.connect()
    try:
        out = labs._run(con, {"kind": "no_findings", "rules": ["SQ005"]}, "SELECT * FROM t", "", {}, MODULE, [])
    finally:
        con.close()
    assert out["passed"] is False and "SQ005" in out["detail"]


def test_a_speed_check_runs_three_times_for_twice_as_fast_by_default(monkeypatch):
    seen = {}

    def faster_than(con, answer, baseline, factor, runs):
        seen.update(factor=factor, runs=runs, baseline=baseline)
        return {"kind": "faster_than", "passed": True, "detail": ""}

    monkeypatch.setattr(labs.checks, "faster_than", faster_than)
    monkeypatch.setattr(labs.content, "file_text", lambda module_id, name: f"-- {name}")
    labs._run(None, {"kind": "faster_than", "baseline": "slow.sql"}, "SELECT 1", "", {}, MODULE, [])
    assert seen == {"factor": 2.0, "runs": 3, "baseline": "-- slow.sql"}
