"""A local data project's script runs its statements in order, each read against the project's tables just
before it runs, so a statement can write to a Delta table an earlier one made. And a DuckDB SQL MERGE, UPDATE
or DELETE followed by a comment after its ";" takes the Delta path on DuckDB 1.5 and 2.0 alike (2.0 keeps
that comment in the statement's text)."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import delta_dml
from fabric_desktop.localdata import LocalData


@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.fixture
def engine(local):
    e = local.engine(local.create("Scripts")["id"])
    e.execute("CREATE TABLE dbo.orders AS SELECT * FROM (VALUES (1, DATE '2024-05-01', 10.0), "
              "(2, DATE '2025-02-01', 20.0)) v(id, order_date, amount)")
    e.execute("CREATE TABLE dbo.new_orders AS SELECT * FROM (VALUES (3, DATE '2025-03-01', 30.0, 2025)) "
              "v(id, order_date, amount, year)")
    return e


def _ids(engine, table: str) -> list[int]:
    return sorted(r[0] for r in engine.execute(f"SELECT id FROM {table}")["rows"])


SALES_SCRIPT = """-- Create one
CREATE TABLE sales USING DELTA PARTITIONED BY (year) AS
SELECT *, year(order_date) AS year FROM dbo.orders;

INSERT INTO sales SELECT * FROM dbo.new_orders;      -- add rows
INSERT INTO sales BY NAME SELECT year, amount, order_date, id FROM dbo.new_orders;  -- by name
INSERT OVERWRITE sales SELECT * FROM sales WHERE year >= 2025;  -- replace every row
"""


def test_a_script_writes_to_the_delta_table_it_made(engine):
    out = engine.execute(SALES_SCRIPT)
    assert [s["type"] for s in out["statements"]][:4] == ["write delta"] * 4
    assert _ids(engine, "sales") == [2, 3, 3]


def test_a_spark_script_writes_to_the_delta_table_it_made(engine):
    engine.execute("CREATE TABLE sales USING DELTA AS SELECT id, amount FROM dbo.orders;\n"
                   "INSERT INTO sales SELECT id, amount FROM dbo.new_orders;\n"
                   "INSERT OVERWRITE sales SELECT * FROM sales WHERE id > 1;  -- keep 2 and 3\n", dialect="spark")
    assert _ids(engine, "sales") == [2, 3]


def test_a_script_changes_the_delta_table_it_made(engine):
    engine.execute("CREATE TABLE t USING DELTA AS SELECT * FROM (VALUES (1, 'a'), (2, 'b')) v(id, name);\n"
                   "UPDATE t SET name = 'z' WHERE id = 2;  -- a comment after the ;\n"
                   "DELETE FROM t WHERE id = 1;\n")
    assert engine.execute("SELECT id, name FROM t")["rows"] == [[2, "z"]]


def test_the_last_statement_s_rows_come_back(engine):
    out = engine.execute("CREATE TABLE t USING DELTA AS SELECT 1 AS id; INSERT INTO t SELECT 2; "
                         "SELECT count(*) AS n FROM t")
    assert out["rows"] == [[2]]


def test_versions_read_by_later_statements_are_numbered_across_the_script(engine):
    out = engine.execute("CREATE TABLE t USING DELTA AS SELECT 1 AS id; INSERT INTO t SELECT 2; "
                         "SELECT (SELECT count(*) FROM t AT (VERSION => 0)) + "
                         "10 * (SELECT count(*) FROM t AT (VERSION => 1)) AS n")
    assert out["rows"] == [[21]]


def test_a_failing_later_statement_stops_the_script_and_keeps_what_ran(engine):
    with pytest.raises(duckdb.Error, match="no_such_table"):
        engine.execute("CREATE TABLE t USING DELTA AS SELECT 1 AS id; INSERT INTO t SELECT 2; "
                       "SELECT * FROM no_such_table; INSERT INTO t SELECT 3")
    assert _ids(engine, "t") == [1, 2]


@pytest.mark.parametrize("dialect", ["duckdb", "spark"])
def test_a_refusal_on_a_table_that_is_there_runs_nothing(engine, dialect):
    # Every statement that names only tables that are there is read and checked before the first one runs.
    engine.execute("CREATE TABLE t USING DELTA AS SELECT 1 AS id")
    with pytest.raises(ValueError, match="local_drop_delta"):
        engine.execute("INSERT INTO t SELECT 2; CREATE TABLE d AS SELECT 1 AS id; DROP TABLE t; "
                       "INSERT INTO t SELECT 3", dialect=dialect)
    assert _ids(engine, "t") == [1]
    assert engine.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'd'")["rows"] == [[0]]


def test_a_refusal_after_a_create_then_write_runs_nothing(engine):
    # The refused statement names no table the script makes, so it is read up front, and the create never runs.
    engine.execute("CREATE TABLE old USING DELTA AS SELECT 1 AS id")
    with pytest.raises(ValueError, match="local_drop_delta"):
        engine.execute("CREATE TABLE t USING DELTA AS SELECT 1 AS id; INSERT OVERWRITE t SELECT 2 AS id; "
                       "DROP TABLE old")
    assert "t" not in {r[0] for r in engine.execute("SELECT view_name FROM duckdb_views()")["rows"]}
    assert not (engine.tables_dir / "dbo" / "t").exists()
    assert _ids(engine, "old") == [1]


def test_a_statement_on_a_table_the_script_made_is_checked_when_it_runs(engine):
    # Read only once the table is there, so its refusal comes after the statements before it ran.
    with pytest.raises(ValueError, match="local_drop_delta"):
        engine.execute("CREATE TABLE t USING DELTA AS SELECT 1 AS id; INSERT INTO t SELECT 2; DROP TABLE t; "
                       "INSERT INTO t SELECT 3")
    assert _ids(engine, "t") == [1, 2]


def test_a_database_table_script_with_a_failing_statement_keeps_what_ran(engine):
    with pytest.raises(duckdb.Error):
        engine.execute("CREATE TABLE d (id INTEGER); INSERT INTO d VALUES (1); INSERT INTO d VALUES ('x'); "
                       "INSERT INTO d VALUES (3)")
    assert _ids(engine, "d") == [1]


def test_a_script_duckdb_cant_read_still_runs_nothing(engine):
    with pytest.raises(ValueError, match="Parser Error"):
        engine.execute("CREATE TABLE d (id INTEGER); INSERT INTO d VALUES (1); SELEC 1")
    assert engine.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'd'")["rows"] == [[0]]


def test_one_statement_runs_as_before(engine):
    assert engine.execute("SELECT 41 + 1 AS n")["rows"] == [[42]]
    assert engine.execute("SELECT 41 + 1 AS n", dialect="spark")["rows"] == [[42]]
    assert engine.execute("SELECT 41 + 1 AS n", dialect="tsql")["rows"] == [[42]]


def test_a_cancel_before_the_script_starts_runs_nothing(engine):
    engine.cancel("q1", not_started=True)
    with pytest.raises(Exception, match="cancelled"):
        engine.execute("CREATE TABLE t USING DELTA AS SELECT 1 AS id; INSERT INTO t SELECT 2", query_id="q1")
    assert engine.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 't'")["rows"] == [[0]]
    assert engine.execute("SELECT count(*) FROM duckdb_views() WHERE view_name = 't'")["rows"] == [[0]]


# ---- MERGE in DuckDB SQL, on DuckDB 1.5 and 2.0

MERGE_SCRIPT = """MERGE INTO customers AS t USING updates AS s ON t.id = s.id
  WHEN MATCHED AND s.deleted THEN DELETE
  WHEN MATCHED THEN UPDATE SET *                  -- or SET t.name = s.name, ...
  WHEN NOT MATCHED THEN INSERT *
  WHEN NOT MATCHED BY SOURCE AND t.region = 'EU' THEN DELETE;  -- EU rows the source no longer has
UPDATE customers SET spend = spend * 1.1 WHERE region = 'EU';
DELETE FROM customers WHERE last_order < '2020-01-01';"""


@pytest.fixture
def customers(engine):
    engine.execute("CREATE TABLE customers USING DELTA AS SELECT * FROM (VALUES "
                   "(1, 'a', 'EU', 100.0, DATE '2019-01-01'), (2, 'b', 'US', 50.0, DATE '2025-01-01'), "
                   "(3, 'c', 'EU', 10.0, DATE '2025-01-01'), (5, 'e', 'EU', 1.0, DATE '2025-01-01')) "
                   "v(id, name, region, spend, last_order)")
    engine.execute("CREATE TABLE updates AS SELECT * FROM (VALUES (1, 'a2', 'EU', 100.0, DATE '2019-01-01', false), "
                   "(4, 'd', 'EU', 5.0, DATE '2025-06-01', false), (3, 'c', 'EU', 10.0, DATE '2025-01-01', true)) "
                   "v(id, name, region, spend, last_order, deleted)")
    return engine


@pytest.mark.parametrize("dialect", ["duckdb", "spark"])
def test_merge_update_delete_script_takes_the_delta_path(customers, dialect):
    out = customers.execute(MERGE_SCRIPT, dialect=dialect)
    assert [s["type"] for s in out["statements"]] == ["merge delta", "update delta", "delete delta"]
    rows = customers.execute("SELECT id, name FROM customers ORDER BY id")["rows"]
    assert [tuple(r) for r in rows] == [(2, "b"), (4, "d")]


def test_one_merge_with_a_comment_after_it_takes_the_delta_path(customers):
    out = customers.execute("MERGE INTO customers AS t USING updates AS s ON t.id = s.id "
                            "WHEN MATCHED THEN UPDATE SET name = s.name;  -- names from the source\n")
    assert out["statements"][0]["type"] == "merge delta"
    assert customers.execute("SELECT name FROM customers WHERE id = 1")["rows"] == [["a2"]]


def test_a_merge_on_a_database_table_with_a_comment_after_it_is_still_checked(engine):
    engine.execute("CREATE TABLE d AS SELECT 1 AS id, 'a' AS name")
    engine.execute("CREATE TABLE src AS SELECT * FROM (VALUES (1, 'x'), (1, 'y')) v(id, name)")
    with pytest.raises(ValueError, match="same row more than once"):
        engine.execute("MERGE INTO d USING src ON d.id = src.id WHEN MATCHED THEN UPDATE SET name = src.name;"
                       "  -- two source rows\nSELECT 1")
    assert engine.execute("SELECT name FROM d")["rows"] == [["a"]]


@pytest.mark.parametrize("text", [
    "MERGE INTO c AS t USING u AS s ON t.id = s.id WHEN MATCHED THEN DELETE;  -- a comment\n",
    "UPDATE c SET a = 1;  -- a comment\n",
    "DELETE FROM c WHERE a = 1; /* a comment */\n",
])
def test_a_change_with_a_comment_after_its_semicolon_is_read(text):
    # DuckDB 2.0's extract_statements gives each statement's text this way; 1.5 gave the comment to the next one.
    change = delta_dml.parse(text, "duckdb")
    assert change is not None and change.table == "c"


def test_a_spark_comment_after_the_last_statement_keeps_its_rows(engine):
    # sqlglot reads the comment as a statement of its own, which ran last and so gave no rows.
    out = engine.execute("SELECT 41 + 1 AS n; -- the answer", dialect="spark")
    assert out["rows"] == [[42]] and [s["type"] for s in out["statements"]] == ["select"]
