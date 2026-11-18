"""Whole-number + - * on this PC as Spark 3.5.3 computes them with ANSI mode off: wrapping around past the
type's range as Java does, where DuckDB stops on an overflow. Each case is one of
tools/spark_value_cases/integers.txt, answered by Spark in tests/fixtures/spark_3_5_3_integers.json."""

from __future__ import annotations

import pytest
from spark_values import check, load

from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_whole_number_arithmetic_answers_as_spark_3_5(spark):
    check(spark, load("integers"), [])


def test_an_overflow_wraps_in_the_wider_type_and_keeps_its_name(spark):
    df = spark.sql(
        "SELECT 2147483647 + 1, CAST(id AS SMALLINT) * CAST(30000 AS SMALLINT) AS s FROM range(3)"
    )
    assert df.columns == ["(2147483647 + 1)", "s"]
    assert [tuple(r) for r in df.collect()] == [
        (-2147483648, 0),
        (-2147483648, 30000),
        (-2147483648, -5536),
    ]


def test_ansi_mode_keeps_duckdbs_own_arithmetic():
    import sqlglot

    from fabric_desktop import spark_decimals

    statement = sqlglot.parse_one("SELECT 2147483647 + 1", read="spark")
    assert "& 4294967295" not in spark_decimals.spark_decimal_types(
        statement.copy(), _con(), ansi=True
    ).sql("duckdb")
    assert "& 4294967295" in spark_decimals.spark_decimal_types(statement, _con()).sql(
        "duckdb"
    )


def _con():
    import duckdb

    return duckdb.connect()
