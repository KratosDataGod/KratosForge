"""url_decode and url_encode in Spark SQL (spark.sql, selectExpr, F.expr) on this PC, as Spark's (Java's
URLDecoder and URLEncoder): + is a space, a % that doesn't start two hex digits is CANNOT_DECODE_URL,
and only A-Z a-z 0-9 . - * _ stay as they are when encoding. Also a quote or backslash in a map key,
a struct field name (getField) or an unpivoted column name. Each expected output was printed by
Apache Spark 3.5.3 for the same cell."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

URL_CELL = r'''from pyspark.sql import functions as F
for s in ["a%20b+c", "%e4%bd%a0", "%2B%25", "", None, "%2s", "%", "a%2", "%ZZ"]:
    try:
        print(repr(s), repr(spark.createDataFrame([(s,)], "x string").selectExpr("url_decode(x)").collect()[0][0]))
    except Exception as e:
        print(repr(s), "ERR", "CANNOT_DECODE_URL" in str(e))
for s in ["a b&é~*.-_!", "", "100%/x?y=1"]:
    print(repr(s), repr(spark.createDataFrame([(s,)], "x string").select(F.expr("url_encode(x)")).collect()[0][0]))
print(spark.sql("select url_decode('a+b%21'), url_encode('a b')").collect()[0][:])
'''

# Apache Spark 3.5.3's output for URL_CELL.
URL_SPARK = r''''a%20b+c' 'a b c'
'%e4%bd%a0' '你'
'%2B%25' '+%'
'' ''
None None
'%2s' ERR True
'%' ERR True
'a%2' ERR True
'%ZZ' ERR True
'a b&é~*.-_!' 'a+b%26%C3%A9%7E*.-_%21'
'' ''
'100%/x?y=1' '100%25%2Fx%3Fy%3D1'
('a b!', 'a+b')
'''

QUOTE_CELL = r'''from pyspark.sql import functions as F
df = spark.createDataFrame([({"k'": 1, "a\\b": 2, "c\\'": 3}, (4,))], "m map<string,int>, s struct<`x'y`: int>")
print(df.select(df.m["k'"], df.m["a\\b"], df.m["c\\'"]).collect())
print(df.select(df.s.getField("x'y")).collect())
u = spark.createDataFrame([(1, 2, 3)], ["id", "it's", "a\\b"])
print(u.unpivot("id", ["it's", "a\\b"], "var", "val").collect())
'''

# Apache Spark 3.5.3's output for QUOTE_CELL.
QUOTE_SPARK = r'''[Row(m[k']=1, m[a\b]=2, m[c\']=3)]
[Row(s.x'y=4)]
[Row(id=1, var="it's", val=2), Row(id=1, var='a\\b', val=3)]
'''


@pytest.fixture(scope="module")
def kernel():
    return Kernel()


def _printed(kernel, cell):
    out = kernel.run(cell)
    assert out["status"] == "ok", out["outputs"]
    return "".join(o.get("text", "") for o in out["outputs"])


def test_url_decode_and_encode_answer_as_spark(kernel):
    assert _printed(kernel, URL_CELL).splitlines() == URL_SPARK.splitlines()


def test_url_decode_refuses_bytes_that_are_not_utf8(kernel):
    out = kernel.run("spark.sql(\"select url_decode('%C3')\").collect()")
    assert out["status"] == "error" and "U+FFFD" in str(out["outputs"])


def test_a_quote_or_backslash_in_a_key_or_name_is_read_as_written(kernel):
    assert _printed(kernel, QUOTE_CELL).splitlines() == QUOTE_SPARK.splitlines()
