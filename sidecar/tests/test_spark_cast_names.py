"""Spark SQL names an unnamed cast as Spark does, not after the DuckDB SQL it becomes. Casts to whole
numbers and timestamps are written out (EA-003, EA-014), which named the column after a ~1 KB CASE."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel


def run(code):
    out = Kernel().run("spark.createDataFrame([(1, 2.5, '3', 100)], 'i int, d double, s string, kf_cast int')"
                       ".createOrReplaceTempView('t')\n" + code)
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


def test_unnamed_casts_get_sparks_names():
    status, text = run("print(spark.sql('SELECT CAST(d AS INT), CAST(s AS BIGINT), try_cast(s AS int), "
                       "CAST(i AS TIMESTAMP), CAST(s AS DATE), CAST(d AS STRING), CAST(d AS INT) AS named FROM t')"
                       ".columns)")
    assert status == "ok", text
    assert ("['CAST(d AS INT)', 'CAST(s AS BIGINT)', 'TRY_CAST(s AS INT)', 'CAST(i AS TIMESTAMP)', "
            "'CAST(s AS DATE)', 'CAST(d AS STRING)', 'named']") in text


def test_a_cast_column_can_be_read_by_its_name():
    status, text = run("r = spark.sql('SELECT CAST(d AS INT) FROM t')\n"
                       "print(r.select(r['CAST(d AS INT)']).collect())")
    assert status == "ok", text
    assert "=2)]" in text


@pytest.mark.parametrize("query", ["SELECT CAST(d AS INT) + kf_cast AS v FROM t",
                                   "SELECT CAST(CAST(d AS DOUBLE) * 3 AS INT) + kf_cast AS v FROM t"])
def test_a_column_named_like_the_cast_lambda_doesnt_change_the_answer(query):
    # DuckDB takes the lambda's own parameter over a column of the same name inside the lambda.
    status, text = run(f"print(spark.sql({query!r}).collect())")
    assert status == "ok", text
    assert ("v=102" in text) if "* 3" not in query else ("v=107" in text)


@pytest.mark.parametrize("expr, name", [
    ("CAST(d AS DECIMAL(10, 2))", "CAST(d AS DECIMAL(10,2))"),
    ("CAST(d AS INT) + 1", "(CAST(d AS INT) + 1)"),
    ("CAST(i + 1 AS BIGINT)", "CAST((i + 1) AS BIGINT)"),
    ("CAST(i AS INT) + i * 2", "(CAST(i AS INT) + (i * 2))"),
    ("NOT CAST(i AS INT) = 1", "(NOT (CAST(i AS INT) = 1))"),
    ("CAST(m AS MAP<STRING, DECIMAL(10, 2)>)", "CAST(m AS MAP<STRING, DECIMAL(10,2)>)"),
])
def test_names_are_formatted_as_spark_writes_them(expr, name):
    import sqlglot

    from fabric_desktop.sql import spark_name
    assert spark_name(sqlglot.parse_one(expr, read="spark")) == name
