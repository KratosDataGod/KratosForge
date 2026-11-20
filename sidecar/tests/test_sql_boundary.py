"""Spark SQL on this PC at its exact edges (boundary re-score of batch 7, mutation batch 157,
docs/quality/mutation-testing.md). Expected values are what Spark gives for the same query."""

from __future__ import annotations

import decimal

import duckdb
import pytest
from sqlglot import exp

from fabric_desktop import sql


@pytest.fixture(scope="module")
def con():
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    yield connection
    connection.close()


def _rows(con, query: str) -> list:
    return con.execute(sql.duckdb_sql(sql.parse(query)[0])).fetchall()


def test_a_json_path_is_read_to_its_end():
    assert sql.spark_json_path("$.a") == '$."a"' and sql.spark_json_path("$") == "$"


def test_bround_to_0_places_keeps_a_decimal_a_decimal(con):
    assert _rows(con, "SELECT bround(2.5, 0)") == [(decimal.Decimal("2"),)]


def test_a_whole_number_literal_at_the_top_of_int_is_in_range():
    INT = exp.DataType.Type.INT
    assert sql._past(exp.Literal.number(2 ** 31 - 1), INT) is False
    assert sql._past(exp.Literal.number(2 ** 31), INT) is True


def test_a_cast_to_the_same_integer_width_cant_be_past_it():
    INT = exp.DataType.Type.INT
    assert sql._past(exp.Cast(this=exp.column("x"), to=exp.DataType.build("INT")), INT) is False
    assert sql._past(exp.Cast(this=exp.column("x"), to=exp.DataType.build("BIGINT")), INT) is True


def test_a_json_number_of_exactly_38_digits_is_a_decimal():
    assert sql._json_decimal(38, 0) == ("DECIMAL", 38, 0) and sql._json_decimal(39, 0) == "DOUBLE"


def test_a_json_whole_number_with_prefers_decimal_is_a_decimal():
    assert sql._infer_json_type(decimal.Decimal("5"), prefers_decimal=True) == ("DECIMAL", 1, 0)


def test_a_json_whole_number_at_the_edges_of_bigint():
    assert sql._infer_json_type(-2 ** 63) == "BIGINT" and sql._infer_json_type(2 ** 63 - 1) == "BIGINT"
    assert sql._infer_json_type(2 ** 63) == ("DECIMAL", 19, 0)
    assert sql._infer_json_type(-2 ** 63 - 1) == ("DECIMAL", 19, 0)


def test_json_decimals_merged_to_exactly_38_digits_stay_decimal():
    assert sql._merge_json_types(("DECIMAL", 38, 0), ("DECIMAL", 20, 0)) == ("DECIMAL", 38, 0)
    assert sql._merge_json_types(("DECIMAL", 38, 1), ("DECIMAL", 38, 0)) == "DOUBLE"


def test_range_with_one_and_with_four_arguments(con):
    assert _rows(con, "SELECT * FROM range(3)") == [(0,), (1,), (2,)] and con.description[0][0] == "id"
    assert _rows(con, "SELECT * FROM range(0, 10, 3, 2)") == [(0,), (3,), (6,), (9,)]


def test_a_json_path_with_one_wildcard_is_read(con):
    assert _rows(con, """SELECT get_json_object('{"a":[1,2]}', '$.a[*]')""") == [("[1,2]",)]


def test_from_json_to_a_decimal_with_only_a_precision(con):
    assert _rows(con, """SELECT from_json('{"a": 12.5}', 'a DECIMAL(5)')""") == [({"a": decimal.Decimal("13")},)]


def test_a_query_that_ends_at_struct_less_than_is_read_without_an_error():
    assert sql._only_empty_structs_fail("SELECT STRUCT<") is False


def test_head_of_exactly_its_limit_keeps_every_row():
    table, total = sql.head("SELECT * FROM range(5)", limit=5)
    assert table.num_rows == 5 and total == 5
