"""Column.withField on this PC: the names and values mutation testing (batch 10,
docs/quality/mutation-testing.md) showed no test checked. These are a dotted column path, an aliased
struct, a nested edit, two edits in a row, an edit whose value is itself an edit, a NULL value, and a
field name with a double quote in it."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

CELL = r'''from pyspark.sql import Row
from pyspark.sql import functions as F
from pyspark.sql.functions import lit, col
df = spark.createDataFrame([Row(s=Row(b=1, c=Row(x=1, y=2)), n=7)])
def run(make):
    try:
        out = make()
        print(out.columns, out.collect())
    except Exception as e:
        print(type(e).__name__, str(e).split("\n")[0])
run(lambda: df.select(col("s.c").withField("z", lit(3))))
run(lambda: df.select(df.s.withField('a"b', lit(3))))
run(lambda: df.select(df.s.withField("z", lit(None))))
run(lambda: df.select(df.s.alias("t").withField("z", lit(1))))
run(lambda: df.select(df.s.withField("z", lit(1)).alias("t")))
run(lambda: df.select(df.s.withField("c", df.s.c.withField("q", lit(1)))))
run(lambda: df.select(df.s.withField("z", lit(1)).withField("w", lit(2))))
run(lambda: df.select(F.col("s")["c"].withField("z", lit(1))))
run(lambda: df.select(df.s.withField("z", df.s.withField("q", lit(1)))))
run(lambda: df.withColumn("u", df.s.withField("z", lit(1))))
'''

EXPECTED = r'''['update_fields(s.c, WithField(3))'] [Row(update_fields(s.c, WithField(3))=Row(x=1, y=2, z=3))]
['update_fields(s, WithField(3))'] [Row(update_fields(s, WithField(3))=Row(b=1, c=Row(x=1, y=2), a"b=3))]
['update_fields(s, WithField(NULL))'] [Row(update_fields(s, WithField(NULL))=Row(b=1, c=Row(x=1, y=2), z=None))]
['update_fields(s AS t, WithField(1))'] [Row(update_fields(s AS t, WithField(1))=Row(b=1, c=Row(x=1, y=2), z=1))]
['t'] [Row(t=Row(b=1, c=Row(x=1, y=2), z=1))]
['update_fields(s, WithField(update_fields(s.c, WithField(1))))'] [Row(update_fields(s, WithField(update_fields(s.c, WithField(1))))=Row(b=1, c=Row(x=1, y=2, q=1)))]
['update_fields(s, WithField(1), WithField(2))'] [Row(update_fields(s, WithField(1), WithField(2))=Row(b=1, c=Row(x=1, y=2), z=1, w=2))]
['update_fields(s.c, WithField(1))'] [Row(update_fields(s.c, WithField(1))=Row(x=1, y=2, z=1))]
['update_fields(s, WithField(update_fields(s, WithField(1))))'] [Row(update_fields(s, WithField(update_fields(s, WithField(1))))=Row(b=1, c=Row(x=1, y=2), z=Row(b=1, c=Row(x=1, y=2), q=1)))]
['s', 'n', 'u'] [Row(s=Row(b=1, c=Row(x=1, y=2)), n=7, u=Row(b=1, c=Row(x=1, y=2), z=1))]
'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def test_with_field_names_and_values_as_spark_gives_them(kernel):
    out = kernel.run(CELL)
    assert out["status"] == "ok", out["outputs"]
    printed = "".join(o.get("text", "") for o in out["outputs"])
    assert printed.splitlines() == EXPECTED.splitlines()
