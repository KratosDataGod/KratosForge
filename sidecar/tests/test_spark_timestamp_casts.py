"""Casts between timestamps and numbers use seconds since 1970 (UTC), as in Spark (EA-014)."""

from __future__ import annotations

import duckdb
import pytest
import sqlglot

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel


def run(code):
    out = Kernel().run("from pyspark.sql import functions as F\n" + code)
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


def test_spark_sql_timestamp_to_bigint_and_back():
    status, text = run("print(spark.sql(\"SELECT CAST(TIMESTAMP '1970-01-01 00:00:10' AS BIGINT) AS s, "
                       "CAST(10 AS TIMESTAMP) AS t\").collect())")
    assert status == "ok", text
    assert "s=10" in text and "1970, 1, 1, 0, 0, 10" in text


def test_fractions_negatives_and_other_sources():
    status, text = run("r = spark.sql(\"SELECT CAST(TIMESTAMP '1969-12-31 23:59:59.5' AS BIGINT) AS n, "
                       "CAST(10.5 AS TIMESTAMP) AS f, CAST(CAST(10 AS DOUBLE) AS TIMESTAMP) AS d, "
                       "try_cast(TIMESTAMP '2024-01-01 00:00:00' AS INT) AS i, CAST(CAST(NULL AS INT) AS TIMESTAMP) AS z, "
                       "CAST('2024-01-02' AS TIMESTAMP) AS s, CAST(DATE '2024-01-02' AS TIMESTAMP) AS dt\").first()\n"
                       "print(r.n, r.f, r.d, r.i, r.z, r.s, r.dt)")
    assert status == "ok", text
    # Whether a timestamp carries +00:00 depends on the DuckDB version's reading of Spark's TIMESTAMP.
    assert text.replace("+00:00", "").split() == ["-1", "1970-01-01", "00:00:10.500000", "1970-01-01", "00:00:10",
                                                  "1704067200", "None", "2024-01-02", "00:00:00", "2024-01-02",
                                                  "00:00:00"]


def test_dataframe_timestamp_to_long():
    status, text = run("t = spark.sql(\"SELECT TIMESTAMP '1970-01-01 00:00:10' AS ts\")\n"
                       "print([tuple(r) for r in t.select(F.col('ts').cast('long'), F.col('ts').cast('int')).collect()])")
    assert status == "ok", text
    assert "[(10, 10)]" in text


def test_databricks_sql_casts():
    statement = sql.spark_fixes(sqlglot.parse_one(
        "SELECT CAST(TIMESTAMP '1970-01-01 00:00:10' AS BIGINT) AS s, CAST(10 AS TIMESTAMP) AS t", read="databricks"))
    conn = duckdb.connect()
    conn.execute("SET TimeZone = 'UTC'")
    s, t = conn.execute(sql.duckdb_sql(statement)).fetchall()[0]
    assert s == 10 and t.timestamp() == 10


@pytest.mark.parametrize("query", ["SELECT CAST('2024-01-02' AS TIMESTAMP)", "SELECT CAST(x AS DATE) FROM t"])
def test_casts_not_from_numbers_or_timestamps_are_left_alone(query):
    tree = sql.spark_integer_casts(sqlglot.parse_one(query, read="spark"))
    assert "typeof" not in sql.duckdb_sql(tree)


def test_out_of_range_nan_and_huge_numbers_are_null_outside_ansi():
    status, text = run("print(spark.sql(\"SELECT try_cast(1e20 AS TIMESTAMP) AS a, CAST(9223372036854775807 AS TIMESTAMP) "
                       "AS b, CAST(CAST('NaN' AS DOUBLE) AS TIMESTAMP) AS c, CAST(-1e20 AS TIMESTAMP) AS d\").collect())")
    assert status == "ok", text
    assert "a=None, b=None, c=None, d=None" in text


def test_databricks_ansi_cast_out_of_range_stops():
    statement = sql.spark_fixes(sqlglot.parse_one("SELECT CAST(1e20 AS TIMESTAMP)", read="databricks"))
    with pytest.raises(duckdb.Error):
        duckdb.connect().execute(sql.duckdb_sql(statement)).fetchall()


def test_dataframe_number_to_timestamp():
    status, text = run("from pyspark.sql.types import TimestampType\n"
                       "d = spark.createDataFrame([(10,), (None,)], 'n int')\n"
                       "out = d.select(F.col('n').cast('timestamp'), F.col('n').cast(TimestampType()).alias('z'))\n"
                       "print(out.columns, [(r[0], r[1]) for r in out.collect()])")
    assert status == "ok", text
    assert "['CAST(" in text and "', 'z']" in text  # DuckDB's own name for the cast, which varies by version
    assert text.count("1970, 1, 1, 0, 0, 10") == 2 and "(None, None)" in text


def test_sessions_are_utc_and_casts_follow_the_session_time_zone():
    """Seconds are counted in UTC, and a timestamp literal is read in the session's zone, as in Spark."""
    status, text = run("print(spark.conf.get('spark.sql.session.timeZone'))\n"
                       "q = \"SELECT CAST(TIMESTAMP '1970-01-01 00:00:10' AS BIGINT) AS s\"\n"
                       "print(spark.sql(q).first().s)\n"
                       "spark.conf.set('spark.sql.session.timeZone', 'America/New_York')\n"
                       "print(spark.sql(q).first().s, spark.sql('SELECT CAST(10 AS TIMESTAMP) AS t').first().t.timestamp())")
    assert status == "ok", text
    assert text.split() == ["UTC", "10", "18010", "10.0"]
