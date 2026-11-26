"""WR-116: Spark 3.5's math functions on this PC, value-checked against Spark 3.5.3 (see spark_values.py).

round of a double, log, ln, log10, log2, pow, hypot, factorial, atan2, acos, asin, cot and try_subtract, where
this PC stopped with DuckDB's error or gave another answer: 28 queries in tools/spark_value_cases/math.txt, each
answered by Spark 3.5.3 in tests/fixtures/spark_3_5_3_math.json (its "values_from": the Spark 3.5.3 probe's answers
in spark-probe-findings-2026-11-18.md). Every case answers as Spark does, so nothing is refused."""

from __future__ import annotations

from decimal import Decimal

import pytest
from spark_values import Refused, check_part, parts

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel

REFUSED: list[Refused] = []


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


@pytest.mark.parametrize("cases", parts("math"))
def test_spark_math_functions_answer_as_spark_3_5_or_are_refused_by_name(spark, cases):
    check_part(spark, cases, REFUSED)


def one(query: str):
    return next(iter(sql.run(query, dialect="spark").to_pylist()[0].values()))


@pytest.mark.parametrize("query, value", [
    # Java's answers for inputs both engines take, kept as they were
    ("SELECT log(2, 8)", 3.0), ("SELECT log10(100)", 2.0), ("SELECT ln(1)", 0.0), ("SELECT pow(2, 10)", 1024.0),
    ("SELECT hypot(3, 4)", 5.0), ("SELECT factorial(20)", 2432902008176640000), ("SELECT factorial('5')", 120),
    ("SELECT acos(1)", 0.0), ("SELECT atan2(-0.0D, -1.0D)", 3.141592653589793),
    ("SELECT try_multiply(-2147483648, 2)", None), ("SELECT try_subtract(-2147483648, 1L)", -2147483649),
    ("SELECT typeof(try_subtract(-2147483648, 1))", "int"), ("SELECT typeof(factorial(5))", "bigint"),
    # round of a double rounds Double.toString's digits half up; a decimal keeps Spark's decimal type
    ("SELECT round(2.5D)", 3.0), ("SELECT round(-2.5D)", -3.0), ("SELECT round(0.125D, 2)", 0.13),
    ("SELECT round(149.99999999999997D, -2)", 100.0), ("SELECT round(1e300D, 2)", 1e300),
    ("SELECT round(1.5e-7D, 7)", 2e-7), ("SELECT round(-0.0D, 1)", 0.0),
    ("SELECT round(1.234, 2)", Decimal("1.23")), ("SELECT typeof(round(1.234, 2))", "decimal(4,2)"),
    ("SELECT typeof(round(CAST(1.005 AS FLOAT), 2))", "float"),
])
def test_math_functions_keep_javas_answers(query, value):
    assert one(query) == value


def test_round_of_a_double_column_rounds_its_written_digits():
    got = sql.run("SELECT round(d, 2) AS r FROM VALUES (1.005D), (9.995D), (CAST(NULL AS DOUBLE)) AS t(d) "
                  "ORDER BY d NULLS LAST", dialect="spark").to_pylist()
    assert [r["r"] for r in got] == [1.01, 10.0, None]


def duckdb_text(query: str) -> str:
    return sql.duckdb_sql(sql.parse(query)[0]).upper()


def test_an_argument_that_changes_between_reads_is_read_once():
    # rand() goes into a lambda argument, so the check and the logarithm see one draw
    for query in ("SELECT log(rand())", "SELECT pow(rand(), 2)", "SELECT hypot(rand(), 1)", "SELECT atan2(rand(), 1)"):
        text = duckdb_text(query)
        assert text.count("RANDOM()") == 1 and "LIST_TRANSFORM" in text, query


def test_a_column_argument_costs_no_lambda_per_row():
    assert "LIST_TRANSFORM" not in duckdb_text("SELECT ln(x), log(2, x), pow(x, 2), hypot(x, x), atan2(x, x) "
                                               "FROM VALUES (1.0D) AS t(x)")


def test_the_smallest_int_literal_stays_a_plain_cast_so_try_can_take_it():
    # -2147483648 is CAST(-2147483648 AS INT); written out as Spark's cast it held ERROR(), which DuckDB 2.0's
    # TRY refuses ("TRY can not be used in combination with a volatile function")
    assert "ERROR(" not in duckdb_text("SELECT try_add(-2147483648, 1)")
    got = sql.run("SELECT try_add(-2147483648, 1) a, CAST(-9999999999 AS INT) b, CAST(-200 AS TINYINT) c, "
                  "typeof(-2147483648) d", dialect="spark").to_pylist()
    assert got == [{"a": -2147483647, "b": -1410065407, "c": 56, "d": "int"}]
