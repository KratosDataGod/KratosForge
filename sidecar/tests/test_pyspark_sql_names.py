"""``from pyspark.sql import Row, Column, GroupedData, Window`` works in a notebook cell on this PC, as
in Spark (DuckDB's pyspark.sql keeps Row in types and leaves the others out), and a DataFrame made
from Rows reads as Spark's does (the examples of PySpark's own documentation start this way)."""

from __future__ import annotations

from fabric_desktop.notebook import Kernel


def run(code: str) -> tuple[str, str]:
    out = Kernel().run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    return out["status"], text


def test_the_names_spark_s_pyspark_sql_has_import():
    status, text = run("from pyspark.sql import Row, Column, GroupedData, Window, SparkSession, DataFrame\n"
                       "from pyspark.sql import functions as F\n"
                       "df = spark.range(2)\n"
                       "print(isinstance(df.id, Column), isinstance(df.groupBy('id'), GroupedData), "
                       "isinstance(df, DataFrame))\n"
                       "print(Window.partitionBy('id').orderBy('id') is not None)")
    assert status == "ok", text
    assert text.split() == ["True", "True", "True", "True"]


def test_a_dataframe_from_rows_reads_as_spark_s():
    status, text = run("from pyspark.sql import Row\n"
                       "df = spark.createDataFrame([Row(name='Alice', age=2), Row(name='Bob', age=5)])\n"
                       "print(sorted(df.columns), sorted((r['name'], r['age']) for r in df.collect()))\n"
                       "r = Row(name='Tom', age=14)\n"
                       "print(r.name, r['age'], r.asDict())")
    assert status == "ok", text
    assert "['age', 'name'] [('Alice', 2), ('Bob', 5)]" in text
    assert "Tom 14 {'name': 'Tom', 'age': 14}" in text
