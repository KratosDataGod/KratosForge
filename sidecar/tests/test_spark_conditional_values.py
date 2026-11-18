"""Spark 3.5's type coercion in CASE, if, nvl2 and coalesce (nvl) on this PC, value-checked against Spark 3.5.3
(see spark_values.py).

With ANSI off, as Fabric runs Spark SQL, a string among numbers or dates makes the result a string, each other
value cast to STRING: CASE WHEN c THEN 1 ELSE '2x' END is '1' or '2x', where DuckDB cast '2x' to an INTEGER and
failed. A boolean among other types, and greatest or least of a number and a string, stop, as in Spark. The
queries in tools/spark_value_cases/conditional.txt, each answered by Spark 3.5.3 in
tests/fixtures/spark_3_5_3_conditional.json. What still differs is listed in docs/sql-compatibility.md."""

from __future__ import annotations

import pytest
from spark_values import Refused, check, load

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel

REFUSED: list[Refused] = []


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_conditionals_answer_as_spark_3_5_or_are_refused_by_name(spark):
    check(spark, load("conditional"), REFUSED)


def test_a_column_beside_a_string_gives_a_string_as_a_literal_does(spark):
    spark.sql("CREATE OR REPLACE TEMP VIEW kf_cond AS SELECT 1 AS n, CAST(2.5 AS DOUBLE) AS d, DATE'2024-01-01' AS t, "
              "'s' AS s")
    rows = spark.sql("SELECT CASE WHEN n = 1 THEN n ELSE '2x' END, coalesce(NULL, d, 'a'), if(false, n, 'x'), "
                     "nvl2(NULL, t, 'y'), nvl(t, 'z'), typeof(coalesce(n, 'a')), coalesce(s, 'b') FROM kf_cond").collect()
    assert [list(r) for r in rows] == [["1", "2.5", "x", "y", "2024-01-01", "string", "s"]]


@pytest.mark.parametrize("query", [
    "SELECT CASE WHEN true THEN 1 ELSE 2 END",
    "SELECT CASE WHEN x > 0 THEN 'a' ELSE 'b' END FROM t",
    "SELECT coalesce(a, b), nvl(a, 0), if(c, 1.5, 2) FROM t",
    "SELECT CASE WHEN true THEN true ELSE NULL END, greatest(1, 2.5D), least(a, b) FROM t",
])
def test_a_conditional_of_one_type_is_written_as_before(query):
    statement = sql.parse(query)[0]
    for node in statement.walk():
        node.meta.pop("kf_conditional", None)  # a mark only: what spark_complex_types types once bound
    assert sql.duckdb_sql(statement) == sql.duckdb_sql(sqlglot_unchanged(query))


def sqlglot_unchanged(query: str):
    """``query`` as spark_fixes writes it without the conditional rewrite."""
    from unittest import mock
    with mock.patch.object(sql, "_spark_conditional_text", lambda statement, databricks: statement):
        return sql.parse(query)[0]


def test_databricks_sql_keeps_duckdb_s_types():
    import sqlglot
    statement = sql.spark_fixes(sqlglot.parse_one("SELECT CASE WHEN true THEN 1 ELSE '2' END", read="databricks"),
                                databricks=True)
    assert "'2'" in sql.duckdb_sql(statement) and "TYPEOF" not in sql.duckdb_sql(statement).upper()


def test_sql_cells_type_a_column_beside_a_string_as_spark_sql_does():
    table = sql.run("SELECT CASE WHEN n = 1 THEN n ELSE '2x' END AS v FROM (SELECT 1 AS n)")
    assert table.column("v").to_pylist() == ["1"]


BOOLEAN = "can't mix a boolean with another type, so nothing ran. Spark stops on it too. Cast the values to one type first."
COMPARE = ("can't compare a number with a string, so nothing ran. Spark stops on it too. Cast the values to one type "
           "first, for example CAST('2' AS INT).")


@pytest.mark.parametrize(("query", "says"), [
    ("SELECT greatest(1, '2')", "greatest() " + COMPARE),
    ("SELECT least('a', 1.5D, NULL)", "least() " + COMPARE),
    ("SELECT coalesce(true, 1)", "coalesce() " + BOOLEAN),
    ("SELECT nvl(true, 'a')", "nvl() " + BOOLEAN),
    ("SELECT ifnull(NULL, true, 1)", "ifnull() " + BOOLEAN),
    ("SELECT nvl2(1, true, 'x')", "nvl2() " + BOOLEAN),
    ("SELECT if(true, true, 1)", "if() " + BOOLEAN),
    ("SELECT CASE WHEN true THEN true ELSE 'false' END", "CASE " + BOOLEAN),
    ("SELECT n FROM (SELECT 1 AS n) WHERE coalesce(false, 0) IS NULL", "coalesce() " + BOOLEAN),
])
def test_where_spark_stops_on_mixed_types_this_pc_stops_with_what_to_change(spark, query, says):
    with pytest.raises(Exception) as stop:
        spark.sql(query).collect()
    assert str(stop.value) == says


@pytest.mark.parametrize(("query", "says"), [
    ("SELECT coalesce(b, 1) FROM kf_mixed", "coalesce() " + BOOLEAN),
    ("SELECT if(n = 1, b, s) FROM kf_mixed", "if() " + BOOLEAN),
    ("SELECT greatest(n, s) FROM kf_mixed", "greatest() " + COMPARE),
    ("SELECT least(d, 'x') FROM kf_mixed WHERE false", "least() " + COMPARE),
])
def test_columns_of_mixed_types_stop_once_their_types_are_known(spark, query, says):
    spark.sql("CREATE OR REPLACE TEMP VIEW kf_mixed AS SELECT 1 AS n, CAST(2.5 AS DOUBLE) AS d, true AS b, 's' AS s")
    with pytest.raises(Exception) as stop:
        spark.sql(query).collect()
    assert str(stop.value) == says


def test_a_boolean_beside_null_or_booleans_and_numbers_compared_still_run(spark):
    rows = spark.sql("SELECT coalesce(NULL, true), CASE WHEN false THEN true ELSE NULL END, greatest(1, 2.5D), "
                     "least(CAST(NULL AS INT), 3)").collect()
    assert [list(r) for r in rows] == [[True, None, 2.5, 3]]


def test_sql_cells_stop_on_a_boolean_beside_a_number():
    with pytest.raises(ValueError, match=r"^coalesce\(\) can't mix a boolean"):
        sql.run("SELECT coalesce(b, 1) FROM (SELECT true AS b)")
