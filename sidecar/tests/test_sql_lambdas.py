"""Lambdas reach DuckDB as ``LAMBDA x : body``, which DuckDB 1.3 and later read and 2.0 requires (#1341).

DuckDB 2.0 refuses the old ``x -> body`` ("Deprecated lambda arrow"). sqlglot before 30.25 writes
the arrow, both for lambdas in the query and for ones it makes itself (initcap). CI runs this file on
both ends of the sqlglot range.
"""

from __future__ import annotations

import duckdb
import pytest
import sqlglot

from fabric_desktop import snowflake_sql, sql
from fabric_desktop.localdata import tsql

SPARK = [
    ("SELECT transform(array(1, 2, 3), x -> x + 1)", [2, 3, 4]),
    ("SELECT filter(array(3, 1, 2), (x, i) -> x > i)", [3]),  # Spark counts i from 0
    ("SELECT aggregate(array(1, 2, 3), 0, (acc, x) -> acc + x)", 6),
    ("SELECT initcap('hello wORLD')", "Hello World"),
]


@pytest.mark.parametrize("spark,answer", SPARK)
def test_spark_lambdas_are_written_with_lambda_and_run(spark, answer):
    text = sql.duckdb_sql(sqlglot.parse_one(spark, read="spark"))
    assert "->" not in text and "LAMBDA" in text.upper()
    assert duckdb.sql(text).fetchall() == [(answer,)]


def test_snowflake_translations_write_lambda():
    translation = snowflake_sql.translate("SELECT INITCAP('hello world')")
    assert "->" not in translation.duckdb_sql


def test_the_dialect_writes_lambda_for_lambdas_sqlglot_makes_itself():
    tree = sqlglot.parse_one("SELECT list_transform([1, 2], x -> x * 2)", read="duckdb")
    assert tree.sql(dialect=sql.KfDuckDB) == "SELECT LIST_TRANSFORM([1, 2], LAMBDA x : x * 2)"
    assert tsql.KfDuckDB is sql.KfDuckDB
