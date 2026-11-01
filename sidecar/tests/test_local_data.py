"""Local data projects (FD-LDE-001, FD-LDE-002): files on this PC as SQL tables, no Fabric involved."""

import gzip
import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import polars as pl
import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop import server
from fabric_desktop.localdata import EngineBusy, LocalData, QueryCancelled
from fabric_desktop.localdata.discovery import Source, discover
from fabric_desktop.service import Service


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    (d / "Sales").mkdir(parents=True)
    pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]}).write_parquet(d / "orders.parquet")
    (d / "Sales" / "Customer.csv").write_text("id,country\n1,NL\n2,US\n3,US\n")
    (d / "Ref.Countries.csv").write_text("code,label\nNL,Netherlands\nUS,United States\n")
    (d / "events.jsonl").write_text('{"id": 1, "kind": "x"}\n{"id": 2, "kind": "y"}\n')
    (d / "arr.json").write_text('[{"v": 1}, {"v": 2}]')
    with gzip.open(d / "zipped.csv.gz", "wt") as f:
        f.write("k\n1\n2\n")
    (d / "inventory").mkdir()
    for i in range(2):
        pl.DataFrame({"sku": [f"s{i}"], "qty": [i]}).write_parquet(d / "inventory" / f"part-0000{i}.parquet")
    (d / "inventory" / ".part-00000.parquet.crc").write_bytes(b"crc")  # writer leftovers are never read
    (d / "inventory" / "_SUCCESS").write_text("")
    for year in (2025, 2026):
        (d / "facts" / f"year={year}").mkdir(parents=True)
        pl.DataFrame({"amount": [year]}).write_parquet(d / "facts" / f"year={year}" / "f.parquet")
    write_deltalake(str(d / "sales_delta"), pa.table({"id": [1, 2], "amount": [10.5, 20.0]}))
    (d / "Budget.xlsx").write_bytes(b"PK not really")
    (d / "broken.parquet").write_bytes(b"not parquet")
    (d / "node_modules").mkdir()
    (d / "node_modules" / "x.csv").write_text("a\n1\n")
    (d / ".git").mkdir()
    (d / ".git" / "y.csv").write_text("a\n1\n")
    (d / "package.json").write_text("{}")
    return d


@pytest.fixture
def local(tmp_path):
    return LocalData(tmp_path / "app" / "local-data")


def table_names(catalog):
    return {f"{t['schema']}.{t['table']}": t for t in catalog["tables"]}


def test_discovery_names_files_the_daffydb_way(data):
    found = discover(Source(str(data)))
    names = {f"{t.schema}.{t.table}": t.format for t in found.tables}
    assert names == {"dbo.orders": "parquet", "Sales.Customer": "csv", "Ref.Countries": "csv",
                     "dbo.events": "json", "dbo.arr": "json", "dbo.zipped": "csv", "dbo.inventory": "parquet",
                     "dbo.facts": "parquet", "dbo.sales_delta": "delta", "dbo.Budget": "excel", "dbo.broken": "parquet"}
    # Tool folders and config files are never data.
    assert not any("node_modules" in p or ".git" in p for t in found.tables for p in t.paths)


def test_flat_naming_and_explicit_names(data):
    found = discover(Source(str(data), naming="flat", schema="raw", names={"orders.parquet": "core.orders"}))
    names = {f"{t.schema}.{t.table}" for t in found.tables}
    assert "raw.Sales_Customer" in names and "core.orders" in names and "raw.orders" not in names


def test_same_name_in_two_formats_keeps_parquet_and_reports_the_other(tmp_path):
    (tmp_path / "t.csv").write_text("a\n1\n")
    pl.DataFrame({"a": [1]}).write_parquet(tmp_path / "t.parquet")
    found = discover(Source(str(tmp_path)))
    assert [(t.table, t.format) for t in found.tables] == [("t", "parquet")]
    assert "Also named dbo.t" in found.issues[0].problem


def test_query_files_as_tables_and_report_bad_ones(local, data):
    p = local.create("Sales dev")
    added = local.add_source(p["id"], str(data))
    tables = {f"{t['schema']}.{t['table']}": t for t in added["tables"]}
    assert tables["dbo.broken"]["status"] == "error" and tables["dbo.broken"]["error"]
    assert tables["dbo.Budget"]["status"] == "error" and "Excel" in tables["dbo.Budget"]["error"]
    assert tables["dbo.orders"]["status"] == "ok"
    engine = local.engine(p["id"])
    r = engine.execute("""
        SELECT c.country, count(*) AS n, sum(o.id) OVER () AS total
        FROM dbo.orders o JOIN Sales.Customer c USING (id) GROUP BY c.country, o.id ORDER BY 1""")
    assert r["row_count"] == 3 and r["engine"] == "local" and r["seconds"] >= 0
    assert engine.execute("SELECT sum(qty)::INT FROM dbo.inventory")["rows"] == [[1]]
    assert engine.execute("SELECT year, amount FROM dbo.facts ORDER BY 1")["rows"] == [[2025, 2025], [2026, 2026]]
    assert engine.execute("SELECT count(*) FROM dbo.zipped")["rows"] == [[2]]
    assert engine.execute("SELECT sum(amount) FROM dbo.sales_delta")["rows"] == [[30.5]]
    assert engine.execute("SELECT kind FROM events ORDER BY id")["rows"] == [["x"], ["y"]]


def test_persists_across_restarts(local, data, tmp_path):
    pid = local.create("Persist")["id"]
    local.add_source(pid, str(data))
    engine = local.engine(pid)
    engine.execute("""
        CREATE TABLE silver_orders AS SELECT * FROM dbo.orders;
        INSERT INTO silver_orders VALUES (4, 'd');
        UPDATE silver_orders SET name = upper(name) WHERE id = 1;
        DELETE FROM silver_orders WHERE id = 2;
        CREATE VIEW gold_orders AS SELECT count(*) AS n FROM silver_orders;""")
    local.close_all()
    again = LocalData(tmp_path / "app" / "local-data")  # the app was closed and opened again
    assert [p["name"] for p in again.projects()] == ["Persist"]
    e2 = again.engine(pid)
    assert e2.execute("SELECT * FROM gold_orders")["rows"] == [[3]]
    assert e2.execute("SELECT name FROM silver_orders ORDER BY id")["rows"] == [["A"], ["c"], ["d"]]
    assert e2.execute("SELECT count(*) FROM Sales.Customer")["rows"] == [[3]]  # file views persist too
    kinds = {f"{t['schema']}.{t['table']}": t["kind"] for t in e2.tables()}
    assert kinds["dbo.silver_orders"] == "table" and kinds["dbo.gold_orders"] == "view"
    assert kinds["dbo.orders"] == "file"
    again.close_all()


def test_temporary_tables_last_the_session(local, data):
    pid = local.create("Temp")["id"]
    e = local.engine(pid)
    e.execute("CREATE TEMP TABLE scratch AS SELECT 1 AS a")
    assert e.execute("SELECT a FROM scratch")["rows"] == [[1]]
    assert any(t["kind"] == "temp table" for t in e.tables())
    local.close(pid)
    with pytest.raises(Exception, match="scratch"):
        local.engine(pid).execute("SELECT * FROM scratch")


def test_memory_project_keeps_nothing_but_its_folders(local, data, tmp_path):
    pid = local.create("Scratch", storage="memory")["id"]
    local.add_source(pid, str(data))
    local.engine(pid).execute("CREATE TABLE t AS SELECT 1")
    local.close_all()
    assert not (tmp_path / "app" / "local-data" / pid / "data.duckdb").exists()
    e = LocalData(tmp_path / "app" / "local-data").engine(pid)
    assert e.execute("SELECT count(*) FROM dbo.orders")["rows"] == [[3]]  # views made again
    with pytest.raises(Exception):
        e.execute("SELECT * FROM t")


def test_changed_files_refresh_the_catalog(local, data):
    pid = local.create("Watch")["id"]
    local.add_source(pid, str(data))
    e = local.engine(pid)
    (data / "new_table.csv").write_text("x\n1\n")
    (data / "events.jsonl").unlink()
    e._last_check = 0  # as if the last look was a while ago
    assert e.execute("SELECT x FROM dbo.new_table")["rows"] == [[1]]
    with pytest.raises(Exception, match="events"):
        e.execute("SELECT * FROM dbo.events")
    # A new Delta version is picked up too.
    write_deltalake(str(data / "sales_delta"), pa.table({"id": [3], "amount": [1.0]}), mode="append")
    e._last_check = 0
    assert e.execute("SELECT count(*) FROM dbo.sales_delta")["rows"] == [[3]]


def test_no_automatic_refresh_when_switched_off(local, data):
    pid = local.create("Manual", settings={"auto_refresh": False})["id"]
    local.add_source(pid, str(data))
    e = local.engine(pid)
    (data / "later.csv").write_text("x\n1\n")
    e._last_check = 0
    with pytest.raises(Exception):
        e.execute("SELECT * FROM dbo.later")
    assert "/".join(e.refresh()["changed"]).endswith("data")
    assert e.execute("SELECT x FROM dbo.later")["rows"] == [[1]]


def test_type_overrides(local, data):
    pid = local.create("Types")["id"]
    local.add_source(pid, str(data))
    e = local.engine(pid)
    entry = e.set_override("Sales.Customer", {"id": "VARCHAR"})
    assert {c["name"]: c["type"] for c in entry["columns"]}["id"] == "VARCHAR"
    with pytest.raises(ValueError, match="not a column type"):
        e.set_override("Sales.Customer", {"id": "INT); DROP TABLE x; --"})


def test_discovered_names_never_replace_tables_people_made(local, data):
    pid = local.create("Mine")["id"]
    e = local.engine(pid)
    e.execute("CREATE TABLE orders AS SELECT 42 AS answer")  # dbo.orders, as in Fabric
    added = local.add_source(pid, str(data))
    assert any("already a table or view you made" in i["problem"] for i in added["issues"])
    assert e.execute("SELECT answer FROM dbo.orders")["rows"] == [[42]]


def test_removing_a_folder_drops_its_views_not_its_files(local, data):
    pid = local.create("Remove")["id"]
    local.add_source(pid, str(data))
    local.engine(pid).remove_source(str(data))
    assert local.catalog(pid)["tables"] == []
    assert (data / "orders.parquet").exists()


def test_results_are_paged_and_counted(local):
    e = local.engine(local.create("Big")["id"])
    r = e.execute("SELECT range AS n FROM range(250000)", limit=10)
    assert len(r["rows"]) == 10 and r["truncated"] and r["row_count"] == 250000
    r = e.execute("SELECT range AS n FROM range(250000)", limit=10, count=False)
    assert r["row_count"] is None and r["truncated"]


SLOW = "SELECT count(*) FROM range(10000000000) a"


def test_timeout_stops_a_query(local):
    e = local.engine(local.create("Slow")["id"])
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="longer than 0.5 seconds"):
        e.execute(SLOW, timeout=0.5)
    assert time.monotonic() - started < 10
    assert e.execute("SELECT 1")["rows"] == [[1]]  # the engine still works


def test_cancel_a_background_query(local):
    pid = local.create("Cancel")["id"]
    started = local.start(pid, SLOW)
    time.sleep(0.3)  # usually running by now; on a slow runner it may not have started yet
    assert local.cancel(started["query_id"]) == {"cancelled": True}
    for _ in range(100):
        status = local.status(started["query_id"])
        if status["state"] != "running":
            break
        time.sleep(0.1)
    assert status["state"] == "cancelled"
    done = local.start(pid, "SELECT 2 AS two")
    for _ in range(100):
        status = local.status(done["query_id"])
        if status["state"] != "running":
            break
        time.sleep(0.05)
    assert status["result"]["rows"] == [[2]]


def test_requests_from_several_threads_take_turns(local, data):
    pid = local.create("Threads")["id"]
    local.add_source(pid, str(data))
    e = local.engine(pid)
    e.execute("CREATE TABLE counter (n INT)")
    errors = []

    def work():
        try:
            for _ in range(20):
                e.execute("INSERT INTO counter VALUES (1)")
                e.tables()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert e.execute("SELECT count(*) FROM counter")["rows"] == [[80]]


def test_settings_reach_duckdb(local):
    pid = local.create("Limits", settings={"memory_limit": "1GB", "threads": 2})["id"]
    e = local.engine(pid)
    assert e.execute("SELECT current_setting('threads')")["rows"] == [[2]]
    assert "MiB" in e.execute("SELECT current_setting('memory_limit')")["rows"][0][0]
    local.set_settings(pid, {"memory_limit": "", "threads": 0})
    assert e.execute("SELECT current_setting('threads')")["rows"][0][0] == os.cpu_count()
    with pytest.raises(ValueError, match="memory_limit"):
        local.set_settings(pid, {"memory_limit": "lots"})


def test_spark_sql_dialect(local, data):
    pid = local.create("Spark")["id"]
    local.add_source(pid, str(data))
    r = local.engine(pid).execute("SELECT name, nvl(NULL, 'x') AS v FROM dbo.orders ORDER BY id LIMIT 1",
                                  dialect="spark")
    assert r["rows"] == [["a", "x"]]
    r = local.engine(pid).execute("SELECT TOP 1 [name] FROM dbo.orders ORDER BY id", dialect="tsql")
    assert r["rows"] == [["a"]]


def test_another_process_cant_open_the_database_for_writing(local, data, tmp_path):
    pid = local.create("Locked")["id"]
    e = local.engine(pid)
    e.execute("CREATE TABLE t AS SELECT 1 AS a")
    db = local.project(pid).database
    script = ("import duckdb, sys\n"
              "try:\n    duckdb.connect(sys.argv[1]); print('opened')\n"
              "except Exception as exc:\n    print('refused', exc)\n")
    out = subprocess.run([sys.executable, "-c", script, db], capture_output=True, text=True, timeout=60).stdout
    assert out.startswith("refused")
    # And this app reports it in words when it is the one locked out.
    local.close(pid)
    holder = subprocess.Popen([sys.executable, "-c", "import duckdb, sys, time\nc = duckdb.connect(sys.argv[1])\n"
                               "print('ready', flush=True)\ntime.sleep(30)", db], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ready"
        with pytest.raises(EngineBusy, match="open in another program"):
            LocalData(tmp_path / "app" / "local-data").engine(pid).execute("SELECT 1")
    finally:
        holder.kill()


def test_project_names_and_folders(local, tmp_path, data):
    local.create("Same")
    with pytest.raises(ValueError, match="already"):
        local.create("same")
    elsewhere = tmp_path / "repo" / "localdb"
    p = local.create("In my repo", folder=str(elsewhere))
    assert (elsewhere / "project.json").exists() and p["folder"] == str(elsewhere.resolve())
    with pytest.raises(ValueError, match="own database"):
        local.add_source(p["id"], str(tmp_path / "repo"))
    with pytest.raises(ValueError, match="isn't a folder"):
        local.add_source(p["id"], str(tmp_path / "nope"))
    local.forget(p["id"])
    assert elsewhere.exists() and [x["name"] for x in local.projects()] == ["Same"]


def test_app_calls(tmp_path, data):
    svc = Service(root=tmp_path / "app")
    assert {"local_query", "local_query_start", "list_local_tables", "local_add_source"} <= server.METHODS

    def call(method, **params):
        reply = server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
        assert "error" not in reply, reply
        return reply["result"]
    pid = call("local_create_project", name="RPC")["id"]
    call("local_add_source", project=pid, path=str(data))
    assert "dbo.orders" in table_names(call("list_local_tables", project=pid))
    r = call("local_query", project=pid, query="SELECT count(*) AS n FROM dbo.orders")
    assert r["rows"] == [[3]]
    # The audit trail notes the query ran, never its text.
    entries = svc.audit.recent(10, "local_query")
    assert entries and entries[0]["target"]["query"] == "(given)"
    svc.local_data.close_all()


def test_local_query_makes_the_managed_run_checks(tmp_path, data):
    """local_query runs in this process with no worker, so it classes the statements as sql_start
    does for target="local" before the engine sees them."""
    svc = Service(root=tmp_path / "app")
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\n42\n")

    def call(method, **params):
        return server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    pid = call("local_create_project", name="Checks")["result"]["id"]
    call("local_add_source", project=pid, path=str(data))
    for query in (f"SELECT * FROM read_csv('{outside}')", f"COPY dbo.orders TO '{tmp_path / 'out.csv'}'",
                  "SET enable_external_access = true", "INSTALL httpfs",
                  f"SELECT 1; SELECT * FROM '{outside}'"):
        reply = call("local_query", project=pid, query=query)
        assert "nothing ran" in reply.get("error", ""), (query, reply)
    assert not (tmp_path / "out.csv").exists()
    # T-SQL is checked as the DuckDB SQL it becomes.
    reply = call("local_query", project=pid, query=f"SELECT * FROM OPENROWSET(BULK '{outside}') AS r", dialect="tsql")
    assert "error" in reply
    assert call("local_query", project=pid, query="CREATE TABLE t AS SELECT 1 AS a")["result"]
    assert call("local_query", project=pid, query="INSERT INTO t VALUES (2)")["result"]
    reply = call("local_query", project=pid, query="DROP TABLE t")
    assert "needs confirming" in reply["error"]
    assert call("local_query", project=pid, query="SELECT count(*) FROM t")["result"]["rows"] == [[2]]
    assert "result" in call("local_query", project=pid, query="DROP TABLE t", confirm=True)
    # EXPLAIN ANALYZE runs the query, so local_explain checks it too.
    reply = call("local_explain", project=pid, query=f"SELECT * FROM read_csv('{outside}')", analyze=True)
    assert "nothing ran" in reply.get("error", ""), reply
    assert "result" in call("local_explain", project=pid, query="SELECT * FROM dbo.orders", analyze=True)
    svc.local_data.close_all()


def test_partitioned_delta_tables_keep_their_partition_columns(local, tmp_path):
    d = tmp_path / "lake"
    write_deltalake(str(d / "by_year"), pa.table({"year": [2025, 2026, 2026], "v": [1, 2, 3]}), partition_by=["year"])
    pid = local.create("Delta parts")["id"]
    local.add_source(pid, str(d))
    e = local.engine(pid)
    r = e.execute("SELECT year, sum(v)::INT FROM by_year GROUP BY year ORDER BY year")
    assert r["rows"] == [[2025, 1], [2026, 5]]
    assert r["columns"][0]["type"] == "int64"  # the Delta schema's type, not a string from the folder name
    entry = local.catalog(pid)["tables"][0]
    assert entry["delta"]["partitions"] == ["year"] and entry["delta"]["version"] == 0


def test_delta_features_a_file_read_would_get_wrong_are_refused(local, tmp_path):
    d = tmp_path / "lake"
    write_deltalake(str(d / "dv"), pa.table({"a": [1]}), configuration={"delta.enableDeletionVectors": "true"})
    pid = local.create("Delta dv")["id"]
    added = local.add_source(pid, str(d))
    entry = added["tables"][0]
    assert entry["status"] == "error" and "deletion vectors" in entry["error"]
    with pytest.raises(Exception):
        local.engine(pid).execute("SELECT * FROM dv")


def test_spills_to_disk_under_a_small_memory_limit(local):
    pid = local.create("Spill", settings={"memory_limit": "64MB", "threads": 1})["id"]
    r = local.engine(pid).execute(
        "SELECT count(*) FROM (SELECT range % 1000003 AS k, md5(range::VARCHAR) AS h FROM range(3000000) ORDER BY h)")
    assert r["rows"] == [[3000000]]


def test_streamed_results_have_no_row_limit_and_free_the_connection(local):
    e = local.engine(local.create("Stream")["id"])
    reader = e.execute_reader("CREATE TABLE s AS SELECT range AS n FROM range(300000); SELECT n FROM s",
                              batch_size=50000)
    assert sum(b.num_rows for b in reader) == 300000
    assert e.execute("SELECT count(*) FROM s")["rows"] == [[300000]]  # the connection is free again
    # Closed after one batch: the connection is freed too.
    reader = e.execute_reader("SELECT n FROM s", batch_size=1000)
    assert reader.read_next_batch().num_rows == 1000
    reader.close()
    assert e.execute("SELECT 1")["rows"] == [[1]]
    # A statement with no result streams nothing.
    assert e.execute_reader("CREATE TABLE t2 (a INT)").read_all().num_rows == 0
    assert e.execute("SELECT count(*) FROM t2")["rows"] == [[0]]


def test_a_stream_can_be_stopped(local):
    e = local.engine(local.create("Stream stop")["id"])
    reader = e.execute_reader("SELECT range AS n FROM range(10000000000)", query_id="q1", batch_size=1000)
    reader.read_next_batch()
    assert e.cancel("q1")
    with pytest.raises(QueryCancelled):  # pyarrow's reader reports an interrupt mid-stream as OSError
        for _ in reader:
            pass
    assert e.execute("SELECT 1")["rows"] == [[1]]


def test_a_stream_past_its_time_limit_says_so(local):
    e = local.engine(local.create("Stream limit")["id"])
    reader = e.execute_reader("SELECT range AS n FROM range(10000000000)", timeout=0.5, batch_size=1000)
    reader.read_next_batch()
    with pytest.raises(TimeoutError, match="longer than 0.5 seconds"):
        for _ in reader:
            pass
    assert e.execute("SELECT 1")["rows"] == [[1]]


@pytest.mark.parametrize("stream", [False, True], ids=["whole result", "stream"])
def test_a_query_duckdb_stops_by_itself_is_not_called_cancelled(local, stream):
    """When one of DuckDB's threads fails it stops the others, and now and then reports that stop in
    place of the failure. Nobody pressed Stop, so the person is told to run it again for the error,
    not that it was cancelled (a whole result) or "INTERRUPT Error" (a stream, through pyarrow)."""
    e = local.engine(local.create("Stopped alone")["id"])
    e.execute("SELECT 1")  # opens the connection
    threading.Timer(0.3, e._con.interrupt).start()  # as DuckDB stops itself: no cancel, no time limit
    with pytest.raises(RuntimeError, match="part of the query failed") as caught:
        if stream:
            for _ in e.execute_reader("SELECT range AS n FROM range(10000000000)", batch_size=1000):
                pass
        else:
            e.execute("SELECT count(*) FROM range(10000000000) a")
    assert not isinstance(caught.value, QueryCancelled)
    assert e.execute("SELECT 1")["rows"] == [[1]]


def test_a_stream_stops_on_cancel_even_when_duckdb_misses_the_interrupt(local):
    # #1340: the stream checks the cancel flag between batches as well as DuckDB's interrupt.
    e = local.engine(local.create("Stream flag")["id"])
    reader = e.execute_reader("SELECT range AS n FROM range(10000000000)", query_id="q2", batch_size=1000)
    reader.read_next_batch()
    e._running["q2"]["cancelled"] = True  # as cancel() sets it, without the interrupt
    with pytest.raises(QueryCancelled):
        for _ in reader:
            pass
    assert e.execute("SELECT 1")["rows"] == [[1]]


def test_closing_a_stream_inside_a_with_block_frees_the_connection(local):
    e = local.engine(local.create("Stream with")["id"])
    with e.execute_reader("SELECT range AS n FROM range(1000000)", batch_size=100) as stream:
        assert stream.read_next_batch().num_rows == 100
    assert not e._lock.locked()
    assert pa.RecordBatchReader.from_stream(e.execute_reader("SELECT 1 AS a")).read_all().num_rows == 1


def test_a_cancel_that_arrives_before_the_query_starts_still_stops_it(local):
    e = local.engine(local.create("Early cancel")["id"])
    assert e.cancel("early", not_started=True)
    with pytest.raises(QueryCancelled):
        e.execute(SLOW, query_id="early")
    assert e.execute("SELECT 1")["rows"] == [[1]]
    # Without not_started, an unknown query is simply not running.
    assert not e.cancel("nobody")


def test_cancel_right_after_start_never_leaves_it_running(local):
    pid = local.create("Race")["id"]
    for _ in range(5):  # cancel with no pause, the case a slow runner hit
        started = local.start(pid, SLOW)
        assert local.cancel(started["query_id"]) == {"cancelled": True}
        for _ in range(200):
            status = local.status(started["query_id"])
            if status["state"] != "running":
                break
            time.sleep(0.05)
        assert status["state"] == "cancelled"


def test_the_engine_itself_reaches_only_the_projects_folders(local, tmp_path, data):
    """Behind the statement checks (sql_exec.check_local): DuckDB itself is locked to the project's
    folder and its sources, so SQL that reaches the engine without those checks still can't read
    or write anything else, attach another database, install an extension or undo the lock."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.csv").write_text("s\n42\n")
    pid = local.create("Locked")["id"]
    local.add_source(pid, str(data))
    engine = local.engine(pid)
    assert engine.execute("SELECT count(*) FROM dbo.orders")["rows"] == [[3]]
    assert engine.execute(f"SELECT count(*) FROM read_csv('{data / 'Sales' / 'Customer.csv'}')")["rows"] == [[3]]
    for sql in (f"SELECT * FROM read_csv('{outside / 'secret.csv'}')",
                f"COPY (SELECT 1 AS a) TO '{outside / 'out.csv'}'",
                f"ATTACH '{outside / 'other.duckdb'}' AS other",
                "SELECT * FROM read_parquet('https://example.com/x.parquet')",
                "INSTALL httpfs",
                "SET enable_external_access = true",
                f"SET allowed_directories = ['{outside}']"):
        with pytest.raises(Exception):
            engine.execute(sql)
    assert sorted(p.name for p in outside.iterdir()) == ["secret.csv"]
    # The project's own folder still works: its tables, and files written inside it.
    engine.execute("CREATE TABLE kept AS SELECT 1 AS a")
    engine.execute(f"COPY kept TO '{engine.project.root / 'tmp' / 'kept.csv'}'")
    # A folder added later is reachable: the connection is opened again with it, keeping tables.
    engine.execute("CREATE TEMP TABLE scratch AS SELECT 1 AS a")
    local.add_source(pid, str(outside))
    assert engine.execute(f"SELECT * FROM read_csv('{outside / 'secret.csv'}')")["rows"] == [[42]]
    assert engine.execute("SELECT a FROM kept")["rows"] == [[1]]
    with pytest.raises(Exception):
        engine.execute("SELECT * FROM scratch")  # temporary tables don't survive the new connection
    engine.remove_source(str(outside.resolve()))
    with pytest.raises(Exception):
        engine.execute(f"SELECT * FROM read_csv('{outside / 'secret.csv'}')")



def test_a_reader_that_fails_part_way_is_still_closed(local):
    """On DuckDB 2.0 a reader left open keeps its connection busy, so it is closed even when reading fails."""
    from fabric_desktop.localdata import engine as local_engine
    closed = []

    class Reader:
        schema = None

        def __iter__(self):
            raise RuntimeError("late failure")

        def close(self):
            closed.append(True)

    result = SimpleNamespace(description=[("n",)], to_arrow_reader=lambda rows: Reader())
    with pytest.raises(RuntimeError, match="late failure"):
        local_engine._collect(result, 10, True)
    assert closed == [True]
    e = local.engine(local.create("Fails late")["id"])
    # DuckDB reports its own stop in place of the failure now and then (about 1 run in 100 under load).
    with pytest.raises(Exception, match="late failure|part of the query failed"):
        e.execute("SELECT CASE WHEN range < 200000 THEN range ELSE error('late failure') END AS n "
                  "FROM range(250000)", limit=10)
    assert e.execute("SELECT 42 AS answer")["rows"] == [[42]]
