"""Column.withField and Column.dropFields on this PC, as Spark's: a struct's field added, replaced
(named as given) or dropped, also inside a nested struct, matched whatever its case; a NULL struct stays
NULL; the column is named update_fields(...) as Spark names it; and Spark's errors for a missing parent
field, a non-struct and dropping every field. Each expected output was printed by Apache Spark 3.5.3 for
the same cell."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

CELL = r'''from pyspark.sql import Row
from pyspark.sql.functions import col, lit
df = spark.createDataFrame([Row(a=Row(b=1, c=2, d=3, e=Row(f=4, g=5, h=6)), n=7),
                            Row(a=Row(b=8, c=9, d=10, e=None), n=11), Row(a=None, n=12)])
def run(make):
    try:
        out = make()
        print(out.columns, out.collect())
    except Exception as e:
        print(type(e).__name__, str(e).split("\n")[0])
run(lambda: df.withColumn('a', df['a'].dropFields('b')))
run(lambda: df.withColumn('a', df['a'].dropFields('b', 'c')))
run(lambda: df.withColumn("a", col("a").dropFields("e.g", "e.h")))
run(lambda: df.select(col("a").withField("e", col("a.e").dropFields("g", "h")).alias("a")))
run(lambda: df.withColumn('a', df['a'].withField('b', lit(3))).select('a.b'))
run(lambda: df.withColumn('a', df['a'].withField('d', lit(4))).select('a.d'))
run(lambda: df.select(df.a.withField("e.f", lit(0)), df.a.dropFields("e.g")))
run(lambda: df.select(df.a.withField("z", lit("x")).dropFields("b"), df.a.withField("z", df.n + 1)))
run(lambda: df.select(df.a.withField("e.f", lit(0)).withField("e.g", lit(1))))
run(lambda: df.select(df.a.withField("B", lit(9)), df.a.dropFields("C", "zz")))
run(lambda: df.select(df.a.withField("z", lit(1.5)), df.a.withField("z", lit(True)).alias("t")))
run(lambda: df.select(df.a.dropFields("b").c, df.a.withField("b", lit(5)).getField("b"), df.a.dropFields("b")["e"]))
run(lambda: df.withColumns({"x": df.a.dropFields("e"), "y": df.a.withField("`p.q`", df.n)}))
run(lambda: df.select(df.a.dropFields("b", "c", "d", "e")))
run(lambda: df.select(df.a.dropFields("e.f", "e.g", "e.h")))
run(lambda: df.select(df.a.withField("y.z", lit(0))))
run(lambda: df.select(df.a.dropFields("zz.q")))
run(lambda: df.select(df.n.withField("x", lit(1))))
run(lambda: df.select(df.a.b.withField("x", lit(1))))
'''

# Apache Spark 3.5.3's output for CELL.
SPARK = r'''['a', 'n'] [Row(a=Row(c=2, d=3, e=Row(f=4, g=5, h=6)), n=7), Row(a=Row(c=9, d=10, e=None), n=11), Row(a=None, n=12)]
['a', 'n'] [Row(a=Row(d=3, e=Row(f=4, g=5, h=6)), n=7), Row(a=Row(d=10, e=None), n=11), Row(a=None, n=12)]
['a', 'n'] [Row(a=Row(b=1, c=2, d=3, e=Row(f=4)), n=7), Row(a=Row(b=8, c=9, d=10, e=None), n=11), Row(a=None, n=12)]
['a'] [Row(a=Row(b=1, c=2, d=3, e=Row(f=4))), Row(a=Row(b=8, c=9, d=10, e=None)), Row(a=None)]
['b'] [Row(b=3), Row(b=3), Row(b=None)]
['d'] [Row(d=4), Row(d=4), Row(d=None)]
['update_fields(a, WithField(update_fields(a.e, WithField(0))))', 'update_fields(a, WithField(update_fields(a.e, dropfield())))'] [Row(update_fields(a, WithField(update_fields(a.e, WithField(0))))=Row(b=1, c=2, d=3, e=Row(f=0, g=5, h=6)), update_fields(a, WithField(update_fields(a.e, dropfield())))=Row(b=1, c=2, d=3, e=Row(f=4, h=6))), Row(update_fields(a, WithField(update_fields(a.e, WithField(0))))=Row(b=8, c=9, d=10, e=None), update_fields(a, WithField(update_fields(a.e, dropfield())))=Row(b=8, c=9, d=10, e=None)), Row(update_fields(a, WithField(update_fields(a.e, WithField(0))))=None, update_fields(a, WithField(update_fields(a.e, dropfield())))=None)]
['update_fields(a, WithField(x), dropfield())', 'update_fields(a, WithField((n + 1)))'] [Row(update_fields(a, WithField(x), dropfield())=Row(c=2, d=3, e=Row(f=4, g=5, h=6), z='x'), update_fields(a, WithField((n + 1)))=Row(b=1, c=2, d=3, e=Row(f=4, g=5, h=6), z=8)), Row(update_fields(a, WithField(x), dropfield())=Row(c=9, d=10, e=None, z='x'), update_fields(a, WithField((n + 1)))=Row(b=8, c=9, d=10, e=None, z=12)), Row(update_fields(a, WithField(x), dropfield())=None, update_fields(a, WithField((n + 1)))=None)]
['update_fields(a, WithField(update_fields(update_fields(a, WithField(update_fields(a.e, WithField(0)))).e, WithField(1))))'] [Row(update_fields(a, WithField(update_fields(update_fields(a, WithField(update_fields(a.e, WithField(0)))).e, WithField(1))))=Row(b=1, c=2, d=3, e=Row(f=0, g=1, h=6))), Row(update_fields(a, WithField(update_fields(update_fields(a, WithField(update_fields(a.e, WithField(0)))).e, WithField(1))))=Row(b=8, c=9, d=10, e=None)), Row(update_fields(a, WithField(update_fields(update_fields(a, WithField(update_fields(a.e, WithField(0)))).e, WithField(1))))=None)]
['update_fields(a, WithField(9))', 'update_fields(a, dropfield(), dropfield())'] [Row(update_fields(a, WithField(9))=Row(B=9, c=2, d=3, e=Row(f=4, g=5, h=6)), update_fields(a, dropfield(), dropfield())=Row(b=1, d=3, e=Row(f=4, g=5, h=6))), Row(update_fields(a, WithField(9))=Row(B=9, c=9, d=10, e=None), update_fields(a, dropfield(), dropfield())=Row(b=8, d=10, e=None)), Row(update_fields(a, WithField(9))=None, update_fields(a, dropfield(), dropfield())=None)]
['update_fields(a, WithField(1.5))', 't'] [Row(update_fields(a, WithField(1.5))=Row(b=1, c=2, d=3, e=Row(f=4, g=5, h=6), z=1.5), t=Row(b=1, c=2, d=3, e=Row(f=4, g=5, h=6), z=True)), Row(update_fields(a, WithField(1.5))=Row(b=8, c=9, d=10, e=None, z=1.5), t=Row(b=8, c=9, d=10, e=None, z=True)), Row(update_fields(a, WithField(1.5))=None, t=None)]
['update_fields(a, dropfield()).c', 'update_fields(a, WithField(5)).b', 'update_fields(a, dropfield()).e'] [Row(update_fields(a, dropfield()).c=2, update_fields(a, WithField(5)).b=5, update_fields(a, dropfield()).e=Row(f=4, g=5, h=6)), Row(update_fields(a, dropfield()).c=9, update_fields(a, WithField(5)).b=5, update_fields(a, dropfield()).e=None), Row(update_fields(a, dropfield()).c=None, update_fields(a, WithField(5)).b=None, update_fields(a, dropfield()).e=None)]
['a', 'n', 'x', 'y'] [Row(a=Row(b=1, c=2, d=3, e=Row(f=4, g=5, h=6)), n=7, x=Row(b=1, c=2, d=3), y=Row(b=1, c=2, d=3, e=Row(f=4, g=5, h=6), p.q=7)), Row(a=Row(b=8, c=9, d=10, e=None), n=11, x=Row(b=8, c=9, d=10), y=Row(b=8, c=9, d=10, e=None, p.q=11)), Row(a=None, n=12, x=None, y=None)]
AnalysisException [DATATYPE_MISMATCH.CANNOT_DROP_ALL_FIELDS] Cannot resolve "update_fields(a, dropfield(), dropfield(), dropfield(), dropfield())" due to data type mismatch: Cannot drop all fields in struct.;
AnalysisException [DATATYPE_MISMATCH.CANNOT_DROP_ALL_FIELDS] Cannot resolve "update_fields(update_fields(a, WithField(update_fields(update_fields(a, WithField(update_fields(a.e, dropfield()))).e, dropfield()))).e, dropfield())" due to data type mismatch: Cannot drop all fields in struct.;
AnalysisException [FIELD_NOT_FOUND] No such struct field `y` in `b`, `c`, `d`, `e`.
AnalysisException [FIELD_NOT_FOUND] No such struct field `zz` in `b`, `c`, `d`, `e`.
AnalysisException [DATATYPE_MISMATCH.UNEXPECTED_INPUT_TYPE] Cannot resolve "update_fields(n, WithField(1))" due to data type mismatch: Parameter 1 requires the "STRUCT" type, however "n" has the type "BIGINT".;
AnalysisException [DATATYPE_MISMATCH.UNEXPECTED_INPUT_TYPE] Cannot resolve "update_fields(a.b, WithField(1))" due to data type mismatch: Parameter 1 requires the "STRUCT" type, however "a.b" has the type "BIGINT".;
'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def _printed(kernel, cell):
    out = kernel.run(cell)
    assert out["status"] == "ok", out["outputs"]
    return "".join(o.get("text", "") for o in out["outputs"])


def test_with_field_and_drop_fields_answer_as_spark(kernel):
    assert _printed(kernel, CELL).splitlines() == SPARK.splitlines()


@pytest.mark.parametrize("use", [
    "df.select(F.upper(df.a.dropFields('b').c))",
    "df.filter(df.a.dropFields('b').isNotNull())",
    "df.select(F.coalesce(df.a.withField('z', F.lit(1)), df.a))",
])
def test_an_edited_struct_inside_another_expression_is_refused_by_name(kernel, use):
    setup = ("from pyspark.sql import Row\nfrom pyspark.sql import functions as F\n"
             "df = spark.createDataFrame([Row(a=Row(b=1, c='x'))])\n")
    out = kernel.run(setup + use + ".collect()")
    assert out["status"] == "error"
    assert "withField and dropFields run on this PC only as a whole column" in str(out["outputs"])
