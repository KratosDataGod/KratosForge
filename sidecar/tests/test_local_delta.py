"""Local Delta tables (FD-LDE-005): written from SQL on this PC, read as they were, Fabric-safe."""

import json
import time

import duckdb
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_profile, server
from fabric_desktop.localdata import LocalData, QueryCancelled
from fabric_desktop.service import Service

SALES = ("CREATE TABLE sales USING DELTA AS "
         "SELECT i AS id, 'r' || (i % 3) AS region, i * 1.5 AS amount FROM range(10) t(i)")


@pytest.fixture
def local(tmp_path):
    return LocalData(tmp_path / "app" / "local-data")


@pytest.fixture
def engine(local):
    return local.engine(local.create("Delta")["id"])


def folder(engine, table="sales", schema="dbo"):
    return engine.tables_dir / schema / table


def rows(engine, sql, dialect="duckdb"):
    return engine.execute(sql, dialect=dialect)["rows"]


def test_create_a_delta_table_from_sql_and_query_it(engine):
    r = engine.execute(SALES)
    assert r["columns"][0]["name"] == "table" and r["rows"] == [["dbo.sales", "create", 0, 10]]
    assert r["statements"][0]["type"] == "write delta"
    table = DeltaTable(str(folder(engine)))
    assert table.version() == 0 and table.to_pyarrow_table().num_rows == 10
    assert rows(engine, "SELECT count(*), sum(amount)::INT FROM sales") == [[10, 68]]
    assert rows(engine, "SELECT count(*) FROM dbo.sales WHERE region = 'r0'") == [[4]]
    listed = {t["table"]: t for t in engine.tables()}
    assert listed["sales"]["kind"] == "delta table" and listed["sales"]["delta"]["version"] == 0
    assert listed["sales"]["files"] >= 1 and listed["sales"]["bytes"] > 0


def test_written_tables_stay_in_the_fabric_safe_profile(engine):
    engine.execute("CREATE TABLE typed USING DELTA AS SELECT TIMESTAMP '2026-10-01 12:30:00' AS ts, "
                   "200::UTINYINT AS small, 4000000000::UINTEGER AS medium, 18446744073709551615::UBIGINT AS big, "
                   "[1, 2] AS list, {'a': 1} AS rec, DATE '2026-10-01' AS d, 12.34::DECIMAL(10, 2) AS money")
    table = DeltaTable(str(folder(engine, "typed")))
    delta_profile.check_table(table)  # reader 1, writer 2, no table features
    protocol = table.protocol()
    assert (protocol.min_reader_version, protocol.min_writer_version) == (1, 2)
    schema = pa.schema(table.schema().to_arrow())
    assert schema.field("ts").type == pa.timestamp("us", tz="UTC")  # not timestamp_ntz (reader 3)
    assert schema.field("small").type == pa.int16() and schema.field("medium").type == pa.int64()
    assert schema.field("big").type == pa.decimal128(20, 0)
    got = table.to_pyarrow_table().to_pylist()[0]
    assert got["small"] == 200 and got["medium"] == 4000000000 and int(got["big"]) == 18446744073709551615
    assert got["ts"].isoformat() == "2026-10-01T12:30:00+00:00"  # the session's time zone is UTC


@pytest.mark.parametrize("sql, problem", [
    ("SELECT TIME '10:00' AS t", "TIME"),
    ("SELECT INTERVAL 1 DAY AS i", "INTERVAL"),
    ('SELECT 1 AS "order date"', "space"),
    ('SELECT 1 AS a, 2 AS "A"', "differ only in case"),
])
def test_types_and_names_fabric_cant_read_are_refused_before_anything_is_written(engine, sql, problem):
    with pytest.raises(ValueError, match=problem):
        engine.execute(f"CREATE TABLE bad USING DELTA AS {sql}")
    assert not folder(engine, "bad").exists()


def test_insert_appends_and_insert_overwrite_replaces(engine):
    engine.execute(SALES)
    assert rows(engine, "INSERT INTO sales SELECT 100, 'r9', 1.0") == [["dbo.sales", "append", 1, 1]]
    assert rows(engine, "INSERT INTO sales BY NAME SELECT 2.0 AS amount, 101 AS id, 'r9' AS region")[0][2] == 2
    assert rows(engine, "SELECT count(*) FROM sales") == [[12]]
    assert rows(engine, "INSERT OVERWRITE sales SELECT * FROM sales WHERE region = 'r9'")[0][1:] == \
        ["overwrite", 3, 2]
    assert rows(engine, "SELECT count(*) FROM sales") == [[2]]
    # Spark SQL, as in a Fabric notebook.
    engine.execute("INSERT INTO TABLE sales SELECT 102, 'r9', 3.0", dialect="spark")
    assert rows(engine, "SELECT max(id) FROM sales") == [[102]]


def test_time_travel_in_both_dialects(engine):
    engine.execute(SALES)
    time.sleep(0.05)
    engine.execute("INSERT INTO sales SELECT 100, 'r9', 1.0")
    engine.execute("INSERT OVERWRITE sales SELECT * FROM sales WHERE id >= 100")
    assert rows(engine, "SELECT count(*) FROM sales") == [[1]]
    assert rows(engine, "SELECT count(*) FROM sales AT (VERSION => 0)") == [[10]]
    assert rows(engine, "SELECT count(*) FROM dbo.sales VERSION AS OF 1", dialect="spark") == [[11]]
    # Two versions of one table side by side, each with its own alias.
    assert rows(engine, "SELECT count(*) FROM sales v0 AT (VERSION => 0) JOIN sales v1 AT (VERSION => 1) "
                        "USING (id)") == [[10]]
    history = engine.delta_history("sales")
    first = history["versions"][-1]
    assert [v["version"] for v in history["versions"]] == [2, 1, 0]
    assert history["versions"][0]["mode"] == "Overwrite" and first["rows_added"] == 10
    assert first["timestamp"].endswith("Z")
    assert rows(engine, f"SELECT count(*) FROM sales TIMESTAMP AS OF '{first['timestamp']}'", dialect="spark") == \
        [[10]]
    with pytest.raises(ValueError, match="no version 9"):
        engine.execute("SELECT * FROM sales AT (VERSION => 9)")
    with pytest.raises(ValueError, match="no version as of"):
        engine.execute("SELECT * FROM sales AT (TIMESTAMP => '2001-01-01')")
    # Restoring an old version is an overwrite from it, and is a new version itself.
    engine.execute("INSERT OVERWRITE sales SELECT * FROM sales AT (VERSION => 0)")
    assert rows(engine, "SELECT count(*) FROM sales") == [[10]] and engine.delta_history("sales")["version"] == 3
    # Placeholder views don't outlive the query.
    assert not [t for t in engine.tables() if t["table"].startswith("__fd_")]


def test_create_if_not_exists_or_replace(engine):
    engine.execute(SALES)
    with pytest.raises(ValueError, match="already exists"):
        engine.execute(SALES)
    assert rows(engine, SALES.replace("TABLE", "TABLE IF NOT EXISTS"))[0][1] == "none"
    assert DeltaTable(str(folder(engine))).version() == 0
    engine.execute("CREATE OR REPLACE TABLE sales USING DELTA PARTITIONED BY (region) AS "
                   "SELECT 'x' AS region, 1 AS n")
    assert rows(engine, "SELECT * FROM sales") == [["x", 1]]
    assert DeltaTable(str(folder(engine))).metadata().partition_columns == ["region"]
    assert rows(engine, "SELECT count(*) FROM sales AT (VERSION => 0)") == [[10]]  # still there


def test_a_schema_change_needs_saying_so(engine):
    engine.execute(SALES)
    with pytest.raises(ValueError, match="columns don't match dbo.sales: extra is new"):
        engine.write_delta("sales", "SELECT 1 AS id, 'r' AS region, 1.0 AS amount, 'e' AS extra", mode="append")
    with pytest.raises(ValueError, match="region is int32, the table has string"):
        engine.write_delta("sales", "SELECT 1 AS id, 2 AS region, 1.0 AS amount", mode="append")
    with pytest.raises(ValueError, match="returns 2 columns and dbo.sales has 3"):
        engine.execute("INSERT INTO sales SELECT 1, 'r'")
    r = engine.write_delta("sales", "SELECT 1 AS id, 'r' AS region, 1.0 AS amount, 'e' AS extra", mode="append",
                           schema_mode="merge")
    assert r["schema_change"] == ["extra is new"] and r["version"] == 1
    assert rows(engine, "SELECT count(*), count(extra) FROM sales") == [[11, 1]]
    with pytest.raises(ValueError, match="partitioned by"):
        engine.write_delta("sales", "SELECT * FROM sales", mode="append", partition_by=["region"])
    with pytest.raises(ValueError, match="goes with mode overwrite"):
        engine.write_delta("sales", "SELECT 1 AS x", mode="append", schema_mode="overwrite")
    engine.write_delta("sales", "SELECT 1 AS x", mode="overwrite", schema_mode="overwrite")
    assert rows(engine, "SELECT * FROM sales") == [[1]]


def test_a_failed_write_commits_nothing(engine):
    engine.execute("CREATE TABLE s USING DELTA AS SELECT 1::SMALLINT AS n")
    with pytest.raises(ValueError, match="nothing was committed: a value in column n doesn't fit"):
        engine.execute("INSERT INTO s SELECT 70000")
    with pytest.raises((ValueError, duckdb.Error), match="boom"):
        engine.execute("INSERT INTO s SELECT CASE WHEN i < 5 THEN i ELSE error('boom') END FROM range(10) t(i)")
    assert DeltaTable(str(folder(engine, "s"))).version() == 0 and rows(engine, "SELECT count(*) FROM s") == [[1]]
    # A table that was being created leaves no folder behind.
    with pytest.raises((ValueError, duckdb.Error)):
        engine.execute("CREATE TABLE gone USING DELTA AS SELECT CASE WHEN i < 5 THEN i ELSE error('boom') END AS v "
                       "FROM range(10) t(i)")
    assert not folder(engine, "gone").exists() and "gone" not in {t["table"] for t in engine.tables()}


def test_a_write_can_be_stopped_and_commits_nothing(local, engine):
    engine.execute("CREATE TABLE big USING DELTA AS SELECT 1 AS n")
    slow = "INSERT INTO big SELECT count(*)::INT FROM range(100000000) a(x), range(1000) b(y) WHERE x * y % 7 = 3"
    with pytest.raises(TimeoutError):
        engine.execute(slow, timeout=0.5)
    started = local.start(engine.project.id, slow)
    time.sleep(0.3)
    assert local.cancel(started["query_id"])["cancelled"] is True
    deadline = time.time() + 20
    while (status := local.status(started["query_id"]))["state"] == "running" and time.time() < deadline:
        time.sleep(0.1)
    assert status["state"] == "cancelled", status
    assert DeltaTable(str(folder(engine, "big"))).version() == 0


def test_a_dry_run_writes_nothing(engine):
    plan = engine.write_delta("planned", "SELECT i AS id FROM range(5) t(i)", dry_run=True)
    assert plan["dry_run"] and plan["would_create"] and plan["rows"] == 5 and plan["version"] is None
    assert not folder(engine, "planned").exists() and "planned" not in {t["table"] for t in engine.tables()}
    engine.execute(SALES)
    plan = engine.write_delta("sales", "SELECT * FROM sales", mode="append", dry_run=True)
    assert plan["version"] == 1 and plan["rows"] == 10 and DeltaTable(str(folder(engine))).version() == 0
    with pytest.raises(ValueError, match="one query"):
        engine.write_delta("x", "CREATE TABLE t AS SELECT 1; SELECT * FROM t", dry_run=True)
    assert rows(engine, "SELECT count(*) FROM duckdb_tables() WHERE table_name = 't'") == [[0]]


def test_registered_folders_are_only_read(local, engine, tmp_path):
    outside = tmp_path / "files"
    write_deltalake(str(outside / "theirs"), pa.table({"id": [1, 2]}))
    write_deltalake(str(outside / "theirs"), pa.table({"id": [3]}), mode="append")
    local.add_source(engine.project.id, str(outside))
    with pytest.raises(ValueError, match="registered folders are only read"):
        engine.write_delta("theirs", "SELECT 9 AS id", mode="append")
    with pytest.raises(Exception):
        engine.execute("INSERT INTO theirs SELECT 9")  # a view: DuckDB refuses it
    assert DeltaTable(str(outside / "theirs")).version() == 1
    # They can be read as they were, and copied into the project.
    assert rows(engine, "SELECT count(*) FROM theirs AT (VERSION => 0)") == [[2]]
    engine.execute("CREATE TABLE mine USING DELTA AS SELECT * FROM theirs")
    assert rows(engine, "SELECT count(*) FROM mine") == [[3]]
    with pytest.raises(ValueError, match="own database and Delta tables"):
        local.add_source(engine.project.id, str(engine.tables_dir))


def test_names_must_be_lakehouse_names(engine):
    for bad in ("../escape", "a b", "dbo.__fd_x", "1st"):
        with pytest.raises(ValueError, match="can't be a Delta"):
            engine.write_delta(bad, "SELECT 1 AS a")
    engine.execute("CREATE TABLE made AS SELECT 1 AS a")  # a DuckDB table
    with pytest.raises(ValueError, match="already a table or view"):
        engine.execute("CREATE TABLE made USING DELTA AS SELECT 1 AS a")
    engine.execute("CREATE SCHEMA Sales")
    engine.execute("CREATE TABLE Sales.orders USING DELTA AS SELECT 1 AS a")
    assert (engine.tables_dir / "Sales" / "orders" / "_delta_log").is_dir()
    assert rows(engine, "SELECT a FROM sales.orders") == [[1]]


def test_dropping_needs_confirming_and_deletes_only_the_table(engine):
    engine.execute(SALES)
    engine.execute("CREATE TABLE keep USING DELTA AS SELECT 1 AS a")
    with pytest.raises(ValueError, match="done as its own step"):
        engine.execute("DROP TABLE sales")
    with pytest.raises(ValueError, match="done as its own step"):
        engine.execute("DROP VIEW IF EXISTS dbo.sales")
    preview = engine.drop_delta("sales")
    assert preview["confirm_required"] and not preview["dropped"] and folder(engine).exists()
    assert engine.drop_delta("sales", confirm=True)["dropped"]
    assert not folder(engine).exists() and folder(engine, "keep").exists()
    assert {t["table"] for t in engine.tables()} == {"keep"}
    with pytest.raises(ValueError, match="isn't one of"):
        engine.drop_delta("sales", confirm=True)


@pytest.mark.parametrize("storage", ["persistent", "memory"])
def test_tables_come_back_after_a_restart_and_follow_the_folder(tmp_path, storage):
    root = tmp_path / "app" / "local-data"
    first = LocalData(root)
    pid = first.create("Again", storage=storage)["id"]
    first.engine(pid).execute(SALES)
    first.engine(pid).execute("CREATE TABLE other USING DELTA AS SELECT 1 AS a")
    first.close_all()
    again = LocalData(root).engine(pid)
    assert rows(again, "SELECT count(*) FROM sales") == [[10]]
    # A table folder deleted while it was closed is gone from the project too.
    import shutil
    shutil.rmtree(folder(again, "other"))
    again.refresh()
    assert "other" not in {t["table"] for t in again.tables()}


def test_app_calls_for_delta_tables(tmp_path):
    svc = Service(root=tmp_path / "app")
    assert {"local_write_delta", "local_delta_history", "local_drop_delta"} <= server.METHODS

    def call(method, **params):
        reply = server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
        assert "error" not in reply, reply
        return reply["result"]
    pid = call("local_create_project", name="RPC")["id"]
    r = call("local_write_delta", project=pid, table="dbo.t", query="SELECT 1 AS a", mode="create")
    assert r["version"] == 0 and r["rows"] == 1
    call("local_query", project=pid, query="INSERT INTO t SELECT 2")
    assert [v["version"] for v in call("local_delta_history", project=pid, table="t")["versions"]] == [1, 0]
    assert call("local_drop_delta", project=pid, table="t")["confirm_required"]
    # The audit trail notes the write and the drop, never the query's text; reading history isn't noted.
    writes = svc.audit.recent(10, "local_write_delta")
    assert writes and writes[0]["target"]["query"] == "(given)"
    assert not svc.audit.recent(10, "local_delta_history") and svc.audit.recent(10, "local_drop_delta")
    svc.local_data.close_all()


def test_queries_without_delta_still_take_the_plain_path(engine):
    # Scripts DuckDB parses on its own are run as before, statement by statement.
    r = engine.execute("CREATE TEMP TABLE x AS SELECT 1 AS a; INSERT INTO x VALUES (2); SELECT sum(a)::INT FROM x")
    assert r["rows"] == [[3]] and [s["type"] for s in r["statements"]] == ["create", "insert", "select"]
    with pytest.raises(ValueError, match="syntax error"):
        engine.execute("SELEC 1")
    with pytest.raises(ValueError, match="syntax error"):
        engine.execute("CREATE TABLE ok USING DELTA AS SELECT 1 AS a; SELEC 2")
    assert not folder(engine, "ok").exists()  # the script never started
    with pytest.raises(QueryCancelled):
        engine.cancel("q1", not_started=True)
        engine.execute("SELECT 1", query_id="q1")
