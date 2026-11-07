"""format_string and printf (Java's Formatter, as Spark's) and conv, in F, selectExpr and spark.sql in
a notebook cell on this PC. A NULL argument prints as null (false for %b), width and precision apply
to it as to text, %x and %o print a negative number as its two's complement, %f rounds half up as
Java does, a double prints as Java writes it (1.0E20), and %d of a double is an error, as in Spark.
selectExpr reads Spark SQL as spark.sql does and names a column as Spark does. Each expected output
was printed by Apache Spark 3.5.3 for the same cell."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

FORMAT_CELL = r'''from pyspark.sql import functions as F
df = spark.createDataFrame([(2, 5000000000, "Alice", 1.5, True), (-3, -1, "Bob", None, False), (None, None, None, -2.25, None)],
                           "i int, l bigint, s string, h double, b boolean")
def show(*cols):
    for c in cols:
        try:
            r = df.select(c)
            print(r.columns[0], [tuple(x)[0] for x in r.collect()])
        except Exception as e:
            print("ERR", type(e).__name__, str(e).splitlines()[0][:100])
show(F.format_string("%s|%S|%10s|%-10s|%.2s", "s", "s", "s", "s", "s"),
     F.format_string("%d|%5d|%-5d|%05d|%+d|% d|%,d", "i", "i", "i", "i", "i", "i", "l"),
     F.format_string("%x|%X|%o|%x|%08x", "i", "i", "i", "l", "i"),
     F.format_string("%f|%.1f|%10.3f|%-10.2f|%e|%.2E|%+.1f|%,.2f", "h", "h", "h", "h", "h", "h", "h", "h"),
     F.format_string("%s %s %s %s", "i", "h", "b", "l"),
     F.format_string("%b|%B|%5b|%b", "b", "b", "b", "s"),
     F.format_string("%2$s-%1$s", "i", "s"),
     F.format_string("%n|%%|x"),
     F.format_string("%10.4s|", "s"),
     F.printf(F.lit("%s=%d"), "s", "i"))
for src in ['F.format_string("%d", "h")', 'F.format_string("%s %s", "i")', 'F.format_string("%c", "i")']:
    try:
        r = df.select(eval(src)); r.collect(); print(src, "ran")
    except Exception as e:
        print(src, "ERR")
for q in ["select format_string('%s|%5d|%.2f', s, i, h) from t", "select printf('%x|%s', i, b) from t", "select format_string('%d', h) from t"]:
    df.createOrReplaceTempView("t")
    try:
        print(q, [tuple(x)[0] for x in spark.sql(q).collect()])
    except Exception as e:
        print(q, "ERR")
'''

# Apache Spark 3.5.3's output for FORMAT_CELL.
FORMAT_SPARK = r'''format_string(%s|%S|%10s|%-10s|%.2s, s, s, s, s, s) ['Alice|ALICE|     Alice|Alice     |Al', 'Bob|BOB|       Bob|Bob       |Bo', 'null|NULL|      null|null      |nu']
format_string(%d|%5d|%-5d|%05d|%+d|% d|%,d, i, i, i, i, i, i, l) ['2|    2|2    |00002|+2| 2|5,000,000,000', '-3|   -3|-3   |-0003|-3|-3|-1', 'null| null|null | null|null|null|null']
format_string(%x|%X|%o|%x|%08x, i, i, i, l, i) ['2|2|2|12a05f200|00000002', 'fffffffd|FFFFFFFD|37777777775|ffffffffffffffff|fffffffd', 'null|NULL|null|null|    null']
format_string(%f|%.1f|%10.3f|%-10.2f|%e|%.2E|%+.1f|%,.2f, h, h, h, h, h, h, h, h) ['1.500000|1.5|     1.500|1.50      |1.500000e+00|1.50E+00|+1.5|1.50', 'null|n|       nul|nu        |null|NU|n|nu', '-2.250000|-2.3|    -2.250|-2.25     |-2.250000e+00|-2.25E+00|-2.3|-2.25']
format_string(%s %s %s %s, i, h, b, l) ['2 1.5 true 5000000000', '-3 null false -1', 'null -2.25 null null']
format_string(%b|%B|%5b|%b, b, b, b, s) ['true|TRUE| true|true', 'false|FALSE|false|true', 'false|FALSE|false|false']
format_string(%2$s-%1$s, i, s) ['Alice-2', 'Bob--3', 'null-null']
format_string(%n|%%|x) ['\n|%|x', '\n|%|x', '\n|%|x']
format_string(%10.4s|, s) ['      Alic|', '       Bob|', '      null|']
printf(%s=%d, s, i) ['Alice=2', 'Bob=-3', 'null=null']
F.format_string("%d", "h") ERR
F.format_string("%s %s", "i") ERR
F.format_string("%c", "i") ERR
select format_string('%s|%5d|%.2f', s, i, h) from t ['Alice|    2|1.50', 'Bob|   -3|nu', 'null| null|-2.25']
select printf('%x|%s', i, b) from t ['2|true', 'fffffffd|false', 'null|null']
select format_string('%d', h) from t ERR
'''

CONV_CELL = r'''from pyspark.sql import functions as F
df = spark.createDataFrame([("a.b", 10, 2), ("ff", -10, None), (None, 0, 3)], "s string, n int, g int")
def show(*cols):
    for c in cols:
        r = df.select(c)
        print(r.columns[0], [tuple(x)[0] for x in r.collect()])
show(F.conv("s", 16, 10), F.conv(F.lit("ff"), 16, 10), F.conv(F.lit("-10"), 10, 2), F.conv(F.lit("-10"), 10, -2), F.conv(F.lit("1z"), 16, 10),
     F.conv(F.lit("255"), 10, 36), F.conv(df.n.cast("string"), 10, 2), F.conv(F.lit("zz"), 36, 16), F.conv(F.lit("18446744073709551616"), 10, 16))
show(df.n.cast("string"), F.upper(df.n.cast("string")), F.max(df.n.cast("string")).alias("m") if False else F.lit(1))
print(df.agg(F.max(df.n.cast("string"))).columns, df.select(df.n.cast("long"), (df.n + 1).cast("string")).columns)
show(F.format_string("%s|%5s|%-5s|", "s", "n", "g"), F.format_string("[%04d] %x %o", "n", "n", "g"), F.printf(F.lit("%s=%d"), "s", "n"))
spark.sql("select 1.0e20D a, 1.0e-5D b, 12345678.0D c, 0.15D d, cast('NaN' as double) e, cast('Infinity' as double) f").selectExpr(
    "format_string('%s %s %s', a, b, c)", "format_string('%.1f %.1f %8.2f %s %f', d, d * 10, f, e, e)", "printf('%5.1e|%-12.3E|', a, b)").show(truncate=False)
df.createOrReplaceTempView("t")
spark.sql("select s, format_string('%s:%3d:%b', s, g, g) as x, printf('%X', n) as y from t order by n").show()
print(spark.range(1).selectExpr("format_string('%s-%d', 'x', id)", "'lit'", "id + 1", "upper('a')", "conv('ff', 16, 10)").columns)
'''

# Apache Spark 3.5.3's output for CONV_CELL.
CONV_SPARK = r'''conv(s, 16, 10) ['10', '255', None]
conv(ff, 16, 10) ['255', '255', '255']
conv(-10, 10, 2) ['1111111111111111111111111111111111111111111111111111111111110110', '1111111111111111111111111111111111111111111111111111111111110110', '1111111111111111111111111111111111111111111111111111111111110110']
conv(-10, 10, -2) ['-1010', '-1010', '-1010']
conv(1z, 16, 10) ['1', '1', '1']
conv(255, 10, 36) ['73', '73', '73']
conv(CAST(n AS STRING), 10, 2) ['1010', '1111111111111111111111111111111111111111111111111111111111110110', '0']
conv(zz, 36, 16) ['50F', '50F', '50F']
conv(18446744073709551616, 10, 16) ['FFFFFFFFFFFFFFFF', 'FFFFFFFFFFFFFFFF', 'FFFFFFFFFFFFFFFF']
n ['10', '-10', '0']
upper(CAST(n AS STRING)) ['10', '-10', '0']
1 [1, 1, 1]
['max(CAST(n AS STRING))'] ['n', 'CAST((n + 1) AS STRING)']
format_string(%s|%5s|%-5s|, s, n, g) ['a.b|   10|2    |', 'ff|  -10|null |', 'null|    0|3    |']
format_string([%04d] %x %o, n, n, g) ['[0010] a 2', '[-010] fffffff6 null', '[0000] 0 3']
printf(%s=%d, s, n) ['a.b=10', 'ff=-10', 'null=0']
+--------------------------------+----------------------------------------------------------+----------------------------+
|format_string(%s %s %s, a, b, c)|format_string(%.1f %.1f %8.2f %s %f, d, (d * 10), f, e, e)|printf(%5.1e|%-12.3E|, a, b)|
+--------------------------------+----------------------------------------------------------+----------------------------+
|1.0E20 1.0E-5 1.2345678E7       |0.2 1.5 Infinity NaN NaN                                  |1.0e+20|1.000E-05   |       |
+--------------------------------+----------------------------------------------------------+----------------------------+

+----+-------------+--------+
|   s|            x|       y|
+----+-------------+--------+
|  ff|ff:null:false|FFFFFFF6|
|NULL|null:  3:true|       0|
| a.b| a.b:  2:true|       A|
+----+-------------+--------+

['format_string(%s-%d, x, id)', 'lit', '(id + 1)', 'upper(a)', 'conv(ff, 16, 10)']
'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def _printed(kernel, cell):
    out = kernel.run(cell)
    assert out["status"] == "ok", out["outputs"]
    return "".join(o.get("text", "") for o in out["outputs"])


def test_format_string_and_printf_print_as_spark_does(kernel):
    assert _printed(kernel, FORMAT_CELL).splitlines() == FORMAT_SPARK.splitlines()


def test_conv_selectexpr_and_spark_sql_give_spark_s_values_and_names(kernel):
    assert _printed(kernel, CONV_CELL).splitlines() == CONV_SPARK.splitlines()


@pytest.mark.parametrize("fmt", ["%g", "%c", "%h", "%(d", "%#x", "%<s", "%tY", "%5.2d"])
def test_a_conversion_this_pc_cant_write_as_java_is_refused_by_name(kernel, fmt):
    out = kernel.run(f"from pyspark.sql import functions as F\nF.format_string({fmt!r}, F.lit(1))")
    assert out["status"] == "error"
    assert "Fabric" in out["outputs"][0]["evalue"]


def test_a_missing_argument_or_a_lone_percent_is_an_error(kernel):
    for fmt in ("%s %s", "100%"):
        out = kernel.run(f"from pyspark.sql import functions as F\nF.format_string({fmt!r}, F.lit(1))")
        assert out["status"] == "error", fmt
