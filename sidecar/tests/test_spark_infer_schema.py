"""spark.createDataFrame(rows) with no types given, in a notebook cell on this PC, inferring the schema
as PySpark does: a tuple, Row or namedtuple inside a row as a struct (_1, _2, ... without names), a dict
inside a row as a map, a dict row's keys sorted, unnamed columns _1, _2, ..., every row's types merged,
and PySpark's errors when they can't merge or a column has nothing to infer from. Also a struct
field's name (r.b, from getField, r.b or r['b']; a map's key stays m[k]). Each expected output was printed by Apache Spark 3.5.3 for the same cell."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

INFER_CELL = r'''import collections
from pyspark.sql import Row
import datetime, decimal
cases = [
 ([(1, (2, 2))], ["a", "b"]),
 ([(1, (2, "x"))], None),
 ([(1, [(2, 3), (4, 5)])], ["a", "b"]),
 ([(1, Row(p=1, q="z"))], ["a", "b"]),
 ([(1, {"k": (1, 2)})], ["a", "m"]),
 ([(1, {"k": 1})], ["a", "m"]),
 ([{"x": 1, "y": "a"}], None),
 ([(1, "a")], None),
 ([[1, "a"]], None),
 ([(1, {"a": 1, "b": None})], ["i", "m"]),
 ([(1, [Row(z=1)])], ["i", "l"]),
 ([(1.5, True, decimal.Decimal("1.20"), datetime.date(2024, 1, 2))], None),
 ([Row(a=1, b=Row(c=2, d=[1]))], None),
 ([(1, None), (2, (3, 4))], ["a", "b"]),
]
for data, schema in cases:
    try:
        d = spark.createDataFrame(data, schema); print(d.dtypes, d.collect())
    except Exception as e:
        print("ERR", type(e).__name__)

P = collections.namedtuple("P", "u v")
cases = [
 ([(1,), (2.5,)], None), ([(1,), (None,)], None), ([(None,), (None,)], None), ([(1, [])], None), ([(1, {})], None),
 ([{"y": 1, "x": "a"}], None), ([{"y": 1}, {"x": 2}], None), ([(1, 2, 3)], ["a"]), ([(1,)], ["a", "b"]),
 ([(1, [1, None, 2])], None), ([(1, [1, 2.5])], None), ([(1, {"a": None, "b": 2})], None), ([(1, P(1, "z"))], None),
 ([P(1, "z")], None), ([("a", True)], ["n", "b"]),
 ([(1, (1, None)), (2, (None, "x"))], None), ([(1, [(1, None), (None, 2)])], None), ([[1, 2], (3, 4)], None),
]
for data, schema in cases:
    try:
        d = spark.createDataFrame(data, schema); print(d.dtypes, d.collect())
    except Exception as e:
        print("ERR", type(e).__name__, getattr(e, "getErrorClass", lambda: "")())
'''

# Apache Spark 3.5.3's output for INFER_CELL.
INFER_SPARK = r'''[('a', 'bigint'), ('b', 'struct<_1:bigint,_2:bigint>')] [Row(a=1, b=Row(_1=2, _2=2))]
[('_1', 'bigint'), ('_2', 'struct<_1:bigint,_2:string>')] [Row(_1=1, _2=Row(_1=2, _2='x'))]
[('a', 'bigint'), ('b', 'array<struct<_1:bigint,_2:bigint>>')] [Row(a=1, b=[Row(_1=2, _2=3), Row(_1=4, _2=5)])]
[('a', 'bigint'), ('b', 'struct<p:bigint,q:string>')] [Row(a=1, b=Row(p=1, q='z'))]
[('a', 'bigint'), ('m', 'map<string,struct<_1:bigint,_2:bigint>>')] [Row(a=1, m={'k': Row(_1=1, _2=2)})]
[('a', 'bigint'), ('m', 'map<string,bigint>')] [Row(a=1, m={'k': 1})]
[('x', 'bigint'), ('y', 'string')] [Row(x=1, y='a')]
[('_1', 'bigint'), ('_2', 'string')] [Row(_1=1, _2='a')]
[('_1', 'bigint'), ('_2', 'string')] [Row(_1=1, _2='a')]
[('i', 'bigint'), ('m', 'map<string,bigint>')] [Row(i=1, m={'a': 1, 'b': None})]
[('i', 'bigint'), ('l', 'array<struct<z:bigint>>')] [Row(i=1, l=[Row(z=1)])]
[('_1', 'double'), ('_2', 'boolean'), ('_3', 'decimal(38,18)'), ('_4', 'date')] [Row(_1=1.5, _2=True, _3=Decimal('1.200000000000000000'), _4=datetime.date(2024, 1, 2))]
[('a', 'bigint'), ('b', 'struct<c:bigint,d:array<bigint>>')] [Row(a=1, b=Row(c=2, d=[1]))]
[('a', 'bigint'), ('b', 'struct<_1:bigint,_2:bigint>')] [Row(a=1, b=None), Row(a=2, b=Row(_1=3, _2=4))]
ERR PySparkTypeError CANNOT_MERGE_TYPE
[('_1', 'bigint')] [Row(_1=1), Row(_1=None)]
ERR PySparkValueError CANNOT_DETERMINE_TYPE
ERR PySparkValueError CANNOT_DETERMINE_TYPE
ERR PySparkValueError CANNOT_DETERMINE_TYPE
[('x', 'string'), ('y', 'bigint')] [Row(x='a', y=1)]
[('y', 'bigint'), ('x', 'bigint')] [Row(y=1, x=None), Row(y=None, x=2)]
[('a', 'bigint'), ('_2', 'bigint'), ('_3', 'bigint')] [Row(a=1, _2=2, _3=3)]
ERR IndexError 
[('_1', 'bigint'), ('_2', 'array<bigint>')] [Row(_1=1, _2=[1, None, 2])]
ERR PySparkTypeError CANNOT_INFER_TYPE_FOR_FIELD
[('_1', 'bigint'), ('_2', 'map<string,bigint>')] [Row(_1=1, _2={'a': None, 'b': 2})]
[('_1', 'bigint'), ('_2', 'struct<u:bigint,v:string>')] [Row(_1=1, _2=Row(u=1, v='z'))]
[('u', 'bigint'), ('v', 'string')] [Row(u=1, v='z')]
[('n', 'string'), ('b', 'boolean')] [Row(n='a', b=True)]
[('_1', 'bigint'), ('_2', 'struct<_1:bigint,_2:string>')] [Row(_1=1, _2=Row(_1=1, _2=None)), Row(_1=2, _2=Row(_1=None, _2='x'))]
[('_1', 'bigint'), ('_2', 'array<struct<_1:bigint,_2:bigint>>')] [Row(_1=1, _2=[Row(_1=1, _2=None), Row(_1=None, _2=2)])]
[('_1', 'bigint'), ('_2', 'bigint')] [Row(_1=1, _2=2), Row(_1=3, _2=4)]
'''

FIELD_CELL = r'''from pyspark.sql import Row, functions as F
df = spark.createDataFrame([Row(r=Row(a=1, b="b"))])
for e in ['df.r.getField("b")', 'F.col("r").getField("a")', 'F.col("r.a")', 'df.r.getField("b").alias("x")']:
    print(e, df.select(eval(e)).columns, df.select(eval(e)).collect())
'''

# Apache Spark 3.5.3's output for FIELD_CELL.
FIELD_SPARK = r'''df.r.getField("b") ['r.b'] [Row(r.b='b')]
F.col("r").getField("a") ['r.a'] [Row(r.a=1)]
F.col("r.a") ['a'] [Row(a=1)]
df.r.getField("b").alias("x") ['x'] [Row(x='b')]
'''


ITEM_CELL = r'''from pyspark.sql import Row, functions as F
df = spark.createDataFrame([Row(r=Row(a=1, b="b"), m={"k": 2}, l=[5, 6])])
for e in ['df.r.a', 'df.r["b"]', 'df["r"]["b"]', 'df.m["k"]', 'df.m.k', 'df.l[1]', 'F.col("r")["a"]', 'df.r.a.alias("z")', 'df.r.getField("a")']:
    print(e, df.select(eval(e)).columns, df.select(eval(e)).collect())
print(df.select(df.r.a, df.m["k"]).columns, df.selectExpr("r.a", "m['k']").columns)
'''

# Apache Spark 3.5.3's output for ITEM_CELL.
ITEM_SPARK = r'''df.r.a ['r.a'] [Row(r.a=1)]
df.r["b"] ['r.b'] [Row(r.b='b')]
df["r"]["b"] ['r.b'] [Row(r.b='b')]
df.m["k"] ['m[k]'] [Row(m[k]=2)]
df.m.k ['m[k]'] [Row(m[k]=2)]
df.l[1] ['l[1]'] [Row(l[1]=6)]
F.col("r")["a"] ['r.a'] [Row(r.a=1)]
df.r.a.alias("z") ['z'] [Row(z=1)]
df.r.getField("a") ['r.a'] [Row(r.a=1)]
['r.a', 'm[k]'] ['a', 'm[k]']
'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def _printed(kernel, cell):
    out = kernel.run(cell)
    assert out["status"] == "ok", out["outputs"]
    return "".join(o.get("text", "") for o in out["outputs"])


def test_create_dataframe_infers_the_schema_as_pyspark_does(kernel):
    assert _printed(kernel, INFER_CELL).splitlines() == INFER_SPARK.splitlines()


def test_get_field_is_named_as_spark_names_it(kernel):
    assert _printed(kernel, FIELD_CELL).splitlines() == FIELD_SPARK.splitlines()


def test_a_struct_field_by_attribute_or_key_is_named_as_spark_names_it(kernel):
    assert _printed(kernel, ITEM_CELL).splitlines() == ITEM_SPARK.splitlines()
