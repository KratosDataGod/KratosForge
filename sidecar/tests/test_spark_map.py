"""mapInPandas, mapInArrow, foreach, foreachPartition and groupBy().applyInPandas on this PC's Spark
(spark_map.py, Fabric notebook parity F2), run through a notebook kernel as a cell would."""

import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", None)


@pytest.fixture
def kernel():
    return Kernel()


def run(kernel, code):
    out = kernel.run(code)
    assert out["status"] == "ok", out
    return "".join(o.get("text", "") for o in out["outputs"]).strip()


SETUP = """
import pandas as pd
import pyarrow as pa
df = spark.createDataFrame([(1, "a", 2.0), (2, "b", None), (1, "c", 4.0)], ["k", "s", "v"])
"""


def test_map_in_pandas_keeps_the_schema(kernel):
    out = run(kernel, SETUP + """
def double(it):
    for pdf in it:
        yield pdf.assign(v2=pdf.v * 2)[["k", "v2"]]
r = df.mapInPandas(double, "k long, v2 double")
print(r.dtypes, [tuple(x) for x in r.orderBy("k", "v2").collect()])
""")
    assert out == "[('k', 'bigint'), ('v2', 'double')] [(1, 4.0), (1, 8.0), (2, None)]"


def test_map_in_pandas_batches_follow_max_records_per_batch(kernel):
    out = run(kernel, SETUP + """
spark.conf.set("spark.sql.execution.arrow.maxRecordsPerBatch", 2)
sizes = []
def count(it):
    for pdf in it:
        sizes.append(len(pdf))
        yield pdf
df.mapInPandas(count, df.schema).collect()
print(sum(sizes), max(sizes) <= 2)
""")
    assert out == "3 True"


def test_map_in_pandas_matches_columns_by_name_then_by_position(kernel):
    out = run(kernel, SETUP + """
by_name = df.mapInPandas(lambda it: (p[["s", "k"]] for p in it), "k long, s string").orderBy("s").first()
by_pos = df.mapInPandas(lambda it: (p[["k", "s"]].set_axis(["x", "y"], axis=1) for p in it), "id long, name string")
print(tuple(by_name), by_pos.columns)
""")
    assert out == "(1, 'a') ['id', 'name']"


def test_map_in_pandas_says_when_the_columns_do_not_fit_or_the_output_is_not_a_frame(kernel):
    out = kernel.run(SETUP + "df.mapInPandas(lambda it: (p[['k']] for p in it), 'a long, b long').collect()")
    assert out["status"] == "error" and "returned 1 columns" in str(out["outputs"])
    out = kernel.run(SETUP + "df.mapInPandas(lambda it: (1 for p in it), 'a long').collect()")
    assert out["status"] == "error" and "must yield pandas DataFrames" in str(out["outputs"])


def test_map_in_arrow_and_an_empty_result(kernel):
    out = run(kernel, SETUP + """
def sizes(it):
    for b in it:
        yield pa.RecordBatch.from_pydict({"n": [b.num_rows]})
print(df.mapInArrow(sizes, "n long").collect())
print(df.filter("k > 5").mapInPandas(lambda it: it, "k long, s string, v double").collect())
""")
    assert out.splitlines() == ["[Row(n=3)]", "[]"]


def test_foreach_and_foreach_partition_see_every_row(kernel):
    out = run(kernel, SETUP + """
seen = []
df.foreach(lambda row: seen.append(row.s))
df.foreachPartition(lambda rows: seen.append(sum(1 for _ in rows)))
print(sorted(seen[:3]), seen[3])
""")
    assert out == "['a', 'b', 'c'] 3"


def test_apply_in_pandas_per_group_with_and_without_the_key(kernel):
    out = run(kernel, SETUP + """
def total(pdf):
    return pd.DataFrame({"k": [pdf.k.iloc[0]], "total": [pdf.v.sum()]})
def count(key, pdf):
    return pd.DataFrame({"k": [key[0]], "n": [len(pdf)]})
print([tuple(r) for r in df.groupBy("k").applyInPandas(total, "k long, total double").orderBy("k").collect()])
print([tuple(r) for r in df.groupby(df.k).applyInPandas(count, "k long, n long").orderBy("k").collect()])
""")
    assert out.splitlines() == ["[(1, 6.0), (2, 0.0)]", "[(1, 2), (2, 1)]"]


def test_these_are_no_longer_flagged_as_fabric_only():
    from fabric_desktop import fabric_run
    code = "df.mapInPandas(f, s)\ndf.groupBy('k').applyInPandas(g, s)"
    assert [why for p, why in fabric_run._FABRIC_ONLY if p.search(code)] == []
    assert any(p.search("df.rdd.map(f)") for p, _ in fabric_run._FABRIC_ONLY)
