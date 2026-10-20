"""PySpark calls added on top of DuckDB's Spark API (spark_compat.py), checked against Spark's answers."""

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


@pytest.fixture
def df(spark):
    return spark.createDataFrame([(1, "EU", 10.0), (2, "US", 5.0), (3, "EU", 7.5), (4, None, None)],
                                 ["id", "region", "amount"])


def rows(frame):
    return [tuple(r) for r in frame.collect()]


def test_window_functions_with_partition_order_and_frames(spark, F, df):
    from pyspark.sql.window import Window
    w = Window.partitionBy("region").orderBy(F.col("amount").desc())
    out = df.where("region IS NOT NULL").select(
        "id", F.row_number().over(w).alias("rn"), F.rank().over(w).alias("rk"),
        F.lag("amount").over(w).alias("prev"), F.lead("amount", 1, 0.0).over(w).alias("next"),
        F.sum("amount").over(Window.partitionBy("region")).alias("total"),
        F.sum("amount").over(Window.orderBy("id").rowsBetween(Window.unboundedPreceding, Window.currentRow)).alias("run"))
    assert rows(out.orderBy("id")) == [(1, 1, 1, None, 7.5, 17.5, 10.0), (2, 1, 1, None, 0.0, 5.0, 15.0),
                                       (3, 2, 2, 10.0, 0.0, 17.5, 22.5)]
    from pyspark.sql import Window as W2
    assert W2 is Window


def test_count_star_agg_and_dict_agg(F, df):
    assert rows(df.groupBy("region").agg(F.count("*").alias("n")).orderBy("region")) == [(None, 1), ("EU", 2), ("US", 1)]
    assert rows(df.agg(F.max("amount"), F.count("*"))) == [(10.0, 4)]
    assert rows(df.agg({"amount": "sum"})) == [(22.5,)]
    assert df.agg({"amount": "sum"}).columns == ["sum(amount)"]
    with pytest.raises(ValueError, match="agg doesn't know"):
        df.agg({"amount": "median_ish"})


def test_select_expr_reads_spark_sql(df):
    out = df.selectExpr("id * 2 AS d", "upper(region) AS r", "CAST(amount AS STRING) s")
    assert out.columns == ["d", "r", "s"] and rows(out.orderBy("d"))[0] == (2, "EU", "10.0")


def test_fillna_and_na_follow_spark_types(df):
    assert rows(df.fillna(0).orderBy("id"))[-1] == (4, None, 0.0)  # numbers fill only number columns
    assert rows(df.fillna("n/a").orderBy("id"))[-1] == (4, "n/a", None)
    assert rows(df.fillna({"region": "?", "amount": 1}).orderBy("id"))[-1] == (4, "?", 1.0)
    assert rows(df.na.fill(0, subset=["id"]).orderBy("id"))[-1] == (4, None, None)
    assert df.na.drop().count() == 3 and df.dropna(how="all", subset=["region", "amount"]).count() == 3
    assert df.dropna(thresh=2).count() == 3
    assert rows(df.na.replace("EU", "Europe", "region").orderBy("id"))[0] == (1, "Europe", 10.0)


def test_pivot_with_and_without_values(F, df):
    out = df.where("region IS NOT NULL").groupBy("id").pivot("region").sum("amount")
    assert out.columns == ["id", "EU", "US"] and sorted(rows(out)) == [(1, 10.0, None), (2, None, 5.0), (3, 7.5, None)]
    out = df.groupBy().pivot("region", ["EU"]).agg(F.sum("amount"))
    assert rows(out) == [(17.5,)] and out.columns == ["EU"]
    out = df.groupBy("region").pivot("id", [1, 2]).agg(F.sum("amount").alias("t"), F.count("*").alias("n"))
    assert out.columns == ["region", "1_t", "1_n", "2_t", "2_n"]


def test_dates_and_times(spark, F):
    d = spark.createDataFrame([("2026-01-31 13:45:00",)], ["ts"]).select(F.to_timestamp("ts").alias("ts"))
    out = d.select(F.date_format("ts", "yyyy-MM-dd HH:mm").alias("f"), F.date_add("ts", 1).alias("a"),
                   F.datediff(F.lit("2026-02-10"), "ts").alias("dd"), F.trunc("ts", "MM").alias("m"))
    f, a, dd, m = out.first()
    assert (f, str(a), dd, str(m)[:10]) == ("2026-01-31 13:45", "2026-02-01", 10, "2026-01-01")
    now = spark.createDataFrame([(1,)], ["x"]).select(F.current_timestamp().alias("t")).first()[0]
    assert now.tzinfo is None


def test_arrays_and_distincts(spark, F):
    a = spark.createDataFrame([(1, [1, 2]), (2, []), (3, None)], ["id", "xs"])
    assert sorted(rows(a.select("id", F.explode("xs").alias("x")))) == [(1, 1), (1, 2)]
    assert sorted(rows(a.select("id", F.explode_outer("xs").alias("x"))), key=str) == [(1, 1), (1, 2), (2, None), (3, None)]
    assert rows(a.where("id = 1").select(F.size("xs"))) == [(2,)]
    b = spark.createDataFrame([("a", 1), ("a", 1), ("b", None)], ["k", "v"])
    assert rows(b.select(F.countDistinct("k"), F.count_distinct("k", "v"))) == [(2, 1)]
    assert sorted(b.groupBy("k").agg(F.collect_set("v").alias("s")).collect()[0]["s"] or []) in ([1], [])
    assert rows(b.select(F.max_by("k", "v"))) == [("a",)]
    ids = [r[0] for r in b.select(F.monotonically_increasing_id()).collect()]
    assert len(set(ids)) == 3


def test_describe_sample_subtract_unpivot_and_layout_calls(spark, df):
    d = {r[0]: r[1:] for r in df.describe("amount").collect()}
    assert d["count"] == ("3",) and d["max"] == ("10.0",) and d["min"] == ("5.0",)
    assert df.sample(0.0, 1).count() == 0 and df.sample(fraction=1.0).count() == 4
    with pytest.raises(NotImplementedError):
        df.sample(True, 0.5)
    other = spark.createDataFrame([(1, "EU", 10.0)], ["id", "region", "amount"])
    assert df.subtract(other).count() == 3
    long = df.where("id = 1").unpivot("id", ["region"], "k", "val")
    assert rows(long) == [(1, "region", "EU")]
    assert df.repartition(8).coalesce(1).persist().count() == 4


def test_self_join_works(df):
    assert df.join(df, "id").count() == 4
    assert df.crossJoin(df).count() == 16


def test_a_fabric_style_cell_runs_in_the_notebook_kernel():
    from fabric_desktop.notebook import Kernel
    out = Kernel().run(
        "from pyspark.sql import functions as F\n"
        "from pyspark.sql.window import Window\n"
        "df = spark.createDataFrame([(1, 'a', 3.0), (2, 'a', 1.0), (3, 'b', 2.0)], ['id', 'k', 'v'])\n"
        "w = Window.partitionBy('k').orderBy(F.col('v').desc())\n"
        "df.withColumn('rn', F.row_number().over(w)).filter('rn = 1').selectExpr('id', 'v * 10 AS v10').orderBy('id')")
    assert out["status"] == "ok", out
    assert out["outputs"][-1]["rows"] == [[1, 30.0], [3, 20.0]]


def test_spark_conf_keeps_settings_and_the_time_zone_changes_duckdb():
    spark = spark_local.session()  # the kernel test above restarted the session
    spark.conf.set("spark.sql.shuffle.partitions", 8)
    assert spark.conf.get("spark.sql.shuffle.partitions") == "8"
    spark.conf.set("spark.sql.parquet.vorder.enabled", True)
    assert spark.conf.get("spark.sql.parquet.vorder.enabled") == "true"
    assert spark.conf.get("not.set", None) is None
    with pytest.raises(KeyError):
        spark.conf.get("not.set")
    spark.conf.set("spark.sql.session.timeZone", "America/New_York")
    try:
        assert spark.sql("SELECT current_setting('TimeZone') AS tz").first()[0] == "America/New_York"
    finally:
        spark.conf.unset("spark.sql.session.timeZone")
    assert spark.sql("SELECT current_setting('TimeZone') AS tz").first()[0] == "UTC"
    assert spark.conf.get("spark.sql.session.timeZone") == "UTC"
