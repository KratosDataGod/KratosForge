"""Tests for the SQL translation (sql.py) that mutation testing showed were missing.

Each test pins one behaviour a changed line in sql.py got past: reading Spark SQL and falling back to
DuckDB SQL, counting a call's arguments, the empty struct<> message, the session function message,
array sorts, whole-number casts, schema_of_json, from_json and str_to_map, timestampdiff's units,
substring's start, Spark's column names, time travel points, and DuckDB 2.0's lambda spelling."""

from __future__ import annotations

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from fabric_desktop import sql


@pytest.fixture(scope="module")
def con():
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")   # as translated cells run (engines/local_sql)
    yield connection
    connection.close()


def _run(con, query: str) -> tuple[list, str]:
    """The rows and the first column's name of a Spark SQL query run on DuckDB."""
    statement = sql.parse(query)[0]
    rows = con.execute(sql.duckdb_sql(statement)).fetchall()
    return rows, con.description[0][0]


def _tokens(text: str) -> list:
    return sqlglot.tokenize(text, read="spark")


# ------------------------------------------------------------- parse

def test_duckdb_sql_spark_cant_read_is_read_as_duckdb():
    assert len(sql.parse("SELECT {'a': 1} AS s")) == 1


def test_a_key_value_call_with_an_odd_number_of_arguments_is_never_read_as_duckdb():
    with pytest.raises(ValueError, match="MAP needs key and value pairs"):
        sql.parse("SELECT map('a') AS m")


def test_a_query_neither_dialect_reads_says_so():
    with pytest.raises(ValueError, match="Could not read the query"):
        sql.parse("SELECT FROM WHERE (")


def test_a_lone_x_before_hex_is_a_binary_literal(con):
    rows, _ = _run(con, "SELECT X '0A' AS b")
    assert rows == [(b"\n",)]
    assert sql._spark_spellings("SELECT t.x '0A'") == "SELECT t.x '0A'"
    assert sql._spark_spellings("SELECT X '0A'") == "SELECT X'0A'"


def test_a_datediff_in_brackets_is_still_counted_as_three_arguments():
    assert sql.diff_spellings("SELECT (datediff(DAY, a, b))") == "SELECT (timestampdiff(DAY, a, b))"
    assert sql.diff_spellings("SELECT datediff(a, b)") == "SELECT datediff(a, b)"


@pytest.mark.parametrize("text, paren, count", [
    ("f(a, b, c)", 1, 3),
    ("f(a, [b, c], (d, e), {x})", 1, 4),
    ("f(a, b", 1, 2),             # an unclosed call counts what it has
    ("f(g(a, b), c)", 1, 2),
    ("f()", 1, 1),
])
def test_arguments_counts_commas_at_the_calls_own_depth(text, paren, count):
    assert sql._arguments(_tokens(text), paren) == count


def test_an_empty_struct_is_named_whichever_way_it_is_spelled():
    assert sql._only_empty_structs_fail("SELECT CAST(x AS struct< >)")
    assert sql._only_empty_structs_fail("SELECT x::struct< >")
    assert not sql._only_empty_structs_fail("SELECT CAST(x AS struct<a: int>)")
    for query in ("SELECT CAST(NULL AS struct< >)", "SELECT CAST(NULL AS struct<>)", "SELECT x::struct< >"):
        with pytest.raises(ValueError, match="empty struct"):
            sql.parse(query)


def test_an_empty_struct_sqlglot_reads_is_refused():
    statement = sqlglot.parse_one("SELECT CAST(NULL AS STRUCT<a INT>)", read="spark")
    statement.find(exp.DataType).set("expressions", [])
    with pytest.raises(ValueError, match="empty struct"):
        sql.spark_fixes(statement)


@pytest.mark.parametrize("name", ["current_database", "current_schema"])
def test_a_session_function_refusal_names_the_function(name):
    with pytest.raises(ValueError, match=rf"^{name}\(\) can't run"):
        sql.parse(f"SELECT {name}()")


# ------------------------------------------------------------- functions

def _first(query: str, kind):
    return sqlglot.parse_one(query, read="spark").find(kind)


def test_where_spark_puts_null_in_each_array_sort():
    assert sql._sort_nulls_first(_first("SELECT array_sort(a)", exp.ArraySort)) is False
    assert sql._sort_nulls_first(_first("SELECT array_sort(a, (l, r) -> 0)", exp.ArraySort)) is None
    sort_array = _first("SELECT sort_array(a)", sql._SORTS)
    assert sql._sort_nulls_first(sort_array) is True
    assert sql._sort_nulls_first(_first("SELECT sort_array(a, false)", sql._SORTS)) is False


def test_a_whole_number_in_brackets_is_a_whole_number():
    assert sql._whole_number(sqlglot.parse_one("(3)", read="spark")) is True
    assert sql._whole_number(sqlglot.parse_one("((CAST(a AS INT)))", read="spark")) is True
    assert sql._whole_number(sqlglot.parse_one("(3.5)", read="spark")) is False


def test_schema_of_json_merges_equal_types():
    assert sql._merge_json_types("BIGINT", "BIGINT") == "BIGINT"
    assert sql.schema_of_json("[1, 2]") == "ARRAY<BIGINT>"


def test_schema_of_json_refuses_a_number_just_past_bigint():
    assert sql.schema_of_json(f'{{"a": {2**63 - 1}}}') == "STRUCT<a: BIGINT>"
    with pytest.raises(ValueError, match="too big for BIGINT"):
        sql.schema_of_json(f'{{"a": {2**63}}}')


def test_a_from_json_that_is_the_whole_expression_is_replaced():
    node = sqlglot.parse_one("from_json('{\"a\": 1}', 'a INT')", read="spark")
    assert isinstance(node, exp.FromJson)
    replaced = sql._spark_json(False, node)
    assert isinstance(replaced, exp.Expression) and not isinstance(replaced, exp.FromJson)


def test_str_to_map_splits_pairs_and_keys(con):
    rows, _ = _run(con, "SELECT str_to_map('a:1,b:2') AS m")
    assert rows == [({"a": "1", "b": "2"},)]


@pytest.mark.parametrize("unit, start, end, expected", [
    ("MINUTE", "2024-01-01 00:00:00", "2024-01-01 02:00:00", 120),
    ("HOUR", "2024-01-01 00:00:00", "2024-01-01 05:30:00", 5),
    ("MONTH", "2021-02-28 12:00:00", "2021-03-28 11:59:59", 0),
    ("MONTH", "2021-01-15 00:00:00", "2021-03-15 00:00:00", 2),
    ("QUARTER", "2020-01-01 00:00:00", "2021-01-01 00:00:00", 4),
    ("YEAR", "2020-01-01 00:00:00", "2022-01-01 00:00:00", 2),
])
def test_timestampdiff_counts_whole_units_as_spark_does(con, unit, start, end, expected):
    rows, _ = _run(con, f"SELECT timestampdiff({unit}, TIMESTAMP'{start}', TIMESTAMP'{end}') AS d")
    assert rows == [(expected,)]


@pytest.mark.parametrize("start, expected", [(0, "he"), (1, "he"), (2, "el"), (-3, "ll")])
def test_substring_from_a_column_counts_0_as_1(con, start, expected):
    rows, _ = _run(con, f"SELECT substring('hello', s, 2) AS r FROM (SELECT {start} AS s)")
    assert rows == [(expected,)]
    rows, _ = _run(con, f"SELECT substring('hello', {start}, 2) AS r")
    assert rows == [(expected,)]


@pytest.mark.parametrize("query, name", [
    ("SELECT substring('hello', 2, 2)", "substring(hello, 2, 2)"),
    ("SELECT pmod(-7, 3)", "pmod(-7, 3)"),
    ("SELECT zip_with(array(1), array(2), (a, b) -> a + b)", None),
    ("SELECT add_months(DATE'2024-01-31', 1)", "add_months(DATE '2024-01-31', 1)"),
])
def test_a_rewritten_column_keeps_a_spark_name(con, query, name):
    _, column = _run(con, query)
    rewritten = sql.duckdb_sql(sql.parse(query)[0])
    assert " AS " in rewritten, rewritten
    if name is not None:
        assert column.replace("'", "") == name.replace("'", "")


@pytest.mark.parametrize("query", [
    "SELECT (SELECT pmod(-7, 3))",                                       # a template sqlglot has no node for
    "SELECT (SELECT substring('hello', 0, 2))",                          # a node this module rewrites
    "SELECT (SELECT zip_with(array(1), array(2), (a, b) -> a + b))",     # a lambda this module writes
])
def test_a_rewritten_scalar_subquery_is_named_as_written(con, query):
    """A scalar subquery is a column spark_sql_names can't name, so the older rule names it."""
    _, column = _run(con, query)
    assert column == query.removeprefix("SELECT ")


# ------------------------------------------------------------- lakehouse tables

def test_bind_tables_binds_every_table_unless_told_it_is_local(monkeypatch):
    class Opened:
        table_uri = "file:///sales"

        def version(self) -> int:
            return 3

        def to_pyarrow_dataset(self):
            import pyarrow as pa
            return pa.table({"a": [1]})

    class Lakehouse:
        @staticmethod
        def open_table(name, **_at):
            return Opened()

    monkeypatch.setattr(sql, "_lakehouse", lambda: Lakehouse)
    con = duckdb.connect()
    try:
        views: dict[str, str] = {}
        statement = sql.parse("SELECT * FROM sales")[0]
        sql.bind_tables(statement, con, views)
        assert list(views) == ["sales@file:///sales#3"]
        kept: dict[str, str] = {}
        sql.bind_tables(sql.parse("SELECT * FROM sales")[0], con, kept, is_local=lambda _name: True)
        assert kept == {}
    finally:
        con.close()


def test_an_older_version_of_the_same_table_is_superseded():
    views = {"sales@u#1": "v1", "other@u#1": "o1", "sales@u#2": "v2", "sales@v1": "t"}
    assert sql.superseded(views) == ["sales@u#1"]


def test_a_databricks_timestamp_point_reads_as_a_time():
    table = sqlglot.parse_one("SELECT * FROM sales@20261020130405123", read="databricks").find(exp.Table)
    assert sql._time_travel(table) == "t2026-10-20 13:04:05"


def test_point_args_splits_versions_from_times():
    assert sql.point_args("v3") == {"version": "3"}
    assert sql.point_args("t2026-10-20 13:00:00") == {"timestamp": "2026-10-20 13:00:00"}


# ------------------------------------------------------------- DuckDB 2.0 lambdas

def test_a_lambda_is_written_with_the_lambda_keyword(con):
    rows, _ = _run(con, "SELECT transform(array(1, 2), x -> x + 1) AS t")
    assert rows == [([2, 3],)]
    text = sql.duckdb_sql(sql.parse("SELECT transform(array(1, 2), x -> x + 1) AS t")[0])
    assert "LAMBDA x : x + 1" in text and "->" not in text
    lam = sqlglot.parse_one("SELECT list_transform(a, (x, y) -> x + y)", read="duckdb").find(exp.Lambda)
    assert sql.KfDuckDB().generator().lambda_sql(lam) == sql.KfDuckDB().generator().lambda_sql(lam, wrap=True)
