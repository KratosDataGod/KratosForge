"""SQL-003: managed SQL page runs (sql_exec.py, sql_worker.py, sql_policy.py).

Every run here is a real worker process over synthetic data (range(), local Delta folders); no
test reaches Fabric or OneLake.
"""

from __future__ import annotations

import json
import resource
import subprocess
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq
import pytest
from deltalake import write_deltalake

from fabric_desktop import app_settings, sql, sql_exec, sql_policy

SLOW = "SELECT sum(a.range * b.range) FROM range(200000000) a, range(1000) b"


def limits(**changes):
    base = {"sql_page_rows": 1000, "sql_time_limit_minutes": 10, "sql_memory_limit_percent": 30,
            "sql_max_concurrent": 2, "sql_result_retention_hours": 24, "sql_temp_dir": ""}
    base.update(changes)
    return lambda: base


@pytest.fixture
def lake(tmp_path):
    root = tmp_path / "lake"
    write_deltalake(str(root / "sales"), pa.table({"id": [1, 2, 3], "country": ["NL", None, "US"],
                                                   "amount": [1.5, None, 3.25]}))
    return {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Test"}


@pytest.fixture
def runs(tmp_path):
    audit: list[tuple] = []
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "SECRET-TOKEN",
                                     audit=lambda *a: audit.append(a), limits=limits(),
                                     downloads=lambda: tmp_path / "Downloads", test_lake=tmp_path / "lake")
    manager.audit_lines = audit  # type: ignore[attr-defined]
    yield manager
    manager.shutdown()


def wait(manager, run_id, states=sql_exec.FINISHED, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        status = manager.status(run_id)
        if status["state"] in states:
            return status
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still {manager.status(run_id)['state']}")


def wait_export(manager, export_id, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        status = manager.export_status(export_id)
        if status["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("export still running")


# ---- what a run may do

@pytest.mark.parametrize("query,kinds", [
    ("SELECT * FROM sales", ["read_only"]),
    ("WITH t AS (SELECT 1 a) SELECT * FROM t UNION ALL SELECT 2", ["read_only"]),
    ("DESCRIBE sales", ["read_only"]),
    ("CREATE TEMP VIEW v AS SELECT 1 x; SELECT * FROM v", ["local_mutation", "read_only"]),
    ("CREATE TABLE t (a INT); INSERT INTO t VALUES (1); DROP TABLE t", ["local_mutation"] * 3),
    ("CACHE TABLE sales", ["local_mutation"]),
    ("SET spark.sql.shuffle.partitions = 4", ["local_mutation"]),
    ("INSERT INTO sales VALUES (4, 'DE', 1.0)", ["onelake_write"]),
    ("DELETE FROM sales WHERE id = 1", ["onelake_write"]),
    ("UPDATE sales SET amount = 0", ["onelake_write"]),
    ("DROP TABLE sales", ["onelake_write"]),
    ("TRUNCATE TABLE sales", ["onelake_write"]),
    ("ALTER TABLE sales ADD COLUMNS (x INT)", ["onelake_write"]),
    ("CREATE OR REPLACE TABLE sales AS SELECT 1 a", ["onelake_write"]),
    ("CREATE SCHEMA gold", ["onelake_write"]),
    ("MERGE INTO sales USING upd ON sales.id = upd.id WHEN MATCHED THEN UPDATE SET amount = upd.amount",
     ["onelake_write"]),
    ("OPTIMIZE sales", ["fabric_mutation"]),
    ("VACUUM sales", ["fabric_mutation"]),
    ("GRANT SELECT ON sales TO someone", ["fabric_mutation"]),
])
def test_statements_are_classified_before_anything_runs(query, kinds):
    verdicts = sql_policy.classify(sql.parse(query, "spark"))
    assert [v.kind for v in verdicts] == kinds


def test_copy_install_and_attach_are_refused_in_duckdb_sql():
    for query in ("COPY (SELECT 1) TO 'x.csv'", "INSTALL httpfs", "LOAD httpfs", "ATTACH 'x.db'"):
        verdicts = sql_policy.classify(sql.parse(query, "duckdb"))
        assert verdicts[0].kind == "fabric_mutation", query
        assert sql_policy.refused(verdicts, ("read_only", "local_mutation")) is verdicts[0]


def test_only_reads_and_local_changes_can_ever_be_allowed(runs, lake):
    with pytest.raises(PermissionError):
        runs.start("DROP TABLE sales", lake, allow=["onelake_write"])
    with pytest.raises(PermissionError):
        runs.start("OPTIMIZE sales", lake, allow=["fabric_mutation"])


def test_a_lakehouse_write_is_refused_and_nothing_runs(runs, lake):
    status = wait(runs, runs.start("SELECT 1; DELETE FROM sales", lake)["id"])
    assert status["state"] == "failed" and status["error_kind"] == "permission"
    assert status["refused"]["permission"] == "onelake_write" and status["refused"]["statement"] == 2
    assert "nothing ran" in status["error"]


def test_a_local_change_needs_the_person_to_confirm(runs, lake):
    query = "CREATE TEMP VIEW nl AS SELECT * FROM sales WHERE country = 'NL'; SELECT * FROM nl"
    refused = wait(runs, runs.start(query, lake)["id"])
    assert refused["state"] == "failed" and refused["refused"]["permission"] == "local_mutation"
    allowed = wait(runs, runs.start(query, lake, allow=["local_mutation"])["id"])
    assert allowed["state"] == "completed" and allowed["permission"] == "local_mutation"
    assert runs.page(allowed["id"])["rows"] == [[1, "NL", 1.5]]


def test_the_token_is_asked_for_only_when_a_table_is_read(tmp_path, lake):
    asked = []

    def signed_out():
        asked.append(1)
        raise RuntimeError("not signed in")
    manager = sql_exec.SqlExecutions(tmp_path, token=signed_out, limits=limits())
    try:
        assert wait(manager, manager.start("SELECT 1 AS a", lake)["id"])["state"] == "completed"
        assert asked == []
        status = wait(manager, manager.start("SELECT * FROM sales", lake)["id"])
        assert status["state"] == "failed" and "Sign in to read lakehouse tables" in status["error"]
        assert asked == [1]
    finally:
        manager.shutdown()


def test_a_call_cannot_point_a_run_at_local_folders(tmp_path, lake):
    manager = sql_exec.SqlExecutions(tmp_path, token=lambda: "", limits=limits())
    try:
        ref = {**lake, "local_root": str(tmp_path / "lake"), "test_lake": str(tmp_path / "lake")}
        status = wait(manager, manager.start("SELECT * FROM sales", ref)["id"])
        assert status["state"] == "failed"  # read from OneLake (offline here), never from the folder
    finally:
        manager.shutdown()


def test_a_query_cannot_read_local_files_or_change_its_limits(runs, lake, tmp_path):
    (tmp_path / "secret.csv").write_text("a\n1\n")
    status = wait(runs, runs.start(f"SELECT * FROM read_csv('{tmp_path / 'secret.csv'}')", lake, dialect="duckdb")["id"])
    assert status["state"] == "failed" and "disabled by configuration" in status["error"]
    status = wait(runs, runs.start("SET memory_limit = '100GB'; SELECT 1", lake, dialect="duckdb",
                                   allow=["local_mutation"])["id"])
    assert status["state"] == "failed" and "Settings › SQL" in status["error"]


# ---- results and pages

def test_lakehouse_tables_page_with_nulls_types_and_repeated_names(runs, lake):
    status = wait(runs, runs.start("SELECT id, country, amount, id FROM sales ORDER BY id", lake)["id"])
    assert status["state"] == "completed" and status["rows"] == 3
    assert [c["name"] for c in status["columns"]] == ["id", "country", "amount", "id"]
    assert [c["type"] for c in status["columns"]] == ["int64", "string", "double", "int64"]
    page = runs.page(status["id"])
    assert page["rows"] == [[1, "NL", 1.5, 1], [2, None, None, 2], [3, "US", 3.25, 3]]
    assert not page["has_next"] and not page["has_previous"]


def test_pages_move_forward_and_back_without_counting(runs, lake):
    run = wait(runs, runs.start("SELECT range AS n FROM range(2500)", lake)["id"])
    first = runs.page(run["id"])
    assert len(first["rows"]) == 1000 and first["rows"][0] == [0] and first["has_next"]
    last = runs.page(run["id"], 2000)
    assert [r[0] for r in last["rows"]] == list(range(2000, 2500)) and not last["has_next"] and last["has_previous"]
    middle = runs.page(run["id"], 999, 3)
    assert [r[0] for r in middle["rows"]] == [999, 1000, 1001]


def test_a_statement_without_rows_completes_empty(runs, lake):
    status = wait(runs, runs.start("CREATE TEMP VIEW v AS SELECT 1 x", lake, allow=["local_mutation"])["id"])
    assert status["state"] == "completed" and status["rows"] == 0
    assert runs.page(status["id"])["rows"] == []


def test_a_sql_error_fails_the_run_with_the_reason(runs, lake):
    status = wait(runs, runs.start("SELECT nope FROM sales", lake)["id"])
    assert status["state"] == "failed" and status["error_kind"] == "sql" and "nope" in status["error"]
    with pytest.raises(ValueError):
        runs.page(status["id"])


def test_ten_million_rows_page_without_being_held_in_memory(runs, lake):
    run = wait(runs, runs.start("SELECT range AS id, range % 7 AS bucket, 'row ' || range AS label "
                                "FROM range(10000000)", lake)["id"], seconds=180)
    assert run["state"] == "completed" and run["rows"] == 10_000_000
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KiB on Linux
    pages = [runs.page(run["id"], offset) for offset in (0, 5_000_000, 9_999_000, 9_998_000)]
    grew_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
    assert pages[0]["rows"][0] == [0, 0, "row 0"]
    assert pages[1]["rows"][0] == [5_000_000, 5_000_000 % 7, "row 5000000"]
    assert pages[2]["rows"][-1] == [9_999_999, 9_999_999 % 7, "row 9999999"] and not pages[2]["has_next"]
    assert len(pages[3]["rows"]) == 1000 and pages[3]["has_next"]
    # The snapshot is ~250 MB; paging reads one batch at a time, so the engine barely grows.
    assert run_snapshot_mb(runs, run["id"]) > 150
    assert grew_mb < 60, f"paging grew the engine by {grew_mb:.0f} MB"


def run_snapshot_mb(runs, run_id):
    return runs._get(run_id).snapshot.stat().st_size / 2 ** 20


# ---- Stop, time limit, queue

def test_stop_ends_only_that_run(runs, lake):
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        run = runs.start(SLOW, lake)
        wait(runs, run["id"], ("running",))
        time.sleep(0.5)
        pid = runs._get(run["id"]).proc.pid
        assert runs.cancel(run["id"])["state"] in ("cancelling", "cancelled")
        status = wait(runs, run["id"])
        assert status["state"] == "cancelled"
        assert not Path(f"/proc/{pid}").exists() or "zombie" in Path(f"/proc/{pid}/status").read_text().lower()
        assert bystander.poll() is None, "Stop ended a process it didn't start"
        assert not runs._get(run["id"]).folder.exists()
    finally:
        bystander.kill()


def test_the_time_limit_stops_a_run(tmp_path, lake):
    manager = sql_exec.SqlExecutions(tmp_path, token=lambda: "", limits=limits(sql_time_limit_minutes=0.02))
    try:
        status = wait(manager, manager.start(SLOW, lake)["id"])
        assert status["state"] == "timed_out" and "time limit" in status["error"]
    finally:
        manager.shutdown()


def test_runs_beyond_the_limit_wait_and_a_queued_run_can_be_stopped(tmp_path, lake):
    manager = sql_exec.SqlExecutions(tmp_path, token=lambda: "", limits=limits(sql_max_concurrent=1))
    try:
        first, second, third = (manager.start(q, lake) for q in (SLOW, SLOW, "SELECT 42"))
        assert manager.status(second["id"])["state"] == "queued"
        assert manager.cancel(second["id"])["state"] == "cancelled"
        assert manager.status(third["id"])["state"] == "queued"
        manager.cancel(first["id"])
        assert wait(manager, third["id"])["state"] == "completed"
        assert manager.page(third["id"])["rows"] == [[42]]
        assert manager.status(second["id"])["started"] is None
    finally:
        manager.shutdown()


def test_a_run_ends_when_the_engine_goes(tmp_path):
    job = {"query": SLOW, "dialect": "spark", "allow": ["read_only"], "token": "",
           "lakehouse": {"workspace_id": "w", "lakehouse_id": "l", "name": "LH"}, "work_dir": str(tmp_path),
           "out": str(tmp_path / "r.arrow"), "temp_dir": str(tmp_path / "spill")}
    proc = subprocess.Popen([sys.executable, "-m", "fabric_desktop.sql_worker"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    proc.stdin.write(json.dumps(job) + "\n")
    proc.stdin.flush()
    assert json.loads(proc.stdout.readline())["event"] == "started"
    proc.stdin.close()
    assert proc.wait(20) == 3


# ---- export

def test_csv_export_streams_and_never_overwrites(runs, lake, tmp_path):
    run = wait(runs, runs.start("SELECT id, country, id FROM sales ORDER BY id", lake)["id"])
    first = wait_export(runs, runs.export(run["id"], "csv", "sales")["id"])
    second = wait_export(runs, runs.export(run["id"], "csv", "sales")["id"])
    assert first["state"] == second["state"] == "completed" and first["rows"] == 3
    assert first["path"] != second["path"]
    assert pa_csv.read_csv(first["path"]).column_names == ["id", "country", "id"]
    assert Path(first["path"]).read_text().splitlines() == ['"id","country","id"', '1,"NL",1', '2,,2', '3,"US",3']
    assert not list((tmp_path / "Downloads").glob("*.partial"))


def test_an_existing_file_is_never_replaced(runs, lake, tmp_path, monkeypatch):
    run = wait(runs, runs.start("SELECT 1 AS a", lake)["id"])
    monkeypatch.setattr(sql_exec.time, "strftime", lambda _fmt: "2026-10-17 120000")
    taken = tmp_path / "Downloads" / "sales query 2026-10-17 120000.csv"
    taken.parent.mkdir(parents=True)
    taken.write_text("keep me")
    status = wait_export(runs, runs.export(run["id"], "csv", "sales")["id"])
    assert taken.read_text() == "keep me"
    assert status["path"].endswith("sales query 2026-10-17 120000 (2).csv")


def test_parquet_export_renames_repeated_columns_so_it_reads_back(runs, lake):
    run = wait(runs, runs.start("SELECT id, amount, id FROM sales ORDER BY id", lake)["id"])
    status = wait_export(runs, runs.export(run["id"], "parquet")["id"])
    table = pq.read_table(status["path"])
    assert table.column_names == ["id", "amount", "id_2"] and table.column(1).to_pylist() == [1.5, None, 3.25]


def test_a_stopped_export_leaves_no_files(runs, lake, tmp_path, monkeypatch):
    run = wait(runs, runs.start("SELECT range FROM range(300000)", lake)["id"])
    real = sql_exec._writer

    class SlowWriter:
        def __init__(self, *args):
            self.inner = real(*args)

        def write_batch(self, batch):
            time.sleep(0.2)
            self.inner.write_batch(batch)

        def close(self):
            self.inner.close()

    slow_writer = SlowWriter
    monkeypatch.setattr(sql_exec, "_writer", slow_writer)
    export = runs.export(run["id"], "csv")
    time.sleep(0.3)
    runs.export_cancel(export["id"])
    status = wait_export(runs, export["id"])
    assert status["state"] == "cancelled"
    assert list((tmp_path / "Downloads").iterdir()) == []


def test_a_cleaned_up_result_asks_to_run_again(runs, lake):
    run = wait(runs, runs.start("SELECT 1 AS a", lake)["id"])
    runs._get(run["id"]).snapshot.unlink()
    assert runs.export(run["id"], "csv")["rerun_needed"] is True
    with pytest.raises(sql_exec.RerunNeeded):
        runs.page(run["id"])


# ---- retention and audit

def test_old_snapshots_are_deleted(tmp_path, lake):
    stale = tmp_path / "sql-runs" / "abcdef123456"
    stale.mkdir(parents=True)
    manager = sql_exec.SqlExecutions(tmp_path, token=lambda: "", limits=limits(sql_result_retention_hours=0))
    try:
        wait(manager, manager.start("SELECT 1", lake)["id"])
        assert not stale.exists()
    finally:
        manager.shutdown()


def test_only_the_newest_results_are_kept(tmp_path, lake, monkeypatch):
    monkeypatch.setattr(sql_exec, "MAX_KEPT", 2)
    manager = sql_exec.SqlExecutions(tmp_path, token=lambda: "", limits=limits())
    try:
        ids = [wait(manager, manager.start(f"SELECT {n}", lake)["id"])["id"] for n in range(4)]
        assert [r["id"] for r in manager.recent()] == ids[:1:-1]
    finally:
        manager.shutdown()


def test_the_audit_records_the_run_but_never_its_sql_error_or_token(runs, lake):
    wait(runs, runs.start("SELECT 'card 4111-1111' AS secret_value FROM sales", lake)["id"])
    wait(runs, runs.start("SELECT password_column FROM sales", lake)["id"])
    lines = runs.audit_lines
    assert [line[0] for line in lines] == ["sql_execution", "sql_execution"]
    assert [line[1] for line in lines] == ["ok", "error"]
    params = lines[0][3]
    assert set(params) == {"execution", "state", "dialect", "permission", "rows", "workspace_id", "lakehouse_id",
                           "lakehouse"}
    text = json.dumps(lines, default=str)
    for leaked in ("4111", "password_column", "SECRET-TOKEN", "SELECT"):
        assert leaked not in text


def test_status_never_returns_the_sql(runs, lake):
    run = wait(runs, runs.start("SELECT 'marker-xyz' AS a", lake)["id"])
    assert "marker-xyz" not in json.dumps(runs.status(run["id"])) + json.dumps(runs.recent())
    assert runs._get(run["id"]).query == ""


def test_service_calls_are_wired_and_audited_as_intended():
    from fabric_desktop import audit, server
    for method in ("sql_start", "sql_status", "sql_executions", "sql_page", "sql_cancel", "sql_export",
                   "sql_export_status", "sql_export_cancel"):
        assert method in server.METHODS
    assert not audit.audited("sql_status") and not audit.audited("sql_page")
    assert audit.audited("sql_start") and audit.audited("sql_cancel") and audit.audited("sql_export")
    assert audit.target({"query": "SELECT secret", "lakehouse_ref": {"name": "LH"}})["query"] == "(given)"
    watchdog = (Path(__file__).parents[2] / "app" / "src-tauri" / "src" / "watchdog.rs").read_text()
    for method in ("sql_status", "sql_page", "sql_cancel", "sql_start"):
        assert f'"{method}"' in watchdog


# ---- settings

def test_sql_settings_have_defaults_ranges_and_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "policy"))
    assert app_settings.sql_limits() == {"sql_page_rows": 1000, "sql_time_limit_minutes": 10,
                                         "sql_memory_limit_percent": 60, "sql_max_concurrent": 2,
                                         "sql_result_retention_hours": 24, "sql_temp_dir": ""}
    app_settings.save({"sql_page_rows": "500", "sql_time_limit_minutes": 0})
    assert app_settings.sql_limits()["sql_page_rows"] == 500
    assert app_settings.sql_limits()["sql_time_limit_minutes"] == 0
    with pytest.raises(ValueError):
        app_settings.save({"sql_max_concurrent": 50})
    with pytest.raises(ValueError):
        app_settings.save({"sql_temp_dir": "relative/folder"})
    policy = tmp_path / "policy" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({"sql_memory_limit_percent": 40, "sql_max_concurrent": 99}))
    assert app_settings.sql_limits()["sql_memory_limit_percent"] == 40
    assert app_settings.sql_limits()["sql_max_concurrent"] == 2  # out of range: the default
    assert any("sql_max_concurrent" in p for p in app_settings.policy_problems())
    with pytest.raises(ValueError, match="organization"):
        app_settings.save({"sql_memory_limit_percent": 50})
