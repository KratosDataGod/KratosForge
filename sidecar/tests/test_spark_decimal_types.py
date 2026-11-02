"""Spark SQL on this PC gives Spark's decimal result types and values (EA-004, part 2)."""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel
from fabric_desktop.spark_decimals import spark_decimal_types


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW t AS SELECT CAST(10 AS DECIMAL(10,2)) AS a, CAST(3 AS DECIMAL(10,2)) AS b, "
              "CAST(2 AS INTEGER) AS n, CAST(5 AS BIGINT) AS l, CAST(99999999.99 AS DECIMAL(10,2)) AS big, "
              "CAST(1 AS DECIMAL(38,18)) AS wide, 1.5::DOUBLE AS f, 'x' AS s")
    return c


def answer(conn, query):
    statement = spark_decimal_types(sql.parse(query)[0], conn)
    rel = conn.sql(sql.duckdb_sql(statement))
    return [str(t) for t in rel.types], list(rel.fetchall()[0])


@pytest.mark.parametrize("expr, kind, value", [
    ("a / b", "DECIMAL(23,13)", Decimal("3.3333333333333")),
    ("a * b", "DECIMAL(21,4)", Decimal("30.0000")),
    ("a + b", "DECIMAL(11,2)", Decimal("13.00")),
    ("a - b", "DECIMAL(11,2)", Decimal("7.00")),
    ("a * n", "DECIMAL(21,2)", Decimal("20.00")),     # an INT column is decimal(10,0)
    ("a * l", "DECIMAL(31,2)", Decimal("50.00")),     # a BIGINT column is decimal(20,0)
    ("a / 3", "DECIMAL(14,6)", Decimal("3.333333")),  # the literal 3 is decimal(1,0)
    ("a * 1.5", "DECIMAL(13,3)", Decimal("15.000")),  # the literal 1.5 is decimal(2,1)
    ("-a / b", "DECIMAL(23,13)", Decimal("-3.3333333333333")),
    ("(a + b) / b", "DECIMAL(24,13)", Decimal("4.3333333333333")),  # nested: (11,2) / (10,2)
    ("big * big", "DECIMAL(21,4)", Decimal("9999999998000000.0001")),  # DuckDB alone overflows DECIMAL(18)
    ("wide / wide", "DECIMAL(38,6)", Decimal("1.000000")),  # over 38 digits: scale cut to 6
])
def test_arithmetic_types_and_values(conn, expr, kind, value):
    assert answer(conn, f"SELECT {expr} AS v FROM t") == ([kind], [value])


def test_division_rounds_half_up_and_by_zero_is_null(conn):
    types, values = answer(conn, "SELECT CAST(2 AS DECIMAL(10,2)) / CAST(3 AS DECIMAL(10,2)), "
                                 "CAST(-2 AS DECIMAL(10,2)) / CAST(3 AS DECIMAL(10,2)), "
                                 "CAST(1 AS DECIMAL(10,2)) / CAST(0 AS DECIMAL(10,2)), "
                                 "CAST(NULL AS DECIMAL(10,2)) / b FROM t")
    assert values == [Decimal("0.6666666666667"), Decimal("-0.6666666666667"), None, None]


def test_a_dividend_too_wide_to_scale_exactly_still_has_sparks_type(conn):
    types, values = answer(conn, "SELECT CAST(12345678901234567890 AS DECIMAL(38,0)) / CAST(7 AS DECIMAL(38,0)) FROM t")
    assert types == ["DECIMAL(38,6)"]
    assert abs(values[0] - Decimal("1763668414462081127.142857")) < Decimal("1e3")  # through DOUBLE


@pytest.mark.parametrize("expr, kind, value", [
    ("sum(a)", "DECIMAL(20,2)", Decimal("10.00")),
    ("avg(a)", "DECIMAL(14,6)", Decimal("10.000000")),
    ("avg(DISTINCT a)", "DECIMAL(14,6)", Decimal("10.000000")),
    ("avg(b) FILTER (WHERE b > 0)", "DECIMAL(14,6)", Decimal("3.000000")),
    ("avg(wide)", "DECIMAL(38,22)", Decimal("1.0000000000000000000000")),
    ("sum(a) / count(*)", "DECIMAL(38,20)", Decimal("10." + "0" * 20)),  # (20,2) / BIGINT (20,0)
])
def test_aggregate_types(conn, expr, kind, value):
    assert answer(conn, f"SELECT {expr} AS v FROM t") == ([kind], [value])


def test_avg_rounds_half_up_over_rows():
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW r AS SELECT CAST(v AS DECIMAL(5,2)) AS v FROM (VALUES (1), (1), (2)) AS x(v)")
    assert answer(c, "SELECT avg(v) FROM r") == (["DECIMAL(9,6)"], [Decimal("1.333333")])
    assert answer(c, "SELECT avg(v) OVER () FROM r") == (["DECIMAL(9,6)"], [Decimal("1.333333")])


@pytest.mark.parametrize("expr", ["n * l", "f * a", "a || s", "n / 2"])
def test_other_types_are_left_as_they_were(conn, expr):
    query = f"SELECT {expr} AS v FROM t"
    assert answer(conn, query)[0] == [str(t) for t in conn.sql(sql.duckdb_sql(sql.parse(query)[0])).types]


def test_spark_sql_in_a_notebook():
    out = Kernel().run("spark.createDataFrame([(1,)], 'i int').selectExpr('CAST(10 AS DECIMAL(10,2)) AS a', "
                       "'CAST(3 AS DECIMAL(10,2)) AS b').createOrReplaceTempView('t')\n"
                       "r = spark.sql('SELECT first(a / b) AS q, first(a * b) AS m, avg(a) AS av, sum(a) AS su FROM t')\n"
                       "print(r.dtypes, [tuple(x) for x in r.collect()])")
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    assert "('q', 'decimal(23,13)'), ('m', 'decimal(21,4)'), ('av', 'decimal(14,6)'), ('su', 'decimal(20,2)')" in text
    assert "Decimal('3.3333333333333'), Decimal('30.0000'), Decimal('10.000000'), Decimal('10.00')" in text
