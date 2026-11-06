"""F functions that DuckDB's Spark API lacks or gets wrong, in a notebook cell on this PC: bool_and,
every, bool_or, some, count_if, bit_and/or/xor, log (and ln, log2, log10, log1p: NULL at or below zero),
pow/power, factorial, bin, locate, substring_index, translate, char_length, split with a limit, and
rand/randn with a seed. Each expected output was printed by Apache Spark 3.5.3 for the same cell."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

VALUES_CELL = r'''from pyspark.sql import functions as F
df = spark.createDataFrame([(2, "Alice", 1.5, "a.b.c.d"), (-3, "Bob", None, "x"), (0, None, 4.0, ""), (None, "AAbb", -2.0, None)],
                           "age int, name string, h double, s string")
def show(*cols):
    for c in cols:
        try:
            r = df.select(c)
            print(r.columns[0], r.dtypes[0][1], [tuple(x)[0] for x in r.collect()])
        except Exception as e:
            print("ERR", type(e).__name__, str(e).splitlines()[0][:120])
def agg(*cols):
    for c in cols:
        try:
            r = df.agg(c)
            print(r.columns[0], r.dtypes[0][1], [tuple(x)[0] for x in r.collect()])
            r = df.filter("age > 100").agg(c)
            print("  empty:", [tuple(x)[0] for x in r.collect()])
        except Exception as e:
            print("ERR", type(e).__name__, str(e).splitlines()[0][:120])
agg(F.bool_and(df.age > 1), F.every(df.age > -5), F.bool_or(df.age > 1), F.some(df.age > 5), F.count_if(df.age > -1),
    F.bit_and(df.age), F.bit_or(df.age), F.bit_xor(df.age), F.bit_and(F.lit(6).cast("long")))
show(F.log(df.age), F.log(df.h), F.log(2.0, df.age), F.log(10.0, df.h), F.log(-1.0, df.h), F.log(1.0, df.h), F.log10(df.age), F.log10(df.h), F.log2(df.age), F.ln(df.age), F.log1p(df.h))
show(F.pow(df.age, 2), F.power(df.h, 0.5), F.pow(df.age, -1), F.pow(2, df.age))
show(F.factorial(df.age), F.factorial(df.age + 18), F.factorial(F.lit(21)), F.factorial(F.lit(20)))
show(F.bin(df.age), F.bin(df.h), F.bin(F.lit(0)), F.bin(F.lit(1024)))
show(F.locate("l", df.name), F.locate("b", df.name, 4), F.locate("", df.name), F.locate("", df.name, 3), F.locate("A", df.name, 0), F.locate("A", df.name, -1), F.locate("zz", df.name))
show(F.substring_index(df.s, ".", 2), F.substring_index(df.s, ".", -2), F.substring_index(df.s, ".", 0), F.substring_index(df.s, ".", 9), F.substring_index(df.s, ".", -9), F.substring_index(df.s, "", 1), F.substring_index(df.s, "b.c", 1), F.substring_index(df.name, "b", -1))
show(F.translate(df.name, "Al", "aL"), F.translate(df.name, "Abc", "x"), F.translate(df.name, "AA", "xy"), F.translate(df.name, "", "x"), F.translate(df.name, "b", ""))
show(F.char_length(df.name), F.character_length(df.name), F.char_length(df.age))
show(F.split(df.s, "\\.", 2), F.split(df.s, "\\.", 1), F.split(df.s, "\\.", 0), F.split(df.s, "\\.", 10), F.split(df.s, "[.]", 3), F.split(df.name, "", 2), F.split(df.name, "b", 2))
'''

# Apache Spark 3.5.3's output for VALUES_CELL: each column's name, type and values (an aggregate also
# on an empty frame).
VALUES_SPARK = r'''bool_and((age > 1)) boolean [False]
  empty: [None]
every((age > -5)) boolean [True]
  empty: [None]
bool_or((age > 1)) boolean [True]
  empty: [None]
some((age > 5)) boolean [False]
  empty: [None]
count_if((age > -1)) bigint [2]
  empty: [0]
bit_and(age) int [0]
  empty: [None]
bit_or(age) int [-1]
  empty: [None]
bit_xor(age) int [-1]
  empty: [None]
bit_and(CAST(6 AS BIGINT)) bigint [6]
  empty: [None]
ln(age) double [0.6931471805599453, None, None, None]
ln(h) double [0.4054651081081644, None, 1.3862943611198906, None]
LOG(2.0, age) double [1.0, None, None, None]
LOG(10.0, h) double [0.17609125905568124, None, 0.6020599913279623, None]
LOG(-1.0, h) double [None, None, None, None]
LOG(1.0, h) double [inf, None, inf, None]
LOG10(age) double [0.3010299956639812, None, None, None]
LOG10(h) double [0.17609125905568124, None, 0.6020599913279624, None]
LOG2(age) double [1.0, None, None, None]
ln(age) double [0.6931471805599453, None, None, None]
LOG1P(h) double [0.9162907318741551, None, 1.6094379124341003, None]
POWER(age, 2) double [4.0, 9.0, 0.0, None]
POWER(h, 0.5) double [1.224744871391589, None, 2.0, nan]
POWER(age, -1) double [0.5, -0.3333333333333333, inf, None]
POWER(2, age) double [4.0, 0.125, 1.0, None]
factorial(age) bigint [2, None, 1, None]
factorial((age + 18)) bigint [2432902008176640000, 1307674368000, 6402373705728000, None]
factorial(21) bigint [None, None, None, None]
factorial(20) bigint [2432902008176640000, 2432902008176640000, 2432902008176640000, 2432902008176640000]
bin(age) string ['10', '1111111111111111111111111111111111111111111111111111111111111101', '0', None]
bin(h) string ['1', None, '100', '1111111111111111111111111111111111111111111111111111111111111110']
bin(0) string ['0', '0', '0', '0']
bin(1024) string ['10000000000', '10000000000', '10000000000', '10000000000']
locate(l, name, 1) int [2, 0, None, 0]
locate(b, name, 4) int [0, 0, None, 4]
locate(, name, 1) int [1, 1, None, 1]
locate(, name, 3) int [1, 1, None, 1]
locate(A, name, 0) int [0, 0, None, 0]
locate(A, name, -1) int [0, 0, None, 0]
locate(zz, name, 1) int [0, 0, None, 0]
substring_index(s, ., 2) string ['a.b', 'x', '', None]
substring_index(s, ., -2) string ['c.d', 'x', '', None]
substring_index(s, ., 0) string ['', '', '', None]
substring_index(s, ., 9) string ['a.b.c.d', 'x', '', None]
substring_index(s, ., -9) string ['a.b.c.d', 'x', '', None]
substring_index(s, , 1) string ['', '', '', None]
substring_index(s, b.c, 1) string ['a.', 'x', '', None]
substring_index(name, b, -1) string ['Alice', '', None, '']
translate(name, Al, aL) string ['aLice', 'Bob', None, 'aabb']
translate(name, Abc, x) string ['xlie', 'Bo', None, 'xx']
translate(name, AA, xy) string ['xlice', 'Bob', None, 'xxbb']
translate(name, , x) string ['Alice', 'Bob', None, 'AAbb']
translate(name, b, ) string ['Alice', 'Bo', None, 'AA']
char_length(name) int [5, 3, None, 4]
character_length(name) int [5, 3, None, 4]
char_length(age) int [1, 2, 1, None]
split(s, \., 2) array<string> [['a', 'b.c.d'], ['x'], [''], None]
split(s, \., 1) array<string> [['a.b.c.d'], ['x'], [''], None]
split(s, \., 0) array<string> [['a', 'b', 'c', 'd'], ['x'], [''], None]
split(s, \., 10) array<string> [['a', 'b', 'c', 'd'], ['x'], [''], None]
split(s, [.], 3) array<string> [['a', 'b', 'c.d'], ['x'], [''], None]
split(name, , 2) array<string> [['A', 'l'], ['B', 'o'], None, ['A', 'A']]
split(name, b, 2) array<string> [['Alice'], ['Bo', ''], None, ['AA', 'b']]
'''

TABLES_CELL = r'''from pyspark.sql import functions as F
df = spark.createDataFrame([(1, "a,b,c", 8.0), (1, "d", 1e-10), (2, None, 0.5)], "g int, s string, x double")
df.groupBy("g").agg(F.count_if(F.col("x") > 0.1), F.bool_and(F.col("x") > 0), F.bit_or("g")).orderBy("g").show()
df.select(F.log2("x"), F.log1p("x"), F.log("x"), F.log10("x")).show(truncate=False)
df.withColumn("p", F.split("s", ",", 2)).select("g", F.explode("p").alias("e")).show()
df.select(F.substring_index("s", ",", -1).alias("last"), F.locate(",", "s").alias("at"), F.translate("s", ",", ";")).show()
r = df.withColumn("r", F.rand(42)).withColumn("n", F.randn(42))
print(r.columns, [0.0 <= x.r < 1.0 for x in r.collect()])
print(sorted(x.r for x in r.collect()) == sorted(x.r for x in df.withColumn("r", F.rand(42)).collect()))
print(df.select(F.sum(F.pow("x", 2)), F.max(F.factorial("g"))).columns)
'''

# Apache Spark 3.5.3's output for TABLES_CELL.
TABLES_SPARK = r'''+---+-------------------+-----------------+---------+
|  g|count_if((x > 0.1))|bool_and((x > 0))|bit_or(g)|
+---+-------------------+-----------------+---------+
|  1|                  1|             true|        1|
|  2|                  1|             true|        2|
+---+-------------------+-----------------+---------+

+-------------------+---------------------+-------------------+-------------------+
|LOG2(x)            |LOG1P(x)             |ln(x)              |LOG10(x)           |
+-------------------+---------------------+-------------------+-------------------+
|3.0                |2.1972245773362196   |2.0794415416798357 |0.9030899869919435 |
|-33.219280948873624|9.999999999500001E-11|-23.025850929940457|-10.0              |
|-1.0               |0.4054651081081644   |-0.6931471805599453|-0.3010299956639812|
+-------------------+---------------------+-------------------+-------------------+

+---+---+
|  g|  e|
+---+---+
|  1|  a|
|  1|b,c|
|  1|  d|
+---+---+

+----+----+------------------+
|last|  at|translate(s, ,, ;)|
+----+----+------------------+
|   c|   2|             a;b;c|
|   d|   0|                 d|
|NULL|NULL|              NULL|
+----+----+------------------+

['g', 's', 'x', 'r', 'n'] [True, True, True]
True
['sum(POWER(x, 2))', 'max(factorial(g))']
'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def _printed(kernel, cell):
    out = kernel.run(cell)
    assert out["status"] == "ok", out["outputs"]
    return "".join(o.get("text", "") for o in out["outputs"])


def test_functions_give_spark_s_names_types_and_values(kernel):
    assert _printed(kernel, VALUES_CELL).splitlines() == VALUES_SPARK.splitlines()


def test_functions_work_in_group_by_withcolumn_explode_and_show(kernel):
    assert _printed(kernel, TABLES_CELL).splitlines() == TABLES_SPARK.splitlines()


def test_pow_matches_spark_to_within_the_last_bit(kernel):
    # Spark's pow is Java's StrictMath.pow; this PC's can differ in the last bit (2 ** 1.5 here).
    text = _printed(kernel, "print(spark.createDataFrame([(2, 1.5)], 'a int, b double')"
                            ".select(F.pow('a', 'b')).first()[0])")
    assert abs(float(text) - 2.82842712474619) < 1e-15


SEEDED_CELL = """
r = spark.range(1000)
a = [x[0] for x in r.select(F.rand(7)).collect()]
b = [x[0] for x in r.select(F.rand(7)).collect()]
n = [x[0] for x in r.select(F.randn(7)).collect()]
print(a == b, all(0.0 <= v < 1.0 for v in a), len(set(a)), -0.2 < sum(n) / len(n) < 0.2)
print(r.filter(F.rand(7) < 0.5).count() == sum(v < 0.5 for v in a))
print(r.where(F.monotonically_increasing_id() < 5).count(), r.filter(r.id < 3).columns)
"""


def test_a_seeded_rand_gives_the_same_numbers_each_run_and_filters(kernel):
    assert _printed(kernel, SEEDED_CELL).splitlines() == ["True True 1000 True", "True", "5 ['id']"]
