"""A SQL page run on a busy PC: its memory comes from what is free when it starts, not from the
PC's total, and its process stops itself with a clear message before it runs the PC out of memory
(free_memory.py, sql_worker._hold_to)."""

import sys

import pytest
from test_sql_exec import lake, limits, wait  # noqa: F401  (lake is a fixture)

from fabric_desktop import free_memory, sql_exec

GB = 1 << 30


def test_a_busy_pc_s_free_memory_sets_the_budget():
    b = free_memory.budget(9_600, 3 * GB)  # 60% of 16 GB asked for, 3 GB free
    assert b == {"cap_mb": 2304, "duckdb_mb": 1152, "bound": "free", "free_mb": 3072}


def test_an_idle_pc_keeps_the_share_set_in_settings():
    b = free_memory.budget(4_000, 12 * GB)
    assert b["cap_mb"] == 4_000 and b["duckdb_mb"] == 2_000 and b["bound"] == "share"


def test_unknown_or_almost_no_free_memory_still_gives_a_working_limit():
    assert free_memory.budget(4_000, None) == {"cap_mb": 4_000, "duckdb_mb": 2_000, "bound": "share", "free_mb": 0}
    tiny = free_memory.budget(4_000, 100 << 20)
    assert tiny["cap_mb"] == 2 * free_memory.MIN_MB and tiny["duckdb_mb"] == free_memory.MIN_MB


@pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="read on Windows and Linux only")
def test_free_memory_is_read_on_this_pc():
    free = free_memory.available()
    assert free is not None and 0 < free


def test_the_engine_hands_the_run_its_budget(tmp_path, lake, monkeypatch):  # noqa: F811
    jobs = []
    monkeypatch.setattr(sql_exec, "memory_budget",
                        lambda percent: {"cap_mb": 2304, "duckdb_mb": 1152, "bound": "free", "free_mb": 3072})
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", limits=limits(),
                                     test_lake=tmp_path / "lake")
    real = manager._launch

    def launch(run, lim):
        jobs.append(manager._memory_budget(30))
        real(run, lim)
    manager._launch = launch
    try:
        status = wait(manager, manager.start("SELECT count(*) FROM sales", lake)["id"])
        assert status["state"] == "completed"
        assert jobs == [{"cap_mb": 2304, "duckdb_mb": 1152, "bound": "free", "free_mb": 3072}]
    finally:
        manager.shutdown()


@pytest.mark.parametrize("bound", ["free", "share"])
def test_a_run_past_its_memory_is_stopped_with_why(tmp_path, lake, bound):  # noqa: F811
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", limits=limits(),
                                     test_lake=tmp_path / "lake")
    # Smaller than any Python process: the watch stops the run as soon as it looks, well before
    # this query (minutes of hashing) could finish.
    manager._memory_budget = lambda percent: {"cap_mb": 1, "duckdb_mb": 256, "bound": bound, "free_mb": 3072}
    try:
        query = "SELECT sum(hash(i)) FROM sales, range(100000000000) t(i)"
        status = wait(manager, manager.start(query, lake)["id"])
        assert status["state"] == "failed" and status["error"].startswith("Stopped: the query needed more than 0.0 GB")
        if bound == "free":
            assert "this PC had 3.0 GB free when it started" in status["error"]
            assert "Close other apps" in status["error"]
        else:
            assert "memory share set in Settings › SQL" in status["error"]
    finally:
        manager.shutdown()
