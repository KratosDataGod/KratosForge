"""A NaN in createDataFrame stays NaN, as in Spark (DuckDB 1.x turned it into NULL when a schema was
given), and F.isnan of a NULL is false, not NULL. The expected output was printed by Apache Spark
3.5.3 for the same cell."""

from __future__ import annotations

from fabric_desktop.notebook import Kernel

CELL = r'''from pyspark.sql import functions as F
df = spark.createDataFrame([(float("nan"), float("inf"), 1.5, 1), (2.0, None, float("nan"), None), (float("-inf"), float("nan"), None, 3)], "a double, b double, f float, i int")
print(df.collect())
print(df.select(F.isnan("a"), F.isnull("a"), F.isnan("f")).collect())
print(df.agg(F.count("a"), F.max("a"), F.min("b")).collect())
df.show()
print(spark.createDataFrame([(float("nan"),)], ["x"]).collect())
'''

# Apache Spark 3.5.3's output for CELL.
SPARK = r'''[Row(a=nan, b=inf, f=1.5, i=1), Row(a=2.0, b=None, f=nan, i=None), Row(a=-inf, b=nan, f=None, i=3)]
[Row(isnan(a)=True, (a IS NULL)=False, isnan(f)=False), Row(isnan(a)=False, (a IS NULL)=False, isnan(f)=True), Row(isnan(a)=False, (a IS NULL)=False, isnan(f)=False)]
[Row(count(a)=3, max(a)=nan, min(b)=inf)]
+---------+--------+----+----+
|        a|       b|   f|   i|
+---------+--------+----+----+
|      NaN|Infinity| 1.5|   1|
|      2.0|    NULL| NaN|NULL|
|-Infinity|     NaN|NULL|   3|
+---------+--------+----+----+

[Row(x=nan)]
'''


def test_nan_stays_nan_and_isnan_of_null_is_false():
    out = Kernel().run(CELL)
    assert out["status"] == "ok", out["outputs"]
    assert "".join(o.get("text", "") for o in out["outputs"]).splitlines() == SPARK.splitlines()
