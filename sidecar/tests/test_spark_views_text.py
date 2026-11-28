"""Temp views, text functions over other types, and createDataFrame with one type, on this PC, as in
Spark: createTempView refuses a name it already has (whatever its case) or one with a dot, dropTempView
says whether it dropped one, a global temp view is read as global_temp.name; upper(14) is '14' as
Spark's implicit cast writes it (a double as Java does); createDataFrame(["a"], "string") is one column,
value. Each expected output was printed by Apache Spark 3.5.3 for the same cell."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

VIEWS_CELL = r'''df = spark.createDataFrame([(2, "Alice"), (5, "Bob")], schema=["age", "name"])
def run(f):
    try:
        r = f()
        print(repr(r) if not hasattr(r, "collect") else sorted(r.collect()))
    except Exception as e:
        print(type(e).__name__, str(e).split("\n")[0])
run(lambda: df.createTempView("people"))
run(lambda: spark.sql("SELECT * FROM people"))
run(lambda: df.createTempView("people"))
run(lambda: df.createTempView("PEOPLE"))
run(lambda: df.filter(df.age > 3).createOrReplaceTempView("people"))
run(lambda: spark.sql("SELECT * FROM people"))
run(lambda: spark.catalog.dropTempView("people"))
run(lambda: spark.catalog.dropTempView("people"))
run(lambda: df.registerTempTable("p2"))
run(lambda: spark.table("p2"))
run(lambda: spark.catalog.dropTempView("P2"))
run(lambda: df.createGlobalTempView("people"))
run(lambda: spark.sql("SELECT * FROM global_temp.people"))
run(lambda: spark.table("global_temp.people"))
run(lambda: df.createGlobalTempView("people"))
run(lambda: df.filter(df.age > 3).createOrReplaceGlobalTempView("people"))
run(lambda: spark.sql("select name from GLOBAL_TEMP.People"))
run(lambda: spark.catalog.dropGlobalTempView("people"))
run(lambda: spark.catalog.dropGlobalTempView("people"))
run(lambda: spark.sql("SELECT * FROM global_temp.people"))
run(lambda: spark.catalog.dropTempView("nothing"))
run(lambda: df.createTempView("a.b"))
run(lambda: df.createGlobalTempView("a.b"))
run(lambda: spark.catalog.tableExists("people", "global_temp"))
'''

# Apache Spark 3.5.3's output for VIEWS_CELL.
VIEWS_SPARK = r'''None
[Row(age=2, name='Alice'), Row(age=5, name='Bob')]
AnalysisException [TEMP_TABLE_OR_VIEW_ALREADY_EXISTS] Cannot create the temporary view `people` because it already exists.
AnalysisException [TEMP_TABLE_OR_VIEW_ALREADY_EXISTS] Cannot create the temporary view `PEOPLE` because it already exists.
None
[Row(age=5, name='Bob')]
True
False
None
[Row(age=2, name='Alice'), Row(age=5, name='Bob')]
True
None
[Row(age=2, name='Alice'), Row(age=5, name='Bob')]
[Row(age=2, name='Alice'), Row(age=5, name='Bob')]
AnalysisException [TEMP_TABLE_OR_VIEW_ALREADY_EXISTS] Cannot create the temporary view `people` because it already exists.
None
[Row(name='Bob')]
True
False
AnalysisException [TABLE_OR_VIEW_NOT_FOUND] The table or view `global_temp`.`people` cannot be found. Verify the spelling and correctness of the schema and catalog.
False
AnalysisException [TEMP_VIEW_NAME_TOO_MANY_NAME_PARTS] CREATE TEMPORARY VIEW or the corresponding Dataset APIs only accept single-part view names, but got: a.b.
AnalysisException [TEMP_VIEW_NAME_TOO_MANY_NAME_PARTS] CREATE TEMPORARY VIEW or the corresponding Dataset APIs only accept single-part view names, but got: a.b.
False
'''

TEXT_CELL = r'''import datetime, decimal
from pyspark.sql import functions as F
df = spark.createDataFrame([(14, 2.5, True, datetime.date(2024, 1, 5), decimal.Decimal("1.50"), 1e20, -0.0005),
                            (None, None, None, None, None, None, None)],
                           "i long, d double, b boolean, dt date, m decimal(5,2), big double, small double")
for name in ["upper", "lower", "length", "trim", "ltrim", "rtrim", "initcap", "ascii", "char_length"]:
    out = df.select(*[getattr(F, name)(c) for c in df.columns])
    print(out.columns, out.collect())
for c in [F.lpad("i", 5, "0"), F.rpad(F.col("d"), 6, "*"), F.instr(F.col("i"), "4"), F.locate("4", F.col("i")),
          F.split(F.col("d"), "[.]"), F.regexp_replace(F.col("i"), "1", "x"), F.translate(F.col("i"), "1", "x"),
          F.repeat(F.col("i"), 2), F.upper(F.col("i") + 1), F.length(F.col("big"))]:
    print(df.select(c).collect())
for expr in ["upper(i)", "lower(b)", "length(d)", "trim(dt)", "ltrim(m)", "rtrim(i)", "ascii(i)", "char_length(dt)",
             "replace(i, '1', 'x')", "left(i, 1)", "right(d, 1)", "repeat(i, 2)", "lpad(i, 5, '0')", "rpad(d, 6, '*')",
             "upper(big)", "length(small)", "upper(cast(d as float))", "upper(timestamp'2024-01-05 10:00:00')",
             "instr(i, '4')", "regexp_replace(i, '1', 'x')", "split(d, '[.]')"]:
    print(expr, [tuple(r) for r in df.selectExpr(expr).collect()])  # values: Spark SQL names some of these its own way
for bad in [lambda: df.select(F.upper(F.array(F.col("i")))).collect(), lambda: df.selectExpr("upper(array(i))").collect()]:
    try:
        bad()
    except Exception as e:
        print("refused")
'''

# Apache Spark 3.5.3's output for TEXT_CELL.
TEXT_SPARK = r'''['upper(i)', 'upper(d)', 'upper(b)', 'upper(dt)', 'upper(m)', 'upper(big)', 'upper(small)'] [Row(upper(i)='14', upper(d)='2.5', upper(b)='TRUE', upper(dt)='2024-01-05', upper(m)='1.50', upper(big)='1.0E20', upper(small)='-5.0E-4'), Row(upper(i)=None, upper(d)=None, upper(b)=None, upper(dt)=None, upper(m)=None, upper(big)=None, upper(small)=None)]
['lower(i)', 'lower(d)', 'lower(b)', 'lower(dt)', 'lower(m)', 'lower(big)', 'lower(small)'] [Row(lower(i)='14', lower(d)='2.5', lower(b)='true', lower(dt)='2024-01-05', lower(m)='1.50', lower(big)='1.0e20', lower(small)='-5.0e-4'), Row(lower(i)=None, lower(d)=None, lower(b)=None, lower(dt)=None, lower(m)=None, lower(big)=None, lower(small)=None)]
['length(i)', 'length(d)', 'length(b)', 'length(dt)', 'length(m)', 'length(big)', 'length(small)'] [Row(length(i)=2, length(d)=3, length(b)=4, length(dt)=10, length(m)=4, length(big)=6, length(small)=7), Row(length(i)=None, length(d)=None, length(b)=None, length(dt)=None, length(m)=None, length(big)=None, length(small)=None)]
['trim(i)', 'trim(d)', 'trim(b)', 'trim(dt)', 'trim(m)', 'trim(big)', 'trim(small)'] [Row(trim(i)='14', trim(d)='2.5', trim(b)='true', trim(dt)='2024-01-05', trim(m)='1.50', trim(big)='1.0E20', trim(small)='-5.0E-4'), Row(trim(i)=None, trim(d)=None, trim(b)=None, trim(dt)=None, trim(m)=None, trim(big)=None, trim(small)=None)]
['ltrim(i)', 'ltrim(d)', 'ltrim(b)', 'ltrim(dt)', 'ltrim(m)', 'ltrim(big)', 'ltrim(small)'] [Row(ltrim(i)='14', ltrim(d)='2.5', ltrim(b)='true', ltrim(dt)='2024-01-05', ltrim(m)='1.50', ltrim(big)='1.0E20', ltrim(small)='-5.0E-4'), Row(ltrim(i)=None, ltrim(d)=None, ltrim(b)=None, ltrim(dt)=None, ltrim(m)=None, ltrim(big)=None, ltrim(small)=None)]
['rtrim(i)', 'rtrim(d)', 'rtrim(b)', 'rtrim(dt)', 'rtrim(m)', 'rtrim(big)', 'rtrim(small)'] [Row(rtrim(i)='14', rtrim(d)='2.5', rtrim(b)='true', rtrim(dt)='2024-01-05', rtrim(m)='1.50', rtrim(big)='1.0E20', rtrim(small)='-5.0E-4'), Row(rtrim(i)=None, rtrim(d)=None, rtrim(b)=None, rtrim(dt)=None, rtrim(m)=None, rtrim(big)=None, rtrim(small)=None)]
['initcap(i)', 'initcap(d)', 'initcap(b)', 'initcap(dt)', 'initcap(m)', 'initcap(big)', 'initcap(small)'] [Row(initcap(i)='14', initcap(d)='2.5', initcap(b)='True', initcap(dt)='2024-01-05', initcap(m)='1.50', initcap(big)='1.0e20', initcap(small)='-5.0e-4'), Row(initcap(i)=None, initcap(d)=None, initcap(b)=None, initcap(dt)=None, initcap(m)=None, initcap(big)=None, initcap(small)=None)]
['ascii(i)', 'ascii(d)', 'ascii(b)', 'ascii(dt)', 'ascii(m)', 'ascii(big)', 'ascii(small)'] [Row(ascii(i)=49, ascii(d)=50, ascii(b)=116, ascii(dt)=50, ascii(m)=49, ascii(big)=49, ascii(small)=45), Row(ascii(i)=None, ascii(d)=None, ascii(b)=None, ascii(dt)=None, ascii(m)=None, ascii(big)=None, ascii(small)=None)]
['char_length(i)', 'char_length(d)', 'char_length(b)', 'char_length(dt)', 'char_length(m)', 'char_length(big)', 'char_length(small)'] [Row(char_length(i)=2, char_length(d)=3, char_length(b)=4, char_length(dt)=10, char_length(m)=4, char_length(big)=6, char_length(small)=7), Row(char_length(i)=None, char_length(d)=None, char_length(b)=None, char_length(dt)=None, char_length(m)=None, char_length(big)=None, char_length(small)=None)]
[Row(lpad(i, 5, 0)='00014'), Row(lpad(i, 5, 0)=None)]
[Row(rpad(d, 6, *)='2.5***'), Row(rpad(d, 6, *)=None)]
[Row(instr(i, 4)=2), Row(instr(i, 4)=None)]
[Row(locate(4, i, 1)=2), Row(locate(4, i, 1)=None)]
[Row(split(d, [.], -1)=['2', '5']), Row(split(d, [.], -1)=None)]
[Row(regexp_replace(i, 1, x, 1)='x4'), Row(regexp_replace(i, 1, x, 1)=None)]
[Row(translate(i, 1, x)='x4'), Row(translate(i, 1, x)=None)]
[Row(repeat(i, 2)='1414'), Row(repeat(i, 2)=None)]
[Row(upper((i + 1))='15'), Row(upper((i + 1))=None)]
[Row(length(big)=6), Row(length(big)=None)]
upper(i) [('14',), (None,)]
lower(b) [('true',), (None,)]
length(d) [(3,), (None,)]
trim(dt) [('2024-01-05',), (None,)]
ltrim(m) [('1.50',), (None,)]
rtrim(i) [('14',), (None,)]
ascii(i) [(49,), (None,)]
char_length(dt) [(10,), (None,)]
replace(i, '1', 'x') [('x4',), (None,)]
left(i, 1) [('1',), (None,)]
right(d, 1) [('5',), (None,)]
repeat(i, 2) [('1414',), (None,)]
lpad(i, 5, '0') [('00014',), (None,)]
rpad(d, 6, '*') [('2.5***',), (None,)]
upper(big) [('1.0E20',), (None,)]
length(small) [(7,), (None,)]
upper(cast(d as float)) [('2.5',), (None,)]
upper(timestamp'2024-01-05 10:00:00') [('2024-01-05 10:00:00',), ('2024-01-05 10:00:00',)]
instr(i, '4') [(2,), (None,)]
regexp_replace(i, '1', 'x') [('x4',), (None,)]
split(d, '[.]') [(['2', '5'],), (None,)]
refused
refused
'''

ATOMIC_CELL = r'''from pyspark.sql.types import StringType, IntegerType, LongType
def run(f):
    try:
        df = f()
        print(df.columns, df.dtypes, df.collect(), df.isEmpty())
    except Exception as e:
        print("error")
run(lambda: spark.createDataFrame(["a"], "STRING"))
run(lambda: spark.createDataFrame([1, 2, None], "int"))
run(lambda: spark.createDataFrame([1, 2], "bigint"))
run(lambda: spark.createDataFrame([1.5], "double"))
run(lambda: spark.createDataFrame(["a", "b"], StringType()))
run(lambda: spark.createDataFrame([3], IntegerType()))
run(lambda: spark.createDataFrame([], "string"))
run(lambda: spark.createDataFrame([[1, 2]], "array<int>"))
run(lambda: spark.createDataFrame(["x"], "int"))
'''

# Apache Spark 3.5.3's output for ATOMIC_CELL.
ATOMIC_SPARK = r'''['value'] [('value', 'string')] [Row(value='a')] False
['value'] [('value', 'int')] [Row(value=1), Row(value=2), Row(value=None)] False
['value'] [('value', 'bigint')] [Row(value=1), Row(value=2)] False
['value'] [('value', 'double')] [Row(value=1.5)] False
['value'] [('value', 'string')] [Row(value='a'), Row(value='b')] False
['value'] [('value', 'int')] [Row(value=3)] False
['value'] [('value', 'string')] [] True
['value'] [('value', 'array<int>')] [Row(value=[1, 2])] False
error
'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def _printed(kernel, cell):
    out = kernel.run(cell)
    assert out["status"] == "ok", out["outputs"]
    return "".join(o.get("text", "") for o in out["outputs"])


@pytest.mark.parametrize(("cell", "spark"), [(VIEWS_CELL, VIEWS_SPARK), (TEXT_CELL, TEXT_SPARK), (ATOMIC_CELL, ATOMIC_SPARK)],
                         ids=["temp views", "text functions", "createDataFrame with one type"])
def test_the_cell_answers_as_spark(kernel, cell, spark):
    assert _printed(kernel, cell).splitlines() == spark.splitlines()


def test_a_row_given_to_one_type_is_refused_by_name(kernel):
    # Spark stores the Java object's name ([Ljava.lang.Object;@...) as the string.
    out = kernel.run('spark.createDataFrame([("a",)], "string").collect()')
    assert out["status"] == "error" and "makes each item one value" in str(out["outputs"])


@pytest.mark.parametrize("use", [
    "df.groupBy(df.a.withField('z', F.lit(1)).c).count()",
    "df.orderBy(F.upper(df.a.dropFields('b').c))",
    "df.agg(F.max(df.a.dropFields('b').c))",
    "df.groupBy('n').agg(F.max(df.a.dropFields('b').c))",
])
def test_an_edited_struct_inside_grouping_or_ordering_is_refused_by_name(kernel, use):
    setup = ("from pyspark.sql import Row\nfrom pyspark.sql import functions as F\n"
             "df = spark.createDataFrame([Row(a=Row(b=1, c=2), n=1)])\n")
    out = kernel.run(setup + use + ".collect()")
    assert out["status"] == "error"
    assert "withField and dropFields run on this PC only as a whole column" in str(out["outputs"])
