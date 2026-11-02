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
    statement = sql.duckdb_sql(sql.spark_fixes(sqlglot.parse_one("SELECT to_timestamp(x), CAST(y AS TIMESTAMP)",
                                                                  read="databricks")))
    # Text reaches the ANSI CAST, which stops on a bad value (numbers are read as seconds, EA-014).
    assert "ELSE CAST(x AS TIMESTAMP) END" in statement and "ELSE CAST(y AS TIMESTAMPTZ) END" in statement
    assert "TRY_CAST(x AS TIMESTAMP" not in statement and "TRY_CAST(y AS TIMESTAMP" not in statement
    legacy = sql.spark_legacy_times(sqlglot.parse_one("SELECT to_timestamp(x)", read="spark"))
    assert "TRY_CAST" in sql.duckdb_sql(legacy)


def test_timestamp_and_whole_number_casts_compose_on_column_cast():
    """Column.cast is wrapped twice: whole-number targets truncate as Spark does (EA-003), dates and
    timestamps give NULL for bad text (EA-015); other targets reach DuckDB's own cast."""
    status, text = run("e = spark.createDataFrame([('abc', 3.7, '2024-01-02 03:04:05'), ('2024-01-02', -3.7, 'x')],"
                       " 's string, x double, t string')\n"
                       "out = e.select(F.col('s').cast('timestamp').alias('ts'), F.col('x').cast('int').alias('i'),"
                       " F.col('t').cast(TimestampType()).alias('tt'), F.col('s').cast(DateType()).alias('d'),"
                       " F.col('x').cast('long').alias('l'), F.col('x').cast('string').alias('str'))\n"
                       "print([type(out.schema[c].dataType).__name__ for c in ('ts', 'tt')])\n"
                       # DuckDB 1.x hands a TimestampType back with its zone (UTC here), 2.x as a naive local
                       # time, as Spark does: the type above says which it is, the values are compared without it.
                       "print([tuple(v.replace(tzinfo=None) if hasattr(v, 'hour') else v for v in r)"
                       " for r in out.collect()])")
    assert status == "ok", text
    types, rows = text.strip().splitlines()
    assert types == "['TimestampType', 'TimestampType']"  # cast('timestamp') is TimestampType, not NTZ
    assert "(None, 3, datetime.datetime(2024, 1, 2, 3, 4, 5), None, 3, '3.7')" in rows
    assert "(datetime.datetime(2024, 1, 2, 0, 0), -3, None, datetime.date(2024, 1, 2), -3, '-3.7')" in rows


def test_time_cast_keeps_duckdbs_cast_for_numbers():
    """_time_cast alone (without the whole-number wrapper in front): a number cast to a date isn't
    silently NULL; text that isn't a date is."""
    status, text = run("from fabric_desktop import spark_compat\n"
                       "from duckdb.experimental.spark.sql.column import Column\n"
                       "import duckdb\n"
                       "plain = spark_compat._time_cast(lambda self, t: Column(self.expr.cast(duckdb.sqltype('DATE'))))\n"
                       "n = spark.createDataFrame([(1,)], 'n int')\n"
                       "print(d.select(plain(F.col('s'), 'date').alias('v')).collect())\n"
                       "n.select(plain(F.col('n'), 'date').alias('v')).collect()")
    assert status == "error" and "v=None" in text, text


def test_cast_to_timestamp_by_name_and_by_type_agree():
    """cast('timestamp') is Spark's TimestampType, a moment in time, as cast(TimestampType()) and Spark
    SQL's CAST AS TIMESTAMP are; DuckDB's own 'timestamp' has no time zone."""
    status, text = run("n = spark.createDataFrame([(10, '2024-01-02')], 'n int, s string')\n"
                       "out = n.select(F.col('n').cast('timestamp').alias('a'), F.col('n').cast(TimestampType()).alias('b'),"
                       " F.col('s').cast('timestamp').alias('c'), F.col('s').cast(TimestampType()).alias('d'))\n"
                       "print(out.relation.types)\n"
                       "print(spark.sql(\"SELECT CAST('2024-01-02' AS TIMESTAMP) AS e\").relation.types)")
    assert status == "ok", text
    by_cast, by_sql = text.strip().splitlines()
    assert by_cast == "[TIMESTAMP WITH TIME ZONE, TIMESTAMP WITH TIME ZONE, TIMESTAMP WITH TIME ZONE, TIMESTAMP WITH TIME ZONE]"
    assert by_sql == "[TIMESTAMP WITH TIME ZONE]"


def test_f_expr_casts_to_whole_numbers_as_spark_does():
    status, text = run("e = spark.createDataFrame([('3.7', 3.7)], 't string, x double')\n"
                       "print(tuple(e.select(F.expr('CAST(t AS INT)'), F.expr('CAST(x AS BIGINT) AS b'),"
                       " F.expr('CAST(-3.7 AS INT)')).first()))")
    assert status == "ok", text
    assert "(3, 3, -3)" in text
