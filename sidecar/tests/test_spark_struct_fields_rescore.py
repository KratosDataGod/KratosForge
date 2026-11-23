"""Column.withField on a dotted path whose first part DuckDB 1.5.6 quotes when it prints it (a keyword
such as Order). Batch 10 could only kill the line 108 mutant on DuckDB 2.0, which quotes every part;
batch 236 (docs/quality/mutation-testing.md) kills it on both."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

CELL = r'''from pyspark.sql import Row
from pyspark.sql.functions import lit, col
df = spark.createDataFrame([Row(Order=Row(c=Row(x=1)), sel=Row(c=Row(x=2)))])
for name in ["Order.c", "sel.c"]:
    out = df.select(col(name).withField("z", lit(3)))
    print(out.columns, out.collect())
'''

EXPECTED = [
    "['update_fields(Order.c, WithField(3))'] [Row(update_fields(Order.c, WithField(3))=Row(x=1, z=3))]",
    "['update_fields(sel.c, WithField(3))'] [Row(update_fields(sel.c, WithField(3))=Row(x=2, z=3))]",
]


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def test_a_keyword_struct_name_is_written_without_quotes_as_spark_does(kernel):
    out = kernel.run(CELL)
    assert out["status"] == "ok", out["outputs"]
    printed = "".join(o.get("text", "") for o in out["outputs"])
    assert printed.splitlines() == EXPECTED
