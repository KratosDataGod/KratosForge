"""CAST(x AS STRING) and Column.cast("string") as Spark writes a timestamp and a double
(sql.spark_text_cast). DuckDB wrote '+00' after every timestamp and '1e+20' for a double. The
values below are what Apache Spark 3.5.5 gave for the same queries."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.engines.local_sql import _databricks
from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def one(spark, query):
    return spark.sql(query).collect()[0].asDict()


_STAMPS = ("FROM (SELECT TIMESTAMP '2026-01-02 03:04:05' t, TIMESTAMP '2026-01-02 03:04:05.120' t2, "
           "TIMESTAMP_NTZ '2026-01-02 03:04:05' t3)")


def test_a_timestamp_as_text_has_no_offset(spark):
    got = one(spark, f"SELECT CAST(t AS STRING) a, string(t) b, CAST(t2 AS STRING) e, CAST(t3 AS STRING) f, "
                     f"try_cast(t AS STRING) d {_STAMPS}")
    assert got == {"a": "2026-01-02 03:04:05", "b": "2026-01-02 03:04:05", "e": "2026-01-02 03:04:05.12",
                   "f": "2026-01-02 03:04:05", "d": "2026-01-02 03:04:05"}
    got = one(spark, "SELECT CAST(TIMESTAMP '2026-01-02 03:04:05.000001' AS STRING) a, "
                     "CAST(TIMESTAMP '0001-01-01 00:00:00' AS STRING) b, CAST(CAST(NULL AS TIMESTAMP) AS STRING) c")
    assert got == {"a": "2026-01-02 03:04:05.000001", "b": "0001-01-01 00:00:00", "c": None}


def test_a_timestamp_as_text_is_the_session_zones_wall_time(spark):
    spark.conf.set("spark.sql.session.timeZone", "America/New_York")
    try:
        assert one(spark, "SELECT CAST(TIMESTAMP '2026-01-02 03:04:05+00:00' AS STRING) x") == {"x": "2026-01-01 22:04:05"}
    finally:
        spark.conf.unset("spark.sql.session.timeZone")


def test_a_double_as_text_is_javas(spark):
    got = one(spark, "SELECT CAST(1e20 AS STRING) h, CAST(1234567.0D AS STRING) a, CAST(1.0E7D AS STRING) b, "
                     "CAST(0.0001D AS STRING) c, CAST(CAST(1.1 AS FLOAT) AS STRING) d, CAST(-0.0D AS STRING) e, "
                     "CAST(CAST('NaN' AS DOUBLE) AS STRING) f, CAST(x AS STRING) g FROM (SELECT 2.5D x)")
    assert got == {"h": "1.0E20", "a": "1234567.0", "b": "1.0E7", "c": "1.0E-4", "d": "1.1", "e": "-0.0", "f": "NaN",
                   "g": "2.5"}


def test_other_types_as_text_are_unchanged(spark):
    got = one(spark, "SELECT CAST(1.0D AS STRING) g, CAST(1.50 AS STRING) i, CAST(true AS STRING) j, "
                     "CAST(DATE '2026-01-02' AS STRING) k, CAST(7 AS STRING) l, CAST('x' AS STRING) m")
    assert got == {"g": "1.0", "i": "1.50", "j": "true", "k": "2026-01-02", "l": "7", "m": "x"}


def test_column_cast_to_string_keeps_its_name_and_writes_as_spark(spark):
    from pyspark.sql.types import StringType
    df = spark.sql("SELECT TIMESTAMP '2026-01-02 03:04:05' t, 1e20D d, 7 i")
    out = df.select(df.t.cast("string"), df.d.cast(StringType()), df.i.cast("string"))
    assert out.columns == ["t", "d", "i"]
    assert out.collect()[0].asDict() == {"t": "2026-01-02 03:04:05", "d": "1e+20", "i": "7"}  # see _text_cast


def test_databricks_sql_writes_a_timestamp_as_text_the_same_way():
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    [statement] = _databricks("SELECT CAST(TIMESTAMP '2026-01-02 03:04:05' AS STRING), CAST(1e20D AS STRING)")
    assert con.execute(sql.duckdb_sql(statement)).fetchone() == ("2026-01-02 03:04:05", "1.0E20")


def test_concat_and_pipes_with_text_write_their_parts_as_spark(spark):
    """Apache Spark 3.5.5 in UTC, for the same query."""
    got = one(spark, "SELECT concat('at ', TIMESTAMP '2026-01-02 03:04:05') a, 'x' || TIMESTAMP '2026-01-02 03:04:05' b, "
                     "concat('d', 1e20D) c, concat(t, '|') e, concat('n', NULL) f, "
                     "concat('y', 2.5D, 7, true, DATE '2026-01-02') g, concat(array(1), array(2)) h, "
                     "'a' || 'b' || t i, concat('k', CAST(t AS STRING)) k "
                     "FROM (SELECT TIMESTAMP '2026-01-02 03:04:05.12' t)")
    assert got == {"a": "at 2026-01-02 03:04:05", "b": "x2026-01-02 03:04:05", "c": "d1.0E20",
                   "e": "2026-01-02 03:04:05.12|", "f": None, "g": "y2.57true2026-01-02", "h": [1, 2],
                   "i": "ab2026-01-02 03:04:05.12", "k": "k2026-01-02 03:04:05.12"}


def test_concat_keeps_spark_column_names(spark):
    df = spark.sql("SELECT concat('at ', t), 'x' || t FROM (SELECT TIMESTAMP '2026-01-02 03:04:05' t)")
    assert df.columns == ["concat(at , t)", "concat(x, t)"]


def test_databricks_sql_concat_writes_a_timestamp_as_spark():
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    [statement] = _databricks("SELECT 'at ' || TIMESTAMP '2026-01-02 03:04:05', concat('d', 1e20D)")
    assert con.execute(sql.duckdb_sql(statement)).fetchone() == ("at 2026-01-02 03:04:05", "d1.0E20")


def test_nested_and_parenthesized_joins_write_their_parts_as_spark(spark):
    """Apache Spark 3.5.5 in UTC: a join inside another, with or without parentheses, is one join of strings."""
    got = one(spark, "SELECT 'a' || ('b' || t) p, 'a' || (t || t) q, 'a' || concat(t, t) r, concat(t, 'x' || t) u, "
                     "concat('a', (t)) v FROM (SELECT TIMESTAMP '2026-01-02 03:04:05' t)")
    stamp = "2026-01-02 03:04:05"
    assert got == {"p": f"ab{stamp}", "q": f"a{stamp}{stamp}", "r": f"a{stamp}{stamp}", "u": f"{stamp}x{stamp}",
                   "v": f"a{stamp}"}
