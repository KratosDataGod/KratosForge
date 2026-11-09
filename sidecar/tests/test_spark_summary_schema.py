"""describe(), summary(), GroupedData's max/min/sum/avg/mean, printSchema() and randomSplit() in a
notebook cell on this PC, as Spark gives them: stddev to Spark's last digit (its running update in row
order) and every value as Java writes it; with no column named, every number column; Spark's type
names (long, integer, short, byte) and nested types one part per line; randomSplit's parts never
overlapping, together holding every row, and Spark's checks on the weights. Each expected output was
printed by Apache Spark 3.5.3 for the same cell."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

DESCRIBE_CELL = r'''from pyspark.sql import functions as F
df = spark.createDataFrame([("Bob", 13, 40.3, 150.5), ("Alice", 12, 37.8, 142.3), ("Tom", 11, 44.1, 142.2)], ["name", "age", "weight", "height"])
df.describe(['age', 'weight', 'height']).show()
df.select("age", "weight", "height").summary().show()
df.summary("count", "min", "25%", "75%", "max", "mean", "stddev").show()
print(df.describe().dtypes)
d2 = spark.createDataFrame([(1.0,), (2.0,), (None,)], "x double")
d2.describe().show(); d2.summary().show()
d3 = spark.createDataFrame([(1,), (2,), (4,)], "x int"); d3.describe().show(); d3.summary().show()
spark.createDataFrame([(1/3,), (2/3,)], "x double").describe().show()
spark.createDataFrame([("a",), ("b",)], "s string").summary().show()
spark.createDataFrame([(7,)], "x int").describe().show()
p = spark.createDataFrame([(2, "Alice"), (5, "Bob")], ["age", "name"])
p.groupBy(p.name).max().sort("name").show()
p.groupBy("name").min().sort("name").show(); p.groupBy().sum().show()
print(p.groupBy("name").avg().columns, p.groupBy("name").mean("age").columns, p.groupBy().max("age").columns)
p.printSchema()
spark.createDataFrame([(1, 2.0, "a", True, [1], {"a": 1})], "a tinyint, b float, c string, d boolean, e array<bigint>, f map<string,int>").printSchema()
'''

# Apache Spark 3.5.3's output for DESCRIBE_CELL.
DESCRIBE_SPARK = r'''+-------+----+-----------------+-----------------+
|summary| age|           weight|           height|
+-------+----+-----------------+-----------------+
|  count|   3|                3|                3|
|   mean|12.0|40.73333333333333|            145.0|
| stddev| 1.0|3.172275734127371|4.763402145525822|
|    min|  11|             37.8|            142.2|
|    max|  13|             44.1|            150.5|
+-------+----+-----------------+-----------------+

+-------+----+-----------------+-----------------+
|summary| age|           weight|           height|
+-------+----+-----------------+-----------------+
|  count|   3|                3|                3|
|   mean|12.0|40.73333333333333|            145.0|
| stddev| 1.0|3.172275734127371|4.763402145525822|
|    min|  11|             37.8|            142.2|
|    25%|  11|             37.8|            142.2|
|    50%|  12|             40.3|            142.3|
|    75%|  13|             44.1|            150.5|
|    max|  13|             44.1|            150.5|
+-------+----+-----------------+-----------------+

+-------+-----+----+-----------------+-----------------+
|summary| name| age|           weight|           height|
+-------+-----+----+-----------------+-----------------+
|  count|    3|   3|                3|                3|
|    min|Alice|  11|             37.8|            142.2|
|    25%| NULL|  11|             37.8|            142.2|
|    75%| NULL|  13|             44.1|            150.5|
|    max|  Tom|  13|             44.1|            150.5|
|   mean| NULL|12.0|40.73333333333333|            145.0|
| stddev| NULL| 1.0|3.172275734127371|4.763402145525822|
+-------+-----+----+-----------------+-----------------+

[('summary', 'string'), ('name', 'string'), ('age', 'string'), ('weight', 'string'), ('height', 'string')]
+-------+------------------+
|summary|                 x|
+-------+------------------+
|  count|                 2|
|   mean|               1.5|
| stddev|0.7071067811865476|
|    min|               1.0|
|    max|               2.0|
+-------+------------------+

+-------+------------------+
|summary|                 x|
+-------+------------------+
|  count|                 2|
|   mean|               1.5|
| stddev|0.7071067811865476|
|    min|               1.0|
|    25%|               1.0|
|    50%|               1.0|
|    75%|               2.0|
|    max|               2.0|
+-------+------------------+

+-------+------------------+
|summary|                 x|
+-------+------------------+
|  count|                 3|
|   mean|2.3333333333333335|
| stddev|1.5275252316519465|
|    min|                 1|
|    max|                 4|
+-------+------------------+

+-------+------------------+
|summary|                 x|
+-------+------------------+
|  count|                 3|
|   mean|2.3333333333333335|
| stddev|1.5275252316519465|
|    min|                 1|
|    25%|                 1|
|    50%|                 2|
|    75%|                 4|
|    max|                 4|
+-------+------------------+

+-------+-------------------+
|summary|                  x|
+-------+-------------------+
|  count|                  2|
|   mean|                0.5|
| stddev|0.23570226039551584|
|    min| 0.3333333333333333|
|    max| 0.6666666666666666|
+-------+-------------------+

+-------+----+
|summary|   s|
+-------+----+
|  count|   2|
|   mean|NULL|
| stddev|NULL|
|    min|   a|
|    25%|NULL|
|    50%|NULL|
|    75%|NULL|
|    max|   b|
+-------+----+

+-------+----+
|summary|   x|
+-------+----+
|  count|   1|
|   mean| 7.0|
| stddev|NULL|
|    min|   7|
|    max|   7|
+-------+----+

+-----+--------+
| name|max(age)|
+-----+--------+
|Alice|       2|
|  Bob|       5|
+-----+--------+

+-----+--------+
| name|min(age)|
+-----+--------+
|Alice|       2|
|  Bob|       5|
+-----+--------+

+--------+
|sum(age)|
+--------+
|       7|
+--------+

['name', 'avg(age)'] ['name', 'avg(age)'] ['max(age)']
root
 |-- age: long (nullable = true)
 |-- name: string (nullable = true)

root
 |-- a: byte (nullable = true)
 |-- b: float (nullable = true)
 |-- c: string (nullable = true)
 |-- d: boolean (nullable = true)
 |-- e: array (nullable = true)
 |    |-- element: long (containsNull = true)
 |-- f: map (nullable = true)
 |    |-- key: string
 |    |-- value: integer (valueContainsNull = true)

'''


GROUPED_CELL = r'''from pyspark.sql import functions as F
p = spark.createDataFrame([(2, "Alice", 1.5), (5, "Bob", 2.5), (3, "Bob", None)], "age int, name string, w double")
for src in ['p.groupBy("age").max()', 'p.groupBy("name").sum()', 'p.groupBy().avg()', 'p.groupBy("name").mean("w")', 'p.groupBy("name").min("age", "w")', 'p.groupBy("name").max("name")', 'p.groupBy().sum("nope")', 'p.groupBy("name").avg()', 'p.groupBy(p.name).max().sort("name")']:
    try:
        r = eval(src); print(src, r.dtypes, sorted(map(tuple, r.collect()), key=str))
    except Exception as e:
        print(src, "ERR", type(e).__name__)
'''

# Apache Spark 3.5.3's output for GROUPED_CELL.
GROUPED_SPARK = r'''p.groupBy("age").max() [('age', 'int'), ('max(age)', 'int'), ('max(w)', 'double')] [(2, 2, 1.5), (3, 3, None), (5, 5, 2.5)]
p.groupBy("name").sum() [('name', 'string'), ('sum(age)', 'bigint'), ('sum(w)', 'double')] [('Alice', 2, 1.5), ('Bob', 8, 2.5)]
p.groupBy().avg() [('avg(age)', 'double'), ('avg(w)', 'double')] [(3.3333333333333335, 2.0)]
p.groupBy("name").mean("w") [('name', 'string'), ('avg(w)', 'double')] [('Alice', 1.5), ('Bob', 2.5)]
p.groupBy("name").min("age", "w") [('name', 'string'), ('min(age)', 'int'), ('min(w)', 'double')] [('Alice', 2, 1.5), ('Bob', 3, 2.5)]
p.groupBy("name").max("name") ERR AnalysisException
p.groupBy().sum("nope") ERR AnalysisException
p.groupBy("name").avg() [('name', 'string'), ('avg(age)', 'double'), ('avg(w)', 'double')] [('Alice', 2.0, 1.5), ('Bob', 4.0, 2.5)]
p.groupBy(p.name).max().sort("name") [('name', 'string'), ('max(age)', 'int'), ('max(w)', 'double')] [('Alice', 2, 1.5), ('Bob', 5, 2.5)]
'''


SCHEMA_CELL = r'''spark.createDataFrame([(2, "Alice")], ["age", "name"]).printSchema()
df = spark.createDataFrame([], "a tinyint, b smallint, i int, l bigint, f float, d double, m decimal(10,2), s string, bo boolean, bi binary, dt date, ts timestamp, e array<bigint>, mp map<string,int>, st struct<x:int, y:array<struct<z:string>>>, n array<map<string,array<int>>>")
df.printSchema()
df.printSchema(1)
df.printSchema(2)
print(df.schema.simpleString())
print(df.schema["st"].dataType.simpleString())
'''

# Apache Spark 3.5.3's output for SCHEMA_CELL.
SCHEMA_SPARK = r'''root
 |-- age: long (nullable = true)
 |-- name: string (nullable = true)

root
 |-- a: byte (nullable = true)
 |-- b: short (nullable = true)
 |-- i: integer (nullable = true)
 |-- l: long (nullable = true)
 |-- f: float (nullable = true)
 |-- d: double (nullable = true)
 |-- m: decimal(10,2) (nullable = true)
 |-- s: string (nullable = true)
 |-- bo: boolean (nullable = true)
 |-- bi: binary (nullable = true)
 |-- dt: date (nullable = true)
 |-- ts: timestamp (nullable = true)
 |-- e: array (nullable = true)
 |    |-- element: long (containsNull = true)
 |-- mp: map (nullable = true)
 |    |-- key: string
 |    |-- value: integer (valueContainsNull = true)
 |-- st: struct (nullable = true)
 |    |-- x: integer (nullable = true)
 |    |-- y: array (nullable = true)
 |    |    |-- element: struct (containsNull = true)
 |    |    |    |-- z: string (nullable = true)
 |-- n: array (nullable = true)
 |    |-- element: map (containsNull = true)
 |    |    |-- key: string
 |    |    |-- value: array (valueContainsNull = true)
 |    |    |    |-- element: integer (containsNull = true)

root
 |-- a: byte (nullable = true)
 |-- b: short (nullable = true)
 |-- i: integer (nullable = true)
 |-- l: long (nullable = true)
 |-- f: float (nullable = true)
 |-- d: double (nullable = true)
 |-- m: decimal(10,2) (nullable = true)
 |-- s: string (nullable = true)
 |-- bo: boolean (nullable = true)
 |-- bi: binary (nullable = true)
 |-- dt: date (nullable = true)
 |-- ts: timestamp (nullable = true)
 |-- e: array (nullable = true)
 |-- mp: map (nullable = true)
 |-- st: struct (nullable = true)
 |-- n: array (nullable = true)

root
 |-- a: byte (nullable = true)
 |-- b: short (nullable = true)
 |-- i: integer (nullable = true)
 |-- l: long (nullable = true)
 |-- f: float (nullable = true)
 |-- d: double (nullable = true)
 |-- m: decimal(10,2) (nullable = true)
 |-- s: string (nullable = true)
 |-- bo: boolean (nullable = true)
 |-- bi: binary (nullable = true)
 |-- dt: date (nullable = true)
 |-- ts: timestamp (nullable = true)
 |-- e: array (nullable = true)
 |    |-- element: long (containsNull = true)
 |-- mp: map (nullable = true)
 |    |-- key: string
 |    |-- value: integer (valueContainsNull = true)
 |-- st: struct (nullable = true)
 |    |-- x: integer (nullable = true)
 |    |-- y: array (nullable = true)
 |-- n: array (nullable = true)
 |    |-- element: map (containsNull = true)

struct<a:tinyint,b:smallint,i:int,l:bigint,f:float,d:double,m:decimal(10,2),s:string,bo:boolean,bi:binary,dt:date,ts:timestamp,e:array<bigint>,mp:map<string,int>,st:struct<x:int,y:array<struct<z:string>>>,n:array<map<string,array<int>>>>
struct<x:int,y:array<struct<z:string>>>
'''


SPLIT_CELL = r'''df = spark.range(10000)
a, b = df.randomSplit([0.7, 0.3], seed=42)
ra = sorted(x.id for x in a.collect()); rb = sorted(x.id for x in b.collect())
print(len(ra) + len(rb), len(set(ra) & set(rb)), 6500 < len(ra) < 7500)
a2, b2 = df.randomSplit([7.0, 3.0], seed=42)
print(sorted(x.id for x in a2.collect()) == ra, sorted(x.id for x in a.collect()) == ra)
print([s.count() > 0 for s in df.randomSplit([1.0, 1.0, 2.0], 1)])
for bad in ['df.randomSplit([0.5, -0.5])', 'df.randomSplit([0.0, 0.0])']:
    try:
        eval(bad); print(bad, "ran")
    except Exception as e:
        print(bad, "ERR", type(e).__name__)
print(type(df.randomSplit([0.5, 0.5])).__name__, len(df.randomSplit([0.5, 0.5])))
'''

# Apache Spark 3.5.3's output for SPLIT_CELL.
SPLIT_SPARK = r'''10000 0 True
True True
[True, True, True]
df.randomSplit([0.5, -0.5]) ERR PySparkValueError
df.randomSplit([0.0, 0.0]) ERR IllegalArgumentException
list 2
'''


LEVELS_CELL = r'''d = spark.createDataFrame([(1, (2, [3]))], "`a\nb` int, s struct<x:int, y:array<int>>")
d.printSchema(0)
d.printSchema(1)
d.printSchema(-1)
d.printSchema(2)
'''

# Apache Spark 3.5.3's output for LEVELS_CELL.
LEVELS_SPARK = r'''root
 |-- a\nb: integer (nullable = true)
 |-- s: struct (nullable = true)
 |    |-- x: integer (nullable = true)
 |    |-- y: array (nullable = true)
 |    |    |-- element: integer (containsNull = true)

root
 |-- a\nb: integer (nullable = true)
 |-- s: struct (nullable = true)

root
 |-- a\nb: integer (nullable = true)
 |-- s: struct (nullable = true)
 |    |-- x: integer (nullable = true)
 |    |-- y: array (nullable = true)
 |    |    |-- element: integer (containsNull = true)

root
 |-- a\nb: integer (nullable = true)
 |-- s: struct (nullable = true)
 |    |-- x: integer (nullable = true)
 |    |-- y: array (nullable = true)

'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def _printed(kernel, cell):
    out = kernel.run(cell)
    assert out["status"] == "ok", out["outputs"]
    return "".join(o.get("text", "") for o in out["outputs"])


def test_describe_and_summary_answer_as_spark(kernel):
    assert _printed(kernel, DESCRIBE_CELL).splitlines() == DESCRIBE_SPARK.splitlines()


def test_grouped_number_aggregates_answer_as_spark(kernel):
    assert _printed(kernel, GROUPED_CELL).splitlines() == GROUPED_SPARK.splitlines()


def test_print_schema_answers_as_spark(kernel):
    assert _printed(kernel, SCHEMA_CELL).splitlines() == SCHEMA_SPARK.splitlines()


def test_random_split_keeps_spark_rules(kernel):
    assert _printed(kernel, SPLIT_CELL).splitlines() == SPLIT_SPARK.splitlines()


def test_print_schema_escapes_names_and_takes_spark_levels(kernel):
    assert _printed(kernel, LEVELS_CELL).splitlines() == LEVELS_SPARK.splitlines()


def test_describe_stays_fast_on_many_rows(kernel):
    # Spark's row-by-row stddev folds a list, so it is kept to small frames; a big one uses DuckDB's own
    text = _printed(kernel, "import time\nd = spark.range(1000000).selectExpr('id', 'id * 2 AS b', 'id % 7 AS c')\n"
                            "t = time.monotonic(); r = d.describe().collect(); s = d.summary('stddev').collect()\n"
                            "print(time.monotonic() - t < 10, r[2][0], r[2][1], s[0][3])")
    took, label, stddev, small = text.split()
    assert (took, label) == ("True", "stddev")
    assert abs(float(stddev) - 288675.27893234405) < 1e-6 and abs(float(small) - 2.0000022499987344) < 1e-9


def test_summary_refuses_a_percentile_with_anything_after_it(kernel):
    text = _printed(kernel, "d = spark.range(3)\ntry:\n    d.summary('25%\\n')\nexcept ValueError as e:\n    print('refused')\n"
                            "print(d.summary('25%').collect()[0][1])")
    assert text.splitlines() == ["refused", "0"]
