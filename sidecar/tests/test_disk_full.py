"""A full disk stops a run with a message that says so, changes nothing, and the next run works.

delta-rs words a full disk as "Failed to parse parquet: ... Unable to copy data to file: No space
left on device (os error 28)" (on Windows "os error 112"), and DuckDB words a full temp drive as
running out of memory ("failed to offload data block"). Measured on a full disk; the tests here make
the same errors without filling one.
"""

import errno

import duckdb
import pytest
from test_large_tables import DAY1, DAY2, Setup, rows
from test_write_failures import failing, ids

from fabric_desktop import disk_full, sql_worker

DELTA_TEXT = ("Failed to parse parquet: External: Generic LocalFileSystem error: Unable to copy data to "
              "file: No space left on device (os error 28)")


def test_a_full_disk_is_found_by_errno_windows_code_text_or_cause():
    assert disk_full.is_disk_full(OSError(errno.ENOSPC, "No space left on device"))
    windows = OSError(None, "There is not enough space on the disk")
    windows.winerror = 112
    assert disk_full.is_disk_full(windows)
    assert disk_full.is_disk_full(Exception(DELTA_TEXT))
    assert disk_full.is_disk_full(OSError("Generic LocalFileSystem error: ... (os error 112)"))
    try:
        try:
            raise OSError(errno.ENOSPC, "No space left on device")
        except OSError as inner:
            raise RuntimeError("couldn't write the snapshot") from inner
    except RuntimeError as outer:
        assert disk_full.is_disk_full(outer)
    for other in (PermissionError(errno.EACCES, "Access is denied"), OSError("(os error 2)"),
                  ValueError("the disk folder is empty"), MemoryError()):
        assert not disk_full.is_disk_full(other)


def test_the_message_names_the_free_space_and_what_to_do(tmp_path):
    text = disk_full.message(tmp_path / "not" / "made" / "yet", "The table was not changed.")
    assert text.startswith("The disk is full (") and " free). The table was not changed. " in text
    assert text.endswith("Free some space and run it again.")
    assert disk_full.message(None, "") == "The disk is full. Free some space and run it again."


def test_duckdbs_full_temp_drive_error_is_recognised(tmp_path):
    con = duckdb.connect()
    con.execute("SET memory_limit = '32MB'")
    con.execute("SET threads = 1")
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET temp_directory = '{tmp_path.as_posix()}'")
    con.execute("SET max_temp_directory_size = '1MB'")  # as a full drive does
    with pytest.raises(duckdb.Error) as caught:
        con.execute("SELECT md5(i::VARCHAR) || md5((i + 1)::VARCHAR) AS s FROM range(3000000) t(i) "
                    "ORDER BY s").fetchall()
    kind, message = sql_worker.error_message(caught.value, {"temp_dir": str(tmp_path)})
    assert kind == "disk" and "memory" not in message
    assert message.startswith("Stopped: the query ran out of room for its temp files, as the temp folder's "
                              "drive is full (") and "Nothing was saved." in message
    con.execute("SELECT 1").fetchall()  # the connection still works


def test_a_query_that_cant_save_its_result_says_the_disk_is_full(tmp_path):
    kind, message = sql_worker.error_message(OSError(errno.ENOSPC, "No space left on device"),
                                             {"out": str(tmp_path / "result.arrow")})
    assert kind == "disk" and message.startswith("The disk is full (")
    assert sql_worker.error_message(ValueError("syntax error at SELEC"), {}) == ("sql", "syntax error at SELEC")


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "rewrite"])
def test_a_write_on_a_full_disk_says_so_changes_nothing_and_the_next_run_works(tmp_path, merge):
    s = Setup(tmp_path, merge)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run(day=DAY1)
    before = s.store.version("LH_Bronze", "dbo", "events")
    s.source(rows([10, 11], DAY2))
    names = ("write", "write_stream", "merge_insert", "upsert")
    real = {n: getattr(s.store, n) for n in names}

    def full(name):
        def call(lakehouse, schema, table, *a, **kw):
            if table == "events":
                raise OSError(DELTA_TEXT)
            return real[name](lakehouse, schema, table, *a, **kw)
        return call
    for n in names:
        setattr(s.store, n, full(n))
    res = s.engine(DAY2).run("bronze.events")
    assert res.status == "failed" and not res.retryable
    assert res.message.startswith("The disk is full (") and "The table was not changed." in res.message
    assert any("No space left on device" in step.detail for step in res.steps)  # the full error is kept
    assert s.store.version("LH_Bronze", "dbo", "events") == before and ids(s) == [0, 1, 2]
    for n in names:  # space freed
        setattr(s.store, n, real[n])
    assert s.run(day=DAY2).status == "ok"
    assert ids(s) == [0, 1, 2, 10, 11]


def test_a_run_log_that_cant_be_saved_on_a_full_disk_says_so(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    real = failing(s.store, "run_log", None, OSError(DELTA_TEXT))
    res = s.engine(DAY1).run("bronze.events")
    assert res.status == "ok" and ids(s) == [0, 1, 2]
    assert "couldn't save the run log, as the disk is full (" in res.message
    assert "The table's write stands; free some space before the next run" in res.message
    s.store.meta_append = real


SPILL = "SELECT md5(i::VARCHAR) || md5((i + 1)::VARCHAR) AS s FROM range(3000000) t(i) ORDER BY s"


def _local_run(svc, pid, query):
    import time
    eid = svc.sql_start(query, target="local", project=pid, dialect="duckdb")["id"]
    deadline = time.monotonic() + 120
    while (status := svc.sql_status(eid))["state"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.1)
    return status


def test_a_local_data_query_whose_temp_files_hit_the_projects_limit_says_so_not_memory(tmp_path):
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path / "app")
    try:
        pid = svc.local_create_project("Sales")["id"]
        svc.local_project_settings(pid, {"memory_limit": "32MB", "threads": 1, "max_temp_size": "1MB"})
        status = _local_run(svc, pid, SPILL)
        assert status["state"] == "failed" and status["error_kind"] == "disk", status
        assert status["error"].startswith("Stopped: the query's temp files reached this project's limit (1MB, "
                                          "in the project's settings)."), status["error"]
        assert "memory" not in status["error"].lower()
        assert _local_run(svc, pid, "SELECT 1 AS a")["state"] == "completed"  # the project still runs queries
    finally:
        svc.local_data.close_all()


def test_a_local_data_query_on_a_full_temp_drive_or_disk_says_the_drive_is_full(tmp_path):
    from types import SimpleNamespace

    from fabric_desktop import sql_exec
    con = duckdb.connect()
    for setting in ("memory_limit = '32MB'", "threads = 1", f"temp_directory = '{tmp_path.as_posix()}'",
                    "max_temp_directory_size = '1MB'"):  # as a full drive does when the project sets no limit
        con.execute(f"SET {setting}")
    with pytest.raises(duckdb.Error) as caught:
        con.execute(SPILL).fetchall()
    project = SimpleNamespace(settings=SimpleNamespace(max_temp_size=""), temp_dir=tmp_path, root=tmp_path)
    kind, message = sql_exec.local_error(caught.value, project, None)
    assert kind == "disk" and message.startswith("Stopped: the query ran out of room for its temp files, as the "
                                                 "temp folder's drive is full (") and "memory" not in message.lower()
    kind, message = sql_exec.local_error(OSError(errno.ENOSPC, "No space left on device"), project, None)
    assert kind == "disk" and message.startswith("The disk is full (") and "didn't finish" in message
    assert sql_exec.local_error(ValueError("syntax error at SELEC"), project, None) == ("sql", "syntax error at SELEC")
