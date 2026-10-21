"""The PySpark API this PC offers, pinned so a DuckDB upgrade can't change it unnoticed (KF-DE-008).

``spark`` in a notebook is DuckDB's experimental PySpark-compatible API (``spark_local.py``).
SUPPORTED lists what notebooks may rely on: each must keep working. EXTENDED lists everyday calls
DuckDB's API lacks and KratosForge adds on top (spark_api.py and friends, tested in depth in
test_spark_api.py and test_spark_compat.py); here they are only smoke-checked through the same
session, so an engine upgrade that breaks what they build on fails a test."""

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
    return spark.createDataFrame([(1, "EU", 10.0), (2, "US", 5.0), (3, "EU", 7.5)], ["id", "region", "amount"])


def _rows(frame):
    return [tuple(r) for r in frame.collect()]


SUPPORTED = {
    "select": lambda s, F, df: _rows(df.select("id", "region").orderBy("id")) == [(1, "EU"), (2, "US"), (3, "EU")],
    "filter with a column": lambda s, F, df: df.filter(F.col("amount") > 6).count() == 2,
    "where with SQL text": lambda s, F, df: df.where("amount > 6").count() == 2,
    "withColumn": lambda s, F, df: _rows(df.withColumn("x", F.col("amount") * 2).select("x").orderBy("x")) == [(10.0,), (15.0,), (20.0,)],
    "withColumnRenamed": lambda s, F, df: df.withColumnRenamed("id", "key").columns == ["key", "region", "amount"],
    "drop": lambda s, F, df: df.drop("amount").columns == ["id", "region"],
    "groupBy agg sum and count": lambda s, F, df: _rows(df.groupBy("region").agg(F.sum("amount").alias("t"), F.count("id").alias("n"))
                                                          .orderBy("region")) == [("EU", 17.5, 2), ("US", 5.0, 1)],
    "groupBy count": lambda s, F, df: sorted(_rows(df.groupBy("region").count())) == [("EU", 2), ("US", 1)],
    "groupBy avg": lambda s, F, df: df.groupBy("region").agg(F.avg("amount").alias("a")).count() == 2,
    "join": lambda s, F, df: df.join(s.createDataFrame([("EU", "Europe")], ["region", "name"]), "region", "left").count() == 3,
    "orderBy descending": lambda s, F, df: _rows(df.orderBy(F.col("amount").desc()).select("id")) == [(1,), (3,), (2,)],
    "sort": lambda s, F, df: _rows(df.sort("id").select("id")) == [(1,), (2,), (3,)],
    "distinct": lambda s, F, df: df.select("region").distinct().count() == 2,
    "dropDuplicates": lambda s, F, df: df.dropDuplicates(["region"]).count() == 2,
    "union": lambda s, F, df: df.union(df).count() == 6,
    "unionByName": lambda s, F, df: df.unionByName(df).count() == 6,
    "limit, first, head": lambda s, F, df: df.limit(1).count() == 1 and df.first() is not None and len(df.head(2)) == 2,
    "when / otherwise": lambda s, F, df: sorted(_rows(df.select(F.when(F.col("amount") > 6, "hi").otherwise("lo")))) == [("hi",), ("hi",), ("lo",)],
    "lit, cast, concat, upper, coalesce": lambda s, F, df: df.select(F.lit(1), F.col("id").cast("string"),
                                                                     F.concat(F.col("region"), F.lit("-")), F.upper("region"),
                                                                     F.coalesce(F.col("amount"), F.lit(0))).count() == 3,
    "isNull, isin": lambda s, F, df: df.filter(F.col("amount").isNull()).count() == 0 and df.filter(F.col("region").isin("EU")).count() == 2,
    "to_date": lambda s, F, df: str(s.createDataFrame([("2026-01-02",)], ["d"]).select(F.to_date("d")).first()[0]) == "2026-01-02",
    "schema and dtypes": lambda s, F, df: [f.name for f in df.schema.fields] == ["id", "region", "amount"]
                                          and dict(df.dtypes)["amount"] == "double",
    "temp view and spark.sql": lambda s, F, df: (df.createOrReplaceTempView("kfde008"),
                                                 s.sql("SELECT COUNT(*) AS c FROM kfde008").first()[0] == 3)[1],
    "window functions in spark.sql": lambda s, F, df: (df.createOrReplaceTempView("kfde008w"), _rows(s.sql(
        "SELECT id, ROW_NUMBER() OVER (PARTITION BY region ORDER BY amount) AS rn FROM kfde008w ORDER BY id"))
        == [(1, 2), (2, 1), (3, 1)])[1],
}

# Everyday PySpark that DuckDB's API lacks and KratosForge adds (#393, #409 and the compat layer).
def _window(s, F, df):
    w = importlib.import_module("pyspark.sql.window").Window.partitionBy("region").orderBy(F.col("amount").desc())
    return _rows(df.withColumn("n", F.row_number().over(w)).select("id", "n").orderBy("id")) == [(1, 1), (2, 1), (3, 2)]


EXTENDED = {
    "Window and F.row_number": _window,
    "F.explode": lambda s, F, df: sorted(r[0] for r in s.createDataFrame([(1, [1, 2])], ["id", "xs"])
                                         .select(F.explode("xs")).collect()) == [1, 2],
    "F.current_timestamp": lambda s, F, df: df.select(F.current_timestamp()).first()[0] is not None,
    "F.date_format": lambda s, F, df: df.select(F.date_format(F.to_date(F.lit("2026-10-21")), "yyyy-MM-dd")).first()[0]
                                      == "2026-10-21",
    "F.count('*')": lambda s, F, df: _rows(df.groupBy("region").agg(F.count("*")).orderBy("region")) == [("EU", 2), ("US", 1)],
    "df.agg": lambda s, F, df: _rows(df.agg(F.max("amount"))) == [(10.0,)],
    "df.selectExpr": lambda s, F, df: _rows(df.selectExpr("id * 2 AS d").orderBy("d")) == [(2,), (4,), (6,)],
    "df.fillna": lambda s, F, df: s.createDataFrame([(1, None), (2, 1.0)], ["id", "v"]).fillna(0).orderBy("id").first()[1] == 0,
    "df.na.drop": lambda s, F, df: s.createDataFrame([(1, None), (2, 1.0)], ["id", "v"]).na.drop().count() == 1,
    "pivot": lambda s, F, df: len(df.groupBy("id").pivot("region", ["EU", "US"]).sum("amount").columns) == 3,
}


@pytest.mark.parametrize("name", sorted(SUPPORTED))
def test_supported_pyspark_api_keeps_working(name, spark, F, df):
    assert SUPPORTED[name](spark, F, df), f"{name} gives a different answer on this PC now"


@pytest.mark.parametrize("name", sorted(EXTENDED))
def test_everyday_pyspark_added_on_top_keeps_working(name, spark, F, df):
    assert EXTENDED[name](spark, F, df), f"{name} gives a different answer on this PC now"
