"""DataFrame division by zero gives NULL, as Spark's does with ANSI off (EA-012)."""

from __future__ import annotations

from fabric_desktop.notebook import Kernel


def run(code):
    out = Kernel().run(code)
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


SETUP = ("from pyspark.sql import functions as F\n"
         "df = spark.createDataFrame([(7, 2, 0), (1, 0, 3)], 'x int, b int, z int')\n")


def test_division_by_zero_is_null():
    status, text = run(SETUP + "rows = df.select((F.lit(1) / F.lit(0)).alias('a'), (F.col('x') / 0).alias('b'),"
                       " (F.col('x') / F.col('z')).alias('c'), (F.col('x') / 0.0).alias('d'),"
                       " (F.col('x') / F.col('b')).alias('e')).collect()\n"
                       "print([tuple(r) for r in rows])")
    assert status == "ok", text
    assert "[(None, None, None, None, 3.5), (None, None, 0.3333333333333333, None, None)]" in text


def test_reverse_division_and_types():
    status, text = run(SETUP + "out = df.select((1 / F.col('z')).alias('r'), (F.col('x') / F.col('b')).alias('q'))\n"
                       "print(out.dtypes, [tuple(r) for r in out.collect()])")
    assert status == "ok", text
    assert "[('r', 'double'), ('q', 'double')]" in text
    assert "[(None, 3.5), (0.3333333333333333, None)]" in text


def test_null_divisor_stays_null_and_other_division_unchanged():
    status, text = run("from pyspark.sql import functions as F\n"
                       "df = spark.createDataFrame([(6, None), (-6, 4)], 'x int, y int')\n"
                       "print([tuple(r) for r in df.select((F.col('x') / F.col('y')).alias('v'),"
                       " (F.col('x') / 4).alias('w')).collect()])")
    assert status == "ok", text
    assert "[(None, 1.5), (-1.5, -1.5)]" in text
