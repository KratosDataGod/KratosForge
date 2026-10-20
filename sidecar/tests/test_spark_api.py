"""PySpark API that DuckDB's spark lacks (spark_api): spark.conf, sparkContext, catalog.tableExists
and the common DataFrame methods, so Fabric and Databricks notebook code runs on this PC."""

from pathlib import Path

import pytest

from fabric_desktop import spark_api
from fabric_desktop.notebook import Kernel

DOC = Path(__file__).resolve().parents[2] / "docs" / "notebook-spark-on-this-pc.md"


def run(code):
    out = Kernel().run(code)
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


def test_spark_conf_keeps_every_key_and_applies_the_time_zone():
    assert run("spark.conf.set('spark.sql.parquet.vorder.default', 'true')\n"
               "spark.conf.set('spark.sql.shuffle.partitions', 8)\n"
               "print(spark.conf.get('spark.sql.shuffle.partitions'), spark.conf.get('spark.sql.adaptive.enabled'),"
               " spark.conf.get('my.key', 'fallback'))") == "8 true fallback"
    assert run("spark.conf.isModifiable('spark.sql.ansi.enabled')") == "True"
    out = Kernel().run("spark.conf.get('my.unset.key')")
    assert out["status"] == "error" and "Pass a default" in str(out["outputs"])
    assert run("spark.conf.set('spark.sql.session.timeZone', 'UTC')\n"
               "spark.sql('SELECT current_setting(\\'TimeZone\\') AS tz').collect()[0]['tz']") == "'UTC'"
    text = run("rows = spark.conf.explain()")
    assert "spark.sql.parquet.vorder.default = true  (kept; takes effect in Fabric)" in text
    assert "spark.sql.session.timeZone = UTC  (applied on this PC)" in text


def test_builder_config_goes_to_spark_conf_and_spark_context_answers():
    assert run("from pyspark.sql import SparkSession\n"
               "s = SparkSession.builder.appName('x').config('spark.sql.shuffle.partitions', '4').getOrCreate()\n"
               "print(s is spark, spark.conf.get('spark.sql.shuffle.partitions'))") == "True 4"
    text = run("sc = spark.sparkContext\nsc.setLogLevel('WARN')\n"
               "print(spark.version, sc.version, sc.defaultParallelism > 0, sc.uiWebUrl, sc.getConf().get('spark.app.name'))")
    assert text == "3.5.0 3.5.0 True None KratosForge"
    out = Kernel().run("spark.sparkContext.parallelize([1, 2])")
    assert out["status"] == "error" and "RDDs run in Fabric" in str(out["outputs"])


def test_dataframe_methods_fabric_code_uses():
    setup = ("df = spark.createDataFrame([(1, 'EU', None), (2, 'US', 3.0), (3, None, 5.0)], ['id', 'region', 'x'])\n")
    assert run(setup + "df.agg({'id': 'sum'}).collect()[0][0]") == "6"
    assert run(setup + "from pyspark.sql import functions as F\ndf.agg(F.max('id').alias('m')).collect()[0]['m']") == "3"
    assert run(setup + "[r['y'] for r in df.selectExpr('id * 2 AS y').collect()]") == "[2, 4, 6]"
    assert run(setup + "df.fillna(0).orderBy('id').collect()[0]['x']") == "0.0"
    assert run(setup + "df.na.fill('none').orderBy('id').collect()[2]['region']") == "'none'"
    assert run(setup + "df.na.fill({'x': 1.5}).orderBy('id').collect()[0]['x']") == "1.5"
    assert run(setup + "df.dropna().count()") == "1"
    assert run(setup + "df.dropna(how='all', subset=['region', 'x']).count()") == "3"
    assert run(setup + "df.dropna(thresh=3).count()") == "1"
    assert run(setup + "{r['summary']: r['id'] for r in df.describe('id').collect()}") == \
        "{'count': '3', 'mean': '2.0', 'stddev': '1.0', 'min': '1', 'max': '3'}"
    assert run(setup + "print(df.isEmpty(), df.filter('id > 9').isEmpty())") == "False True"
    assert run(setup + "df.repartition(4).coalesce(1).persist().unpersist().hint('broadcast').count()") == "3"
    assert run(setup + "df.tail(1)[0]['id']") == "3"
    assert run(setup + "df.sample(fraction=1.0, seed=1).count()") == "3"
    assert run(setup + "df.unpivot('id', ['x'], 'col', 'val').count()") == "2"  # UNPIVOT drops nulls, like Spark's
    assert run(setup + "df.createTempView('v1')\nspark.sql('SELECT count(*) AS n FROM v1').collect()[0]['n']") == "3"
    assert run(setup + "spark.catalog.tableExists('v1')") == "False"
    assert run(setup + "df.createOrReplaceTempView('v2')\nspark.catalog.tableExists('v2')") == "True"
    out = Kernel().run(setup + "df.rdd.map(lambda r: r)")
    assert out["status"] == "error" and "run in Fabric" in str(out["outputs"])


def test_the_coverage_page_matches_the_api():
    rows = spark_api.coverage()
    assert {r["state"] for r in rows} <= {"works", "no-op", "Fabric", "missing"}
    assert all(r["why"] for r in rows if r["state"] == "Fabric")
    assert spark_api.coverage_markdown() in DOC.read_text(encoding="utf-8"), \
        "docs/notebook-spark-on-this-pc.md is out of date: paste spark_api.coverage_markdown() into it."
