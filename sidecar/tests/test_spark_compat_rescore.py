"""Mutation batch 240: spark_compat's survivors on today's main. sequence over int columns, with and
without a step, is an array of ints; randn with a seed repeats and without one runs; posexplode puts pos
first; a ranking function over an unordered window stops; a window's frame is in its name; a typed
rewrite keeps the column's alias."""
from __future__ import annotations

import importlib

import pytest

from fabric_desktop import spark_local


@pytest.fixture(scope="module")
def spark():
    spark_local._alias_pyspark()
    return spark_local.session()


@pytest.fixture
def F():
    return importlib.import_module("pyspark.sql.functions")


def test_sequence_of_int_columns_is_an_array_of_ints_with_or_without_a_step(spark, F):
    df = spark.createDataFrame([(1, 5, 2)], "a int, b int, c int")
    out = df.select(F.sequence("a", "b", "c").alias("s"), F.sequence("b", "a").alias("t"))
    assert out.dtypes == [("s", "array<int>"), ("t", "array<int>")]
    assert [tuple(r) for r in out.collect()] == [([1, 3, 5], [5, 4, 3, 2, 1])]


def test_randn_with_a_seed_repeats_and_without_one_runs(spark, F):
    first = spark.range(3).select(F.randn(7).alias("r")).collect()
    assert first == spark.range(3).select(F.randn(7).alias("r")).collect()
    assert spark.range(2).select(F.randn().alias("r")).count() == 2


def test_posexplode_puts_pos_first(spark, F):
    df = spark.createDataFrame([([10, 20], {"k": 1})], ["arr", "m"])
    assert df.select(F.posexplode("arr")).columns == ["pos", "col"]
    assert df.select(F.posexplode("m")).columns == ["pos", "key", "value"]
    assert [tuple(r) for r in df.select(F.posexplode("arr")).collect()] == [(0, 10), (1, 20)]


def test_row_number_over_an_unordered_window_stops(spark, F):
    from pyspark.sql.window import Window
    df = spark.createDataFrame([(1, 2)], ["a", "b"])
    with pytest.raises(ValueError, match="row_number needs an ORDER BY in its window"):
        df.select(F.row_number().over(Window.partitionBy("a"))).collect()


def test_a_windows_frame_is_written_into_its_name(spark, F):
    from pyspark.sql.window import Window
    df = spark.createDataFrame([(1, 2)], ["a", "b"])
    assert df.select(F.sum("b").over(Window.orderBy("a").rowsBetween(-1, 1))).columns == [
        "sum(b) OVER (ORDER BY a ASC NULLS FIRST ROWS BETWEEN -1 FOLLOWING AND 1 FOLLOWING)"]


def test_size_of_a_map_keeps_its_alias(spark, F):
    df = spark.createDataFrame([({"k": 1},)], ["m"])
    assert df.select(F.size("m").alias("n")).columns == ["n"]
    assert [tuple(r) for r in df.select(F.size("m").alias("n")).collect()] == [(1,)]


def test_text_compared_with_a_number_keeps_its_name_and_value(spark, F):
    df = spark.createDataFrame([("1", 2)], ["s", "n"])
    out = df.select(F.col("s") == 1, (F.col("s") == F.lit(1)).alias("eq"))
    assert out.columns == ["(s = 1)", "eq"]
    assert [tuple(r) for r in out.collect()] == [(True, True)]


def test_dropping_the_only_column_by_its_column_is_refused(spark):
    one = spark.createDataFrame([(1,)], ["a"])
    with pytest.raises(ValueError, match=r"drop would leave no columns \(a\)"):
        one.drop(one.a)


def test_a_dataframe_alias_star_is_its_columns(spark):
    df = spark.createDataFrame([((1, "x"),)], "st struct<i:int,t:string>")
    assert df.alias("x").select("x.*").columns == ["st"]
    assert df.select("st.*").columns == ["i", "t"]


@pytest.mark.parametrize(("value", "text"), [(0.0, "0.0"), (-0.0, "-0.0"), (1.0, "1.0"), (0.1, "0.1")])
def test_a_float_is_written_as_java_writes_it(value, text):
    from fabric_desktop import spark_compat
    assert spark_compat._java_float(value) == text
