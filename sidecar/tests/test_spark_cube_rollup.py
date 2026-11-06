"""``df.cube`` and ``df.rollup`` in a notebook cell on this PC, with ``F.grouping`` and ``F.grouping_id``,
give what Spark 3.5 gives. The expected text was printed by Apache Spark 3.5.3 for the same cell."""

from __future__ import annotations

from fabric_desktop.notebook import Kernel

CELL = 'from pyspark.sql import functions as F\ndf = spark.createDataFrame([(2, "Alice", 165), (5, "Bob", 180), (5, "Bob", 170)], schema=["age", "name", "height"])\ndf.cube("name", df.age).count().orderBy("name", "age").show()\ndf.rollup("name", df.age).count().orderBy("name", "age").show()\ndf.cube("name").agg(F.grouping("name"), F.sum("age")).orderBy("name").show()\ndf.rollup("name", "age").agg(F.grouping_id("name", "age"), F.max("height")).orderBy("name", "age").show()\ndf.cube(["name"]).agg({"age": "avg"}).orderBy("name").show()\ndf.rollup().count().show()\ndf.cube("name").sum("age", "height").orderBy("name").show()\nprint(df.cube("name").agg(F.grouping("name")).dtypes)\ndf.rollup("name", "age").agg(F.grouping_id()).orderBy("name", "age").show()\n'

SPARK = "+-----+----+-----+\n| name| age|count|\n+-----+----+-----+\n| NULL|NULL|    3|\n| NULL|   2|    1|\n| NULL|   5|    2|\n|Alice|NULL|    1|\n|Alice|   2|    1|\n|  Bob|NULL|    2|\n|  Bob|   5|    2|\n+-----+----+-----+\n\n+-----+----+-----+\n| name| age|count|\n+-----+----+-----+\n| NULL|NULL|    3|\n|Alice|NULL|    1|\n|Alice|   2|    1|\n|  Bob|NULL|    2|\n|  Bob|   5|    2|\n+-----+----+-----+\n\n+-----+--------------+--------+\n| name|grouping(name)|sum(age)|\n+-----+--------------+--------+\n| NULL|             1|      12|\n|Alice|             0|       2|\n|  Bob|             0|      10|\n+-----+--------------+--------+\n\n+-----+----+----------------------+-----------+\n| name| age|grouping_id(name, age)|max(height)|\n+-----+----+----------------------+-----------+\n| NULL|NULL|                     3|        180|\n|Alice|NULL|                     1|        165|\n|Alice|   2|                     0|        165|\n|  Bob|NULL|                     1|        180|\n|  Bob|   5|                     0|        180|\n+-----+----+----------------------+-----------+\n\n+-----+--------+\n| name|avg(age)|\n+-----+--------+\n| NULL|     4.0|\n|Alice|     2.0|\n|  Bob|     5.0|\n+-----+--------+\n\n+-----+\n|count|\n+-----+\n|    3|\n+-----+\n\n+-----+--------+-----------+\n| name|sum(age)|sum(height)|\n+-----+--------+-----------+\n| NULL|      12|        515|\n|Alice|       2|        165|\n|  Bob|      10|        350|\n+-----+--------+-----------+\n\n[('name', 'string'), ('grouping(name)', 'tinyint')]\n+-----+----+-------------+\n| name| age|grouping_id()|\n+-----+----+-------------+\n| NULL|NULL|            3|\n|Alice|NULL|            1|\n|Alice|   2|            0|\n|  Bob|NULL|            1|\n|  Bob|   5|            0|\n+-----+----+-------------+\n\n"


def run(code: str) -> dict:
    return Kernel().run(code)


def test_cube_and_rollup_print_what_spark_prints():
    out = run(CELL)
    assert out["status"] == "ok", out["outputs"]
    assert "".join(o.get("text", "") for o in out["outputs"]) == SPARK


def test_grouping_id_without_columns_outside_agg_stops_with_a_message():
    out = run("from pyspark.sql import functions as F\nspark.range(2).select(F.grouping_id()).show()")
    assert out["status"] == "error"
    assert "grouping_id() without columns works only in agg" in str(out["outputs"])
