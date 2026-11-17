"""Spark's decimal result types apply when a table or column name has capital letters (#2663), in Spark SQL
and the DataFrame API. Every type and value here is Apache Spark 3.5.3's own output."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fabric_desktop.notebook import Kernel

FRAME = ("from decimal import Decimal\n"
         "from pyspark.sql import functions as F\n"
         "df = spark.createDataFrame([(Decimal('1.50'), Decimal('3.000')), (Decimal('2.25'), Decimal('7.000'))], "
         "'Amt decimal(10,2), b decimal(12,3)')\n"
         "df.createOrReplaceTempView('t')\n"
         "df.createOrReplaceTempView('MyT')\n")

QUOTIENT = [Decimal("0.500000000000000"), Decimal("0.321428571428571")]

# (query, Spark 3.5.3's dtypes, Spark 3.5.3's first column)
SQL = [
    ("SELECT Amt / b FROM t", [("(Amt / b)", "decimal(26,15)")], QUOTIENT),
    ("SELECT amt / b FROM t", [("(amt / b)", "decimal(26,15)")], QUOTIENT),
    ("SELECT Amt / b FROM MyT", [("(Amt / b)", "decimal(26,15)")], QUOTIENT),
    ("SELECT x.Amt / x.B FROM MyT x", [("(Amt / B)", "decimal(26,15)")], QUOTIENT),
    ("SELECT Amt * b, Amt + b FROM t", [("(Amt * b)", "decimal(23,5)"), ("(Amt + b)", "decimal(13,3)")],
     [Decimal("4.50000"), Decimal("15.75000")]),
    ("SELECT sum(Amt) FROM t", [("sum(Amt)", "decimal(20,2)")], [Decimal("3.75")]),
    ("SELECT avg(AMT) FROM t", [("avg(AMT)", "decimal(14,6)")], [Decimal("1.875000")]),
]


def run(code: str) -> str:
    out = Kernel().run(FRAME + code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return text


@pytest.mark.parametrize("query, dtypes, values", SQL, ids=[q for q, _, _ in SQL])
def test_spark_sql_decimal_types_whatever_the_case_of_the_names(query, dtypes, values):
    text = run(f"r = spark.sql({query!r})\nprint(r.dtypes)\nprint([row[0] for row in r.collect()])")
    assert text.splitlines() == [repr(dtypes), repr(values)]


def test_dataframe_decimal_types_whatever_the_case_of_the_names():
    text = run("print(df.select((F.col('Amt') / F.col('b')).alias('q'), F.col('Amt') * 2, F.col('Amt') + F.col('b')).dtypes)\n"
               "print(df.groupBy().agg(F.sum('Amt'), F.avg('Amt')).dtypes)\n"
               "print(df.selectExpr('Amt / b', 'AMT * B').dtypes)\n"
               "print([row[0] for row in df.select(F.col('Amt') / F.col('b')).collect()])")
    assert text.splitlines() == [
        "[('q', 'decimal(26,15)'), ('(Amt * 2)', 'decimal(12,2)'), ('(Amt + b)', 'decimal(13,3)')]",
        "[('sum(Amt)', 'decimal(20,2)'), ('avg(Amt)', 'decimal(14,6)')]",
        "[('(Amt / b)', 'decimal(26,15)'), ('(AMT * B)', 'decimal(23,5)')]",
        repr(QUOTIENT),
    ]
