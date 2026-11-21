"""sum of a whole-number column is a BIGINT in Spark SQL and Databricks SQL cells on this PC, as in Spark and
Databricks (Databricks SQL's sum: "If expr is an integral number type, a BIGINT"), where DuckDB gave HUGEINT.
Past BIGINT's range Databricks SQL (ANSI mode on) stops with ARITHMETIC_OVERFLOW; Spark with ANSI mode off wraps,
which this PC never does: it stops by name. try_sum gives NULL. Each test checks the value and the type."""

from __future__ import annotations

import pyarrow as pa
import pytest

from fabric_desktop import spark_decimals
from fabric_desktop.engines import registry
from fabric_desktop.engines.contract import CellRequest, SessionConfig

DIALECTS = ("spark", "databricks")


@pytest.fixture
def session():
    s = registry.adapter("local-duckdb").open_session(SessionConfig("nb"))
    s.execute(CellRequest("CREATE TABLE t (g VARCHAR, v INTEGER, y TINYINT, h SMALLINT, b BIGINT, d DECIMAL(10,2))",
                          "sql"))
    s.execute(CellRequest("INSERT INTO t VALUES ('a', 1, 1, 1, 9223372036854775807, 1.50), ('a', 2, 2, 2, 1, 2.25), "
                          "('b', 5, 3, 3, 0, NULL), ('c', NULL, NULL, NULL, 0, NULL), ('d', 1, 4, 4, 0, 1.00)", "sql"))
    yield s
    s.close()


def run(session, code: str, dialect: str) -> pa.Table:
    result = session.execute(CellRequest(code, "sql", dialect, result_rows=100))
    assert result.status == "ok", result.outputs
    assert result.table is not None
    return result.table


def error(session, code: str, dialect: str) -> str:
    result = session.execute(CellRequest(code, "sql", dialect))
    assert result.status == "error", result.outputs
    return str(result.outputs[-1]["evalue"])


@pytest.mark.parametrize("dialect", DIALECTS)
def test_count_and_sum_of_an_int_column_are_bigint(session, dialect):
    # the parity case null_aggregation: COUNT(*) 3... here over t, SUM ignores the NULL
    table = run(session, "SELECT COUNT(*) AS n, COUNT(v) AS nv, SUM(v) AS s FROM t", dialect)
    assert table.schema.types == [pa.int64()] * 3
    assert table.to_pylist() == [{"n": 5, "nv": 4, "s": 9}]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_sum_in_group_by_having_is_bigint(session, dialect):
    # the parity case group_having
    table = run(session, "SELECT g, SUM(v) AS s FROM t GROUP BY g HAVING SUM(v) > 2 ORDER BY g", dialect)
    assert table.schema.types == [pa.string(), pa.int64()]
    assert table.to_pylist() == [{"g": "a", "s": 3}, {"g": "b", "s": 5}]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_sum_of_every_whole_number_type_distinct_and_over_a_window_is_bigint(session, dialect):
    table = run(session, "SELECT SUM(y) AS sy, SUM(h) AS sh, SUM(DISTINCT v) AS sd, SUM(1) AS one FROM t", dialect)
    assert table.schema.types == [pa.int64()] * 4
    assert table.to_pylist() == [{"sy": 10, "sh": 10, "sd": 8, "one": 5}]
    table = run(session, "SELECT g, SUM(v) OVER (PARTITION BY g) AS w FROM t WHERE g = 'a'", dialect)
    assert table.schema.types == [pa.string(), pa.int64()]
    assert table.column("w").to_pylist() == [3, 3]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_an_unnamed_sum_keeps_its_column_name(session, dialect):
    table = run(session, "SELECT sum(v) FROM t", dialect)
    assert table.column_names == ["sum(v)"] and table.schema.types == [pa.int64()]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_sum_of_an_empty_or_null_group_is_null(session, dialect):
    table = run(session, "SELECT SUM(v) AS s FROM t WHERE g = 'c'", dialect)
    assert table.schema.types == [pa.int64()] and table.to_pylist() == [{"s": None}]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_sum_of_a_decimal_keeps_sparks_decimal_type(session, dialect):
    table = run(session, "SELECT SUM(d) AS s FROM t", dialect)
    assert table.schema.types == [pa.decimal128(20, 2)]


def test_databricks_sum_past_bigint_stops_with_arithmetic_overflow(session):
    assert spark_decimals.LONG_SUM_OVERFLOW in error(session, "SELECT SUM(b) FROM t", "databricks")


def test_spark_sum_past_bigint_stops_by_name_and_never_wraps(session):
    message = error(session, "SELECT SUM(b) FROM t", "spark")
    assert spark_decimals.LONG_SUM_WRAPS in message
    assert "-9223372036854775808" not in message


@pytest.mark.parametrize("dialect", DIALECTS)
def test_try_sum_past_bigint_is_null_and_bigint(session, dialect):
    table = run(session, "SELECT try_sum(b) AS s, try_sum(v) AS t FROM t", dialect)
    assert table.schema.types == [pa.int64(), pa.int64()]
    assert table.to_pylist() == [{"s": None, "t": 9}]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_sum_at_bigints_edge_still_answers(session, dialect):
    table = run(session, "SELECT SUM(b) AS s FROM t WHERE g <> 'a' OR v = 1", dialect)
    assert table.schema.types == [pa.int64()] and table.to_pylist() == [{"s": 9223372036854775807}]
