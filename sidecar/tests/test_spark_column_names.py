"""Columns made by operators, F.when / when / otherwise, isNull, isNotNull, isin and getItem in a
notebook cell on this PC carry Spark 3.5's generated names ((age > 3), CASE WHEN (age > 3) THEN 1
ELSE 0 END), not DuckDB's, and filters and joins on them still run. The expected text was printed
by Apache Spark 3.5.3 for the same cell."""

from __future__ import annotations

from fabric_desktop.notebook import Kernel

CELL = 'from pyspark.sql import functions as F\ndf = spark.createDataFrame([(2, "Alice", 1.5), (5, "Bob", None)], ["age", "name", "h"])\nprint(df.select(df.age == 2, df.age != 2, df.age < 3, df.age <= 3, df.age > 3, df.age >= 3, df.name == "Bob", df.h == None).columns)\nprint(df.select(df.age + 1, df.age - 1, df.age * 2, df.age / 2, df.age % 2, df.age ** 2, -df.age, df.age + df.h).columns)\nprint(df.select(1 + df.age, 1 - df.age, 2 * df.age, 1 / df.age, 7 % df.age, 2 ** df.age).columns)\nprint(df.select((df.age > 1) & (df.h < 2), (df.age > 1) | df.h.isNull(), ~(df.age > 1), df.age == 1.5, df.age + 1.0).columns)\nprint(df.select(F.when(df.age > 3, 1), F.when(df.age > 4, "x").when(df.age < 3, "y").otherwise("z"), F.when(df.h.isNull(), df.age).otherwise(df.h)).columns)\ndf.select(df.name, F.when(df.age > 3, 1).otherwise(0), (df.age * 2 + 1)).show()\nprint(df.filter(df.age > 3).count(), df.where((df.age == 2) | (df.age == 5)).count())\ndf2 = spark.createDataFrame([(2, "x")], ["age", "t"])\nprint(df.join(df2, df.age == df2.age).count(), df.join(df2, (df.age == df2.age) & (df.name != df2.t)).count())\nprint(df.withColumn("y", df.age > 2).columns, df.orderBy(df.age * -1).first().name)\nprint(df.select(df.age.isNull(), df.age.isNotNull(), df.age.isin(1, 2), df.age.isin([2, 5])).columns)\n'

SPARK = "['(age = 2)', '(NOT (age = 2))', '(age < 3)', '(age <= 3)', '(age > 3)', '(age >= 3)', '(name = Bob)', '(h = NULL)']\n['(age + 1)', '(age - 1)', '(age * 2)', '(age / 2)', '(age % 2)', 'POWER(age, 2)', '(- age)', '(age + h)']\n['(age + 1)', '(1 - age)', '(age * 2)', '(1 / age)', '(7 % age)', 'POWER(2, age)']\n['((age > 1) AND (h < 2))', '((age > 1) OR (h IS NULL))', '(NOT (age > 1))', '(age = 1.5)', '(age + 1.0)']\n['CASE WHEN (age > 3) THEN 1 END', 'CASE WHEN (age > 4) THEN x WHEN (age < 3) THEN y ELSE z END', 'CASE WHEN (h IS NULL) THEN age ELSE h END']\n+-----+-------------------------------------+---------------+\n| name|CASE WHEN (age > 3) THEN 1 ELSE 0 END|((age * 2) + 1)|\n+-----+-------------------------------------+---------------+\n|Alice|                                    0|              5|\n|  Bob|                                    1|             11|\n+-----+-------------------------------------+---------------+\n\n1 2\n1 1\n['age', 'name', 'h', 'y'] Bob\n['(age IS NULL)', '(age IS NOT NULL)', '(age IN (1, 2))', '(age IN (2, 5))']\n"


def run(code: str) -> dict:
    return Kernel().run(code)


def test_generated_names_are_what_spark_prints():
    out = run(CELL)
    assert out["status"] == "ok", out["outputs"]
    assert "".join(o.get("text", "") for o in out["outputs"]) == SPARK


def test_a_name_inside_a_function_doesnt_leak_into_the_functions_sql():
    out = run("from pyspark.sql import functions as F\n"
              "df = spark.createDataFrame([(2,), (5,)], ['age'])\n"
              "print(df.agg(F.sum(df.age + 1)).collect()[0][0], df.select(F.abs(df.age - 10)).columns)")
    assert out["status"] == "ok", out["outputs"]
    text = "".join(o.get("text", "") for o in out["outputs"])
    assert text.startswith("9 ") and ":=" not in text


def test_getitem_is_named_as_spark_names_it():
    out = run("df = spark.createDataFrame([([1, 2], {'key': 'value'})], ['l', 'd'])\n"
              "print(df.select(df.l.getItem(0), df.d.getItem('key'), df.l[1]).columns)")
    assert out["status"] == "ok", out["outputs"]
    assert "".join(o.get("text", "") for o in out["outputs"]).strip() == "['l[0]', 'd[key]', 'l[1]']"
