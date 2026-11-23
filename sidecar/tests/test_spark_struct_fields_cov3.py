"""Column.withField and dropFields on this PC: a plain column selected beside an edited struct, an edited
struct used as the value of another edit, and a value naming a column the frame doesn't have (the name is
still worked out, and the select says why it can't run)."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

CELL = r'''from pyspark.sql import Row
from pyspark.sql import functions as F
df = spark.createDataFrame([Row(a=Row(b=1, c=2), n=7)])
def run(make):
    try:
        out = make()
        print(out.columns, [r.asDict(recursive=True) for r in out.collect()])
    except Exception as e:
        print(type(e).__name__)
run(lambda: df.select(df.n, df.a.withField("d", F.lit(3))))
run(lambda: df.select(df.a.withField("inner", df.a.dropFields("b"))))
run(lambda: df.select(df.a.withField("x", F.col("nope"))))
'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def test_struct_edits_beside_plain_columns_nested_in_each_other_and_over_a_missing_column(kernel):
    out = kernel.run(CELL)
    assert out["status"] == "ok", out
    lines = "".join(o.get("text", "") for o in out["outputs"]).strip().splitlines()
    assert lines[0] == ("['n', 'update_fields(a, WithField(3))'] "
                        "[{'n': 7, 'update_fields(a, WithField(3))': {'b': 1, 'c': 2, 'd': 3}}]")
    assert lines[1].endswith("[{'update_fields(a, WithField(update_fields(a, dropfield())))': "
                             "{'b': 1, 'c': 2, 'inner': {'c': 2}}}]"), lines[1]
    assert lines[2] in ("AnalysisException", "BinderException"), lines[2]


def test_the_resolver_reads_an_edited_column_given_as_a_plain_one_and_a_column_the_frame_lacks():
    import duckdb
    from duckdb.experimental.spark.sql import functions as F

    from fabric_desktop import spark_struct_fields as sf

    with duckdb.connect() as con:
        rel = con.sql("SELECT {'b': 1, 'c': 2} AS a")
        r = sf._Resolver(rel)
        assert r.is_struct(F.col("a")) is True
        assert r.is_struct(F.col("nope")) is False   # the frame can't read it: not a struct
        edited = sf._deferred(sf._Update(sf._Col(F.col("a")), (sf._Drop("b"),)))
        text = r.sql(sf._Col(edited))
        assert rel.project(text).fetchall() == [({"c": 2},)]
