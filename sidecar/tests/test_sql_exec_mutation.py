"""Mutation batch 263: sql_exec.py, first score (docs/quality/mutation-testing.md)."""

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from fabric_desktop import sql_exec
from tests.test_sql_exec import lake, runs, wait  # noqa: F401  (fixtures)

WAREHOUSE = {"workspace_id": "ws", "item_id": "wh", "kind": "warehouse"}


def test_a_snapshot_with_fewer_rows_than_the_run_wrote_asks_for_a_rerun(tmp_path):
    path = tmp_path / "result.arrow"
    table = pa.table({"n": [1, 2]})
    with ipc.new_file(str(path), table.schema) as writer:
        writer.write_table(table)
    assert sql_exec._whole(path, 2).num_rows == 2
    with pytest.raises(sql_exec.RerunNeeded, match="holds 2 of the 3 rows"):
        sql_exec._whole(path, 3)


def test_a_warehouse_change_refused_before_anything_is_sent_says_why(runs):  # noqa: F811
    status = runs.start("DELETE FROM dbo.sales", dialect="tsql", target="warehouse", warehouse_ref=WAREHOUSE)
    assert status["state"] == "failed" and status["error_kind"] == "permission"
    assert status["error"].endswith("so nothing was sent. A warehouse run only reads.")
    assert status["refused"]["permission"] == "fabric_mutation" and status["refused"]["statement"] == 1


def test_a_warehouse_dry_run_that_only_reads_judges_a_change_as_refused(runs):  # noqa: F811
    plan = runs.start("DELETE FROM dbo.sales", dialect="tsql", target="warehouse", warehouse_ref=WAREHOUSE,
                      dry_run=True)
    assert plan["dry_run"] and plan["refused"]["permission"] == "fabric_mutation"


def test_a_warehouse_change_reads_the_writes_setting(runs, monkeypatch):  # noqa: F811
    from fabric_desktop import app_settings
    real = app_settings.load()
    monkeypatch.setattr(app_settings, "load", lambda: real | {"sql_warehouse_writes": {"value": True, "source": "user"}})
    with pytest.raises(ValueError, match="aren't available here"):  # allowed, confirmed, and no warehouse here
        runs.start("DELETE FROM dbo.sales", dialect="tsql", target="warehouse", allow=["warehouse_write"],
                   warehouse_ref=WAREHOUSE, confirm=True, confirm_name="WH")
    monkeypatch.setattr(app_settings, "load", lambda: real | {"sql_warehouse_writes": {"value": False, "source": "policy"}})
    with pytest.raises(PermissionError, match="turned off in Settings › SQL by your organization"):
        runs.start("DELETE FROM dbo.sales", dialect="tsql", target="warehouse", allow=["warehouse_write"],
                   warehouse_ref=WAREHOUSE, confirm=True, confirm_name="WH")


def test_a_local_run_out_of_memory_names_the_projects_limit_or_the_default_share():
    from types import SimpleNamespace
    oom = RuntimeError("Out of Memory Error: failed to allocate")
    _, said = sql_exec.local_error(oom, SimpleNamespace(settings=SimpleNamespace(memory_limit="2GB")), None)
    assert "than this project's memory limit (2GB) allows" in said and "raise the memory limit" in said
    _, said = sql_exec.local_error(oom, SimpleNamespace(settings=SimpleNamespace(memory_limit="")), None)
    assert "than DuckDB's default share of this PC's memory (80%) allows" in said and "set a memory limit" in said


def test_a_full_disk_names_the_free_space_where_the_file_was_written(tmp_path):
    import errno
    from types import SimpleNamespace
    full = OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "project.db"))
    kind, said = sql_exec.local_error(full, SimpleNamespace(), None)
    assert kind == "disk" and said.startswith("The disk is full (") and " free)." in said


def test_a_runs_values_and_statements_stay_out_of_its_repr(tmp_path):
    run = sql_exec.Execution(id="r1", query="SELECT :secret", dialect="duckdb", lakehouse={}, allow=("read_only",),
                             folder=tmp_path, units=["SELECT :secret"],
                             params=[{"name": "secret", "type": "text", "value": "hunter2"}])  # pragma: allowlist secret (test value)
    text = repr(run)
    assert "hunter2" not in text and ":secret" not in text  # pragma: allowlist secret (test value)


def test_a_completed_run_whose_snapshot_is_gone_has_none(runs, lake):  # noqa: F811
    status = wait(runs, runs.start("SELECT 1 AS n", lake)["id"])
    assert status["state"] == "completed" and status["snapshot"] is True
    runs._get(status["id"]).snapshot.unlink()
    assert runs.status(status["id"])["snapshot"] is False


def test_an_export_tries_999_names_before_giving_up(tmp_path, monkeypatch):
    import builtins
    real_open = builtins.open
    tried = []

    def taken(path, mode="r", *args, **kwargs):
        if mode == "x":
            tried.append(str(path))
            raise FileExistsError(path)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", taken)
    with pytest.raises(ValueError, match="free file name"):
        sql_exec._reserve(tmp_path, "result", ".csv")
    assert len(tried) == 999 and tried[-1].endswith("result (999).csv")


def test_a_warehouse_run_that_asks_for_more_than_it_may_says_what_it_may_do(runs):  # noqa: F811
    with pytest.raises(PermissionError, match="A warehouse run reads, or changes the warehouse after you confirm it"):
        runs.start("SELECT 1", dialect="tsql", target="warehouse", allow=["onelake_write"], warehouse_ref=WAREHOUSE)


def test_a_precondition_is_only_for_a_warehouse_change(runs):  # noqa: F811
    with pytest.raises(ValueError, match="A precondition is for a warehouse change"):
        runs.start("SELECT 1", dialect="tsql", target="warehouse", warehouse_ref=WAREHOUSE, precondition={})


def test_a_read_only_script_keeps_its_begin_and_commit_and_is_refused(runs):  # noqa: F811
    status = runs.start("", dialect="tsql", target="warehouse", warehouse_ref=WAREHOUSE,
                        statements=["BEGIN TRAN", "SELECT 1 AS n", "COMMIT"])
    assert status["state"] == "failed" and status["refused"]["statement"] == 1


def test_a_page_of_a_snapshot_that_lost_rows_asks_for_a_rerun(runs, lake):  # noqa: F811
    status = wait(runs, runs.start("SELECT * FROM range(3) t(n)", lake)["id"])
    snapshot = runs._get(status["id"]).snapshot
    table = pa.table({"n": [0, 1]})
    with ipc.new_file(str(snapshot), table.schema) as writer:
        writer.write_table(table)
    with pytest.raises(sql_exec.RerunNeeded, match="holds fewer rows than the 3"):
        runs.page(status["id"], 0, 10)


def _manager(tmp_path, audit=None, **extra):
    from tests.test_sql_exec import limits
    return sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "x", audit=(audit.append if audit is not None
                                                                                else lambda *a: None),
                                  limits=limits(), downloads=lambda: tmp_path, test_lake=tmp_path / "lake", **extra)


def test_a_change_confirmed_for_another_warehouse_is_never_sent(tmp_path, monkeypatch):
    import sys
    from fabric_desktop import app_settings, warehouse
    real = app_settings.load()
    monkeypatch.setattr(app_settings, "load", lambda: real | {"sql_warehouse_writes": {"value": True, "source": "user"}})
    manager = _manager(tmp_path, command=[sys.executable, "-c", "pass"],
                       resolve_warehouse=lambda ref: warehouse.Endpoint("abc.datawarehouse.fabric.microsoft.com", "Sales WH"))
    try:
        started = manager.start("UPDATE dbo.sales SET a = 1", dialect="tsql", target="warehouse", allow=["warehouse_write"],
                                warehouse_ref=WAREHOUSE, confirm=True, confirm_name="Sales WH Prod")
        status = wait(manager, started["id"])
    finally:
        manager.shutdown()
    assert status["state"] == "failed" and status["error_kind"] == "permission"
    assert "You confirmed a change to Sales WH Prod, but Fabric names this warehouse Sales WH" in status["error"]


def test_a_worker_that_cant_start_fails_the_run_with_the_reason(tmp_path, lake):  # noqa: F811
    manager = _manager(tmp_path, command=[str(tmp_path / "no-such-python")])
    try:
        status = wait(manager, manager.start("SELECT 1 AS n", lake)["id"])
    finally:
        manager.shutdown()
    assert status["state"] == "failed" and status["error_kind"] == "engine"
    assert "no-such-python" in status["error"]


@pytest.mark.parametrize("query,word", [
    ("SELECT e'a\\'; EXPORT DATABASE 'x'", ""),  # an E'' string: \' is a quote inside it
    ("SELECT 1 +e'a\\'; EXPORT DATABASE 'x'", ""),
    ("SELECT type'a\\'; EXPORT DATABASE 'x'", "EXPORT"),  # the e ends a word, so the string is plain
    ("SELECT x_e'a\\'; EXPORT DATABASE 'x'", "EXPORT"),
    ("SELECT $q$; EXPORT DATABASE 'x'$q$", ""),  # a dollar-quoted string
    ("/**/EXPORT DATABASE 'x'", "EXPORT"),  # an empty comment, then the statement's first word
    ("e'a\\'; EXPORT DATABASE 'x' -- z", ""),  # an E'' string on the text's second character
    ("SELECT a$b$; EXPORT DATABASE 'x'", "EXPORT"),  # a $ inside a name starts no string
    ("SELECT x_$q$; EXPORT DATABASE 'x'", "EXPORT"),
])
def test_strings_and_comments_hide_an_export_and_names_dont(query, word):
    assert sql_exec._outside_command(query) == word


def test_a_change_whose_worker_says_no_count_changed_no_rows(tmp_path, monkeypatch):
    import sys
    from fabric_desktop import app_settings, warehouse
    real = app_settings.load()
    monkeypatch.setattr(app_settings, "load", lambda: real | {"sql_warehouse_writes": {"value": True, "source": "user"}})
    worker = ("import json, sys; sys.stdin.readline(); "
              "print(json.dumps({'event': 'done', 'rows': 0, 'batches': [], 'committed': True}), flush=True)")
    audit = []
    manager = _manager(tmp_path, audit=_Recorder(audit), command=[sys.executable, "-c", worker], warehouse_token=lambda: "t",
                       resolve_warehouse=lambda ref: warehouse.Endpoint("abc.datawarehouse.fabric.microsoft.com", "Sales WH"))
    try:
        started = manager.start("UPDATE dbo.sales SET a = 1", dialect="tsql", target="warehouse", allow=["warehouse_write"],
                                warehouse_ref=WAREHOUSE, confirm=True, confirm_name="Sales WH")
        status = wait(manager, started["id"])
    finally:
        manager.shutdown()
    assert status["state"] == "completed" and status["committed"] is True and status["affected"] == 0
    details = audit[-1][3]
    assert details["affected"] == 0 and details["committed"] is True and "failed_batch" not in details


def test_a_pc_whose_memory_cant_be_read_is_taken_to_have_8_gb(monkeypatch):
    from fabric_desktop import free_memory, side_by_side
    monkeypatch.setattr(side_by_side, "_memory_gb", lambda: None)
    monkeypatch.setattr(free_memory, "available", lambda: None)
    assert sql_exec.memory_budget(50)["cap_mb"] == free_memory.budget(4096, None)["cap_mb"] == 4096


def test_an_earlier_sessions_result_younger_than_an_hours_retention_is_kept(tmp_path):
    from tests.test_sql_exec import limits
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "x", limits=limits(sql_result_retention_hours=1),
                                     downloads=lambda: tmp_path, test_lake=tmp_path / "lake")
    try:
        left = manager.base() / "abcdef123456"
        left.mkdir(parents=True)
        manager.cleanup(earlier_sessions=True)
        assert left.is_dir()
    finally:
        manager.shutdown()


class _Recorder:
    def __init__(self, into):
        self.into = into

    def append(self, *args):
        self.into.append(args)


def test_a_warehouse_read_refused_is_audited_without_change_or_batch_fields(tmp_path):
    audit = []
    manager = _manager(tmp_path, audit=_Recorder(audit))
    try:
        manager.start("DELETE FROM dbo.sales", dialect="tsql", target="warehouse", warehouse_ref=WAREHOUSE)
    finally:
        manager.shutdown()
    details = audit[-1][3]
    assert details["target"] == "warehouse" and details["warehouse_id"] == "wh"
    assert not {"affected", "committed", "failed_batch", "error_kind"} & set(details)


def test_a_failed_lakehouse_run_names_no_batch(runs, lake):  # noqa: F811
    status = wait(runs, runs.start("SELECT * FROM no_such_table", lake)["id"])
    assert status["state"] == "failed" and "failed_batch" not in status


def test_json_export_notes_only_numbers_past_the_largest_javascript_reads_exactly(tmp_path):
    from tests.test_export_exact import export
    _, status = export(pa.table({"n": pa.array([2 ** 53 - 1, -(2 ** 53 - 1)], pa.int64())}), "json", tmp_path)
    assert status["notes"] == []
