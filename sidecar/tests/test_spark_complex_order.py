"""Spark's comparisons and ordering of arrays, structs and maps on this PC, value-checked against Spark 3.5.3
(see spark_values.py) (WR-271).

``=``, ``<=>``, ``<``, ``BETWEEN``, ``IN``, ``ORDER BY``, ``GROUP BY``, ``DISTINCT``, ``min``/``max``,
``greatest``/``least``, ``array_sort``, ``sort_array``, ``array_max``/``array_min`` and ``array_position``
over arrays, structs and maps: element types that differ, NULL elements and fields, nested arrays, NaN and
-0.0. The queries are in tools/spark_value_cases/complex_order.txt, each answered by Spark 3.5.3 in
tests/fixtures/spark_3_5_3_complex_order.json. Every case answers as Spark does here, or stops where Spark
stops; what still differs is listed in docs/sql-compatibility.md."""

from __future__ import annotations

import duckdb
import pytest
from spark_values import check, load
from sqlglot.schema import MappingSchema

from fabric_desktop import spark_ordering, sql
from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_comparisons_and_order_of_complex_values_answer_as_spark_3_5(spark):
    check(spark, load("complex_order"), [])


def _one(spark, query: str) -> object:
    return spark.sql(query).collect()[0][0]


def test_the_known_case_stops_by_name(spark):
    # array(1, 1.5) is an array<decimal(11,1)>, array(1.0, 1.5) an array<decimal(2,1)>: Spark has no one type
    with pytest.raises(ValueError, match=r"Spark stops on = between a array<decimal\(11,1\)> and a "
                                         r"array<decimal\(2,1\)>.*nothing ran. Cast one side"):
        _one(spark, "SELECT array(1, 1.5) = array(1.0, 1.5)")


@pytest.mark.parametrize("query, says", [
    ("SELECT map(1, 2) = map(1, 2)", r"Spark stops on `=` with a map<int, int>: it can't compare a map"),
    ("SELECT m FROM VALUES (map(1, 2)) AS t(m) ORDER BY m", r"Spark stops on ORDER BY .*can't order a map"),
    ("SELECT m FROM VALUES (map(1, 2)) AS t(m) GROUP BY m", r"Spark stops on GROUP BY .*can't group by a map"),
    ("SELECT DISTINCT m FROM VALUES (map(1, 2)) AS t(m)", r"Spark can't deduplicate a map.*the column m"),
    ("SELECT array_max(array(map(1, 2)))", r"Spark stops on array_max with a array<map<int, int>>"),
])
def test_a_map_is_refused_in_plain_english(spark, query: str, says: str):
    with pytest.raises(ValueError, match=says + r".*nothing ran.*map_entries"):
        _one(spark, query)


def test_a_table_column_is_typed_from_the_table():
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT [1, NULL] AS a, {'x': -0.0::DOUBLE} AS s, MAP([1], [2]) AS m")
    statement = sql.parse("SELECT a FROM t WHERE a < array(1, 0) ORDER BY s")[0]  # NULL is before 0
    out = sql.duckdb_sql(spark_ordering.spark_orderings(statement, con))
    assert con.execute(out).fetchall() == [([1, None],)]
    with pytest.raises(ValueError, match="no one type to compare in"):
        spark_ordering.spark_orderings(sql.parse("SELECT a = array(1.5) FROM t")[0], con)
    with pytest.raises(ValueError, match="can't group by a map"):
        spark_ordering.spark_orderings(sql.parse("SELECT m FROM t GROUP BY m")[0], con)


def test_a_comparison_whose_types_are_not_known_runs_as_it_did():
    # A function's result isn't typed here: nothing is refused for it (docs/sql-compatibility.md lists it).
    statement = sql.parse("SELECT array_distinct(a) = array(1.5) FROM t")[0]
    out = spark_ordering.spark_orderings(statement, None, MappingSchema({"t": {"a": "INTEGER[]"}}, dialect="duckdb"))
    assert "kf_o" not in sql.duckdb_sql(out)


def test_a_kf_name_in_a_rewritten_comparison_is_refused():
    with pytest.raises(ValueError, match="names that start with kf_ are reserved"):
        statement = sql.parse("SELECT kf_x < array(1) FROM VALUES (array(1)) AS t(kf_x)")[0]
        spark_ordering.spark_orderings(statement, duckdb.connect())


def test_a_group_key_used_another_way_keeps_duckdbs_value():
    # x is grouped and also read inside a subquery: the normalized key couldn't be matched there, so it is left.
    statement = sql.parse("SELECT x, (SELECT x) FROM VALUES (-0.0D) AS t(x) GROUP BY x")[0]
    out = sql.duckdb_sql(spark_ordering.spark_orderings(statement, duckdb.connect()))
    assert "CAST(0 AS DOUBLE)" not in out


_SCHEMA = MappingSchema({"t": {"s": "STRUCT(x INTEGER[], y STRUCT(z DOUBLE))"}}, dialect="duckdb")


@pytest.mark.parametrize("call", ["greatest({0}, s)", "least(s, {0})", "sort_array(array({0}))[0]",
                                  "array_sort(array({0}, s))[0]", "array_max(array({0}, s))",
                                  "CASE WHEN {0} < s THEN s END", "named_struct('y', {0}) >= named_struct('y', s)"])
def test_nested_rewrites_grow_the_sql_linearly(call: str):
    sizes = []
    for depth in range(1, 6):
        expression = "s"
        for _ in range(depth):
            expression = call.format(expression)
        statement = sql.parse(f"SELECT {expression} AS v FROM t")[0]
        sizes.append(len(sql.duckdb_sql(spark_ordering.spark_orderings(statement, None, _SCHEMA))))
    steps = [b - a for a, b in zip(sizes, sizes[1:], strict=False)]
    assert max(steps) <= 1.2 * min(steps), sizes
    assert sizes[0] > 400, sizes  # the key was written


def test_a_deep_struct_type_grows_the_key_linearly():
    sizes = []
    for depth in range(1, 6):
        kind = "INTEGER"
        for _ in range(depth):
            kind = f"STRUCT(f {kind}, g INTEGER[])"
        schema = MappingSchema({"t": {"s": kind}}, dialect="duckdb")
        statement = sql.parse("SELECT max(s) FROM t")[0]
        sizes.append(len(sql.duckdb_sql(spark_ordering.spark_orderings(statement, None, schema))))
    steps = [b - a for a, b in zip(sizes, sizes[1:], strict=False)]
    assert max(steps) <= 1.2 * min(steps), sizes

