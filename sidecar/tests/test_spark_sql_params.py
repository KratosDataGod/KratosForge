"""spark.sql(sqlQuery, args=None, **kwargs) as PySpark 3.5 takes it (spark_sql_params). The values
below are what Apache Spark 3.5.5 gave for the same calls."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from fabric_desktop import spark_sql_params
from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def one(spark, query, args=None, **kwargs):
    return spark.sql(query, args=args, **kwargs).collect()[0].asDict()


@pytest.mark.parametrize("query, args, want", [
    ("SELECT :a + 1 AS x, typeof(:a) AS t", {"a": 7}, {"x": 8, "t": "int"}),
    ("SELECT :a AS v, typeof(:a) AS t", {"a": 2**40}, {"v": 2**40, "t": "bigint"}),
    ("SELECT ? + 1 AS x, ? AS s", [7, "it's \\ ok"], {"x": 8, "s": "it's \\ ok"}),
    ("SELECT :x AS x, typeof(:x) AS t, :f AS f, typeof(:f) AS tf, :n IS NULL AS n, :b AS b",
     {"x": Decimal("1.25"), "f": 1.5, "n": None, "b": True},
     {"x": Decimal("1.25"), "t": "decimal(3,2)", "f": 1.5, "tf": "double", "n": True, "b": True}),
    ("SELECT :d AS d, typeof(:d) AS td, typeof(:ts) AS tts", {"d": dt.date(2026, 1, 2), "ts": dt.datetime(2026, 1, 2, 3, 4, 5)},
     {"d": dt.date(2026, 1, 2), "td": "date", "tts": "timestamp"}),
    ("SELECT ':a' AS s, :a AS v -- :not_a_marker", {"a": 1}, {"s": ":a", "v": 1}),
    ("SELECT :a", {"a": 3}, {"3": 3}),
    ("SELECT :a AS v", {"a": "x'y"}, {"v": "x'y"}),
], ids=["int", "bigint", "positional", "decimal-double-null-bool", "date-timestamp", "string-and-comment", "name",
        "quote"])
def test_parameter_markers_bind_typed_values_as_spark_does(spark, query, args, want):
    assert one(spark, query, args) == want


def test_a_value_cant_change_the_query(spark):
    assert one(spark, "SELECT :v AS v", {"v": "1' OR '1'='1"}) == {"v": "1' OR '1'='1"}


def test_text_kwargs_are_quoted_literals_never_column_names(spark):
    df = spark.sql("SELECT 1 AS abc")
    assert one(spark, "SELECT {n} AS v, {t} AS s FROM {df}", n=3, t="abc", df=df) == {"v": 3, "s": "abc"}
    assert one(spark, "SELECT {t} AS s", t="a'b\\c") == {"s": "a'b\\c"}


def test_a_dataframe_kwarg_is_read_through_a_temporary_view(spark):
    df = spark.sql("SELECT 5 AS y, 'EU' AS region")
    assert one(spark, "SELECT * FROM {df} WHERE y = :v", {"v": 5}, df=df) == {"y": 5, "region": "EU"}
    assert one(spark, "SELECT a.y FROM {df} a JOIN {df} b ON a.y = b.y", df=df) == {"y": 5}


def test_a_plain_column_kwarg_is_its_name_and_anything_else_is_refused(spark):
    from pyspark.sql import functions as F
    df = spark.sql("SELECT 5 AS y, 'EU' AS region")
    assert one(spark, "SELECT {c} FROM {df}", c=df.region, df=df) == {"region": "EU"}
    assert one(spark, "SELECT {c} FROM {df}", c=F.col("y"), df=df) == {"y": 5}
    with pytest.raises(ValueError, match="plain column reference"):
        spark.sql("SELECT {c} FROM {df}", c=F.col("y") + 1, df=df)


def test_braces_are_left_alone_without_kwargs(spark):
    assert one(spark, "SELECT '{x}' AS s") == {"s": "{x}"}


@pytest.mark.parametrize("query, args, message", [
    ("SELECT :missing", {}, "uses :missing, and args has no value"),
    ("SELECT :a, ?", {"a": 1}, "not both"),
    ("SELECT ?", {"a": 1}, "args is a dict"),
    ("SELECT :a", [1], "args is a list"),
    ("SELECT ?, ?", [1], r"2 \? parameters and args has 1"),
    ("SELECT :a", {"a": object()}, "can't be a SQL parameter"),
    ("SELECT :a", "1", "must be a dict"),
])
def test_a_parameter_that_cant_be_bound_is_a_plain_error(spark, query, args, message):
    with pytest.raises(ValueError, match=message):
        spark.sql(query, args=args)


@pytest.mark.parametrize("query", ["SELECT j:a.b FROM t", "SELECT x::int", "SELECT `a:b`", "SELECT arr[0]:k",
                                   "SELECT \"x:y\""])
def test_paths_casts_and_quoted_names_are_not_markers(query):
    assert spark_sql_params._markers(query) == []


@pytest.mark.parametrize("text, name", [
    ('"unnamed_relation_1a2b"."region"', "region"), ("t.region", "region"), ('"we""ird"', 'we"ird')])
def test_a_qualified_column_kwarg_is_its_name_however_duckdb_writes_it(text, name):
    class Col:
        expr = text
    written = spark_sql_params.format_query("SELECT {c}", {"c": Col()}, lambda df, view: None,
                                            lambda v: False, lambda v: isinstance(v, Col))
    assert written == f"SELECT `{name}`"
