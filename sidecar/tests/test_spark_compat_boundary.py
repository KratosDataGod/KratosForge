"""PySpark calls in spark_compat at their exact edges (boundary re-score of batch 8, mutation batch 157,
docs/quality/mutation-testing.md). Expected values are what Spark gives for the same data."""

from __future__ import annotations

import datetime as dt
import importlib

import pytest

from fabric_desktop import spark_compat as SC
from fabric_desktop import spark_local


@pytest.fixture(scope="module")
def spark():
    spark_local._alias_pyspark()
    return spark_local.session()


@pytest.fixture(scope="module")
def F(spark):
    return importlib.import_module("pyspark.sql.functions")


def test_the_typed_columns_kept_are_exactly_the_most(monkeypatch, F):
    kept = {f"c{i}": ("size", ()) for i in range(SC._TYPED_KEPT - 1)}
    monkeypatch.setattr(SC, "_TYPED", kept)
    SC._typed("size", F.col("a"))
    assert len(kept) == SC._TYPED_KEPT and "c0" in kept
    SC._typed("size", F.col("b"))
    assert len(kept) == SC._TYPED_KEPT and "c0" not in kept


def test_split_with_a_limit_of_0_keeps_every_part(spark, F):
    df = spark.createDataFrame([("a,b,c",)], ["s"])
    row = df.select(F.split("s", ",", 0).alias("p"), F.split("s", "", 0).alias("c")).first()
    assert row["p"] == ["a", "b", "c"] and row["c"] == ["a", ",", "b", ",", "c"]


def test_a_frame_bound_of_exactly_unbounded_following_is_unbounded():
    assert SC._bound(SC.Window.unboundedFollowing) == "UNBOUNDED FOLLOWING"
    assert SC._bound(SC.Window.unboundedPreceding) == "UNBOUNDED PRECEDING"


def test_a_nested_type_at_depth_0_adds_no_lines(spark):
    from duckdb.experimental.spark.sql import types as T
    nested = T.StructType([T.StructField("x", T.IntegerType())])
    out: list[str] = []
    SC._tree_lines(nested, " |", 0, out)
    assert out == []
    SC._tree_lines(nested, " |", 1, out)
    assert out and "x" in out[0]


def test_a_double_is_written_plainly_from_10_to_the_minus_3_up_to_10_to_the_7():
    assert SC._java_double(0.001) == "0.001" and SC._java_double(0.000999) == "9.99E-4"
    assert SC._java_double(9999999.0) == "9999999.0" and SC._java_double(1e7) == "1.0E7"


def test_format_number_with_0_and_with_18_places_is_done_on_exact_decimals(spark, F):
    df = spark.createDataFrame([(0.1, 1e23)], ["a", "b"])
    row = df.select(F.format_number("a", 18).alias("a"), F.format_number("b", 0).alias("b")).first()
    assert row["a"] == "0.100000000000000000" and row["b"] == "100,000,000,000,000,000,000,000"


def test_show_with_exactly_n_rows_says_nothing_about_more(spark):
    assert SC.show_string(spark.createDataFrame([(1,), (2,)], ["n"]), 2) == \
        "+---+\n|  n|\n+---+\n|  1|\n|  2|\n+---+\n"


def test_a_star_after_one_name_is_that_name():
    assert SC._star_prefix("a.*") == ["a"] and SC._star_prefix("`a.b`.*") == ["a.b"]


def test_describe_on_exactly_100000_rows_takes_sparks_running_stddev(spark):
    values = [1e15 + (i % 2) for i in range(SC._WELFORD_ROWS)]
    n = avg = m2 = 0.0
    for v in values:  # Spark's row-by-row (Welford) update
        n += 1
        delta = v - avg
        avg += delta / n
        m2 += delta * (delta - delta / n)
    df = spark.sql(f"SELECT 1e15 + CAST(range % 2 AS DOUBLE) AS v FROM range({SC._WELFORD_ROWS})")
    stddev = {r["summary"]: r["v"] for r in df.describe("v").collect()}["stddev"]
    assert float(stddev) == (m2 / (n - 1)) ** 0.5


def test_a_zero_interval_has_no_sign():
    assert SC._interval(dt.timedelta(0)) == "INTERVAL '0 00:00:00' DAY TO SECOND"


def test_locate_with_its_text_given_by_name(spark, F):
    df = spark.createDataFrame([("abc",)], ["s"])
    assert df.select(F.locate("b", str="s").alias("p")).first()["p"] == 2


def test_show_truncates_at_exactly_its_width_and_keeps_4_or_fewer_whole(spark):
    df = spark.createDataFrame([("abcdefghijklmnopqrstuvwxyz", "abcdefghijklmnopqrst")], ["s", "t"])
    assert "|abcdefghijklmnopqrstuvwxyz|abcdefghijklmnopqrst|" in SC.show_string(df, 20, False)
    assert "|abcdefghijklmnopq...|abcdefghijklmnopqrst|" in SC.show_string(df, 20, True)  # exactly 20 kept
    assert "|a...|a...|" in SC.show_string(df, 20, 4)
    assert "|abc|abc|" in SC.show_string(df, 20, 3)


def test_a_star_after_an_empty_first_name_isnt_a_prefix():
    assert SC._star_prefix(".a.*") is None


def test_locate_from_position_1(spark, F):
    df = spark.createDataFrame([("abc",)], ["s"])
    assert df.select(F.locate("c", "s", 1).alias("p")).first()["p"] == 3
    assert df.select(F.locate("", "s", 1).alias("p")).first()["p"] == 1  # an empty substr is found at 1


def test_drop_of_another_frames_column_that_this_frame_has_once(spark):
    df = spark.createDataFrame([("a", "b")], ["s", "t"])
    other = spark.createDataFrame([("x",)], ["s"])
    assert df.drop(other.s).columns == ["t"]

