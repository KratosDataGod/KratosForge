"""Column.withField and Column.dropFields on this PC: the argument errors the struct tests don't reach,
answered with PySpark's own error classes."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

CELL = r'''from pyspark.sql import Row
from pyspark.sql.functions import lit
df = spark.createDataFrame([Row(a=Row(b=1, c=2), n=7)])
def run(make):
    try:
        out = make()
        print(out.columns, out.collect())
    except Exception as e:
        print(type(e).__name__, str(e).split("\n")[0])
run(lambda: df.select(df.a.withField(1, lit(1))))
run(lambda: df.select(df.a.withField("x", 1)))
run(lambda: df.select(df.a.dropFields()))
run(lambda: df.select(df.a.dropFields(1)))
run(lambda: df.select(df.a.withField("", lit(1))))
'''

EXPECTED = '''PySparkTypeError [NOT_STR] Argument `fieldName` should be a str, got int.
PySparkTypeError [NOT_COLUMN] Argument `col` should be a Column, got int.
AnalysisException dropFields needs at least one field name, as in Spark (empty.tail).
PySparkTypeError [NOT_STR] Argument `fieldName` should be a str, got int.
AnalysisException [PARSE_SYNTAX_ERROR] Syntax error at or near end of input: ''.
'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def test_a_field_name_or_column_of_the_wrong_type_is_refused_as_pyspark_does(kernel):
    out = kernel.run(CELL)
    assert out["status"] == "ok", out["outputs"]
    printed = "".join(o.get("text", "") for o in out["outputs"])
    assert printed.splitlines() == EXPECTED.splitlines()
