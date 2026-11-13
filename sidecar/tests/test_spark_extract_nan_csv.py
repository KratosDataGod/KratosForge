"""extract/date_part on a text column, NaN literals and comparisons, and read.csv's default comma answer as Apache
Spark 3.5.3 does (#2554, #2555, #2557). Every name and value here is Spark's own output, captured from Spark 3.5.3."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from sqlglot import exp

from fabric_desktop import spark_sql_names, sql, sql_dialects
from fabric_desktop.notebook import Kernel

ROWS = ("spark.createDataFrame([('2024-03-05 10:20:30', 1.5), ('bad', float('nan')), (None, 2.0)], "
        "'s string, d double').createOrReplaceTempView('t')\n")


def run(code: str) -> str:
    out = Kernel().run(ROWS + code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return text


EXTRACT = [
    ("extract(year FROM s)", "extract(year FROM s)", [2024, None, None]),
    ("extract(hour FROM s)", "extract(hour FROM s)", [10, None, None]),
    ("date_part('month', s)", "date_part(month, s)", [3, None, None]),
    ("extract(dayofweek FROM s)", "extract(dayofweek FROM s)", [3, None, None]),
    ("extract(day FROM s)", "extract(day FROM s)", [5, None, None]),
]


@pytest.mark.parametrize("query, name, values", EXTRACT, ids=[q for q, _, _ in EXTRACT])
def test_extract_reads_a_text_column_as_spark(query, name, values):
    text = run(f"r = spark.sql({f'SELECT {query} FROM t'!r})\nprint(r.columns)\nprint([row[0] for row in r.collect()])")
    assert text.splitlines() == [repr([name]), repr(values)]


def test_date_part_second_of_a_text_column_is_spark_s_decimal():
    text = run("print([row[0] for row in spark.sql(\"SELECT date_part('second', s) FROM t\").collect()])")
    assert text.strip() == repr([Decimal("30.000000"), None, None])


def test_a_nan_literal_and_nan_comparisons_answer_as_spark():
    text = run("from pyspark.sql import functions as F\n"
               "nan = F.lit(float('nan'))\n"
               "r = spark.table('t').select(nan.alias('n'), (F.col('d') == nan).alias('eq'), (F.col('d') > nan).alias('gt'),"
               " (F.col('d') < nan).alias('lt'), F.isnan(nan).alias('isn'), (F.col('d') != float('nan')).alias('ne'))\n"
               "print([tuple(x) for x in r.collect()])")
    assert text.strip() == ("[(nan, False, False, True, True, True), (nan, True, False, False, True, False), "
                            "(nan, False, False, True, True, True)]")


def test_nan_in_spark_sql_is_named_and_compared_as_spark():
    text = run("print(spark.sql(\"SELECT double('NaN') = double('NaN'), double('NaN') > 1.0, "
               "cast('NaN' as double)\").collect())")
    assert text.strip() == "[Row((NaN = NaN)=True, (NaN > 1.0)=True, CAST(NaN AS DOUBLE)=nan)]"


def test_read_csv_splits_on_a_comma_unless_told_otherwise(tmp_path):
    semi, pipe = tmp_path / "x.csv", tmp_path / "y.csv"
    semi.write_text("a;b,c\n1;2,3\n4;5,6\n", encoding="utf-8")
    pipe.write_text("a|b\n1|2\n", encoding="utf-8")
    text = run(f"x, y = {str(semi)!r}, {str(pipe)!r}\n"
               "for c in [spark.read.csv(x, header=True), spark.read.option('header', True).csv(x),\n"
               "          spark.read.format('csv').option('header', 'true').load(x), spark.read.csv(y),\n"
               "          spark.read.csv(y, sep='|'), spark.read.options(delimiter=';').csv(x, header=True)]:\n"
               "    print(c.columns, [tuple(r) for r in c.collect()])")
    assert text.splitlines() == [
        "['a;b', 'c'] [('1;2', '3'), ('4;5', '6')]",
        "['a;b', 'c'] [('1;2', '3'), ('4;5', '6')]",
        "['a;b', 'c'] [('1;2', '3'), ('4;5', '6')]",
        "['_c0'] [('a|b',), ('1|2',)]",
        "['_c0', '_c1'] [('a', 'b'), ('1', '2')]",
        "['a', 'b,c'] [('1', '2,3'), ('4', '5,6')]",
    ]


BUILTINS = json.loads((Path(sql.__file__).parent / "spark_builtins.json").read_text(encoding="utf-8"))


def test_every_builtin_example_gets_a_column_name_without_raising():
    named = 0
    for entry in BUILTINS:
        try:
            statements = sql_dialects.parse(entry["example"], read="spark")
        except Exception:  # noqa: BLE001 - an example this PC can't parse is another test's concern
            continue
        for statement in statements:
            for select in statement.find_all(exp.Select):
                for column in select.expressions:
                    spark_sql_names.column_name(column, select)
                    named += 1
    assert named > 400
