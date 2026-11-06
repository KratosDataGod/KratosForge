"""Column.between, substr, bitwiseAND/OR/XOR, eqNullSafe, name and astype in a notebook cell on this
PC give what Spark 3.5 gives, values and generated column names alike. The expected text was printed
by Apache Spark 3.5.3 for the same cell."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

CELL = 'from pyspark.sql import Row, functions as F\ndf = spark.createDataFrame([(2, "Alice"), (5, "Bob"), (None, "Carl")], ["age", "name"])\ndf.select(df.name, df.age.between(2, 4)).show()\ndf.select(df.age.between(F.lit(1), df.age)).show()\ndf.select(df.name.substr(1, 3).alias("col")).show()\ndf.select(df.name.substr(0, 2), df.name.substr(-2, 2), df.name.substr(F.lit(2), F.lit(10))).show()\nb = spark.createDataFrame([Row(a=170, b=75)])\nprint(b.select(b.a.bitwiseAND(b.b), b.a.bitwiseOR(b.b), b.a.bitwiseXOR(b.b), b.a.bitwiseAND(15)).collect())\ndf1 = spark.createDataFrame([Row(id=1, value=\'foo\'), Row(id=2, value=None)])\ndf1.select(df1[\'value\'].eqNullSafe(\'foo\'), df1[\'value\'].eqNullSafe(None)).show()\ndf2 = spark.createDataFrame([Row(value=\'bar\'), Row(value=None)])\nprint(df1.join(df2, df1["value"].eqNullSafe(df2["value"])).count())\ndf3 = spark.createDataFrame([Row(id=1, value=float(\'NaN\')), Row(id=2, value=42.0), Row(id=3, value=None)])\ndf3.select(df3[\'value\'].eqNullSafe(None), df3[\'value\'].eqNullSafe(float(\'NaN\')), df3[\'value\'].eqNullSafe(42.0)).show()\nprint(df.select(df.age.name("a"), df.age.astype("string").alias("s")).dtypes)\n'

SPARK = "+-----+---------------------------+\n| name|((age >= 2) AND (age <= 4))|\n+-----+---------------------------+\n|Alice|                       true|\n|  Bob|                      false|\n| Carl|                       NULL|\n+-----+---------------------------+\n\n+-----------------------------+\n|((age >= 1) AND (age <= age))|\n+-----------------------------+\n|                         true|\n|                         true|\n|                         NULL|\n+-----------------------------+\n\n+---+\n|col|\n+---+\n|Ali|\n|Bob|\n|Car|\n+---+\n\n+---------------------+----------------------+----------------------+\n|substring(name, 0, 2)|substring(name, -2, 2)|substring(name, 2, 10)|\n+---------------------+----------------------+----------------------+\n|                   Al|                    ce|                  lice|\n|                   Bo|                    ob|                    ob|\n|                   Ca|                    rl|                   arl|\n+---------------------+----------------------+----------------------+\n\n[Row((a & b)=10, (a | b)=235, (a ^ b)=225, (a & 15)=10)]\n+---------------+----------------+\n|(value <=> foo)|(value <=> NULL)|\n+---------------+----------------+\n|           true|           false|\n|          false|            true|\n+---------------+----------------+\n\n1\n+----------------+---------------+----------------+\n|(value <=> NULL)|(value <=> NaN)|(value <=> 42.0)|\n+----------------+---------------+----------------+\n|           false|           true|           false|\n|           false|          false|            true|\n|            true|          false|           false|\n+----------------+---------------+----------------+\n\n[('a', 'bigint'), ('s', 'string')]\n"


def test_column_methods_print_what_spark_prints():
    out = Kernel().run(CELL)
    assert out["status"] == "ok", out["outputs"]
    assert "".join(o.get("text", "") for o in out["outputs"]) == SPARK


@pytest.mark.parametrize("call", ["substr(1, F.lit(3))", "substr('1', 3)", "substr(True, 3)"])
def test_substr_takes_two_ints_or_two_columns_as_pyspark_does(call):
    out = Kernel().run(f"from pyspark.sql import functions as F\nspark.range(1).select(F.lit('abc').{call}).collect()")
    assert out["status"] == "error"
    assert "both be ints or both be Columns" in str(out["outputs"])
