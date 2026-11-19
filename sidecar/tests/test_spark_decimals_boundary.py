"""Spark's decimal result types at exactly 38 digits (mutation batch 163, a boundary re-score of batch
28, docs/quality/mutation-testing.md): a product, a remainder and a div whose exact result needs exactly
38 digits are still Spark's, and the division and ceil/floor helpers at their edges."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import spark_decimals as sd
from fabric_desktop import sql


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW t AS SELECT CAST(2.5 AS DECIMAL(18,2)) AS p18, CAST(4 AS DECIMAL(19,2)) AS p19, "
              "CAST(7 AS DECIMAL(36,0)) AS w36, CAST(2.5 AS DECIMAL(10,2)) AS d10, "
              "CAST('9007199254740993' AS DECIMAL(38,0)) AS big, CAST(1 AS DECIMAL(38,0)) AS one")
    yield c
    c.close()


def _spark(conn, query):
    return conn.sql(sql.duckdb_sql(sd.spark_decimal_types(sql.parse(query)[0], conn)))


def test_a_product_of_exactly_38_digits_is_sparks_type(conn):
    # decimal(18,2) * decimal(19,2) is decimal(18 + 19 + 1, 4) = decimal(38,4) in Spark.
    rel = _spark(conn, "SELECT p18 * p19 AS v FROM t")
    assert [str(t) for t in rel.types] == ["DECIMAL(38,4)"] and float(rel.fetchall()[0][0]) == 10.0


def test_a_remainder_whose_operands_need_exactly_38_digits_is_sparks_type(conn):
    # decimal(36,0) % decimal(10,2): scale 2, and 36 whole digits + 2 = 38 still fit.
    rel = _spark(conn, "SELECT w36 % d10 AS v FROM t")
    assert [str(t) for t in rel.types] == ["DECIMAL(10,2)"] and float(rel.fetchall()[0][0]) == 2.0


def test_a_div_whose_operands_need_exactly_38_digits_is_exact(conn):
    # Exactly 38 digits: still the exact whole-number quotient, not a DOUBLE one, which would lose the last 1.
    assert _spark(conn, "SELECT big div one AS v FROM t").fetchall()[0][0] == 9007199254740993


def test_division_shifts_from_zero_up_to_38_digits_and_a_scale_of_38_is_refused():
    assert sd._divide("x", "y", 2, 2, 10, 0) is not None  # shift 0
    assert sd._divide("x", "y", 0, 0, 38, sd.MAX - 1) is not None  # shift 37
    assert sd._divide("x", "y", 0, 1, 38, sd.MAX - 1) is not None  # shift 38: exactly the most
    assert sd._divide("x", "y", 0, 2, 38, sd.MAX - 1) is None  # shift 39
    assert sd._divide("x", "y", 1, 0, 38, sd.MAX - 1) is not None  # shift 36
    assert sd._divide("x", "y", sd.MAX, 0, 38, sd.MAX) is None  # scale 38: no room for the rounding digit


def test_ceil_and_floor_at_their_places_edges():
    assert sd._ceil_floor("CEIL", 10, 2, 2) == "fj_arg0"  # as many places as the scale: unchanged
    zero = sd._ceil_floor("CEIL", 10, 2, 0)
    assert zero == "CEIL(CAST(fj_arg0 AS DECIMAL(38, 2)) * CAST('1' AS DECIMAL(1, 0))) * CAST('1' AS DECIMAL(1, 0))"
    assert sd._ceil_floor("FLOOR", 30, 10, 8) is not None  # 30 + 8 = 38 digits still fit
    assert sd._ceil_floor("FLOOR", 31, 10, 8) is None
    assert sd._ceil_floor("FLOOR", 38, 30, -8) is not None  # 30 + 8 = 38 digits still fit
    assert sd._ceil_floor("FLOOR", 38, 31, -8) is None
