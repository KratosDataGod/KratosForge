"""A query that needs more memory than its limit stops with a message that names the setting the user
can change, changes nothing, and the next query runs (WR-103).

DuckDB's own words ("Out of Memory Error: failed to allocate data of size ... Possible solutions:
SET threads=X ... SET memory_limit=...") tell the user to run SET, which a Local data project run
refuses and the SQL page locks: the limit is in the project's settings or in Settings > SQL.
Measured on Linux with a 32MB project memory limit; the same query fails the same way on Windows,
where it is UNVERIFIED by hand but runs in the Windows CI job.
"""

import time
from types import SimpleNamespace

import duckdb
import pytest

from fabric_desktop import sql_exec, sql_worker

# Needs its whole list in memory at once: DuckDB can't spill it, so it runs out of memory, not temp room.
BIG = "SELECT list(md5(i::VARCHAR)) AS l FROM range(3000000) t(i)"


def _oom() -> duckdb.Error:
    con = duckdb.connect()
    con.execute("SET memory_limit = '32MB'")
    con.execute("SET threads = 1")
    with pytest.raises(duckdb.Error) as caught:
        con.execute(BIG).fetchall()
    assert "Out of Memory Error" in str(caught.value) and "SET threads" in str(caught.value)
    return caught.value


def _local_run(svc, pid, query):
    eid = svc.sql_start(query, target="local", project=pid, dialect="duckdb")["id"]
    deadline = time.monotonic() + 120
    while (status := svc.sql_status(eid))["state"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.1)
    return status


def test_a_local_data_run_past_the_projects_memory_limit_names_it_and_changes_nothing(tmp_path):
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path / "app")
    try:
        pid = svc.local_create_project("Sales")["id"]
        svc.local_project_settings(pid, {"memory_limit": "32MB", "threads": 1})
        assert _local_run(svc, pid, "CREATE TABLE t AS SELECT 1 AS a")["state"] == "completed"
        for query in (BIG, "CREATE TABLE big AS " + BIG,
                      "INSERT INTO t SELECT len(list(md5(i::VARCHAR))) FROM range(3000000) t(i)"):
            status = _local_run(svc, pid, query)
            assert status["state"] == "failed" and status["error_kind"] == "memory", status
            assert status["error"].startswith("Stopped: the query needed more memory than this project's memory "
                                              "limit (32MB) allows. That statement didn't finish."), status["error"]
            assert "SET " not in status["error"]  # a local run refuses SET: the advice names the settings
        assert _local_run(svc, pid, "SELECT * FROM big")["error_kind"] == "sql"  # the new table wasn't made
        done = _local_run(svc, pid, "SELECT count(*) AS n FROM t")  # the existing table is unchanged
        assert done["state"] == "completed" and done["rows"] == 1
    finally:
        svc.local_data.close_all()


def test_with_no_project_limit_the_message_names_duckdbs_default():
    project = SimpleNamespace(settings=SimpleNamespace(memory_limit="", max_temp_size=""), temp_dir=None, root=None)
    kind, message = sql_exec.local_error(_oom(), project, None)
    assert kind == "memory"
    assert message.startswith("Stopped: the query needed more memory than DuckDB's default share of this PC's "
                              "memory (80%) allows. That statement didn't finish.")
    assert "set a memory limit or lower the threads in the project's settings" in message
    assert "SET " not in message


def test_the_sql_page_names_settings_sql():
    kind, message = sql_worker.error_message(_oom(), {})
    assert kind == "memory"
    assert message.startswith("Stopped: the query needed more memory than the share of this PC's memory set in "
                              "Settings > SQL allows. Nothing was saved.")
    assert "SET " not in message


def test_other_errors_keep_duckdbs_words():
    project = SimpleNamespace(settings=SimpleNamespace(memory_limit="", max_temp_size=""), temp_dir=None, root=None)
    assert sql_exec.local_error(ValueError("syntax error at SELEC"), project, None) == ("sql", "syntax error at SELEC")
    assert sql_worker.error_message(ValueError("syntax error at SELEC"), {}) == ("sql", "syntax error at SELEC")
