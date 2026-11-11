"""Stopping local T-SQL (Stop, the time limit) frees what the query held: the project's connection,
DuckDB's memory and its spill files in tmp/. The next query on the same project runs. A time limit
the organization's policy sets is a ceiling a caller can shorten but not lengthen or remove."""

import json
import os
import threading
import time

import pytest

from fabric_desktop import app_settings, sql_exec
from fabric_desktop.localdata import LocalData, QueryCancelled

# Sorting 30 million rows in 100 MB spills to tmp/ before the first row comes back.
SORT = "SELECT i FROM range(30000000) t(i) ORDER BY hash(i)"
SLOW = "SELECT sum(hash(a.range)) FROM range(10000000000) a"


@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "local-data")
    pid = local.create("Spill")["id"]
    local.set_settings(pid, {"memory_limit": "100MB", "threads": 2})
    e = local.engine(pid)
    yield e
    local.close_all()


def spill(e):
    return os.listdir(e.project.temp_dir) if e.project.temp_dir.exists() else []


def held(e):
    return e.con.execute("SELECT sum(memory_usage_bytes), sum(temporary_storage_bytes) FROM duckdb_memory()").fetchone()


def freed(e):
    assert spill(e) == [] and held(e) == (0, 0)
    assert e.execute("SELECT 42 AS n")["rows"] == [[42]]  # the next query on the same project runs


def started_spilling(e):
    stream = e.execute_reader(SORT, query_id="q1", batch_size=1000)
    stream.read_next_batch()
    assert spill(e), "the sort didn't spill, so this test proves nothing"
    return stream


def test_stop_mid_stream_frees_the_spill_and_memory(engine):
    stream = started_spilling(engine)
    assert engine.cancel("q1")
    with pytest.raises(QueryCancelled):
        for _ in stream:
            pass
    freed(engine)


def test_a_stream_closed_unread_frees_the_spill(engine):
    started_spilling(engine).close()
    freed(engine)


def test_a_stream_past_its_time_limit_frees_the_spill(engine):
    stream = engine.execute_reader(SORT, query_id="q2", batch_size=1000, timeout=3600)
    stream.read_next_batch()
    assert spill(engine)
    engine._running["q2"]["timed_out"] = True  # as the timer sets it, between batches
    with pytest.raises(TimeoutError):
        for _ in stream:
            pass
    freed(engine)


@pytest.mark.parametrize("how", ["stop", "time limit"])
def test_a_whole_result_stopped_while_sorting_frees_the_spill(engine, how):
    if how == "stop":
        threading.Timer(1.5, lambda: engine.cancel("q3")).start()
        with pytest.raises(QueryCancelled):
            engine.execute(SORT + ", i DESC", query_id="q3")
    else:
        with pytest.raises(TimeoutError):
            engine.execute(SORT + ", i DESC", timeout=1.5)
    freed(engine)


def test_a_finished_stream_leaves_nothing_behind(engine):
    rows = sum(b.num_rows for b in engine.execute_reader(SORT, batch_size=100_000))
    assert rows == 30_000_000
    freed(engine)


# ---- the organization's time limit

def local_manager(tmp_path, **limits):
    local = LocalData(tmp_path / "ld")
    pid = local.create("Slow")["id"]
    base = {"sql_page_rows": 1000, "sql_time_limit_minutes": 10, "sql_memory_limit_percent": 30,
            "sql_max_concurrent": 2, "sql_result_retention_hours": 24, "sql_temp_dir": "",
            "sql_time_limit_by_policy": False, **limits}
    m = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", limits=lambda: base, local_engine=local.engine)
    return m, local, pid


def finished(m, run_id, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        status = m.status(run_id)
        if status["state"] in sql_exec.FINISHED:
            return status
        time.sleep(0.05)
    raise AssertionError(m.status(run_id))


@pytest.mark.parametrize("asked, applied, source", [
    (None, 0.6, "policy"),     # no limit of its own: the organization's
    (3600.0, 0.6, "policy"),   # longer than the organization allows: the organization's
    (0.0, 0.6, "policy"),      # "no limit" isn't allowed either
    (0.3, 0.3, "caller"),      # shorter: the caller's
])
def test_a_policy_time_limit_is_a_ceiling(tmp_path, asked, applied, source):
    m, local, pid = local_manager(tmp_path, sql_time_limit_minutes=0.01, sql_time_limit_by_policy=True)
    try:
        status = finished(m, m.start(SLOW, target="local", project=pid, dialect="duckdb", timeout=asked)["id"])
        assert status["state"] == "timed_out", status
        assert m._get(status["id"]).time_limit_s == applied and status["time_limit_from"] == source
        if source == "policy":
            assert "organization's time limit" in status["error"]
        assert finished(m, m.start("SELECT 1", target="local", project=pid, dialect="duckdb")["id"])["state"] \
            == "completed"
    finally:
        m.shutdown()
        local.close_all()


def test_without_policy_a_caller_limit_still_replaces_the_sql_pages(tmp_path):
    m, local, pid = local_manager(tmp_path, sql_time_limit_minutes=0.01)
    try:
        run = m.start("SELECT 1", target="local", project=pid, dialect="duckdb", timeout=3600)
        finished(m, run["id"])
        assert m._get(run["id"]).time_limit_s == 3600 and m._get(run["id"]).time_limit_from == "caller"
    finally:
        m.shutdown()
        local.close_all()


def test_sql_limits_say_when_the_time_limit_is_the_organizations(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "policy"))
    assert app_settings.sql_limits()["sql_time_limit_by_policy"] is False
    policy = tmp_path / "policy" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({"sql_time_limit_minutes": 5}))
    limits = app_settings.sql_limits()
    assert limits["sql_time_limit_minutes"] == 5 and limits["sql_time_limit_by_policy"] is True
