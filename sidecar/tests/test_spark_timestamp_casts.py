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
