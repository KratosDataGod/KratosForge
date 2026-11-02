"""Casts to a whole-number type answer as Spark does (EA-003): a fraction is truncated, not rounded, and
text is read as Spark reads it."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.engines.local_sql import _databricks


def spark(query: str):
    return sql.run(query).to_pylist()[0]


def databricks(query: str):
    return duckdb.connect().execute(sql.duckdb_sql(_databricks(query)[-1])).fetchone()


@pytest.mark.parametrize("expr, want", [
    ("CAST(3.7 AS INT)", 3), ("CAST(-3.7 AS INT)", -3), ("CAST(-0.5 AS INT)", 0),
    ("CAST(3.7D AS BIGINT)", 3), ("CAST(-2.5F AS INT)", -2), ("CAST(3.7 AS TINYINT)", 3),
    ("CAST(CAST(2.5 AS DECIMAL(5,1)) AS SMALLINT)", 2), ("CAST(3.7E-5 AS INT)", 0), ("CAST(1E16 AS BIGINT)", 10**16),
    ("CAST('1.9' AS INT)", 1), ("CAST(' 2.9 ' AS INT)", 2), ("CAST('+5.' AS INT)", 5), ("CAST('-12' AS BIGINT)", -12),
    ("CAST('1e2' AS INT)", None), ("CAST('abc' AS INT)", None), ("CAST('99999999999' AS INT)", None),
    ("CAST(TRUE AS INT)", 1), ("CAST(12 AS INT)", 12), ("CAST(NULL AS INT)", None),
    ("CAST(CAST(9.99 AS INT) AS BIGINT)", 9),
    # try_cast follows ANSI rules: text with a fraction isn't a whole number, so it's NULL
    ("try_cast(3.7 AS INT)", 3), ("try_cast('1.9' AS INT)", None), ("try_cast(' 7 ' AS INT)", 7),
])
def test_spark_sql_casts(expr, want):
    assert spark(f"SELECT {expr} AS v")["v"] == want


def test_a_column_of_doubles_decimals_and_text():
    rows = sql.run("SELECT CAST(d AS INT) d, CAST(m AS INT) m, CAST(s AS INT) s FROM VALUES "
                   "(3.7D, CAST(3.7 AS DECIMAL(5,2)), '3.7'), (-3.7D, CAST(-3.7 AS DECIMAL(5,2)), '-3.7') AS t(d, m, s)"
                   ).to_pylist()
    assert rows == [{"d": 3, "m": 3, "s": 3}, {"d": -3, "m": -3, "s": -3}]


def test_databricks_sql_cast_truncates_and_stops_on_text_that_isnt_whole():
    """Databricks SQL's CAST is ANSI: a number truncates, and text with a fraction stops (CAST_INVALID_INPUT)."""
    assert databricks("SELECT CAST(3.7 AS INT), CAST(-3.7 AS INT), CAST('12' AS INT), try_cast('1.9' AS INT)") == \
        (3, -3, 12, None)
    with pytest.raises(duckdb.Error, match="CAST_INVALID_INPUT"):
        databricks("SELECT CAST('1.9' AS INT)")


def test_f_expr_casts_truncate():
    from fabric_desktop import spark_compat
    assert duckdb.sql(f"SELECT {spark_compat.spark_expr('CAST(3.7 AS INT)')}").fetchall()[0][0] == 3
