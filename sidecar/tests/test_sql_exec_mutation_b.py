"""Mutation batch 538: sql_exec.py re-scored on main be5ef815c (docs/quality/mutation-testing.md)."""

import os
import time

import pytest

from fabric_desktop import sql_exec
from tests.test_sql_exec import limits


def _manager(tmp_path, **changes):
    return sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "x", limits=limits(**changes),
                                  downloads=lambda: tmp_path, test_lake=tmp_path / "lake")


def test_a_temp_folder_set_outside_onedrive_is_used_and_one_inside_it_is_not(tmp_path, monkeypatch):
    for name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("fabric_desktop.known_folders._registry_onedrive", lambda: [])
    own = tmp_path / "spill-here"
    manager = _manager(tmp_path, sql_temp_dir=str(own))
    try:
        assert manager.base() == own
    finally:
        manager.shutdown()
    cloud = tmp_path / "cloud"
    monkeypatch.setenv("OneDrive", str(cloud))
    manager = _manager(tmp_path, sql_temp_dir=str(cloud / "sql"))
    try:
        assert manager.base() == tmp_path / "data" / "sql-runs"
    finally:
        manager.shutdown()


def test_an_earlier_sessions_folder_that_is_a_link_keeps_the_spill_it_points_at(tmp_path):
    manager = _manager(tmp_path, sql_result_retention_hours=24)
    try:
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "spill").mkdir(parents=True)
        kept = elsewhere / "spill" / "duckdb_temp_storage-0.tmp"
        kept.write_bytes(b"x")
        old = time.time() - 2 * sql_exec.SPILL_QUIET_S
        os.utime(kept, (old, old))
        manager.base().mkdir(parents=True)
        link = manager.base() / "abcdef123456"
        try:
            os.symlink(elsewhere, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("this PC can't make a symbolic link")
        manager.cleanup(earlier_sessions=True)
        assert kept.exists()
    finally:
        manager.shutdown()


def test_a_local_run_whose_temp_files_reach_the_projects_limit_names_that_limit(tmp_path):
    from types import SimpleNamespace
    full = RuntimeError("IO Error: Failed to offload data block of size 256 KiB (max_temp_directory_size)")
    project = SimpleNamespace(settings=SimpleNamespace(max_temp_size="1GB"), temp_dir=str(tmp_path))
    kind, said = sql_exec.local_error(full, project, None)
    assert kind == "disk"
    assert said.startswith("Stopped: the query's temp files reached this project's limit (1GB, in the project's")
    project.settings.max_temp_size = ""
    kind, said = sql_exec.local_error(full, project, None)
    assert kind == "disk" and "as the temp folder's drive is full" in said


def test_a_runs_seconds_are_from_its_start_to_its_end_on_the_monotonic_clock(tmp_path, monkeypatch):
    from types import SimpleNamespace
    now = [0.0]
    monkeypatch.setattr(sql_exec, "time", SimpleNamespace(monotonic=lambda: now[0], time=time.time))
    run = sql_exec.Execution(id="abcdef123456", query="SELECT 1", dialect="duckdb", lakehouse={}, allow=(),
                             folder=tmp_path)
    now[0] = 100.0
    run.started = time.time()
    now[0] = 130.0
    run.ended = time.time()
    now[0] = 500.0
    assert run.seconds() == 30.0


def test_a_warehouse_change_with_a_malformed_precondition_is_never_sent(tmp_path, monkeypatch):
    from fabric_desktop import app_settings
    real = app_settings.load()
    monkeypatch.setattr(app_settings, "load", lambda: real | {"sql_warehouse_writes": {"value": True, "source": "user"}})
    manager = _manager(tmp_path)
    try:
        with pytest.raises(ValueError, match=r"^The change wasn't sent: a precondition names the object's kind"):
            manager.start("DELETE FROM dbo.sales", dialect="tsql", target="warehouse", allow=["warehouse_write"],
                          warehouse_ref={"workspace_id": "ws", "item_id": "wh", "kind": "warehouse"}, confirm=True,
                          confirm_name="WH", precondition={"object": "dbo.sales"})
    finally:
        manager.shutdown()


def test_a_spill_folder_that_cant_be_read_is_not_taken_as_quiet(tmp_path):
    assert sql_exec._quiet(tmp_path / "missing", time.time()) is False
    (tmp_path / "a-file").write_bytes(b"x")
    assert sql_exec._quiet(tmp_path / "a-file", time.time()) is False


def test_a_warehouse_changes_statements_lose_the_begin_and_commit_that_wrap_them(tmp_path, monkeypatch):
    from fabric_desktop import app_settings
    real = app_settings.load()
    monkeypatch.setattr(app_settings, "load", lambda: real | {"sql_warehouse_writes": {"value": True, "source": "user"}})
    manager = _manager(tmp_path)
    try:
        plan = manager.start(None, dialect="tsql", target="warehouse", allow=["warehouse_write"],
                             warehouse_ref={"workspace_id": "ws", "item_id": "wh", "kind": "warehouse"}, dry_run=True,
                             statements=["BEGIN TRAN", "DELETE FROM dbo.sales WHERE id = 1", "COMMIT"])
        assert [s["statement"] for s in plan["statements"]] == [1]
    finally:
        manager.shutdown()


class _Proc:
    def __init__(self):
        import io
        self.stdin = io.StringIO()

    def replies(self):
        import json
        return [json.loads(line) for line in self.stdin.getvalue().splitlines()]


def test_a_warehouse_run_is_given_the_warehouse_token_and_a_lakehouse_run_is_not(tmp_path):
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "lake-token", limits=limits(),
                                     downloads=lambda: tmp_path, warehouse_token=lambda: "wh-token")
    try:
        run = sql_exec.Execution(id="abcdef123456", query="SELECT 1", dialect="tsql", lakehouse={}, allow=(),
                                 folder=tmp_path, target="warehouse")
        proc = _Proc()
        manager._answer(run, proc, "warehouse_token")
        run.target = "lakehouse"
        manager._answer(run, proc, "warehouse_token")
        assert proc.replies() == [{"value": "wh-token"}, {"error": "Unknown question 'warehouse_token'"}]
    finally:
        manager.shutdown()


def test_a_sorted_page_that_reaches_the_last_row_has_no_next_page(tmp_path):
    import pyarrow as pa
    import pyarrow.ipc as ipc
    manager = _manager(tmp_path)
    try:
        run = sql_exec.Execution(id="abcdef123456", query="SELECT 1", dialect="duckdb", lakehouse={}, allow=(),
                                 folder=tmp_path / "run", state="completed", rows=3, offsets=[0],
                                 columns=[{"name": "n", "type": "BIGINT"}])
        run.folder.mkdir()
        table = pa.table({"n": [3, 1, 2]})
        with ipc.new_file(str(run.snapshot), table.schema) as writer:
            writer.write_table(table)
        manager._runs[run.id] = run
        page = manager.page(run.id, 2, 1, sort=[{"column": 0}])
        assert page["rows"] == [[3]] and page["has_previous"] is True and page["has_next"] is False
        page = manager.page(run.id, 1, 1, sort=[{"column": 0}])
        assert page["rows"] == [[2]] and page["has_next"] is True
    finally:
        manager.shutdown()


def test_an_earlier_sessions_result_just_past_an_hours_retention_is_removed(tmp_path):
    manager = _manager(tmp_path, sql_result_retention_hours=1)
    try:
        left = manager.base() / "abcdef123456"
        left.mkdir(parents=True)
        old = time.time() - 3600.5
        os.utime(left, (old, old))
        manager.cleanup(earlier_sessions=True)
        assert not left.exists()
    finally:
        manager.shutdown()
