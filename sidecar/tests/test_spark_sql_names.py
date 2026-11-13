"""Spark SQL columns without AS are named as Apache Spark 3.5.3 names them (spark_sql_names), and split with a
limit, replace with two arguments, btrim and base64 answer as Spark's. Every name and value here is Spark's
own output, captured from Spark 3.5.3."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "spark_3_5_3_sql_names.json").read_text(encoding="utf-8"))


def _name(query: str) -> str:
    return str(sql.parse(f"SELECT {query} FROM t")[0].expressions[0].alias_or_name)


@pytest.mark.parametrize("query", sorted(FIXTURE["names"]))
def test_a_column_is_named_as_spark_names_it(query):
    assert _name(query) == FIXTURE["names"][query]


def run(code: str) -> str:
    out = Kernel().run("spark.createDataFrame([('a.b.c', 'xxhixx', 14), ('a..b.', ' pad ', None), (None, None, 3)], "
                       "'s string, t string, i int').createOrReplaceTempView('t')\n" + code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return text


def test_spark_sql_select_expr_and_expr_give_sparks_names():
    text = run("from pyspark.sql import functions as F\n"
               "df = spark.table('t')\n"
               "print(spark.sql(\"SELECT char_length(s), instr(s, 'b'), locate('b', s), cast(i AS string), "
               "upper(i) || 'x', count(*) OVER () FROM t\").columns)\n"
               "print(df.selectExpr(\"split(s, '[.]')\", \"regexp_replace(s, 'b', 'x')\", 'round(i)', "
               "\"date'2024-01-01'\", 'true', 'i in (1, 2)').columns)\n"
               "print(df.select(F.expr(\"if(i > 1, 'y', 'n')\"), F.expr(\"s || 'x'\"), F.expr('cast(i as string)')).columns)\n"
               "print(df.groupBy('s').agg(F.expr('count(*)'), F.expr('collect_list(i)')).columns)")
    assert text.splitlines() == [
        ("['char_length(s)', 'instr(s, b)', 'locate(b, s, 1)', 'i', 'concat(upper(i), x)', "
         "'count(1) OVER (ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)']"),
        ("['split(s, [.], -1)', 'regexp_replace(s, b, x, 1)', 'round(i, 0)', \"DATE '2024-01-01'\", 'true', "
         "'(i IN (1, 2))']"),
        "['(IF((i > 1), y, n))', 'concat(s, x)', 'i']",
        "['s', 'count(1)', 'collect_list(i)']",
    ]


def test_a_cast_column_is_read_back_by_the_name_spark_gives_it():
    assert "[Row(i='14'), Row(i=None), Row(i='3')]" in run(
        "r = spark.sql('SELECT cast(i AS string) FROM t')\nprint(r.select(r['i']).collect())")


# (query, Spark 3.5.3's column, Spark 3.5.3's values over t)
VALUES = [
    ("split(s, '[.]', 2)", "split(s, [.], 2)", [["a", "b.c"], ["a", ".b."], None]),
    ("split(s, '[.]', 1)", "split(s, [.], 1)", [["a.b.c"], ["a..b."], None]),
    ("split(s, '[.]', 0)", "split(s, [.], 0)", [["a", "b", "c"], ["a", "", "b", ""], None]),
    ("split(s, '[.]', -2)", "split(s, [.], -2)", [["a", "b", "c"], ["a", "", "b", ""], None]),
    ("split(s, '[.]', 10)", "split(s, [.], 10)", [["a", "b", "c"], ["a", "", "b", ""], None]),
    ("split(s, '', 2)", "split(s, , 2)", [["a", "."], ["a", "."], None]),
    ("split(s, '', -1)", "split(s, , -1)", [["a", ".", "b", ".", "c"], ["a", ".", ".", "b", "."], None]),
    ("split(i, '4', 2)", "split(i, 4, 2)", [["1", ""], None, ["3"]]),
    ("replace(s, '.')", "replace(s, ., )", ["abc", "ab", None]),
    ("replace(t, 'x')", "replace(t, x, )", ["hi", " pad ", None]),
    ("replace(i, '4')", "replace(i, 4, )", ["1", None, "3"]),
    ("btrim(t)", "btrim(t)", ["xxhixx", "pad", None]),
    ("btrim(t, 'x')", "btrim(t, x)", ["hi", " pad ", None]),
    ("btrim(t, 'xh ')", "btrim(t, xh )", ["i", "pad", None]),
    ("base64(s)", "base64(s)", ["YS5iLmM=", "YS4uYi4=", None]),
    ("base64(t)", "base64(t)", ["eHhoaXh4", "IHBhZCA=", None]),
    ("base64(cast(s as binary))", "base64(CAST(s AS BINARY))", ["YS5iLmM=", "YS4uYi4=", None]),
]


@pytest.mark.parametrize("query, name, values", VALUES, ids=[q for q, _, _ in VALUES])
def test_text_functions_answer_as_spark(query, name, values):
    text = run(f"r = spark.sql({f'SELECT {query} FROM t'!r})\nprint(r.columns)\nprint([row[0] for row in r.collect()])")
    assert text.splitlines() == [repr([name]), repr(values)]


def test_base64_of_a_number_is_refused_as_in_spark():
    out = Kernel().run("spark.sql('SELECT base64(14)').collect()")
    assert out["status"] == "error"
    assert "base64() takes text or binary" in "".join(str(o.get("evalue", "")) for o in out["outputs"])


def test_a_split_limit_from_the_data_is_refused_by_name():
    with pytest.raises(ValueError, match="split\\(\\) can't run on this PC with a limit worked out from the data"):
        sql.parse("SELECT split(s, ',', n) FROM t")
