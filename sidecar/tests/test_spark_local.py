"""The PySpark API this PC offers, pinned so a DuckDB upgrade can't change it unnoticed (KF-DE-008).

``spark`` in a notebook is DuckDB's experimental PySpark-compatible API (``spark_local.py``).
SUPPORTED lists what notebooks may rely on: each must keep working. KNOWN_GAPS lists common
PySpark calls DuckDB doesn't offer yet: if one starts working after an upgrade, the test says so,
so the list in docs/notebooks.md is moved, not left stale. Both lists match that page."""

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

# Common PySpark calls DuckDB's API lacks today (duckdb pinned in constraints.txt). In SQL, most
# have an equivalent: ROW_NUMBER() OVER (...), UNNEST, CURRENT_TIMESTAMP, strftime.
KNOWN_GAPS = {
    "Window": lambda s, F, df: importlib.import_module("pyspark.sql.window").Window,
    "F.row_number": lambda s, F, df: F.row_number,
    "F.explode": lambda s, F, df: F.explode,
    "F.current_timestamp": lambda s, F, df: F.current_timestamp,
    "F.date_format": lambda s, F, df: F.date_format,
    "F.count('*')": lambda s, F, df: df.groupBy("region").agg(F.count("*")).collect(),
    "df.agg": lambda s, F, df: df.agg(F.max("amount")).collect(),
    "df.selectExpr": lambda s, F, df: df.selectExpr("id * 2 AS d").collect(),
    "df.fillna": lambda s, F, df: df.fillna(0).collect(),
    "df.na": lambda s, F, df: df.na.drop().collect(),
    "pivot": lambda s, F, df: df.groupBy("id").pivot("region").sum("amount").collect(),
}


@pytest.mark.parametrize("name", sorted(SUPPORTED))
def test_supported_pyspark_api_keeps_working(name, spark, F, df):
    assert SUPPORTED[name](spark, F, df), f"{name} gives a different answer on this PC now"


@pytest.mark.parametrize("name", sorted(KNOWN_GAPS))
def test_known_gaps_are_still_gaps(name, spark, F, df):
    try:
        KNOWN_GAPS[name](spark, F, df)
    except Exception:  # noqa: BLE001 - any failure means it's still a gap
        return
    pytest.fail(f"{name} works on this PC now: move it from KNOWN_GAPS to SUPPORTED and update docs/notebooks.md")


def test_the_docs_list_the_same_gaps():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "docs" / "notebooks.md").read_text(encoding="utf-8")
    missing = [n for n in KNOWN_GAPS if f"`{n}`" not in page]
    assert not missing, f"docs/notebooks.md doesn't mention: {missing}"
