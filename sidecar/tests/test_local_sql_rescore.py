"""Mutation batch 241: engines/local_sql.py's survivors on today's main. The preview starts at the first
row, a refused statement is named by its first word, Spark SQL's to_json is Spark's and Databricks SQL's
DuckDB's, and SQLite refuses another dialect and shows a one-statement plan's steps."""
from __future__ import annotations

import duckdb
import pyarrow as pa
import pytest

from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig, Unsupported


@pytest.fixture
def duck():
    session = local_sql.DuckDBSession(SessionConfig("nb"))
    yield session
    session.close()


@pytest.fixture
def lite():
    session = local_sql.SQLiteSession(SessionConfig("nb"))
    yield session
    session.close()


def test_the_preview_of_a_kept_result_starts_at_its_first_row(duck):
    table = pa.table({"a": [10, 20, 30]})
    kept, total = duck._keep(table, 3, CellRequest("", "sql", result_rows=5, preview_rows=2))
    assert kept.column("a").to_pylist() == [10, 20] and total == 3


def test_a_statement_that_reaches_outside_is_named_by_its_first_word(duck):
    with pytest.raises(local_sql.OutsideSession, match=r"^ATTACH isn't allowed in a DuckDB engine session \(statement 1\)"):
        local_sql._refuse_outside("ATTACH 'other.db' AS other", None)


def test_spark_sql_writes_to_json_as_spark_and_databricks_sql_as_duckdb(duck):
    query = "SELECT to_json(named_struct('t', TIMESTAMP '2026-01-02 03:04:05')) AS j"
    spark = duck._sql(query, "spark")
    databricks = duck._sql(query, "databricks")
    assert "LIST_TRANSFORM" in spark and "LIST_TRANSFORM" not in databricks
    with duckdb.connect() as con:
        assert con.sql(spark).fetchall() == [('{"t":"2026-01-02T03:04:05.000Z"}',)]


def test_sqlite_refuses_another_dialect(lite):
    with pytest.raises(Unsupported, match="SQLite doesn't run the spark dialect"):
        lite._run(CellRequest("SELECT 1", "sql", dialect="spark"))


def test_sqlite_plans_one_statement_and_shows_its_steps(lite):
    with pytest.raises(ValueError, match="one statement at a time"):
        lite.plan("SELECT 1; SELECT 2")
    lite.execute(CellRequest("CREATE TABLE t (a INTEGER)", "sql", dialect="sqlite"))
    assert "SCAN" in lite.plan("SELECT * FROM t")


TWO_ARRAYS = "SELECT [1, 2]::ARRAY AS a, [3]::ARRAY AS A"


def test_a_column_never_shows_another_columns_values(duck):
    # names alike but for case are left as they are, not written through a projection that mixes them up
    result = duck.execute(CellRequest(TWO_ARRAYS, "sql", dialect="snowflake", result_rows=10))
    assert result.status != "ok" or result.table.to_pylist()[0]["A"] != "[1,2]"


@pytest.mark.xfail(strict=True, reason="two VARIANT or ARRAY columns whose names differ only in case aren't written as "
                                       "JSON: DuckDB 1.5.6 can't give the VARIANTs to Arrow, and 2.0 shows their bytes (WR-320)")
def test_two_array_columns_named_alike_but_for_case_show_as_json(duck):
    result = duck.execute(CellRequest(TWO_ARRAYS, "sql", dialect="snowflake", result_rows=10))
    assert result.status == "ok" and result.table.to_pylist() == [{"a": "[1,2]", "A": "[3]"}]
