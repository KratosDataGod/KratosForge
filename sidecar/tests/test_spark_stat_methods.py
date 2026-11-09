"""DataFrame.crosstab, colRegex, toJSON and sampleBy (and df.stat's crosstab and sampleBy) in a
notebook cell on this PC, as Spark gives them: crosstab's counts with values as Java writes them
and columns in sorted order, colRegex matching whole names ignoring case, toJSON leaving out NULL
fields of a row or struct (not of a map), sampleBy keeping each key's share. Each expected output was
printed by Apache Spark 3.5.3 for the same cell."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

STAT_CELL = r'''from pyspark.sql import functions as F
df = spark.createDataFrame([(1, "a", 1.5, None), (2, "b", None, [1, 2]), (1, "b", 2.0, []), (3, None, 3.0, [3]), (1, "a", 1.5, None)],
                           "k int, v string, x double, arr array<int>")
for src in ['df.crosstab("k", "v")', 'df.stat.crosstab("v", "k")', 'df.crosstab("k", "x")']:
    try:
        r = eval(src)
        print(src, r.columns, r.dtypes)
        print(sorted(tuple(x) for x in r.collect()) if src != 'df.crosstab("k", "x")' else sorted((tuple(map(str, x)) for x in r.collect())))
    except Exception as e:
        print(src, "ERR", type(e).__name__, str(e).splitlines()[0][:100])
for src in ['df.colRegex("`(k|v)`")', 'df.colRegex("`.*`")', 'df.colRegex("`x.*`")', 'df.colRegex("k")', 'df.colRegex("`K`")']:
    try:
        print(src, df.select(eval(src)).columns)
    except Exception as e:
        print(src, "ERR", type(e).__name__, str(e).splitlines()[0][:100])
print(df.toJSON().collect())
print(df.select("k", "v").toJSON().first(), df.toJSON().count(), df.toJSON().take(1))
s = spark.range(1000).withColumn("key", (F.col("id") % 3).cast("int"))
r = s.sampleBy("key", {0: 0.1, 1: 0.5}, seed=7)
print(r.columns, r.groupBy("key").count().orderBy("key").collect() and sorted(set(x.key for x in r.collect())))
c = r.groupBy("key").count().collect()
print(all(0 < x["count"] < 340 for x in c))
print(s.stat.sampleBy("key", {0: 0.1}, 7).columns)
for bad in ['s.sampleBy("key", {0: 1.5})', 's.sampleBy("key", [0.1])']:
    try:
        eval(bad).count(); print(bad, "ran")
    except Exception as e:
        print(bad, "ERR")
'''

# Apache Spark 3.5.3's output for STAT_CELL.
STAT_SPARK = r'''df.crosstab("k", "v") ['k_v', 'a', 'b', 'null'] [('k_v', 'string'), ('a', 'bigint'), ('b', 'bigint'), ('null', 'bigint')]
[('1', 2, 1, 0), ('2', 0, 1, 0), ('3', 0, 0, 1)]
df.stat.crosstab("v", "k") ['v_k', '1', '2', '3'] [('v_k', 'string'), ('1', 'bigint'), ('2', 'bigint'), ('3', 'bigint')]
[('a', 2, 0, 0), ('b', 1, 1, 0), ('null', 0, 0, 1)]
df.crosstab("k", "x") ['k_x', '1.5', '2.0', '3.0', 'null'] [('k_x', 'string'), ('1.5', 'bigint'), ('2.0', 'bigint'), ('3.0', 'bigint'), ('null', 'bigint')]
[('1', '2', '1', '0', '0'), ('2', '0', '0', '0', '1'), ('3', '0', '0', '1', '0')]
df.colRegex("`(k|v)`") ['k', 'v']
df.colRegex("`.*`") ['k', 'v', 'x', 'arr']
df.colRegex("`x.*`") ['x']
df.colRegex("k") ['k']
df.colRegex("`K`") ['k']
['{"k":1,"v":"a","x":1.5}', '{"k":2,"v":"b","arr":[1,2]}', '{"k":1,"v":"b","x":2.0,"arr":[]}', '{"k":3,"x":3.0,"arr":[3]}', '{"k":1,"v":"a","x":1.5}']
{"k":1,"v":"a"} 5 ['{"k":1,"v":"a","x":1.5}']
['id', 'key'] [0, 1]
True
['id', 'key']
s.sampleBy("key", {0: 1.5}) ERR
s.sampleBy("key", [0.1]) ERR
'''

JSON_CELL = r'''d = spark.sql("""select 1 as i, cast(null as int) as n, 1e20D as big, 0.1D as p, cast('NaN' as double) as nan, cast(2.50 as decimal(5,2)) as dec,
 date'2024-01-02' as dt, timestamp'2024-01-02 03:04:05.12' as ts, named_struct('a', 1, 'b', cast(null as string)) as st,
 map('k', 1, 'z', cast(null as int)) as m, array(1, null) as arr, X'0102' as bin, 'q"\\\\é' as s, true as b, cast(1.5 as float) as f""")
for line in d.toJSON().collect():
    print(line)
print(spark.range(0).toJSON().collect())
'''

# Apache Spark 3.5.3's output for JSON_CELL.
JSON_SPARK = r'''{"i":1,"big":1.0E20,"p":0.1,"nan":"NaN","dec":2.50,"dt":"2024-01-02","ts":"2024-01-02T03:04:05.120Z","st":{"a":1},"m":{"k":1,"z":null},"arr":[1,null],"bin":"AQI=","s":"q\"\\é","b":true,"f":1.5}
[]
'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def _printed(kernel, cell):
    out = kernel.run(cell)
    assert out["status"] == "ok", out["outputs"]
    return "".join(o.get("text", "") for o in out["outputs"])


def test_crosstab_colregex_tojson_and_sampleby_answer_as_spark(kernel):
    assert _printed(kernel, STAT_CELL).splitlines() == STAT_SPARK.splitlines()


def test_tojson_writes_each_type_as_spark_does(kernel):
    assert _printed(kernel, JSON_CELL).splitlines() == JSON_SPARK.splitlines()


def test_colregex_reads_java_syntax_and_odd_names(kernel):
    text = _printed(kernel, "q = spark.createDataFrame([(1, 2, 3, 4)], ['Col1', 'my col', 'a.b', 'x[1]'])\n"
                            "print(q.select(q.colRegex('`(Col1)?+.+`')).columns)\n"
                            "print(q.select(q.colRegex('`a.b|x\\\\[1]`'), 'Col1').columns)")
    assert text.splitlines() == ["['my col', 'a.b', 'x[1]']", "['a.b', 'x[1]', 'Col1']"]


def test_a_seeded_sampleby_keeps_the_same_rows(kernel):
    text = _printed(kernel, "from pyspark.sql import functions as F\ns = spark.range(1000).withColumn('key', (F.col('id') % 3).cast('int'))\n"
                            "a = sorted(r.id for r in s.sampleBy('key', {0: 0.2, 2: 1.0}, seed=3).collect())\n"
                            "b = sorted(r.id for r in s.sampleBy('key', {0: 0.2, 2: 1.0}, seed=3).collect())\n"
                            "print(a == b, 30 < sum(i % 3 == 0 for i in a) < 110, sum(i % 3 == 2 for i in a), "
                            "any(i % 3 == 1 for i in a))")
    assert text.strip() == "True True 333 False"
