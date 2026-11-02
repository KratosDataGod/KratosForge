"""Text that isn't a date or timestamp gives NULL, as in Spark with ANSI off (EA-015)."""

from __future__ import annotations

import pytest
import sqlglot

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel


def run(code):
    out = Kernel().run("from pyspark.sql import functions as F\n"
                       "from pyspark.sql.types import DateType, TimestampType\n"
                       "d = spark.createDataFrame([('abc',), ('2024-01-02',), (None,)], 's string')\n" + code)
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


@pytest.mark.parametrize("expr", ["F.col('s').cast('date')", "F.col('s').cast(DateType())", "F.to_date('s')",
                                  "F.to_date('s', 'yyyy-MM-dd')", "F.expr('to_date(s)')"])
def test_a_bad_date_string_is_null_in_the_dataframe_api(expr):
    status, text = run(f"print([r.v for r in d.select({expr}.alias('v')).collect()])")
    assert status == "ok", text
    assert "[None, datetime.date(2024, 1, 2), None]" in text


@pytest.mark.parametrize("expr", ["F.col('s').cast('timestamp')", "F.col('s').cast(TimestampType())",
                                  "F.to_timestamp('s')", "F.to_timestamp('s', 'yyyy-MM-dd')",
                                  "F.expr('to_timestamp(s)')"])
def test_a_bad_timestamp_string_is_null_in_the_dataframe_api(expr):
    status, text = run(f"print([r.v for r in d.select({expr}.alias('v')).collect()])")
    assert status == "ok", text
    assert "[None, datetime.datetime(2024, 1, 2, 0, 0" in text and text.rstrip().endswith("None]")


def test_names_and_formats():
    status, text = run("e = spark.createDataFrame([('05/03/2024 14:07',)], 's string')\n"
                       "out = e.select(F.to_timestamp('s', 'dd/MM/yyyy HH:mm'), F.to_date('s', 'dd/MM/yyyy HH:mm'),"
                       " F.col('s').cast('date'))\n"
                       "print(out.columns, [tuple(r) for r in out.collect()])")
    assert status == "ok", text
    # The cast keeps DuckDB's own name for it (CAST(s AS DATE); DuckDB 2.0 quotes the s).
    assert "['to_timestamp(s, dd/MM/yyyy HH:mm)', 'to_date(s, dd/MM/yyyy HH:mm)', 'CAST(" in text
    assert "datetime.datetime(2024, 3, 5, 14, 7), datetime.date(2024, 3, 5), None" in text


def test_other_casts_are_unchanged():
    status, text = run("print(d.select(F.col('s').cast('string').alias('a'), F.lit('7').cast('int').alias('b'),"
                       " F.lit('2024-01-02').cast('date').alias('c')).first())")
    assert status == "ok", text
    assert "a='abc', b=7, c=datetime.date(2024, 1, 2)" in text


@pytest.mark.parametrize("query", ["SELECT to_timestamp('abc') AS r", "SELECT to_timestamp('abc', 'dd/MM/yyyy') AS r"])
def test_spark_sql_to_timestamp_of_bad_text_is_null(query):
    status, text = run(f"print(spark.sql({query!r}).collect())")
    assert status == "ok", text
    assert "[Row(r=None)]" in text


def test_databricks_sql_keeps_ansi_casts():
    statement = sql.spark_fixes(sqlglot.parse_one("SELECT to_timestamp(x), CAST(y AS TIMESTAMP)", read="databricks"))
    assert "TRY_CAST" not in sql.duckdb_sql(statement)
    legacy = sql.spark_legacy_times(sqlglot.parse_one("SELECT to_timestamp(x)", read="spark"))
    assert "TRY_CAST" in sql.duckdb_sql(legacy)
