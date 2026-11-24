"""Spark's answers where DuckDB stops with an overflow error: the smallest BIGINT div -1, and a decimal sum
past 38 digits (DuckDB 2's own decimal sum stops there)."""
from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.spark_decimals import spark_decimal_types


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW lo AS SELECT CAST(-9223372036854775808 AS BIGINT) AS lo, CAST(-1 AS BIGINT) AS m1, "
              "CAST(0 AS BIGINT) AS z")
    # decimal(36,2) sums as decimal(38,2): 36 whole digits. 120 rows of 34 nines make 37.
    c.execute("CREATE TEMP VIEW big AS SELECT CAST('9999999999999999999999999999999999.99' AS DECIMAL(36,2)) AS x, "
              "i % 2 AS g FROM range(120) r(i)")
    c.execute("CREATE TEMP VIEW small AS SELECT * FROM (VALUES (CAST('-0.07' AS DECIMAL(30,2))), "
              "(CAST('0.02' AS DECIMAL(30,2))), (CAST('0.02' AS DECIMAL(30,2))), (NULL)) v(x)")
    yield c
    c.close()


def rows(conn, query, ansi=False):
    return conn.sql(sql.duckdb_sql(spark_decimal_types(sql.parse(query)[0], conn, ansi=ansi))).fetchall()


def test_the_smallest_bigint_div_minus_one_is_itself_with_ansi_off(conn):
    assert rows(conn, "SELECT lo div m1 AS v FROM lo") == [(-9223372036854775808,)]


@pytest.mark.parametrize("expr, value", [
    ("lo div 2", -4611686018427387904), ("-7 div 2", -3), ("7 div -2", -3), ("lo div z", None),
    ("(lo + 1) div m1", 9223372036854775807), ("m1 div lo", 0),
])
def test_other_divs_truncate_toward_zero_as_before(conn, expr, value):
    assert rows(conn, f"SELECT {expr} AS v FROM lo") == [(value,)]


def test_the_smallest_bigint_div_minus_one_is_an_error_with_ansi_on(conn):
    with pytest.raises(duckdb.Error, match="(?i)overflow"):
        rows(conn, "SELECT lo div m1 AS v FROM lo", ansi=True)


def test_a_sum_past_38_digits_is_null_with_ansi_off(conn):
    assert rows(conn, "SELECT sum(x) AS v FROM big") == [(None,)]


def test_a_sum_past_38_digits_is_sparks_error_with_ansi_on(conn):
    with pytest.raises(duckdb.Error, match=r"\[ARITHMETIC_OVERFLOW\] Overflow in sum of decimals"):
        rows(conn, "SELECT sum(x) AS v FROM big", ansi=True)


def test_an_average_past_38_digits_is_null_and_a_distinct_sum_fits(conn):
    assert rows(conn, "SELECT avg(x) AS a, sum(DISTINCT x) AS d FROM big") == \
        [(None, Decimal("9999999999999999999999999999999999.99"))]


def test_a_windowed_and_filtered_sum_keep_their_clauses(conn):
    got = rows(conn, "SELECT sum(x) FILTER (WHERE g = 0) AS f, max(w) AS w FROM "
                     "(SELECT x, g, sum(x) OVER (PARTITION BY g ORDER BY g ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) "
                     "AS w FROM big) t")
    assert got == [(Decimal("599999999999999999999999999999999999.40"), Decimal("19999999999999999999999999999999999.98"))]


def test_small_and_negative_sums_keep_their_digits_and_type(conn):
    query = "SELECT sum(x) AS s, avg(x) AS a, sum(x) FILTER (WHERE x < 0) AS n FROM small"
    statement = spark_decimal_types(sql.parse(query)[0], conn)
    result = conn.sql(sql.duckdb_sql(statement))
    assert [str(t) for t in result.types] == ["DECIMAL(38,2)", "DECIMAL(34,6)", "DECIMAL(38,2)"]
    assert result.fetchall() == [(Decimal("-0.03"), Decimal("-0.010000"), Decimal("-0.07"))]


def test_a_whole_number_sum_and_an_empty_sum(conn):
    conn.execute("CREATE TEMP VIEW whole AS SELECT CAST(i AS DECIMAL(30,0)) - 5 AS x FROM range(3) r(i)")
    assert rows(conn, "SELECT sum(x) AS s FROM whole") == [(Decimal("-12"),)]
    assert rows(conn, "SELECT sum(x) AS s FROM whole WHERE x > 100") == [(None,)]
