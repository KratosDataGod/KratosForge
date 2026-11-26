"""spark_branch_types: the rarer kinds and shapes (a float, a date with a timestamp, a parenthesised set
operation, an unnamed column, a star, a one-parameter decimal) and the paths that leave a statement as it was."""

from __future__ import annotations

import logging

import pytest
from sqlglot import parse_one
from sqlglot.schema import MappingSchema

from fabric_desktop import spark_branch_types as branches
from fabric_desktop import sql
from fabric_desktop.spark_decimals import Refused


def _typed(query: str, schema: dict | None = None) -> str:
    statement = branches.mark(parse_one(query, dialect="spark"))
    mapping = MappingSchema(schema or {}, dialect="duckdb")
    return sql.duckdb_sql(branches.spark_branch_types(statement, None, mapping))


def test_an_int_with_a_float_is_a_float():
    assert branches.wider(("int", 10), ("float",)) == ("float",)
    assert branches.wider(("float",), ("int", 20)) == ("float",)
    row = sql.run("SELECT coalesce(1, CAST(1.5 AS FLOAT)) AS a, typeof(coalesce(1, CAST(1.5 AS FLOAT))) AS t").to_pylist()
    assert row == [{"a": 1.0, "t": "float"}]


def test_a_date_with_a_timestamp_is_a_timestamp():
    assert branches.wider(("date",), ("timestamp",)) == ("timestamp",)
    assert branches.wider(("timestamp",), ("date",)) == ("timestamp",)
    expression = "CASE WHEN true THEN DATE'2024-01-02' ELSE TIMESTAMP'2024-01-02 03:04:05' END"
    row = sql.run(f"SELECT typeof({expression}) AS t, CAST({expression} AS STRING) AS s").to_pylist()[0]
    assert row == {"t": "timestamp", "s": "2024-01-02 00:00:00"}


def test_kinds_spark_has_no_wider_type_for():
    assert branches.wider(("date",), ("bool",)) is None
    assert branches.wider(("binary",), ("int", 10)) is None
    assert branches.wider(("text",), ("binary",)) is None


def test_a_refused_decimal_is_named_with_its_precision_and_scale():
    with pytest.raises(Refused, match=r"A CASE mixes .* \(DECIMAL\(2,1\), BOOLEAN\)"):
        sql.run("SELECT CASE WHEN true THEN 1.5 ELSE true END")


def test_spark_names_of_each_kind():
    names = [branches._spark_name(k) for k in [("int", 3), ("int", 5), ("int", 10), ("int", 20), ("decimal", 38, 2),
                                                 ("double",), ("float",), ("text",), ("timestamp",)]]
    assert names == ["TINYINT", "SMALLINT", "INT", "BIGINT", "DECIMAL(38,2)", "DOUBLE", "FLOAT", "STRING", "TIMESTAMP"]


def test_a_parenthesised_set_operation_on_the_left_is_widened_before_the_outer_one():
    # Spark 3.5: the inner UNION is DOUBLE, the outer one with text is STRING written as Spark writes a double.
    rows = sql.run("SELECT a, typeof(a) AS t FROM ((SELECT 1e20D AS a UNION ALL SELECT 2e0D) UNION ALL SELECT 'x') "
                   "ORDER BY 1").to_pylist()
    assert rows == [{"a": "1.0E20", "t": "string"}, {"a": "2.0", "t": "string"}, {"a": "x", "t": "string"}]


@pytest.mark.parametrize("query", ["(SELECT 1 AS a UNION ALL SELECT 2) UNION ALL SELECT true",
                                   "SELECT true AS a UNION ALL (SELECT 1 UNION ALL SELECT 2)"])
def test_a_parenthesised_set_operation_mixed_with_a_boolean_is_refused(query):
    # Spark 3.5 stops on both (INCOMPATIBLE_COLUMN_TYPE); DuckDB turned the boolean into 1.
    with pytest.raises(Refused, match="A UNION's column 1 mixes"):
        sql.run(query)


def test_a_parenthesised_right_side_is_cast_leaf_by_leaf():
    out = _typed("SELECT 1 AS x UNION ALL (SELECT 2.5D UNION ALL SELECT 'x')")
    assert "LIST_TRANSFORM" in out and "CAST(1 AS TEXT)" in out


def test_an_unnamed_column_keeps_the_name_duckdb_gives_it():
    out = _typed("SELECT 1 + 1 UNION ALL SELECT 'a'")
    assert out == "SELECT CAST(1 + 1 AS TEXT) AS \"1 + 1\" UNION ALL SELECT 'a'"
    rows = sql.run("SELECT * FROM (SELECT 1 + 1 UNION ALL SELECT 'x') ORDER BY 1").to_pylist()
    assert rows == [{"(1 + 1)": "2"}, {"(1 + 1)": "x"}]


def test_a_named_column_is_cast_under_its_own_name():
    assert _typed("SELECT y FROM t UNION ALL SELECT 'a'", {"t": {"y": "INT"}}) == \
        "SELECT CAST(y AS TEXT) AS \"y\" FROM t UNION ALL SELECT 'a'"


@pytest.mark.parametrize("query", ["SELECT * FROM t UNION ALL SELECT 'a'",
                                   "SELECT y FROM t UNION ALL VALUES ('a')",
                                   "SELECT 1 AS x UNION ALL SELECT 1, 2",
                                   "SELECT z FROM u UNION ALL SELECT 'a'"])
def test_a_side_whose_columns_cant_be_told_is_left_as_it_was(query):
    assert _typed(query, {"t": {"y": "INT"}}) == sql.duckdb_sql(parse_one(query, dialect="spark"))


def test_a_value_in_parentheses_is_typed_as_the_value_inside():
    assert _typed("SELECT coalesce((1), 'a')") == "SELECT COALESCE(CAST((1) AS TEXT), 'a')"
    assert sql.run("SELECT coalesce((1), 'a') AS a, typeof(coalesce(((1)), 'a')) AS t").to_pylist() == \
        [{"a": "1", "t": "string"}]


def test_a_decimal_column_without_scale_is_left_as_it_was():
    assert _typed("SELECT coalesce(x, 'a') FROM t", {"t": {"x": "DECIMAL(10)"}}) == "SELECT COALESCE(x, 'a') FROM t"


def test_a_given_schema_is_used_without_reading_the_connection():
    class NoConnection:
        def execute(self, *args):
            raise AssertionError("the schema was given")

    statement = branches.mark(parse_one("SELECT coalesce(y, 'a') FROM t", dialect="spark"))
    out = branches.spark_branch_types(statement, NoConnection(), MappingSchema({"t": {"y": "INT"}}, dialect="duckdb"))
    assert sql.duckdb_sql(out) == "SELECT COALESCE(CAST(y AS TEXT), 'a') FROM t"


def test_a_statement_that_cant_be_typed_runs_as_it_was_and_is_logged(caplog):
    class Broken:
        def execute(self, *args):
            raise RuntimeError("catalog gone")

    statement = branches.mark(parse_one("SELECT coalesce(1, 'a')", dialect="spark"))
    with caplog.at_level(logging.INFO, logger=branches.__name__):
        out = branches.spark_branch_types(statement, Broken())
    assert out is statement and sql.duckdb_sql(out) == "SELECT COALESCE(1, 'a')"
    assert "not applied: RuntimeError: catalog gone" in caplog.text
    assert all(branches._KEY not in v.meta for v in out.walk())  # the marks are taken off again


def test_an_unmarked_statement_is_returned_untouched():
    statement = parse_one("SELECT coalesce(1, 'a')", dialect="spark")
    assert branches.spark_branch_types(statement, None) is statement
    assert sql.duckdb_sql(statement) == "SELECT COALESCE(1, 'a')"
