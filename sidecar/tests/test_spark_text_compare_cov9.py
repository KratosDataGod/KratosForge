"""spark_text_compare: an exponent literal, IN over a subquery, a bare comparison as the statement and the
rewrite's fallbacks when something inside it fails."""

from __future__ import annotations

import logging

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.schema import MappingSchema

from fabric_desktop import spark_text_compare as stc
from fabric_desktop.sql import duckdb_sql

SCHEMA = MappingSchema({"t": {"s": "VARCHAR", "n": "INTEGER"}}, dialect="duckdb", normalize=False)


def rewrite(query: str) -> str:
    return duckdb_sql(stc.spark_text_comparisons(sqlglot.parse_one(query, read="duckdb"), None, SCHEMA))


def test_an_exponent_literal_is_a_double_so_text_is_read_as_a_double():
    assert stc._literal_kind(sqlglot.parse_one("1e2")).this == exp.DataType.Type.DOUBLE
    assert stc._literal_kind(sqlglot.parse_one("-2.5")).this == exp.DataType.Type.DECIMAL
    assert stc._literal_kind(sqlglot.parse_one("3000000000")).this == exp.DataType.Type.BIGINT
    assert stc._literal_kind(sqlglot.parse_one("'1'")) is None
    sql = rewrite("SELECT s = 1e2 FROM t")
    assert "AS DOUBLE" in sql and "TRY_CAST" in sql
    with duckdb.connect() as con:
        con.execute("CREATE TABLE t (s VARCHAR, n INTEGER)")
        con.execute("INSERT INTO t VALUES ('100', 1), (' 1e2d ', 2), ('abc', 3)")
        assert con.execute(sql + " ORDER BY n").fetchall() == [(True,), (True,), (None,)]


def test_in_over_a_subquery_is_left_to_duckdb_but_the_rest_is_rewritten():
    query = "SELECT s IN (SELECT n FROM t), s = 1 FROM t"
    out = rewrite(query)
    assert "IN (SELECT n FROM t)" in out
    assert "REGEXP_MATCHES" in out  # the plain comparison still is rewritten
    # Only a subquery IN: nothing to type, the statement comes back as it was.
    only = sqlglot.parse_one("SELECT s IN (SELECT n FROM t) FROM t", read="duckdb")
    assert stc.spark_text_comparisons(only, None, SCHEMA) is only


def test_a_statement_that_is_itself_a_comparison_is_replaced_whole():
    statement = sqlglot.parse_one("'1.5' = 1", read="duckdb")
    out = stc.spark_text_comparisons(statement, None, SCHEMA)
    assert isinstance(out, exp.EQ) and out is not statement
    with duckdb.connect() as con:  # Spark: CAST('1.5' AS INT) = 1, true (DuckDB alone rounds '1.5' to 2)
        assert con.execute(f"SELECT {duckdb_sql(out)}").fetchone() == (True,)


def test_a_failure_while_rewriting_returns_the_statement_as_it_was(monkeypatch, caplog):
    def boom(node, kind):
        raise RuntimeError("cannot")
    monkeypatch.setattr(stc, "_rewritten", boom)
    statement = sqlglot.parse_one("SELECT s = 1 FROM t", read="duckdb")
    with caplog.at_level(logging.INFO, logger=stc.__name__):
        out = stc.spark_text_comparisons(statement, None, SCHEMA)
    assert duckdb_sql(out) == "SELECT s = 1 FROM t"
    assert "Spark text comparisons not applied: RuntimeError: cannot" in caplog.text


def test_a_failure_while_typing_returns_the_statement(monkeypatch, caplog):
    def boom(*a, **k):
        raise ValueError("no types")
    monkeypatch.setattr(stc, "annotate_types", boom)
    statement = sqlglot.parse_one("SELECT s = 1 FROM t", read="duckdb")
    with caplog.at_level(logging.INFO, logger=stc.__name__):
        assert stc.spark_text_comparisons(statement, None, SCHEMA) is statement
    assert "ValueError: no types" in caplog.text
    assert not any(stc._KEY in n.meta for n in statement.walk())  # marks are taken off again


def test_text_as_and_number_of_for_types_that_are_not_numbers():
    side = exp.column("s")
    assert stc._text_as(side, exp.DataType.build("DATE")) is None
    assert stc._number_of(side, exp.DataType.build("INT")) is side
    assert duckdb_sql(stc._number_of(side, exp.DataType.build("DECIMAL(10,2)"))) == "CAST(s AS DOUBLE)"


def test_rewritten_leaves_nodes_it_does_not_handle():
    assert stc._rewritten(exp.column("s"), lambda s: None) is None
    sub = sqlglot.parse_one("s IN (SELECT n FROM t)", read="duckdb")
    assert stc._rewritten(sub, lambda s: None) is None


def test_columns_api_when_the_rewrite_is_not_a_select(monkeypatch):
    monkeypatch.setattr(stc, "spark_text_comparisons", lambda statement, conn, schema: exp.column("x"))
    assert stc.spark_text_comparison_columns(["t = 1"], [("t", "VARCHAR")]) == [None]


def test_columns_api_rewrites_and_names():
    out = stc.spark_text_comparison_columns(["t = 1 AS eq", "n + 1"], [("t", "VARCHAR"), ("n", "INTEGER")])
    assert out[0] is not None and out[0][1] == "eq" and "REGEXP_MATCHES" in out[0][0]
    assert out[1] is None
    assert stc.spark_text_comparison_columns([], []) == []
    assert stc.spark_text_comparison_columns(["n + 1"], [("n", "INTEGER")]) == [None]
