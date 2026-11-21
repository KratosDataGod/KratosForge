"""Spark's comparisons of text with a number (spark_text_compare.py), the paths the other tests leave: IN over a
subquery, a statement that is itself the comparison, a statement that can't be typed, CASE with numbers of two
types, a rewrite too large to run (in SQL and in the DataFrame API), and DataFrame expressions that don't
split one per column. Each rewritten statement is run on its own DuckDB connection."""

from __future__ import annotations

import logging

import duckdb
import pytest
from sqlglot import parse_one
from sqlglot.schema import MappingSchema

from fabric_desktop import spark_text_compare as C
from fabric_desktop.spark_decimals import Refused
from fabric_desktop.sql import duckdb_sql

SCHEMA = {"t": {"s": "VARCHAR", "i": "INT"}, "u": {"x": "INT"}}


def _schema():
    return MappingSchema(SCHEMA, dialect="spark")


def _rows(sql):
    with duckdb.connect() as con:
        con.execute("CREATE TABLE t AS SELECT * FROM (VALUES ('1.5', 1), ('', 2), ('7', 3)) v(s, i)")
        con.execute("CREATE TABLE u AS SELECT * FROM (VALUES (1), (7)) v(x)")
        return con.execute(sql).fetchall()


def test_in_over_a_subquery_is_left_to_duckdb_and_the_comparison_beside_it_is_rewritten():
    statement = parse_one("SELECT s IN (SELECT CAST(x AS STRING) FROM u) AS a, s = 1 AS b FROM t ORDER BY i",
                          read="spark")
    sql = duckdb_sql(C.spark_text_comparisons(statement, None, _schema()))
    assert "IN (SELECT" in sql and "REGEXP_MATCHES" in sql
    # '1.5' = 1 is true in Spark, '' = 1 is NULL; DuckDB alone stops on ''.
    assert _rows(sql) == [(False, True), (False, None), (True, False)]


def test_a_statement_that_is_the_comparison_itself_is_replaced_whole():
    out = C.spark_text_comparisons(parse_one("'1.5' = 1", read="spark"), None, _schema())
    assert out.key == "eq" and "REGEXP_MATCHES" in duckdb_sql(out)
    assert _rows("SELECT " + duckdb_sql(out)) == [(True,)]


def test_a_statement_that_cant_be_typed_runs_as_it_was(caplog):
    statement = parse_one("SELECT '1' = 1", read="spark")
    with caplog.at_level(logging.INFO, logger=C.log.name):
        out = C.spark_text_comparisons(statement, object(), None)  # no tables to read the types from
    assert out is statement and duckdb_sql(out) == "SELECT '1' = 1"
    assert "Spark text comparisons not applied: AttributeError" in caplog.text
    assert not any(C._KEY in n.meta for n in statement.walk()), "the marks are taken off again"


def test_case_with_numbers_of_two_types_is_left_to_duckdb():
    sql = "SELECT CASE s WHEN 1 THEN 'a' WHEN 9999999999 THEN 'b' END FROM t"
    out = C.spark_text_comparisons(parse_one(sql, read="spark"), None, _schema())
    assert duckdb_sql(out) == sql


def test_a_rewrite_too_large_to_run_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(C, "_GROWTH", 0)
    with pytest.raises(Refused, match="^The query's 1 comparisons of text with numbers can't run on this PC"):
        C.spark_text_comparisons(parse_one("SELECT s = 1 FROM t", read="spark"), None, _schema())
    with pytest.raises(Refused, match="Cast the text to the number's type"):
        C.spark_text_comparison_columns(["s = 1"], [("s", "VARCHAR")])


def test_dataframe_expressions_that_dont_split_one_per_column_are_left_as_they_were():
    assert C.spark_text_comparison_columns(["s = 1, i"], [("s", "VARCHAR"), ("i", "INTEGER")]) == [None]
    assert C.spark_text_comparison_columns(["s = 1 AS hit", "i"], [("s", "VARCHAR"), ("i", "INTEGER")])[1] is None


def test_text_against_a_number_written_with_an_exponent_is_compared_as_a_double():
    out = C.spark_text_comparisons(parse_one("SELECT s = 1e0 FROM t ORDER BY i", read="spark"), None, _schema())
    assert _rows(duckdb_sql(out)) == [(False,), (None,), (False,)]
    assert _rows(duckdb_sql(C.spark_text_comparisons(parse_one("SELECT '1.0' = 1e0", read="spark"), None, _schema()))) == [(True,)]


def test_between_with_text_on_every_side_is_left_as_it_was():
    sql = "SELECT s BETWEEN '1' AND '5' FROM t"
    assert duckdb_sql(C.spark_text_comparisons(parse_one(sql, read="spark"), None, _schema())) == sql


def test_a_rewrite_that_fails_part_way_gives_back_the_statement_as_it_was(monkeypatch, caplog):
    def broken(node, kind):
        raise ValueError("no rewrite")
    monkeypatch.setattr(C, "_rewritten", broken)
    statement = parse_one("SELECT s = 1 FROM t", read="spark")
    with caplog.at_level(logging.INFO, logger=C.log.name):
        out = C.spark_text_comparisons(statement, None, _schema())
    assert duckdb_sql(out) == "SELECT s = 1 FROM t"
    assert "Spark text comparisons not applied: ValueError: no rewrite" in caplog.text


def test_dataframe_expressions_without_a_comparison_are_not_parsed():
    assert C.spark_text_comparison_columns(["s", "upper(s)"], [("s", "VARCHAR")]) == [None, None]
    assert C.spark_text_comparison_columns([], [("s", "VARCHAR")]) == []
