"""spark_text_compare.py: what mutation testing showed no test checked, mostly through the DataFrame API's
entry (spark_text_comparison_columns) run on DuckDB, and the module's helpers directly. Which comparisons are
rewritten and which are left as they were (two numbers, two texts, values of two number types, a value with no
type), Spark's answers for the ones rewritten, the size refusal, a literal's type at INT's limit, and what is
returned when sqlglot can't type or rewrite a statement."""

from __future__ import annotations

import logging

import duckdb
import pytest
import sqlglot
from sqlglot import exp
from sqlglot.schema import MappingSchema

from fabric_desktop import spark_text_compare as stc
from fabric_desktop.spark_decimals import Refused

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

def test_a_decimal_side_is_compared_as_a_double_and_others_as_they_are():
    m, i = exp.column("m"), exp.column("i")
    assert stc._number_of(m, T("DECIMAL(10, 2)")).sql() == "CAST(m AS DOUBLE)"
    assert stc._number_of(i, T("INT")) is i


def test_a_number_as_text_writes_a_double_as_spark_does():
    assert stc._as_text(exp.column("i"), T("INT")).sql() == "CAST(i AS VARCHAR)"
    assert "CAST(d AS VARCHAR)" not in stc._as_text(exp.column("d"), T("DOUBLE")).sql()


@pytest.mark.parametrize("kind, numeric, text", [
    (None, False, False), ("INT", True, False), ("DOUBLE", True, False), ("DECIMAL(5, 1)", True, False),
    ("VARCHAR", False, True), ("DATE", False, False),
])
def test_number_and_text_kinds(kind, numeric, text):
    built = T(kind) if kind else None
    assert stc._numeric(built) is numeric and stc._text(built) is text


def test_a_side_with_no_known_type_has_none():
    side = exp.column("x")
    assert stc._kind(side, {}) is None
    typed = exp.column("y")
    typed.type = T("UNKNOWN")
    assert stc._kind(side, {id(side): typed}) is None
    typed.type = T("INT")
    assert stc._kind(side, {id(side): typed}).this == exp.DataType.Type.INT


@pytest.mark.parametrize("text, kind", [
    ("1e2", "DOUBLE"), ("1.5", "DECIMAL(38, 18)"), ("2147483647", "INT"), ("2147483648", "BIGINT"),
    ("-2147483647", "INT"), ("-2147483648", "BIGINT"),
])
def test_a_number_literals_spark_type(text, kind):
    node = sqlglot.parse_one(text, read="spark")
    assert stc._literal_kind(node) == T(kind)


def test_text_and_columns_have_no_literal_type():
    assert stc._literal_kind(exp.Literal.string("1")) is None
    assert stc._literal_kind(exp.column("i")) is None


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


def test_text_and_a_number_are_rewritten_with_sparks_answer():
    assert rewrite("t = i") is not None and value("t = i", t="1.5", i=1) is True
    assert rewrite("i = t") is not None and value("i = t", t="1.5", i=1) is True
    assert value("t = m", t="1.55", m=1.6) is False  # as doubles


def test_between_ends_of_two_types_compare_each_end_on_its_own():
    assert rewrite("t BETWEEN i AND d") is not None
    assert value("t BETWEEN i AND d", t="1.5", i=1, d=1.2) is False  # '1.5' as an INT is 1 >= 1, as a DOUBLE 1.5 > 1.2
    assert value("t BETWEEN i AND d", t="1.5", i=1, d=1.5) is True


def test_a_number_between_text_ends_casts_each_text_end():
    assert rewrite("i BETWEEN t AND 5") is not None
    assert value("i BETWEEN t AND 5", t="1.5", i=1) is True  # '1.5' as an INT is 1


def test_case_keeps_its_else():
    assert rewrite("CASE t WHEN 1 THEN 'y' ELSE 'n' END") is not None
    assert value("CASE t WHEN 1 THEN 'y' ELSE 'n' END", t="x") == "n"
    assert value("CASE t WHEN 1 THEN 'y' ELSE 'n' END", t="1.5") == "y"


def test_a_projections_name_is_kept():
    named, plain = stc.spark_text_comparison_columns(["t = i AS same", "t = i"], COLUMNS)
    assert named[1] == "same" and plain[1] is None and named[0] == plain[0]


def test_a_string_literal_is_enough_to_type_the_statement():
    out = stc.spark_text_comparison_columns(["i = '1.5'"], [("i", "INTEGER")])
    assert out[0] is not None


# What comes back when nothing can be done

def test_nothing_to_rewrite_or_nothing_readable_leaves_every_column():
    assert stc.spark_text_comparison_columns([], COLUMNS) == []
    assert stc.spark_text_comparison_columns(["t = = 1"], COLUMNS) == [None]
    assert stc.spark_text_comparison_columns(["1 = 1 UNION SELECT 2"], COLUMNS) == [None]


def test_a_statement_with_nothing_to_compare_is_returned_as_it_is(caplog):
    statement = sqlglot.parse_one("SELECT 1", read="spark")
    assert stc.spark_text_comparisons(statement, None) is statement
    plain = sqlglot.parse_one("SELECT 1 = 2", read="spark")
    with caplog.at_level(logging.INFO, logger=stc.log.name):
        assert stc.spark_text_comparisons(plain, None) is plain  # no text: not typed, so no connection needed
    assert "not applied" not in caplog.text


def test_a_statement_that_cant_be_typed_runs_as_it_was(monkeypatch):
    statement = sqlglot.parse_one("SELECT t = 1 FROM f", read="spark")
    monkeypatch.setattr(stc, "qualify", lambda *a, **k: 1 / 0)
    assert stc.spark_text_comparisons(statement, None, MappingSchema({"f": {"t": "VARCHAR"}})) is statement


def test_a_rewrite_that_fails_returns_the_statement_as_it_was(monkeypatch):
    statement = sqlglot.parse_one("SELECT t = 1 FROM f", read="spark")
    monkeypatch.setattr(stc, "_rewritten", lambda node, kind: 1 / 0)
    out = stc.spark_text_comparisons(statement, None, MappingSchema({"f": {"t": "VARCHAR"}}))
    assert out is not None and out.sql() == "SELECT t = 1 FROM f"


def test_a_column_named_like_the_lambda_leaves_it():
    out = stc.spark_text_comparisons(sqlglot.parse_one("SELECT kf_text_cmp = 1 FROM f", read="spark"), None,
                                     MappingSchema({"f": {"kf_text_cmp": "VARCHAR"}}))
    assert out.sql() == "SELECT kf_text_cmp = 1 FROM f"


def test_a_rewrite_too_large_is_refused_naming_how_many(monkeypatch):
    monkeypatch.setattr(stc, "_GROWTH", 0)
    with pytest.raises(Refused, match="The query's 2 comparisons of text with numbers"):
        stc.spark_text_comparison_columns(["t = i AND t = b"], COLUMNS)
    monkeypatch.setattr(stc, "_GROWTH", 20_000)
    assert rewrite("t = i AND t = b") is not None


def test_a_decimal_between_numbers_is_left():
    assert rewrite("m BETWEEN 1 AND 5") is None


def test_a_rewrite_exactly_at_the_size_limit_runs(monkeypatch):
    from fabric_desktop.sql import duckdb_sql
    schema = MappingSchema({"f": {"t": "VARCHAR", "i": "INT"}}, dialect="duckdb", normalize=False)
    size = len(duckdb_sql(sqlglot.parse_one("SELECT t = i FROM f", read="duckdb")))
    grown = len(duckdb_sql(stc.spark_text_comparisons(sqlglot.parse_one("SELECT t = i FROM f", read="duckdb"), None, schema)))
    assert grown > 2 * size
    monkeypatch.setattr(stc, "_GROWTH", grown - 2 * size)  # exactly the limit: allowed
    assert stc.spark_text_comparisons(sqlglot.parse_one("SELECT t = i FROM f", read="duckdb"), None, schema) is not None
    monkeypatch.setattr(stc, "_GROWTH", grown - 2 * size - 1)
    with pytest.raises(Refused):
        stc.spark_text_comparisons(sqlglot.parse_one("SELECT t = i FROM f", read="duckdb"), None, schema)
