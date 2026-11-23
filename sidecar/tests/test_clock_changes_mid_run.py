"""A change to the PC's clock in the middle of a run doesn't change how long the run took.

DST doesn't move ``time.time`` (it counts UTC seconds), but setting the clock by hand or a time sync
does, by minutes or hours. A run's seconds, as the SQL page, the audit line, a background local query and
a proof's log show them, come from the monotonic clock; the wall-clock times shown beside them stay wall
clock. (An audit line written after the clock went back stays in trail order: test_clock_skew.py.)
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from fabric_desktop import proof_runtime, sql_exec
from fabric_desktop.localdata import manager


class _Wall:
    """time.time, moved by hand; the monotonic clock is left alone."""

    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def wall(monkeypatch):
    clock = _Wall()
    monkeypatch.setattr(time, "time", clock)
    return clock


def _run(tmp_path: Path) -> sql_exec.Execution:
    return sql_exec.Execution(id="r1", query="select 1", dialect="duckdb", lakehouse={}, allow=(), folder=tmp_path)


@pytest.mark.parametrize("jump", [-3600.0, +7200.0])
def test_a_sql_runs_seconds_dont_move_with_the_clock(tmp_path, wall, jump):
    run = _run(tmp_path)
    run.started = time.time()
    wall.now += jump                    # the clock set back an hour, or on two hours, mid-run
    assert 0.0 <= run.status()["seconds"] < 5
    run.ended = time.time()
    assert 0.0 <= run.seconds() < 5
    assert run.status()["ended"] - run.status()["started"] == jump   # the shown times are still the clock's


def test_a_run_that_never_started_counts_from_when_it_was_made(tmp_path, wall):
    run = _run(tmp_path)
    wall.now -= 86400
    run.ended = time.time()             # cancelled before it started
    assert 0.0 <= run.seconds() < 5
    assert run.status()["seconds"] == 0.0


def test_the_audit_line_for_a_run_gets_its_monotonic_seconds(tmp_path, wall):
    lines = []
    runs = sql_exec.SqlExecutions.__new__(sql_exec.SqlExecutions)
    runs._audit = lambda action, outcome, seconds, params: lines.append(seconds)
    runs.cleanup = lambda earlier_sessions=False: None
    run = _run(tmp_path)
    run.state, run.started = "completed", time.time()
    wall.now -= 3600
    run.ended = time.time()
    runs._finished(run)
    assert len(lines) == 1 and 0.0 <= lines[0] < 5


def test_a_background_local_querys_elapsed_doesnt_move_with_the_clock(tmp_path, wall, monkeypatch):
    class Engine:
        def execute(self, sql, **options):
            wall.now -= 3600            # the clock goes back while the query runs
            return {"rows": []}

    data = manager.LocalData(tmp_path)
    monkeypatch.setattr(data, "engine", lambda pid: Engine())
    monkeypatch.setattr(data, "_sweep", lambda: None)
    qid = data.start("p1", "select 1")["query_id"]
    for _ in range(100):
        status = data.status(qid)
        if status["state"] != "running":
            break
        time.sleep(0.01)
    assert status["state"] == "done"
    assert 0.0 <= status["elapsed"] < 5
    assert "_clock" not in status and "started_at" not in status


def test_a_proofs_seconds_dont_move_with_the_clock(wall):
    rt = proof_runtime.ProofRuntime({"id": "p1"}, "pc")
    wall.now += 7200
    log = rt.finish(modules=[])
    assert 0.0 <= log["seconds"] < 5
