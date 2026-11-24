"""Spark SQL on this PC at more of its exact edges (sql.py's boundary mutants, re-scored on main after
batch 157): bround to 0 places on a decimal past a double's precision, format_number at 10 and 18 places,
slice with a length of 0, and the lowest whole number of a type and a window's int that fit it. Expected
values are what Spark gives for the same query."""

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


def _one(con, query: str):
    return con.execute(sql.duckdb_sql(sql.parse(query)[0])).fetchall()[0][0]


@pytest.mark.parametrize("value, rounded", [("12345678901234566.5", "12345678901234566"),
                                            ("12345678901234567.5", "12345678901234568")])
def test_bround_to_0_places_rounds_half_even_past_a_doubles_precision(con, value, rounded):
    assert _one(con, f"SELECT bround(CAST('{value}' AS DECIMAL(20,1)), 0)") == decimal.Decimal(rounded)


@pytest.mark.parametrize("places, written", [(10, "1.5000000000"), (18, "1.500000000000000000")])
def test_format_number_writes_10_and_18_places(con, places, written):
    assert _one(con, f"SELECT format_number(1.5, {places})") == written


def test_format_number_past_18_places_is_refused():
    with pytest.raises(ValueError, match="more than 18 decimal places"):
        sql.duckdb_sql(sql.parse("SELECT format_number(1.5, 19)")[0])


def test_a_slice_of_length_0_is_empty(con):
    assert _one(con, "SELECT slice(array(1, 2, 3), 1, 0)") == []


def test_the_lowest_whole_number_of_a_type_is_in_its_range():
    INT = exp.DataType.Type.INT
    assert sql._past(exp.Neg(this=exp.Literal.number(2 ** 31)), INT) is False
    assert sql._past(exp.Neg(this=exp.Literal.number(2 ** 31 + 1)), INT) is True


def test_a_windows_int_fits_an_int_but_not_a_smallint():
    window = sql.parse("SELECT row_number() OVER (ORDER BY 1)")[0].find(exp.Window)
    assert sql._past(window, exp.DataType.Type.INT) is False
    assert sql._past(window, exp.DataType.Type.SMALLINT) is True
