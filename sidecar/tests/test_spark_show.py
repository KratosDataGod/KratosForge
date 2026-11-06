"""``df.show()`` in a notebook cell on this PC prints what Spark 3.5 prints: its +---+ table, its
text for each value (NULL, true, [1, NULL, 3], {a -> 1}, 1.0E20, [01 02 FF]), truncation, wide
characters, the vertical form and the "only showing top" footer. Each expected text was printed by
Apache Spark 3.5.3 (UTC session) for the same cell."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

CASES = [
    ('spark.sql("select 1 as i, cast(null as int) as n, 1.0D as d, cast(1.5 as float) as f, 1e20D as big, 0.0001D as small, cast(1.10 as decimal(5,2)) as dec, true as b, \'héllo wörld long string here!!\' as s, \'日本語\' as cjk").show()',
     '+---+----+---+---+------+------+----+----+--------------------+------+\n|  i|   n|  d|  f|   big| small| dec|   b|                   s|   cjk|\n+---+----+---+---+------+------+----+----+--------------------+------+\n|  1|NULL|1.0|1.5|1.0E20|1.0E-4|1.10|true|héllo wörld long ...|日本語|\n+---+----+---+---+------+------+----+----+--------------------+------+\n\n'),
    ('spark.sql("select array(1, null, 3) as arr, map(\'a\', 1, \'b\', null) as m, named_struct(\'x\', 1, \'y\', cast(null as string)) as st, X\'0102ff\' as bin, date\'2024-01-02\' as dt, timestamp\'2024-01-02 03:04:05.120\' as ts").show(truncate=False)',
     '+------------+-------------------+---------+----------+----------+----------------------+\n|arr         |m                  |st       |bin       |dt        |ts                    |\n+------------+-------------------+---------+----------+----------+----------------------+\n|[1, NULL, 3]|{a -> 1, b -> NULL}|{1, NULL}|[01 02 FF]|2024-01-02|2024-01-02 03:04:05.12|\n+------------+-------------------+---------+----------+----------+----------------------+\n\n'),
    ('spark.sql("select \'héllo wörld\' as s, \'日本語\' as cjk, 123456789.123D as dd").show(truncate=2)',
     '+---+----+---+\n|  s| cjk| dd|\n+---+----+---+\n| hé|日本| 1.|\n+---+----+---+\n\n'),
    ('spark.sql("select \'héllo wörld\' as s, cast(\'NaN\' as double) as nan, -0.0D as negz").show(truncate=5)',
     '+-----+---+----+\n|    s|nan|negz|\n+-----+---+----+\n|hé...|NaN|-0.0|\n+-----+---+----+\n\n'),
    ('spark.createDataFrame([(14, "Tom"), (23, "Alice")], ["age", "name"]).show(vertical=True)',
     '-RECORD 0-----\n age  | 14    \n name | Tom   \n-RECORD 1-----\n age  | 23    \n name | Alice \n\n'),
    ('spark.createDataFrame([(14, "Tom"), (23, "Alice")], ["age", "name"]).show(1, vertical=True, truncate=2)',
     '-RECORD 0---\n age  | 14  \n name | To  \nonly showing top 1 row\n\n'),
    ('spark.range(3).show(2)',
     '+---+\n| id|\n+---+\n|  0|\n|  1|\n+---+\nonly showing top 2 rows\n\n'),
    ('spark.range(3).show(1)',
     '+---+\n| id|\n+---+\n|  0|\n+---+\nonly showing top 1 row\n\n'),
    ('spark.range(0).show()',
     '+---+\n| id|\n+---+\n+---+\n\n'),
    ('spark.range(0).show(vertical=True)',
     '(0 rows)\n\n'),
    ('spark.createDataFrame([("a\\nb\\tc",)], ["x"]).show()',
     '+-------+\n|      x|\n+-------+\n|a\\nb\\tc|\n+-------+\n\n'),
]


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


@pytest.mark.parametrize(("cell", "spark"), CASES)
def test_show_prints_what_spark_prints(kernel, cell, spark):
    out = kernel.run(cell)
    assert out["status"] == "ok", out["outputs"]
    assert "".join(o.get("text", "") for o in out["outputs"]) == spark


def test_show_checks_its_arguments_as_pyspark_does(kernel):
    out = kernel.run("spark.range(1).show('2')")
    assert out["status"] == "error"
    assert "should be an int" in str(out["outputs"])
