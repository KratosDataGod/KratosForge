"""PySpark calls this PC adds on top of DuckDB's Spark API (spark_compat.py), the ones the main tests
don't reach: ranking windows and frame bounds, date and time zone functions, grouped shortcuts, and
the arguments Spark itself refuses. Expected values are what Spark gives for the same data."""

from __future__ import annotations

import datetime
import importlib

import pytest

from fabric_desktop import spark_compat, spark_local


@pytest.fixture(scope="module")
def spark():
    spark_local._alias_pyspark()
    return spark_local.session()


@pytest.fixture(scope="module")
def F(spark):
    return importlib.import_module("pyspark.sql.functions")


@pytest.fixture(scope="module")
def W(spark):
    return importlib.import_module("pyspark.sql.window").Window


@pytest.fixture
def df(spark):
    return spark.createDataFrame([(1, "EU", 10.0), (2, "US", 5.0), (3, "EU", 7.5)], ["id", "region", "amount"])


def rows(frame):
    return [tuple(r) for r in frame.collect()]


# ---- windows


def test_ranking_windows_give_sparks_values(df, F, W):
    w = W.partitionBy("region").orderBy("amount")
    got = rows(df.select("id", F.dense_rank().over(w), F.percent_rank().over(w), F.cume_dist().over(w),
                         F.ntile(2).over(w)).orderBy("id"))
    assert got == [(1, 2, 1.0, 1.0, 2), (2, 1, 0.0, 1.0, 1), (3, 1, 0.0, 0.5, 1)]


def test_frame_bounds_by_range_and_rows(df, F, W):
    by_range = W.orderBy("id").rangeBetween(-1, 0)
    assert rows(df.select("id", F.sum("amount").over(by_range)).orderBy("id")) == [(1, 10.0), (2, 15.0), (3, 12.5)]
    ahead = W.orderBy("id").rowsBetween(0, 1)
    assert rows(df.select("id", F.sum("amount").over(ahead)).orderBy("id")) == [(1, 15.0), (2, 12.5), (3, 7.5)]
    everything = W.rowsBetween(W.unboundedPreceding, W.unboundedFollowing)
    assert {r[1] for r in rows(df.select("id", F.sum("amount").over(everything)))} == {22.5}


# ---- dates, times and text


@pytest.fixture
def day(spark, F):
    return spark.createDataFrame([("2026-11-09",)], ["d"]).select(F.to_date("d").alias("d"))  # a Monday


def test_date_functions(day, F):
    assert day.select(F.next_day("d", "Mon")).first()[0] == datetime.date(2026, 11, 16)  # strictly after
    assert day.select(F.trunc("d", "fortnight")).first()[0] is None  # a format Spark doesn't know is null


def test_time_zone_shifts(spark, F):
    ts = spark.createDataFrame([("2026-11-09 10:00:00",)], ["t"]).select(F.to_timestamp("t").alias("t"))
    assert ts.select(F.from_utc_timestamp("t", "Europe/Amsterdam")).first()[0] == datetime.datetime(2026, 11, 9, 11, 0)
    assert ts.select(F.to_utc_timestamp("t", "Europe/Amsterdam")).first()[0] == datetime.datetime(2026, 11, 9, 9, 0)


def test_now_json_uuid_and_a_wide_format_number(spark, df, F):
    assert df.select(F.unix_timestamp()).columns == ["unix_timestamp(current_timestamp(), yyyy-MM-dd HH:mm:ss)"]
    assert df.select(F.unix_timestamp()).first()[0] > 1_700_000_000
    doc = spark.createDataFrame([('{"a":{"b":3}}',)], ["j"])
    assert doc.select(F.get_json_object("j", "$.a.b")).first()[0] == "3"
    assert len(df.select(F.uuid()).first()[0]) == 36
    assert df.select(F.format_number("amount", 20)).first()[0] == "10." + "0" * 20


def test_a_day_time_interval_shows_as_spark_shows_it():
    assert spark_compat._interval(datetime.timedelta(days=1, hours=2, minutes=3, seconds=4, microseconds=500000)) \
        == "INTERVAL '1 02:03:04.5' DAY TO SECOND"
    assert spark_compat._interval(-datetime.timedelta(minutes=1)) == "INTERVAL '-0 00:01:00' DAY TO SECOND"


# ---- grouped shortcuts and nulls


def test_grouped_shortcuts(df):
    g = df.groupBy("region")
    assert sorted(rows(g.avg("amount"))) == [("EU", 8.75), ("US", 5.0)] and g.avg("amount").columns == ["region", "avg(amount)"]
    assert sorted(rows(g.min("amount"))) == [("EU", 7.5), ("US", 5.0)]
    assert sorted(rows(g.max("amount"))) == [("EU", 10.0), ("US", 5.0)]
    assert sorted(rows(g.count())) == [("EU", 2), ("US", 1)]


def test_fill_and_drop_nulls(spark, df):
    holes = spark.createDataFrame([(None, None)], "a boolean, b string")
    assert rows(holes.fillna(True)) == [(True, None)]  # a boolean fills only boolean columns
    assert df.dropna(subset=[]).count() == 3


# ---- what Spark refuses


@pytest.mark.parametrize("call, says", [
    (lambda df, F: df.select(F.printf(F.col("region"), "id")), "takes its format as text"),
    (lambda df, F: df.fillna([1]), "fillna takes a number, text, a boolean or a dict"),
    (lambda df, F: df.show(truncate="no"), "`truncate` should be a bool or an int"),
    (lambda df, F: df.show(vertical=1), "`vertical` should be a bool"),
    (lambda df, F: df.select(F.col("region").getItem(1.5)), "getItem takes a whole number or text"),
    (lambda df, F: df.select(F.col("region").getField(1)), "getField takes a field name"),
])
def test_arguments_spark_refuses_are_refused(df, F, call, says):
    with pytest.raises(TypeError, match=says):
        call(df, F)


def test_install_twice_changes_nothing(spark, df):
    spark_compat.install()
    assert df.select(["id", "region"]).columns == ["id", "region"]
