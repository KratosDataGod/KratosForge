"""Mutation-testing kill tests for spark_complex (batch 86): the typed map forms run straight on a DuckDB
connection, so each case reads the rewrite the module writes and the rows DuckDB gives for it."""
from __future__ import annotations

import duckdb
import pytest
import sqlglot

from fabric_desktop import spark_complex, sql


@pytest.fixture
def conn():
    con = duckdb.connect()
    con.execute("CREATE TABLE c AS SELECT * FROM (VALUES "
                "(1, MAP {'a': 1, 'b': 2}, [1, 2, NULL]), "
                "(2, MAP {}::MAP(VARCHAR, INT), []::INT[]), "
                "(3, NULL::MAP(VARCHAR, INT), NULL::INT[])) t(id, m, arr)")
    yield con
    con.close()


def typed(conn, query: str) -> str:
    """The DuckDB SQL spark.sql runs for ``query`` once its tables are bound (spark_local)."""
    return sql.duckdb_sql(spark_complex.spark_complex_types(sql.parse(query)[0], conn))


def rows(conn, query: str) -> list[tuple]:
    return conn.sql(typed(conn, query)).fetchall()


def test_a_map_size_whose_argument_holds_rand_counts_the_maps_keys(conn):
    # The argument is read once (through _once's one-element list), and the typed pass finds it there.
    text = typed(conn, "SELECT id, size(CASE WHEN rand() < 2 THEN m END) FROM c ORDER BY id")
    assert text.count("RANDOM()") == 1 and "CARDINALITY(" in text and "ARRAY_LENGTH(" not in text
    assert conn.sql(text).fetchall() == [(1, 2), (2, 0), (3, -1)]


def test_lateral_view_outer_of_a_map_holding_rand_keeps_empty_and_null_rows(conn):
    query = ("SELECT id, k, v FROM c LATERAL VIEW OUTER explode(CASE WHEN rand() < 2 THEN m END) t AS k, v "
             "ORDER BY id, k")
    assert typed(conn, query).count("RANDOM()") == 1
    assert rows(conn, query) == [(1, "a", 1), (1, "b", 2), (2, None, None), (3, None, None)]


def test_lateral_view_of_a_map_with_no_table_name_gets_one(conn):
    query = "SELECT id, k, v FROM c LATERAL VIEW explode(m) AS k, v ORDER BY id, k"
    assert '"kf_lateral"' in typed(conn, query)
    assert rows(conn, query) == [(1, "a", 1), (1, "b", 2)]


def test_lateral_view_explode_outer_of_a_map_never_drops_rows(conn):
    # Not given the map form here: DuckDB stops on it rather than leave out ids 2 and 3, as an inner
    # explode of the map would.
    query = "SELECT id, k, v FROM c LATERAL VIEW explode_outer(m) t AS k, v ORDER BY id, k"
    try:
        got = rows(conn, query)
    except duckdb.Error:
        return
    assert len(got) == 4, got


def test_reverse_of_a_number_is_left_to_duckdb(conn):
    # Only an array's reverse is rewritten; a number's type is not a sequence's either.
    assert "REVERSE(123)" in typed(conn, "SELECT reverse(123)")


def test_reverse_of_a_map_is_left_as_written(conn):
    text = typed(conn, "SELECT id, reverse(m) FROM c")
    assert "REVERSE(m)" in text and "LATERAL" not in text and "MAP_KEYS" not in text
    with pytest.raises(duckdb.Error):
        conn.sql(text).fetchall()  # Spark refuses reverse of a map too


def test_only_the_calls_mark_calls_marked_are_rewritten():
    unmarked = sqlglot.parse_one("SELECT array_contains(array(1, NULL), 2), size(array(1)), flatten(array(array(1))), "
                                 "array_position(array(1), 1)", read="spark")
    assert spark_complex.spark_complex_results(unmarked).sql("duckdb") == \
        "SELECT ARRAY_CONTAINS([1, NULL], 2), ARRAY_LENGTH([1]), FLATTEN([[1]]), ARRAY_POSITION([1], 1)"


def test_a_two_argument_function_other_than_map_concat_keeps_its_call():
    # A registered UDF, say, reaches the rewrite as an anonymous call of its own.
    statement = spark_complex.mark_calls(sqlglot.parse_one("SELECT my_udf(1, 2)", read="spark"))
    assert spark_complex.spark_complex_results(statement).sql("duckdb") == "SELECT MY_UDF(1, 2)"


def test_map_concat_of_two_maps_still_gets_its_checks():
    statement = spark_complex.mark_calls(sqlglot.parse_one("SELECT map_concat(map('a', 1), map('b', 2))", read="spark"))
    text = spark_complex.spark_complex_results(statement).sql("duckdb")
    assert "DUPLICATED_MAP_KEY" in text and text.startswith("SELECT CASE WHEN")


def test_a_statement_with_nothing_marked_is_returned_as_it_is(conn):
    statement = sql.parse("SELECT 1 AS x")[0]
    assert spark_complex.spark_complex_types(statement, conn) is statement


def test_typed_forms_on_a_bound_table(conn):
    assert rows(conn, "SELECT id, size(m), cardinality(m) FROM c ORDER BY id") == [(1, 2, 2), (2, 0, 0), (3, -1, -1)]
    assert rows(conn, "SELECT id, reverse(arr) FROM c ORDER BY id") == [(1, [None, 2, 1]), (2, []), (3, None)]
    assert rows(conn, "SELECT sequence(5, 1), sequence(1, 3)") == [([5, 4, 3, 2, 1], [1, 2, 3])]
    assert rows(conn, "SELECT id, explode(m) FROM c ORDER BY id, key") == [(1, "a", 1), (1, "b", 2)]


def test_lateral_view_of_a_generator_other_than_explode_is_left_to_its_own_rewrite(conn):
    # json_tuple and stack are written by their own rewrite, not explode's map forms: one row per table row
    # for json_tuple, two for stack(2, ...).
    assert len(rows(conn, "SELECT id, a FROM c LATERAL VIEW json_tuple('{\"a\": 1}', 'a') t AS a")) == 3
    assert len(rows(conn, "SELECT id, x FROM c LATERAL VIEW stack(2, 1, 2) t AS x")) == 6


def test_lateral_view_stack_and_json_tuple_give_the_values_themselves(conn):
    assert rows(conn, "SELECT id, x FROM c LATERAL VIEW stack(2, 1, 2) t AS x WHERE id = 1 ORDER BY x") == [(1, 1), (1, 2)]
    assert rows(conn, "SELECT id, a FROM c LATERAL VIEW json_tuple('{\"a\": 1}', 'a') t AS a WHERE id = 1") == [(1, "1")]
