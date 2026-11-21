"""The SQL page's time limit counts only the time the PC is awake.

On Windows the monotonic clock keeps counting while the PC sleeps, so a run with a time limit that
was making progress when the lid closed was stopped the moment the PC woke, for hours it never ran.
Here time.monotonic jumps forward by hours mid-run, as it does on waking: the run carries on, its
shown seconds still say the time that went by, and the limit still stops it once it has really run
that long.
"""

from __future__ import annotations

import threading
import time

import pyarrow as pa
from deltalake import write_deltalake

from fabric_desktop import awake_timer, sql_exec
from fabric_desktop.awake_timer import AwakeTimer

HOURS = 3 * 3600.0
SLOW = "SELECT sum(a.range * b.range) FROM range(200000000) a, range(1000) b"
LOCAL_SLOW = "SELECT sum(hash(a.range)) FROM range(10000000000) a"


def limits(**changes):
    base = {"sql_page_rows": 1000, "sql_time_limit_minutes": 10, "sql_memory_limit_percent": 30,
            "sql_max_concurrent": 2, "sql_result_retention_hours": 24, "sql_temp_dir": ""}
    base.update(changes)
    return lambda: base


def pause(seconds: float) -> None:
    """Wait real time, on a clock the jump doesn't move (sleep_jump: tests/conftest.py)."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        time.sleep(0.02)


def wait(manager, run_id, states=sql_exec.FINISHED, seconds=60.0):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        status = manager.status(run_id)
        if status["state"] in states:
            return status
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still {manager.status(run_id)['state']}")


# ---- the timer

def test_a_step_far_longer_than_asked_is_the_pc_asleep_and_isnt_counted():
    t = AwakeTimer(60, lambda: None)
    assert t.awake_after(100.0, 101.0, 5.0) == 6.0  # an ordinary step
    assert t.awake_after(100.0, 100.0 + awake_timer.STEP + awake_timer.SLEEP_GAP, 5.0) == 5.0 + 6.0
    assert t.awake_after(100.0, 100.0 + HOURS, 5.0) == 5.0  # asleep: not counted
    assert t.awake_after(100.0, 99.0, 5.0) == 5.0  # a clock going back isn't counted either


def test_a_jump_of_hours_mid_wait_doesnt_fire_it_and_awake_time_still_does(monkeypatch):
    monkeypatch.setattr(awake_timer, "STEP", 0.05)
    now = [0.0]
    fired = threading.Event()
    t = AwakeTimer(0.5, fired.set, clock=lambda: now[0])
    t.start()
    pause(0.15)
    now[0] += HOURS  # the PC slept
    pause(0.3)
    assert not fired.is_set() and t.is_alive()
    # Awake again: the clock moves with real time and the limit is reached.
    start, base = time.perf_counter(), now[0]
    while not fired.is_set() and time.perf_counter() - start < 5:
        now[0] = base + (time.perf_counter() - start)
        time.sleep(0.01)
    assert fired.is_set()


def test_a_cancelled_timer_never_fires(monkeypatch):
    monkeypatch.setattr(awake_timer, "STEP", 0.05)
    fired = threading.Event()
    t = AwakeTimer(0.2, fired.set)
    t.start()
    t.cancel()
    t.join(2)
    assert not t.is_alive() and not fired.is_set()


def test_without_sleep_it_fires_on_time():
    fired = threading.Event()
    started = time.perf_counter()
    AwakeTimer(0.3, fired.set).start()
    assert fired.wait(5)
    assert 0.25 <= time.perf_counter() - started < 3


# ---- the SQL page's runs

def test_a_lakehouse_run_going_when_the_pc_slept_isnt_stopped_on_waking(tmp_path, sleep_jump):
    write_deltalake(str(tmp_path / "lake" / "sales"), pa.table({"id": [1, 2, 3]}))
    lake = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Test"}
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", limits=limits(sql_time_limit_minutes=0.1),
                                     test_lake=tmp_path / "lake")
    try:
        run = manager.start(SLOW, lake)
        wait(manager, run["id"], ("running",))
        pause(1.5)
        sleep_jump(HOURS)  # the PC slept for hours mid-run and has just woken
        pause(2.5)
        status = manager.status(run["id"])
        assert status["state"] == "running", status
        assert status["seconds"] >= HOURS  # the seconds shown are the time that went by
        # It has run 6 s awake by about 2 s from now: the limit still stops it then.
        status = wait(manager, run["id"], seconds=30)
        assert status["state"] == "timed_out" and "time limit" in status["error"]
    finally:
        manager.shutdown()


def test_a_local_run_going_when_the_pc_slept_isnt_stopped_on_waking(tmp_path, sleep_jump):
    from fabric_desktop.localdata import LocalData
    local = LocalData(tmp_path / "local-data")
    pid = local.create("Slow")["id"]
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", limits=limits(sql_time_limit_minutes=0),
                                     local_engine=local.engine)
    try:
        # The caller's own limit is the local engine's timer; the SQL page's is off.
        run = manager.start(LOCAL_SLOW, target="local", project=pid, dialect="duckdb", timeout=4)
        wait(manager, run["id"], ("running",))
        pause(1.5)
        sleep_jump(HOURS)
        pause(1.5)
        assert manager.status(run["id"])["state"] == "running"
        status = wait(manager, run["id"], seconds=30)
        assert status["state"] == "timed_out" and "4 seconds" in status["error"], status
    finally:
        manager.shutdown()
        local.close_all()
