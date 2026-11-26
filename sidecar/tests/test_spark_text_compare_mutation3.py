"""spark_text_compare.py: the mutants that test_spark_text_compare_mutation.py and test_spark_text_comparisons
leave alive. A number literal's Spark type, a text column found in a schema or on the connection, comparisons
without text or with numbers of one type left as they are, and a rewrite that fails giving back the statement
as it was."""

from __future__ import annotations

import duckdb
import pytest
import sqlglot
from sqlglot import exp
from sqlglot.schema import MappingSchema

from fabric_desktop import spark_text_compare as stc


COLUMNS = [("t", "VARCHAR"), ("i", "INTEGER"), ("d", "DOUBLE"), ("m", "DECIMAL(10,2)"), ("b", "BIGINT"), ("dt", "DATE")]
ROW = {"t": None, "i": 0, "d": 0.0, "m": 0, "b": 0, "dt": "2026-01-01"}


def rewrite(expression):
    return stc.spark_text_comparison_columns([expression], COLUMNS)[0]


def value(expression, **row):
    """The expression, rewritten as Spark compares (when it is), run on DuckDB over one row."""
    out = rewrite(expression)
    sql = out[0] if out else expression
    values = {**ROW, **row}
    with duckdb.connect() as con:
        con.execute("CREATE TABLE kf_frame (t VARCHAR, i INTEGER, d DOUBLE, m DECIMAL(10,2), b BIGINT, dt DATE)")
        con.execute("INSERT INTO kf_frame VALUES (?, ?, ?, ?, ?, ?)", [values[k] for k, _ in COLUMNS])
        return con.execute(f"SELECT {sql} FROM kf_frame").fetchone()[0]


T = exp.DataType.build


# Helpers


@pytest.mark.parametrize("text, kind", [
    ("1e2", "DOUBLE"), ("1.5", "DECIMAL(38, 18)"), ("2147483647", "INT"), ("2147483648", "BIGINT"),
    ("-2147483647", "INT"), ("-2147483648", "BIGINT"),
])
def test_a_number_literals_spark_type(text, kind):
    node = sqlglot.parse_one(text, read="spark")
    assert stc._literal_kind(node) == T(kind)


def test_a_text_column_is_found_in_a_schema_or_on_the_connection():
    sides = [exp.column("a")]
    assert stc._has_text_column(sides, None, MappingSchema({"f": {"a": "VARCHAR(10)"}}, dialect="duckdb")) is True
    assert stc._has_text_column(sides, None, MappingSchema({"f": {"a": "INT"}}, dialect="duckdb")) is False
    assert stc._has_text_column([exp.Literal.number(1)], None, None) is False
    with duckdb.connect() as con:
        con.execute("CREATE TABLE n (a INT)")
        assert stc._has_text_column(sides, con, None) is False
        con.execute("CREATE TABLE s (a VARCHAR)")
        assert stc._has_text_column(sides, con, None) is True


# What is rewritten, and Spark's answers

@pytest.mark.parametrize("expression", [
    "i = 2", "t = 'x'", "i IN (1, 2)", "t IN ('a', 'b')", "t IN (1, dt)", "t IN (1, unknown_fn(i))",
    "CASE i WHEN 2 THEN 'y' END", "CASE m WHEN 2 THEN 'y' END", "CASE t WHEN 1 THEN 'y' WHEN 2.5 THEN 'z' END",
    "CASE t WHEN 1 THEN 'y' WHEN 'x' THEN 'z' END", "t BETWEEN t AND t", "i + 1", "upper(t)",
])
def test_comparisons_without_text_and_a_number_of_one_type_are_left(expression):
    assert rewrite(expression) is None


# What comes back when nothing can be done


def test_a_rewrite_that_fails_returns_the_statement_as_it_was(monkeypatch):
    statement = sqlglot.parse_one("SELECT t = 1 FROM f", read="spark")
    monkeypatch.setattr(stc, "_rewritten", lambda node, kind: 1 / 0)
    out = stc.spark_text_comparisons(statement, None, MappingSchema({"f": {"t": "VARCHAR"}}))
    assert out is not None and out.sql() == "SELECT t = 1 FROM f"








