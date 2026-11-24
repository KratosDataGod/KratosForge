"""Run time limits count only the time the PC is awake (awake_timer.AwakeTimer).

On Windows the monotonic clock keeps counting while the PC sleeps, and threading.Timer waits on it,
so a notebook cell, a pipeline run, a learning lab query or a git clone that was making progress
when the lid closed was stopped the moment the PC woke. Here time.monotonic jumps forward by hours
mid-run, with timed waits counted on it as on Windows (the sleep_jump fixture, tests/conftest.py):
each run carries on. Each test fails with threading.Timer put back.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from fabric_desktop import notebook
from fabric_desktop.git import cli, ops
from fabric_desktop.learn import labs
from fabric_desktop.orchestration import loads
from fabric_desktop.orchestration.runner import PipelineRun

HOURS = 3 * 3600.0


def pause(seconds: float) -> None:
    """Wait real time, on a clock the jump doesn't move."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        time.sleep(0.02)


def until(done, seconds: float = 20.0) -> None:
    end = time.perf_counter() + seconds
    while not done():
        assert time.perf_counter() < end, "timed out"
        time.sleep(0.02)


def test_a_cell_going_when_the_pc_slept_isnt_stopped_on_waking(sleep_jump):
    with notebook._deadline(2, "Stopped: the cell's time limit") as fired:
        pause(0.5)
        sleep_jump(HOURS)
        pause(1.0)
        assert not fired.is_set()
    # The limit still stops a cell that really runs past it.
    with pytest.raises(notebook.RunTimeout, match="time limit"):
        with notebook._deadline(0.3, "Stopped: the cell's time limit"):
            pause(5)


def test_a_pipeline_run_going_when_the_pc_slept_isnt_stopped_on_waking(sleep_jump):
    text = ('{"name": "p", "properties": {"activities": [{"name": "Long", "type": "Wait", "dependsOn": [],'
            ' "typeProperties": {"waitTimeInSeconds": 2}}]}}')
    r = PipelineRun(loads(text), wait_scale=1, timeout_seconds=3)
    t = threading.Thread(target=r.run, daemon=True)
    t.start()
    until(lambda: r.state == "running")
    pause(0.3)
    sleep_jump(HOURS)
    t.join(20)
    assert not t.is_alive()
    assert r.stop_reason != "timed_out" and r.state == "succeeded", (r.state, r.stop_reason)


def test_a_pipeline_run_past_its_limit_is_still_stopped():
    text = ('{"name": "p", "properties": {"activities": [{"name": "Long", "type": "Wait", "dependsOn": [],'
            ' "typeProperties": {"waitTimeInSeconds": 30}}]}}')
    r = PipelineRun(loads(text), wait_scale=1, timeout_seconds=0.3).run()
    assert r.state == "failed" and r.stop_reason == "timed_out"


class Con:
    interrupted = 0

    def interrupt(self) -> None:
        self.interrupted += 1


def test_a_lab_query_going_when_the_pc_slept_isnt_stopped_on_waking(sleep_jump, monkeypatch):
    monkeypatch.setattr(labs, "TIME_LIMIT", 2)
    con = Con()
    with labs._time_limit(con) as limit:  # type: ignore[arg-type]
        pause(0.5)
        sleep_jump(HOURS)
        pause(1.0)
        assert not limit.fired and con.interrupted == 0
    monkeypatch.setattr(labs, "TIME_LIMIT", 0.3)
    with labs._time_limit(con) as limit:  # type: ignore[arg-type]
        until(lambda: limit.fired)
    assert con.interrupted == 1


def test_a_git_clone_going_when_the_pc_slept_isnt_stopped_on_waking(tmp_path, sleep_jump, monkeypatch):
    monkeypatch.setattr(cli, "command", lambda cwd, args: [sys.executable, "-c", "import time; time.sleep(2.5)"])
    monkeypatch.setattr(cli, "NETWORK_TIMEOUT", 60)
    o = ops.Operations()
    op_id = o._start("fetch", None, ["fetch"], cwd=tmp_path)["id"]
    until(lambda: op_id in o._procs)
    pause(0.5)
    sleep_jump(HOURS)
    until(lambda: o.status(op_id)["status"] != "running")
    op = o.status(op_id)
    assert op["status"] == "ok", op
