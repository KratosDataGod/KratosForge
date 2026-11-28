"""spark_text_compare's rewrite, read directly (mutation testing, batch 325).

test_spark_text_comparisons.py checks Spark's answers end to end; these pin the rewrite itself: which
comparisons are rewritten and which are left alone, the casts each side gets, the IN, BETWEEN and CASE
forms, the growth limit and the DataFrame API's column list. Answers run on DuckDB here are the values the
module's docstring and test_spark_text_comparisons.py give for Spark.
"""

from __future__ import annotations

import duckdb
import pytest
import sqlglot
from sqlglot.schema import MappingSchema

from fabric_desktop.spark_decimals import Refused
from fabric_desktop.spark_text_compare import spark_text_comparison_columns, spark_text_comparisons
from fabric_desktop.sql import duckdb_sql

COLUMNS = {"s": "VARCHAR", "n": "INTEGER", "d": "DECIMAL(10,2)", "f": "DOUBLE"}
SCHEMA = MappingSchema({"t": COLUMNS}, dialect="duckdb", normalize=False)
WHOLE = ("(CASE WHEN REGEXP_MATCHES(CAST({v} AS TEXT), '^\\s*[+-]?([0-9]+\\.?[0-9]*|\\.[0-9]*)\\s*$') THEN "
         "TRY_CAST(REGEXP_REPLACE(REGEXP_EXTRACT(CAST({v} AS TEXT), '^\\s*([+-]?[0-9]*)', 1), '^[+-]?$', '0') "
         "AS {t}) END)")


def whole(value: str, kind: str = "INT") -> str:
    return WHOLE.format(v=value, t=kind)


def rewrite(query: str, schema: MappingSchema = SCHEMA) -> str:
    return duckdb_sql(spark_text_comparisons(sqlglot.parse_one(query, read="duckdb"), None, schema))


def unchanged(query: str) -> str:
    return duckdb_sql(sqlglot.parse_one(query, read="duckdb"))


@pytest.fixture(scope="module")
def conn():
    con = duckdb.connect()
    con.execute("CREATE TABLE t (s VARCHAR, n INTEGER, d DECIMAL(10,2), f DOUBLE)")
    con.execute("INSERT INTO t VALUES ('1.5', 1, 1.55, 1e10), ('3000000000', 2, 1.50, 1.0)")
    yield con
    con.close()


def answer(conn, query: str) -> list:
    return conn.execute(rewrite(query)).fetchall()


def test_numbers_compared_with_numbers_are_left_alone():
    for query in ["SELECT n = 1 FROM t", "SELECT 1 = n FROM t", "SELECT n BETWEEN 1 AND 3 FROM t",
                  "SELECT d BETWEEN 1 AND 2 FROM t", "SELECT CASE n WHEN 1 THEN 'a' END FROM t",
                  "SELECT n IN (1, 2) FROM t"]:
        assert rewrite(query) == unchanged(query), query


def test_text_compared_with_something_not_a_number_is_left_alone():
    for query in ["SELECT s IN (1, DATE '2024-01-01') FROM t", "SELECT s IN (DATE '2024-01-01', '1') FROM t",
                  "SELECT CASE s WHEN 1 THEN 'a' WHEN 1.5 THEN 'b' END FROM t",
                  "SELECT CASE s WHEN 1 THEN 'a' WHEN 'x' THEN 'b' END FROM t"]:
        assert rewrite(query) == unchanged(query), query


def test_a_decimal_is_compared_with_text_as_a_double_and_a_whole_number_as_it_is():
    assert rewrite("SELECT s = d FROM t").endswith(" = CAST(d AS DOUBLE) FROM t")
    assert rewrite("SELECT s = n FROM t") == f"SELECT {whole('s')} = n FROM t"
    assert rewrite("SELECT n = s FROM t") == f"SELECT n = {whole('s')} FROM t"
    assert rewrite("SELECT s = 1.5 FROM t").endswith(" = CAST(1.5 AS DOUBLE) FROM t")


def test_a_whole_literal_is_an_int_until_it_is_past_one():
    assert rewrite("SELECT s = 1 FROM t") == f"SELECT {whole('s')} = 1 FROM t"
    assert rewrite("SELECT s = 2147483647 FROM t") == f"SELECT {whole('s')} = 2147483647 FROM t"
    assert rewrite("SELECT s = 2147483648 FROM t") == f"SELECT {whole('s', 'BIGINT')} = 2147483648 FROM t"
    assert rewrite("SELECT s = -2147483648 FROM t") == f"SELECT {whole('s', 'BIGINT')} = -2147483648 FROM t"
    assert rewrite("SELECT s = 3000000000 FROM t") == f"SELECT {whole('s', 'BIGINT')} = 3000000000 FROM t"


def test_text_past_an_int_still_equals_a_bigint_literal(conn):
    assert answer(conn, "SELECT s = 3000000000 FROM t ORDER BY n") == [(False,), (True,)]
    assert answer(conn, "SELECT s = 1 FROM t ORDER BY n") == [(True,), (None,)]


def test_in_compares_a_double_as_spark_writes_it_and_a_decimal_as_written(conn):
    # Spark writes 1e10 as 1.0E10 and keeps a decimal's scale: 1.50 is '1.50'.
    assert answer(conn, "SELECT f IN ('1.0E10') FROM t ORDER BY n") == [(True,), (False,)]
    assert answer(conn, "SELECT '1.50' IN (1.50) FROM t ORDER BY n") == [(True,), (True,)]
    assert rewrite("SELECT n IN ('1') FROM t") == "SELECT CAST(n AS TEXT) IN ('1') FROM t"


def test_between_with_ends_of_two_types_casts_the_text_to_each(conn):
    sql = rewrite("SELECT s BETWEEN 1 AND 2.5 FROM t")
    assert "LIST_TRANSFORM([s], LAMBDA kf_text_cmpx" in sql
    assert ">= 1 AND" in sql and "<= CAST(2.5 AS DOUBLE)" in sql
    assert answer(conn, "SELECT s BETWEEN 1 AND 2.5 FROM t ORDER BY n") == [(True,), (False,)]


def test_between_a_number_and_text_ends_casts_each_text_end():
    assert rewrite("SELECT n BETWEEN s AND 3 FROM t") == f"SELECT n BETWEEN {whole('s')} AND 3 FROM t"
    assert rewrite("SELECT n BETWEEN 0 AND s FROM t") == f"SELECT n BETWEEN 0 AND {whole('s')} FROM t"
    assert rewrite("SELECT d BETWEEN s AND 3 FROM t").startswith("SELECT CAST(d AS DOUBLE) BETWEEN (CASE")


def test_a_simple_case_keeps_its_else(conn):
    sql = rewrite("SELECT CASE s WHEN 1 THEN 'a' ELSE 'z' END FROM t")
    assert sql == f"SELECT CASE {whole('s')} WHEN 1 THEN 'a' ELSE 'z' END FROM t"
    assert rewrite("SELECT CASE s WHEN 1 THEN 'a' END FROM t") == f"SELECT CASE {whole('s')} WHEN 1 THEN 'a' END FROM t"
    assert answer(conn, "SELECT CASE s WHEN 1 THEN 'a' ELSE 'z' END FROM t ORDER BY n") == [("a",), ("z",)]


def test_a_statement_without_comparisons_or_that_cant_be_typed_is_returned_as_it_was():
    statement = sqlglot.parse_one("SELECT s FROM t", read="duckdb")
    assert spark_text_comparisons(statement, None, SCHEMA) is statement
    statement = sqlglot.parse_one("SELECT '1' = 1", read="duckdb")
    assert spark_text_comparisons(statement, None) is statement  # no connection to type it against


def test_a_statement_with_a_column_not_in_the_schema_is_still_rewritten():
    assert rewrite("SELECT s = 1, zz FROM t") == f"SELECT {whole('s')} = 1, zz FROM t"


def test_a_side_sqlglot_cant_type_leaves_only_its_own_comparison_alone():
    # WHERE x = 1 reads the SELECT's alias, which qualify doesn't type: that comparison stays, s = 1 is rewritten.
    assert (rewrite("SELECT s = 1 AS y, s AS x FROM t WHERE x = 1")
            == f"SELECT {whole('s')} = 1 AS y, s AS x FROM t WHERE x = 1")


def test_a_name_like_the_lambdas_anywhere_leaves_the_statement_as_it_was():
    query = "SELECT s = 1 AS kf_text_cmp FROM t"
    assert rewrite(query) == unchanged(query)
    other = MappingSchema({"t": COLUMNS, "u": {"kf_text_cmp": "INTEGER"}}, dialect="duckdb", normalize=False)
    assert rewrite("SELECT s = 1 FROM t", other) == unchanged("SELECT s = 1 FROM t")


def test_a_statement_without_text_isnt_typed_against_the_connection():
    con = duckdb.connect()
    con.execute("CREATE TABLE w (n INTEGER)")
    seen: list[str] = []

    class Spy:
        def execute(self, sql, *args):
            seen.append(sql)
            return con.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(con, name)

    statement = sqlglot.parse_one("SELECT n = 1 FROM w", read="duckdb")
    assert spark_text_comparisons(statement, Spy()) is statement
    assert len(seen) == 1 and "duckdb_columns()" in seen[0]
    seen.clear()
    statement = sqlglot.parse_one("SELECT 1 = 2", read="duckdb")
    assert spark_text_comparisons(statement, Spy()) is statement
    assert seen == []
    con.close()


def _growth(length: int) -> int:
    """How far the rewrite of ``<name> = 1.5`` (a name of ``length`` characters, read three times) is past
    twice the statement."""
    name = "a" * length
    schema = MappingSchema({"t": {name: "VARCHAR"}}, dialect="duckdb", normalize=False)
    query = f"SELECT {name} = 1.5 FROM t"
    return len(rewrite(query, schema)) - 2 * len(unchanged(query))


def test_a_rewrite_past_its_growth_limit_is_refused_by_name():
    assert _growth(11) - _growth(10) == 1
    edge = 10 + 20_000 - _growth(10)  # grows by exactly 20,000 characters: still run
    assert _growth(edge) == 20_000
    name = "a" * (edge + 1)
    schema = MappingSchema({"t": {name: "VARCHAR"}}, dialect="duckdb", normalize=False)
    with pytest.raises(Refused, match="1 comparisons of text with numbers"):
        rewrite(f"SELECT {name} = 1.5 FROM t", schema)
    longer = name + "a" * 50  # the frame's statement is a little longer
    with pytest.raises(Refused):
        spark_text_comparison_columns([f"{longer} = 1.5"], [(longer, "VARCHAR")])


FRAME = [("s", "VARCHAR"), ("n", "INTEGER")]


def test_the_dataframe_api_gets_each_changed_column_with_its_name():
    out = spark_text_comparison_columns(["s = 1 AS x", "n + 1", "s IN (1, 2)"], FRAME)
    assert out == [(f"{whole('s')} = 1", "x"), None, ("s IN (CAST(1 AS TEXT), CAST(2 AS TEXT))", None)]


def test_the_dataframe_api_leaves_columns_it_cant_read_as_they_were():
    assert spark_text_comparison_columns(["n + 1"], FRAME) == [None]
    assert spark_text_comparison_columns(["s = ("], FRAME) == [None]
    assert spark_text_comparison_columns(["s = 1, n + 2"], FRAME) == [None]


def test_the_dataframe_api_rewrites_a_null_safe_comparison():
    out = spark_text_comparison_columns(["(s IS NOT DISTINCT FROM 1)"], FRAME)
    assert out[0] is not None
