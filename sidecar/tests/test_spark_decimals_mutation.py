"""Mutation-testing follow-up for Spark's decimal result types on this PC (spark_decimals.py): the
widths of TINYINT and SMALLINT operands and of the literal 0, a DECIMAL without its precision, mixed
case names, an aggregate of a non-decimal next to one that changes, the names given to unnamed
columns before and after a star, a statement that isn't a query, and the DataFrame API's alias."""

from __future__ import annotations

import duckdb
import pytest
import sqlglot

from fabric_desktop import sql
from fabric_desktop.spark_decimals import spark_decimal_columns, spark_decimal_types


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW t AS SELECT CAST(10 AS DECIMAL(10,2)) AS a, CAST(3 AS DECIMAL(10,2)) AS b, "
              "CAST(2 AS TINYINT) AS ti, CAST(2 AS SMALLINT) AS si, CAST(2 AS INTEGER) AS n, 1.5::DOUBLE AS f, "
              "CAST(4 AS DECIMAL(10,2)) AS \"Amt\", 'x' AS s")
    yield c
    c.close()


def types(conn, statement):
    return [str(t) for t in conn.sql(sql.duckdb_sql(statement)).types]


def answer(conn, query):
    return types(conn, spark_decimal_types(sql.parse(query)[0], conn))


@pytest.mark.parametrize("expr, kind", [
    ("a * ti", "DECIMAL(14,2)"),   # a TINYINT column is decimal(3,0)
    ("a * si", "DECIMAL(16,2)"),   # a SMALLINT column is decimal(5,0)
    ("a * 0", "DECIMAL(12,2)"),    # the literal 0 is decimal(1,0)
    ("a * 00.5", "DECIMAL(12,3)"),  # leading zeros don't count: 00.5 is decimal(1,1)
])
def test_operand_widths(conn, expr, kind):
    assert answer(conn, f"SELECT {expr} AS v FROM t") == [kind]


def test_a_decimal_without_its_precision_leaves_only_that_expression_as_it_was(conn):
    # the rewrite goes from the last expression to the first, so the one it can't type comes first here
    got = answer(conn, "SELECT a * b AS v, CAST(a AS DECIMAL) * 2 AS u FROM t")
    assert got[0] == "DECIMAL(21,4)"


@pytest.mark.parametrize("query, kinds", [
    ("SELECT a * b AS v, sum(f) AS d FROM t GROUP BY a, b", ["DECIMAL(21,4)", "DOUBLE"]),
    # sum of an integral column is a BIGINT in Spark and Databricks (Databricks SQL's sum: "If expr is an integral
    # number type, a BIGINT"), no longer DuckDB's HUGEINT
    ("SELECT a * b AS v, sum(n) AS i FROM t GROUP BY a, b", ["DECIMAL(21,4)", "BIGINT"]),
])
def test_a_sum_of_an_integer_or_a_double_keeps_duckdbs_type_next_to_one_that_changes(conn, query, kinds):
    assert answer(conn, query) == kinds


def test_a_mixed_case_column_is_found(conn):
    assert answer(conn, "SELECT Amt / b AS v FROM t") == ["DECIMAL(23,13)"]


def unnamed(conn, query):
    statement = sqlglot.parse_one(query, read="spark")
    before = conn.sql(sql.duckdb_sql(statement)).columns
    rel = conn.sql(sql.duckdb_sql(spark_decimal_types(statement, conn)))
    return before, rel.columns, [str(t) for t in rel.types]


def test_unnamed_columns_without_a_star_keep_duckdbs_names(conn):
    before, after, kinds = unnamed(conn, "SELECT s, a / b, a * b FROM t")
    assert after == before
    assert kinds == ["VARCHAR", "DECIMAL(23,13)", "DECIMAL(21,4)"]


def test_unnamed_columns_after_a_star_keep_duckdbs_names(conn):
    before, after, kinds = unnamed(conn, "SELECT *, a / b, s, a * b FROM t")
    assert after == before
    assert kinds[-3:] == ["DECIMAL(23,13)", "VARCHAR", "DECIMAL(21,4)"]


def test_a_statement_that_isnt_a_query_is_still_rewritten(conn):
    conn.execute("CREATE TABLE out (v DECIMAL(23,13))")
    statement = sqlglot.parse_one("INSERT INTO out VALUES (CAST(10 AS DECIMAL(10,2)) / CAST(3 AS DECIMAL(10,2)))",
                                  read="spark")
    before = sql.duckdb_sql(statement)
    after = sql.duckdb_sql(spark_decimal_types(statement, conn))
    assert after != before and "LIST_TRANSFORM" in after


def test_frame_expressions_keep_their_alias_apart():
    cols = [("a", "DECIMAL(10,2)"), ("b", "DECIMAL(10,2)")]
    named, bare = spark_decimal_columns(["a * b AS q", "a * b"], cols)
    assert named is not None and named[1] == "q" and not named[0].rstrip(")").upper().endswith(" Q")
    assert bare is not None and bare[1] is None and named[0] == bare[0]
    assert "DECIMAL(21, 4)" in bare[0]


def test_a_frame_column_with_a_mixed_case_name_is_found():
    out = spark_decimal_columns(['"Amt" * b'], [("Amt", "DECIMAL(10,2)"), ("b", "DECIMAL(10,2)")])
    assert out[0] is not None and "DECIMAL(21, 4)" in out[0][0]


def test_avg_of_a_wide_decimal_stays_exact_up_to_its_limit():
    c = duckdb.connect()
    try:
        c.execute("CREATE TEMP VIEW w AS SELECT CAST('12345678901234567890123456789012.34' AS DECIMAL(34,2)) AS x")
        rel = c.sql(sql.duckdb_sql(spark_decimal_types(sql.parse("SELECT avg(x) AS m FROM w")[0], c)))
        assert [str(t) for t in rel.types] == ["DECIMAL(38,6)"]
        assert str(rel.fetchall()[0][0]) == "12345678901234567890123456789012.340000"  # not through DOUBLE
    finally:
        c.close()
