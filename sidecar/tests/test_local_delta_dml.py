"""MERGE INTO, UPDATE and DELETE on a project's Delta tables (FD-LDE-011, local part)."""

import json

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_dml, delta_profile, server
from fabric_desktop.localdata import LocalData
from fabric_desktop.service import Service


@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.fixture
def engine(local):
    e = local.engine(local.create("DML")["id"])
    e.execute("CREATE TABLE customers USING DELTA AS SELECT * FROM (VALUES (1, 'Ann', 'EU', 10.0), "
              "(2, 'Bo', 'US', 20.0), (3, 'Cy', 'EU', 30.0)) t(Id, Name, Region, Spend)")
    e.execute("CREATE TABLE updates AS SELECT * FROM (VALUES (2, 'Bob', 'US', 25.0, false), "
              "(3, 'Cy', 'EU', 0.0, true), (4, 'Di', 'APAC', 40.0, false)) t(id, name, region, spend, gone)")
    return e


def table(engine, name="customers"):
    return DeltaTable(str(engine.tables_dir / "dbo" / name))


def rows(engine, sql="SELECT Id, Name, Region, Spend::DOUBLE FROM customers ORDER BY Id"):
    return engine.execute(sql)["rows"]


MERGE = ("MERGE INTO customers AS t USING updates AS s ON t.id = s.id "
         "WHEN MATCHED AND s.gone THEN DELETE "
         "WHEN MATCHED THEN UPDATE SET name = s.name, spend = s.spend "
         "WHEN NOT MATCHED THEN INSERT (id, name, region, spend) VALUES (s.id, s.name, s.region, s.spend)")


@pytest.mark.parametrize("dialect", ["duckdb", "spark"])
def test_merge_updates_deletes_and_inserts_in_one_commit(engine, dialect):
    r = engine.execute(MERGE, dialect=dialect)
    assert r["columns"][1]["name"] == "operation" and r["statements"][0]["type"] == "merge delta"
    assert r["rows"] == [["dbo.customers", "merge", 1, 1, 1, 1]]
    assert rows(engine) == [[1, "Ann", "EU", 10.0], [2, "Bob", "US", 25.0], [4, "Di", "APAC", 40.0]]
    t = table(engine)
    assert t.version() == 1 and t.history(1)[0]["operation"] == "MERGE"
    delta_profile.check_table(t)  # still reader 1, writer 2, no features


def test_merge_star_clauses_and_not_matched_by_source(engine):
    engine.execute("CREATE TABLE full_load AS SELECT * FROM (VALUES (1, 'Ann', 'EU', 11.0), (5, 'Ed', 'US', 5.0)) "
                   "t(id, name, region, spend)")
    r = engine.change_delta("MERGE INTO customers t USING full_load s ON t.Id = s.id "
                            "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT * "
                            "WHEN NOT MATCHED BY SOURCE THEN DELETE", dialect="spark")
    assert (r["rows_updated"], r["rows_inserted"], r["rows_deleted"]) == (1, 1, 2)
    assert rows(engine) == [[1, "Ann", "EU", 11.0], [5, "Ed", "US", 5.0]]


def test_update_and_delete_with_where(engine):
    r = engine.execute("UPDATE customers SET spend = spend * 2 WHERE region = 'EU'")
    assert r["rows"] == [["dbo.customers", "update", 1, 0, 2, 0]]
    engine.execute("DELETE FROM dbo.customers WHERE spend > 50", dialect="spark")
    assert rows(engine) == [[1, "Ann", "EU", 20.0], [2, "Bo", "US", 20.0]]
    assert table(engine).version() == 2


def test_a_dry_run_counts_exactly_and_changes_nothing(engine):
    before = sorted(p.name for p in (engine.tables_dir / "dbo" / "customers").rglob("*"))
    r = engine.change_delta(MERGE, dry_run=True)
    assert r["dry_run"] and (r["rows_inserted"], r["rows_updated"], r["rows_deleted"]) == (1, 1, 1)
    assert r["version"] == 1
    assert table(engine).version() == 0 and rows(engine)[1] == [2, "Bo", "US", 20.0]
    assert sorted(p.name for p in (engine.tables_dir / "dbo" / "customers").rglob("*")) == before
    assert not list((engine.project.temp_dir / "dry-run").glob("*"))  # the copy is gone
    assert engine.change_delta("DELETE FROM customers WHERE id < 3", dry_run=True)["rows_deleted"] == 2
    assert table(engine).version() == 0


def test_every_row_needs_confirm(engine):
    for sql in ("DELETE FROM customers", "UPDATE customers SET spend = 0"):
        with pytest.raises(ValueError, match="changes every row"):
            engine.execute(sql)
        with pytest.raises(ValueError, match="changes every row"):
            engine.change_delta(sql)
    assert table(engine).version() == 0
    assert engine.change_delta("DELETE FROM customers", dry_run=True, confirm=True)["rows_deleted"] == 3
    assert engine.change_delta("DELETE FROM customers", confirm=True)["rows_deleted"] == 3
    assert rows(engine) == []


def test_two_source_rows_for_one_table_row_are_refused(engine):
    engine.execute("CREATE TABLE twice AS SELECT * FROM (VALUES (1, 'A1'), (1, 'A2')) t(id, name)")
    with pytest.raises(ValueError, match="More than one source row"):
        engine.execute("MERGE INTO customers t USING twice s ON t.id = s.id WHEN MATCHED THEN UPDATE SET name = s.name")
    assert table(engine).version() == 0
    # Inserting only (no WHEN MATCHED) is allowed, as in Spark.
    engine.execute("MERGE INTO customers t USING (SELECT 9 AS id, 'Z' AS name) s ON t.id = s.id "
                   "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (s.id, s.name)")
    assert rows(engine, "SELECT Name, Region FROM customers WHERE Id = 9") == [["Z", None]]


def test_merge_source_is_a_query_and_can_read_an_old_version(engine):
    engine.execute("UPDATE customers SET spend = 0 WHERE id = 1")
    engine.execute("MERGE INTO customers t USING (SELECT * FROM customers VERSION AS OF 0 WHERE id = 1) s "
                   "ON t.id = s.id WHEN MATCHED THEN UPDATE SET spend = s.spend", dialect="spark")
    assert rows(engine)[0] == [1, "Ann", "EU", 10.0]


def test_ordinary_tables_and_registered_folders(engine, local, tmp_path):
    # A table people made in the database is DuckDB's own business.
    engine.execute("UPDATE updates SET spend = 1 WHERE id = 4")
    assert rows(engine, "SELECT spend::DOUBLE FROM updates WHERE id = 4") == [[1.0]]
    # A Delta table in a registered folder is only read.
    src = tmp_path / "data"
    write_deltalake(str(src / "theirs"), pa.table({"a": [1, 2]}))
    local.add_source(engine.project.id, str(src))
    with pytest.raises(ValueError, match="only read"):
        engine.execute("DELETE FROM theirs WHERE a = 1")
    assert DeltaTable(str(src / "theirs")).version() == 0


def test_refusals_say_what_to_do(engine):
    with pytest.raises(ValueError, match="subquery"):
        engine.execute("UPDATE customers SET spend = 0 WHERE id IN (SELECT id FROM updates)")
    with pytest.raises(ValueError, match="no column nope"):
        engine.execute("UPDATE customers SET nope = 1 WHERE id = 1")
    with pytest.raises(ValueError, match="take every column"):
        engine.execute("MERGE INTO customers t USING (SELECT 1 AS id) s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *")
    with pytest.raises(ValueError, match="UPDATE ... FROM"):
        engine.execute("UPDATE customers SET spend = u.spend FROM updates u WHERE customers.id = u.id")
    with pytest.raises(ValueError, match="one MERGE INTO, UPDATE or DELETE"):
        engine.change_delta("SELECT 1")
    with pytest.raises(ValueError, match="one MERGE INTO, UPDATE or DELETE"):
        engine.change_delta("DELETE FROM customers WHERE id = 1; DELETE FROM customers WHERE id = 2")
    assert table(engine).version() == 0


def test_the_merge_source_only_ever_reads(engine):
    with pytest.raises(ValueError, match="one query"):
        engine.execute("MERGE INTO customers t USING (WITH c AS (SELECT 1) DELETE FROM updates) s ON t.id = s.id "
                       "WHEN MATCHED THEN DELETE")
    assert rows(engine, "SELECT count(*) FROM updates") == [[3]]


def test_parse_reads_spark_merge_shapes():
    c = delta_dml.parse("-- note\nMERGE INTO dbo.t USING (SELECT * FROM s) AS src ON t.a = src.a AND t.b = src.b "
                        "WHEN NOT MATCHED BY TARGET THEN INSERT * WHEN NOT MATCHED BY SOURCE AND t.x > 1 THEN UPDATE "
                        "SET x = 0")
    assert (c.kind, c.schema, c.table, c.alias, c.source_alias) == ("merge", "dbo", "t", "t", "src")
    assert [(w.match, w.action) for w in c.whens] == [("not_matched", "insert_all"), ("not_matched_by_source", "update")]
    assert delta_dml.equi_keys(c) == [("a", "a"), ("b", "b")] or delta_dml.equi_keys(c) == [("b", "b"), ("a", "a")]
    assert delta_dml.parse("SELECT 1") is None
    assert delta_dml.parse("DELETE FROM t").whole_table


def test_app_call_for_delta_changes(tmp_path):
    svc = Service(root=tmp_path / "app")
    assert "local_change_delta" in server.METHODS

    def call(method, **params):
        reply = server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
        assert "error" not in reply, reply
        return reply["result"]
    pid = call("local_create_project", name="RPC")["id"]
    call("local_write_delta", project=pid, table="t", query="SELECT 1 AS a", mode="create")
    assert call("local_change_delta", project=pid, query="DELETE FROM t WHERE a = 1", dry_run=True)["rows_deleted"] == 1
    assert call("local_change_delta", project=pid, query="UPDATE t SET a = 2", confirm=True)["rows_updated"] == 1
    changes = svc.audit.recent(10, "local_change_delta")
    assert len(changes) == 2 and changes[0]["target"]["query"] == "(given)"  # never the SQL text
    svc.local_data.close_all()
