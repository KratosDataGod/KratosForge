"""SQL-003: managed SQL page runs (sql_exec.py, sql_worker.py, sql_policy.py).

Every run here is a real worker process over synthetic data (range(), local Delta folders); no
test reaches Fabric or OneLake.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq
import pytest
from deltalake import write_deltalake

from fabric_desktop import app_settings, sql, sql_exec, sql_policy
from fabric_desktop.measure import RunMeter

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


def test_table_gives_the_whole_result_with_unique_names_for_compare(runs, lake):
    status = wait(runs, runs.start("SELECT id, country, amount, id FROM sales ORDER BY id", lake)["id"])
    st, table = runs.table(status["id"])
    assert st["state"] == "completed" and table.num_rows == 3 and len(set(table.column_names)) == 4
    failed = wait(runs, runs.start("SELECT * FROM nowhere", lake)["id"])
    with pytest.raises(ValueError, match="no rows to compare"):
        runs.table(failed["id"])


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
    with RunMeter(interval=0.01) as meter:
        pages = [runs.page(run["id"], offset) for offset in (0, 5_000_000, 9_999_000, 9_998_000)]
    grew_mb = ((meter.peak or 0) - (meter.start_rss or 0)) / 2 ** 20
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
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        run = runs.start(SLOW, lake)
        wait(runs, run["id"], ("running",))
        time.sleep(0.5)
        proc = runs._get(run["id"]).proc
        assert runs.cancel(run["id"])["state"] in ("cancelling", "cancelled")
        status = wait(runs, run["id"])
        assert status["state"] == "cancelled"
        assert proc.poll() is not None, "the run's own process ended"
        assert bystander.poll() is None, "Stop ended a process it didn't start"
        assert not runs._get(run["id"]).folder.exists()
    finally:
        bystander.kill()
        bystander.wait(10)


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
    proc = worker(job)
    try:
        assert json.loads(read_line(proc))["event"] == "started"
        proc.stdin.close()
        assert proc.wait(20) == 3
    finally:
        proc.kill()
        proc.wait(10)


def test_a_finished_run_ends_its_process_while_the_engine_still_listens(tmp_path):
    # The engine keeps stdin open until the process ends, so the worker must leave on its own.
    job = {"query": "SELECT 42 AS answer", "dialect": "spark", "allow": ["read_only"],
           "lakehouse": {"workspace_id": "w", "lakehouse_id": "l", "name": "LH"}, "work_dir": str(tmp_path),
           "out": str(tmp_path / "r.arrow"), "temp_dir": str(tmp_path / "spill")}
    proc = worker(job)
    try:
        events = [json.loads(read_line(proc))["event"] for _ in range(2)]
        while events[-1] not in ("done", "error"):
            events.append(json.loads(read_line(proc))["event"])
        assert events[-1] == "done"
        assert proc.wait(20) == 0
    finally:
        proc.kill()
        proc.wait(10)


def worker(job):
    """A worker as the engine starts it: stdin stays open and nothing is inherited from pytest."""
    proc = subprocess.Popen([sys.executable, "-m", "fabric_desktop.sql_worker"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    proc.stdin.write(json.dumps(job) + "\n")
    proc.stdin.flush()
    return proc


def read_line(proc, seconds=60):
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: lines.put(proc.stdout.readline()), daemon=True).start()
    try:
        return lines.get(timeout=seconds)
    except queue.Empty:
        raise AssertionError("the worker said nothing") from None


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


def test_a_hostile_name_stays_in_downloads(runs, lake, tmp_path):
    run = wait(runs, runs.start("SELECT 1 AS a", lake)["id"])
    status = wait_export(runs, runs.export(run["id"], "csv", "../..\\evil/sales")["id"])
    path = Path(status["path"])
    assert path.parent == tmp_path / "Downloads" and path.name.startswith("evilsales query ")


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
    wait(runs, runs.start("SELECT 'card QZX-4111-QZX' AS secret_value FROM sales", lake)["id"])
    wait(runs, runs.start("SELECT password_column FROM sales", lake)["id"])
    lines = runs.audit_lines
    assert [line[0] for line in lines] == ["sql_execution", "sql_execution"]
    assert [line[1] for line in lines] == ["ok", "error"]
    params = lines[0][3]
    assert set(params) == {"execution", "state", "dialect", "permission", "rows", "workspace_id", "lakehouse_id",
                           "lakehouse", "target", "project"}
    text = json.dumps(lines, default=str)
    # Letters around the card number: digits alone can turn up inside a duration in the audit line.
    for leaked in ("QZX-4111", "password_column", "SECRET-TOKEN", "SELECT"):
        assert leaked not in text


def test_status_never_returns_the_sql(runs, lake):
    run = wait(runs, runs.start("SELECT 'marker-xyz' AS a", lake)["id"])
    assert "marker-xyz" not in json.dumps(runs.status(run["id"])) + json.dumps(runs.recent())
    assert runs._get(run["id"]).query == ""


def test_service_calls_are_wired_and_audited_as_intended():
    from fabric_desktop import audit, server
    for method in ("sql_start", "sql_status", "sql_page", "sql_cancel", "sql_export",
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


# ---- local data projects (KF-DE-027): target="local"

LOCAL_SLOW = "SELECT count(*) FROM range(10000000000) a"


@pytest.fixture
def local_runs(tmp_path):
    from fabric_desktop.localdata import LocalData
    local = LocalData(tmp_path / "local-data")
    pid = local.create("Proj")["id"]
    local.engine(pid).execute("CREATE TABLE orders AS SELECT range AS id, range % 3 AS kind FROM range(2500)")
    audit: list[tuple] = []
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", audit=lambda *a: audit.append(a),
                                     limits=limits(), downloads=lambda: tmp_path / "Downloads",
                                     local_engine=local.engine)
    manager.pid, manager.local, manager.audit_lines = pid, local, audit  # type: ignore[attr-defined]
    yield manager
    manager.shutdown()
    local.close_all()


def start_local(manager, query, **options):
    return manager.start(query, target="local", project=manager.pid, dialect=options.pop("dialect", "duckdb"),
                         **options)


def count(manager, table="orders"):
    return manager.local.engine(manager.pid).execute(f"SELECT count(*) FROM {table}")["rows"][0][0]


@pytest.mark.parametrize("query,expected", [
    ("SELECT * FROM orders", [("read_only", False)]),
    ("CREATE TABLE t AS SELECT 1 a; INSERT INTO t VALUES (2); DELETE FROM t; DROP TABLE t",
     [("local_project_write", False)] * 4),
    ("INSERT INTO orders VALUES (1, 1)", [("local_project_write", False)]),
    ("UPDATE orders SET kind = 0 WHERE id = 1", [("local_project_write", False)]),
    ("DELETE FROM orders WHERE id = 1", [("local_project_write", False)]),
    ("DELETE FROM orders", [("local_project_write", True)]),
    ("UPDATE orders SET kind = 0", [("local_project_write", True)]),
    ("TRUNCATE orders", [("local_project_write", True)]),
    ("DROP TABLE orders", [("local_project_write", True)]),
    ("CREATE TEMP VIEW v AS SELECT 1 x", [("local_mutation", False)]),
    ("COPY orders TO 'out.csv'", [("fabric_mutation", False)]),
    ("ATTACH 'other.db'", [("fabric_mutation", False)]),
    ("CREATE OR REPLACE TABLE orders AS SELECT 1 id", [("local_project_write", True)]),
    ("CREATE TABLE t AS SELECT 1 a; CREATE OR REPLACE TABLE t AS SELECT 2 a", [("local_project_write", False)] * 2),
    ("INSERT OVERWRITE orders SELECT * FROM orders", [("local_project_write", True)]),
    ("SELECT * FROM read_csv('C:/secret.csv')", [("fabric_mutation", False)]),
    ("SELECT read_text('C:/secret.txt')", [("fabric_mutation", False)]),
    ("SELECT * FROM 'C:/secret.parquet'", [("fabric_mutation", False)]),
    ("CREATE TABLE t AS SELECT * FROM glob('C:/*')", [("fabric_mutation", False)]),
    ("SET profiling_output = 'C:/out.json'", [("fabric_mutation", False)]),
    ("PRAGMA enable_profiling", [("fabric_mutation", False)]),
    ("SELECT * FROM query('SELECT * FROM read_csv(''C:/secret.csv'')')", [("fabric_mutation", False)]),
    ("SELECT json_execute_serialized_sql('{}')", [("fabric_mutation", False)]),
    ("SELECT current_setting('home_directory')", [("fabric_mutation", False)]),
    ("SELECT * FROM duckdb_settings()", [("fabric_mutation", False)]),
])
def test_local_target_classes(query, expected):
    verdicts = sql_policy.classify(sql.parse(query, "duckdb"), "local")
    assert [(v.kind, v.confirm) for v in verdicts] == expected


def test_lakehouse_refusals_are_unchanged_by_the_local_class():
    for query in ("DROP TABLE sales", "DELETE FROM sales", "INSERT INTO sales VALUES (1)"):
        verdicts = sql_policy.classify(sql.parse(query, "spark"))
        assert verdicts[0].kind == "onelake_write" and not verdicts[0].confirm
    with pytest.raises(ValueError):
        sql_policy.classify([], "elsewhere")


def test_a_lakehouse_run_can_never_allow_project_writes(runs, lake):
    with pytest.raises(PermissionError):
        runs.start("DROP TABLE sales", lake, allow=["local_project_write"])


def test_local_target_selects_page_and_export(local_runs, tmp_path):
    status = wait(local_runs, start_local(local_runs, "SELECT id, kind FROM orders ORDER BY id")["id"])
    assert status["state"] == "completed" and status["rows"] == 2500
    assert status["target"] == "local" and status["project"] == "Proj" and status["permission"] == "read_only"
    assert [c["name"] for c in status["columns"]] == ["id", "kind"]
    first = local_runs.page(status["id"])
    assert len(first["rows"]) == 1000 and first["rows"][0] == [0, 0] and first["has_next"]
    assert [r[0] for r in local_runs.page(status["id"], 2400, 1000)["rows"]] == list(range(2400, 2500))
    csv = wait_export(local_runs, local_runs.export(status["id"], "csv")["id"])
    assert csv["state"] == "completed" and csv["rows"] == 2500 and Path(csv["path"]).name.startswith("Proj query")
    parquet = wait_export(local_runs, local_runs.export(status["id"], "parquet")["id"])
    assert pq.read_table(parquet["path"]).num_rows == 2500
    assert local_runs.audit_lines[-1][3]["target"] == "local" and local_runs.audit_lines[-1][3]["project"] == "Proj"


def test_local_target_spark_sql_and_project_writes(local_runs):
    status = wait(local_runs, start_local(local_runs, "CREATE TABLE kinds AS SELECT DISTINCT kind FROM orders; "
                                                      "SELECT count(*) AS n FROM kinds", dialect="spark")["id"])
    assert status["state"] == "completed" and status["permission"] == "local_project_write"
    assert local_runs.page(status["id"])["rows"] == [[3]]
    assert count(local_runs, "kinds") == 3  # kept in the project


def test_local_stop_interrupts_a_long_query(local_runs):
    run = start_local(local_runs, LOCAL_SLOW)
    wait(local_runs, run["id"], ("running",))
    time.sleep(0.3)
    started = time.monotonic()
    assert local_runs.cancel(run["id"])["state"] in ("cancelling", "cancelled")
    status = wait(local_runs, run["id"], seconds=20)
    assert status["state"] == "cancelled" and time.monotonic() - started < 15
    assert not local_runs._get(run["id"]).folder.exists()
    # the project still answers
    assert wait(local_runs, start_local(local_runs, "SELECT 1")["id"])["state"] == "completed"


def test_local_time_limit(tmp_path):
    from fabric_desktop.localdata import LocalData
    local = LocalData(tmp_path / "local-data")
    pid = local.create("Slow")["id"]
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", limits=limits(sql_time_limit_minutes=0.01),
                                     local_engine=local.engine)
    try:
        status = wait(manager, manager.start(LOCAL_SLOW, target="local", project=pid, dialect="duckdb")["id"], seconds=30)
        assert status["state"] == "timed_out" and "time limit" in status["error"]
        status = wait(manager, manager.start(LOCAL_SLOW, target="local", project=pid, dialect="duckdb",
                                             timeout=0.3)["id"], seconds=30)
        assert status["state"] == "timed_out" and "0.3 seconds" in status["error"]
    finally:
        manager.shutdown()
        local.close_all()


def test_the_projects_own_time_limit_is_shown_and_named(tmp_path):
    from fabric_desktop.localdata import LocalData
    local = LocalData(tmp_path / "local-data")
    pid = local.create("Slow")["id"]
    local.set_settings(pid, {"timeout_seconds": 1})
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", limits=limits(sql_time_limit_minutes=0),
                                     local_engine=local.engine)
    try:
        status = wait(manager, manager.start(LOCAL_SLOW, target="local", project=pid, dialect="duckdb")["id"], seconds=30)
        # This once ended "completed" on CI (flaky ledger). The query takes seconds, so whether it ran
        # past the 1 s limit unstopped or finished early shows in how long it ran and what it returned.
        seen = {k: status.get(k) for k in ("state", "error", "rows", "time_limit_minutes", "time_limit_from")}
        seen["seconds"] = round((status.get("ended") or 0) - (status.get("started") or 0), 2)
        assert status["state"] == "timed_out" and "project's time limit" in status["error"], seen
        assert status["time_limit_minutes"] == round(1 / 60, 2) and status["time_limit_from"] == "project", seen
    finally:
        manager.shutdown()
        local.close_all()


def test_export_and_import_database_are_refused_as_writes_outside_the_project(local_runs, tmp_path):
    for query in (f"EXPORT DATABASE '{(tmp_path / 'dump').as_posix()}'", "SELECT 1; IMPORT DATABASE 'x'",
                  f"-- back up\n/* first */ EXPORT DATABASE '{(tmp_path / 'dump').as_posix()}'",
                  "SELECT 1; -- then\nIMPORT DATABASE 'x'"):
        status = wait(local_runs, start_local(local_runs, query, confirm=True)["id"])
        assert status["state"] == "failed" and status["error_kind"] == "permission", query
        assert status["refused"]["permission"] == "fabric_mutation"
    assert not (tmp_path / "dump").exists()


@pytest.mark.parametrize("query,word", [
    ("EXPORT DATABASE 'x'", "EXPORT"),
    ("-- a\n/* b */  import database 'x'", "IMPORT"),
    ("SELECT ';EXPORT DATABASE' AS s", ""),  # inside a string
    ("SELECT 1 -- ; EXPORT DATABASE 'x'", ""),  # inside a comment
    ("SELECT 1; /* note */ EXPORT DATABASE 'x'", "EXPORT"),
    ("SELECT 'it''s'; EXPORT DATABASE 'x'", "EXPORT"),
    ("SELECT 1 FROM", ""),
])
def test_export_and_import_database_are_found_after_comments_only(query, word):
    assert sql_exec._outside_command(query) == word


def test_finding_export_database_stays_linear():
    for text in (";--" * 50_000, ";/*" * 50_000, "'" * 100_000, ";" * 150_000 + "EXPORT DATABASE 'x'"):
        started = time.perf_counter()
        sql_exec._outside_command(text)
        assert time.perf_counter() - started < 1.0, text[:12]


def test_local_drop_asks_for_confirmation_then_runs(local_runs):
    refused = wait(local_runs, start_local(local_runs, "DROP TABLE orders")["id"])
    assert refused["state"] == "failed" and refused["error_kind"] == "permission"
    assert refused["refused"]["confirm_required"] and refused["refused"]["statement"] == 1
    assert "needs confirming" in refused["error"]
    assert count(local_runs) == 2500  # nothing ran
    done = wait(local_runs, start_local(local_runs, "DROP TABLE orders", confirm=True)["id"])
    assert done["state"] == "completed"
    with pytest.raises(Exception, match="orders"):
        count(local_runs)


def test_local_delete_or_update_without_where_asks(local_runs):
    for query in ("DELETE FROM orders", "UPDATE orders SET kind = 9", "SELECT 1; TRUNCATE orders"):
        status = wait(local_runs, start_local(local_runs, query)["id"])
        assert status["state"] == "failed" and status["refused"]["confirm_required"], query
    assert count(local_runs) == 2500
    assert local_runs.local.engine(local_runs.pid).execute("SELECT count(*) FROM orders WHERE kind = 9")["rows"] == [[0]]
    status = wait(local_runs, start_local(local_runs, "DELETE FROM orders WHERE kind = 0")["id"])
    assert status["state"] == "completed" and count(local_runs) == 1666
    status = wait(local_runs, start_local(local_runs, "DELETE FROM orders", confirm=True)["id"])
    assert status["state"] == "completed" and count(local_runs) == 0


def test_local_files_and_extensions_are_refused_even_when_confirmed(local_runs, tmp_path):
    out = tmp_path / "leak.csv"
    status = wait(local_runs, start_local(local_runs, f"COPY orders TO '{out}'", confirm=True)["id"])
    assert status["state"] == "failed" and status["refused"]["permission"] == "fabric_mutation"
    assert not out.exists()
    with pytest.raises(PermissionError):
        start_local(local_runs, "SELECT 1", allow=["fabric_mutation"])


def test_local_replace_asks_and_outside_files_stay_unread(local_runs, tmp_path):
    for query in ("CREATE OR REPLACE TABLE orders AS SELECT 1 id, 1 kind", "INSERT OVERWRITE orders SELECT * FROM orders LIMIT 1"):
        status = wait(local_runs, start_local(local_runs, query)["id"])
        assert status["state"] == "failed" and status["refused"]["confirm_required"], query
    assert count(local_runs) == 2500
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    profile = tmp_path / "profile.json"
    for query in (f"SELECT * FROM read_csv('{secret.as_posix()}')", f"SELECT * FROM '{secret.as_posix()}'",
                  f"SET enable_profiling = 'json'; SET profiling_output = '{profile.as_posix()}'; SELECT 1"):
        status = wait(local_runs, start_local(local_runs, query, confirm=True)["id"])
        assert status["state"] == "failed" and status["refused"]["permission"] == "fabric_mutation", query
    assert not profile.exists()


def test_local_target_runs_t_sql_under_the_same_policy(local_runs):
    done = wait(local_runs, start_local(local_runs, "SELECT TOP 1 id FROM orders ORDER BY id", dialect="tsql")["id"])
    assert done["state"] == "completed"
    refused = wait(local_runs, start_local(local_runs, "DELETE FROM orders", dialect="tsql")["id"])
    assert refused["state"] == "failed" and refused["refused"]["confirm_required"]
    assert count(local_runs) == 2500


def test_a_dry_run_changes_nothing(local_runs, runs, lake):
    before = local_runs.recent()
    plan = start_local(local_runs, "INSERT INTO orders VALUES (9999, 1); DROP TABLE orders", dry_run=True)
    assert plan["dry_run"] and plan["confirm_required"]
    assert [(s["permission"], s["confirm"]) for s in plan["statements"]] == [
        ("local_project_write", False), ("local_project_write", True)]
    assert count(local_runs) == 2500 and local_runs.recent() == before
    confirmed = start_local(local_runs, "DROP TABLE orders", dry_run=True, confirm=True)
    assert not confirmed["confirm_required"] and confirmed["refused"] is None
    assert count(local_runs) == 2500
    lake_plan = runs.start("DELETE FROM sales", lake, dry_run=True)
    assert lake_plan["refused"]["permission"] == "onelake_write" and not lake_plan["confirm_required"]
    assert runs.recent() == []


def test_local_target_needs_a_known_project_and_a_local_dialect(local_runs):
    with pytest.raises(ValueError):
        local_runs.start("SELECT 1", target="local")
    with pytest.raises(KeyError):
        local_runs.start("SELECT 1", target="local", project="nope", dialect="duckdb")
    with pytest.raises(ValueError):
        local_runs.start("SELECT 1", target="local", project=local_runs.pid, dialect="fabric_warehouse")


def test_old_local_query_calls_forward_to_managed_runs(tmp_path):
    from fabric_desktop import server
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path / "app")

    def call(method, **params):
        reply = server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
        assert "error" not in reply, reply
        return reply["result"]

    def until_done(query_id):
        for _ in range(400):
            status = call("local_query_status", query_id=query_id)
            if status["state"] != "running":
                return status
            time.sleep(0.05)
        raise AssertionError("still running")
    try:
        pid = call("local_create_project", name="Old")["id"]
        started = call("local_query_start", project=pid, query="SELECT range AS n FROM range(50)", limit=10)
        assert started["state"] == "running"
        assert call("sql_status", execution=started["query_id"])["target"] == "local"  # one lifecycle
        done = until_done(started["query_id"])
        assert done["state"] == "done" and done["result"]["rows"] == [[n] for n in range(10)]
        assert done["result"]["row_count"] == 50 and done["result"]["truncated"]
        slow = call("local_query_start", project=pid, query=LOCAL_SLOW)
        time.sleep(0.3)
        assert call("local_query_cancel", query_id=slow["query_id"]) == {"cancelled": True}
        assert until_done(slow["query_id"])["state"] == "cancelled"
        assert call("local_query_cancel", query_id=slow["query_id"]) == {"cancelled": False}
        call("local_query", project=pid, query="CREATE TABLE t AS SELECT 1 a")
        asked = until_done(call("local_query_start", project=pid, query="DROP TABLE t")["query_id"])
        assert asked["state"] == "error" and asked["refused"]["confirm_required"]
        assert until_done(call("local_query_start", project=pid, query="DROP TABLE t", confirm=True)["query_id"])["state"] == "done"
        timed = until_done(call("local_query_start", project=pid, query=LOCAL_SLOW, timeout=0.3)["query_id"])
        assert timed["state"] == "error" and "0.3 seconds" in timed["error"]
    finally:
        svc.sql_runs.shutdown()
        svc.local_data.close_all()


def test_a_temp_view_is_found_whatever_case_it_is_named_in(runs, lake):
    # #414: the worker compared names case-sensitively, so `recent` was opened as a lakehouse table.
    status = wait(runs, runs.start("CREATE TEMP VIEW Recent AS SELECT * FROM sales WHERE id > 1; "
                                   "SELECT count(*) AS n FROM recent", lake, allow=["local_mutation"])["id"])
    assert status["state"] == "completed", status.get("error")
    assert runs.page(status["id"])["rows"] == [[2]]
