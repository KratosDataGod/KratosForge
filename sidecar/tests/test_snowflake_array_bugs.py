"""Arrays of more than one kind (SFX-085), a SQL NULL in one (SFN-084), and TO_JSON inside a select item named as
written (SFX-070).

Sources:
- functions/array_construct: "The argument values can be different data types" and "An array can contain both SQL
  NULL values and JSON null values". ARRAY_CONSTRUCT(NULL, PARSE_JSON('null'), 'hello', 3::DOUBLE, 4, 5) shows
  undefined, null, "hello", 3.000000000000000e+00, 4, 5: a SQL NULL element is undefined, apart from JSON null.
- functions/array_append: an array keeps each element's type (TYPEOF gives INTEGER beside VARCHAR).
- functions/to_json: TO_JSON(PARSE_JSON('{"b":1,"a":2}')) is {"a":2,"b":1}, and its column is named
  TO_JSON(PARSE_JSON('{"B":1,"A":2}')), the call as written. No page shows TO_JSON of an array holding a column, a
  lambda's variable, an array cast to text, or a whole number beside one with a fraction. The values checked for
  those below ([1,"x"], [[1,"a"],[2,"a"]], [1,"[2]"]) are what an array whose elements keep their own types
  writes as JSON; TO_JSON(ARRAY_CONSTRUCT(1, 2.5)) stops by name (SFN-083), since no page shows how Snowflake
  writes it.

Header case: this branch doesn't upper-case a result column's name, so a name is checked as written."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf


def _run(sql: str) -> tuple[list[tuple], list[str]]:
    translation = sf.translate(sql)
    assert translation.runnable, translation.first_refusal()
    con = duckdb.connect()
    rows = con.execute(translation.duckdb_sql).fetchall()
    return rows, [d[0] for d in con.description]


def _refused(sql: str) -> str:
    translation = sf.translate(sql)
    assert not translation.runnable
    return translation.statements[-1].notes[0].id


def _stops(sql: str, rule: str) -> None:
    translation = sf.translate(sql)
    assert translation.runnable, translation.first_refusal()
    with pytest.raises(duckdb.Error) as caught:
        duckdb.connect().execute(translation.duckdb_sql).fetchall()
    assert sf.stopped_by_name(caught.value) and rule in str(caught.value)


# ---------------------------------------------------------------- 1: SQL NULL and JSON null (SFN-084)

def test_the_construct_pages_array_is_refused_by_name():
    # the page's own array, without 3::DOUBLE, and with it: its SQL NULL is undefined, which no value here can be
    assert _refused("SELECT ARRAY_CONSTRUCT(NULL, PARSE_JSON('null'), 'hello', 3, 4)") == "SFN-084"
    assert _refused("SELECT ARRAY_CONSTRUCT(NULL, PARSE_JSON('null'), 'hello', 3::DOUBLE, 4, 5)") == "SFN-084"
    assert _refused("SELECT ARRAY_PREPEND(ARRAY_CONSTRUCT(1, 'a'), NULL)") == "SFN-084"


def test_json_null_beside_text_and_numbers_runs():
    # the page's array without its SQL NULL element: JSON null, "hello", 3, 4
    rows, _ = _run("SELECT ARRAY_CONSTRUCT(PARSE_JSON('null'), 'hello', 3, 4), "
                   "TO_JSON(ARRAY_CONSTRUCT(PARSE_JSON('null'), 'hello', 3, 4))")
    assert rows == [([None, "hello", 3, 4], '[null,"hello",3,4]')]


def test_a_sql_null_from_a_column_stops_at_its_row():
    _stops("SELECT ARRAY_CONSTRUCT(n, 'x') FROM (SELECT NULL::INT AS n)", "SFN-084")
    _stops("SELECT TRANSFORM(ARRAY_CONSTRUCT(1, NULL), x -> ARRAY_CONSTRUCT(x, 'a'))", "SFN-084")
    _stops("SELECT ARRAY_CONSTRUCT(UPPER(s), 1) FROM (SELECT NULL::VARCHAR AS s)", "SFN-084")
    _stops("SELECT ARRAY_APPEND(ARRAY_CONSTRUCT(1, 2), NULL::VARCHAR)", "SFN-084")


def test_compact_leaves_the_null_out_and_runs():
    rows, _ = _run("SELECT ARRAY_CONSTRUCT_COMPACT(NULL, n, 'x') FROM (SELECT NULL::INT AS n)")
    assert rows == [(["x"],)]


def test_an_array_of_one_kind_with_a_null_is_left_as_it_was():
    sql = sf.translate("SELECT ARRAY_CONSTRUCT('a', NULL), ARRAY_CONSTRUCT(1, NULL)").duckdb_sql
    assert "VARIANT" not in sql and "SFN-084" not in sql


# ---------------------------------------------------------------- 2 and 3: a column, a lambda's variable, a cast

def test_to_json_of_a_column_beside_text():
    rows, names = _run("SELECT TO_JSON(ARRAY_CONSTRUCT(n, 'x')) FROM (SELECT 1::INT AS n)")
    assert rows == [('[1,"x"]',)] and names == ["TO_JSON([n, 'x'])"]


def test_an_array_cast_to_text_beside_a_number():
    rows, _ = _run("SELECT ARRAY_CONSTRUCT(1, ARRAY_CONSTRUCT(2)::VARCHAR), "
                   "TO_JSON(ARRAY_CONSTRUCT(1, ARRAY_CONSTRUCT(2)::VARCHAR))")
    assert rows == [([1, "[2]"], '[1,"[2]"]')]  # the element is text, not the array it was


def test_transform_building_an_array_of_its_variable_and_text():
    rows, _ = _run("SELECT TRANSFORM(ARRAY_CONSTRUCT(1, 2), x -> ARRAY_CONSTRUCT(x, 'a')), "
                   "TO_JSON(TRANSFORM(ARRAY_CONSTRUCT(1, 2), x -> ARRAY_CONSTRUCT(x, 'a')))")
    assert rows == [([[1, "a"], [2, "a"]], '[[1,"a"],[2,"a"]]')]


def test_an_array_of_mixed_arrays_keeps_each_one():
    rows, _ = _run("SELECT TO_JSON(ARRAY_CONSTRUCT(ARRAY_CONSTRUCT(n, 'x'), 'y')) FROM (SELECT 1 AS n)")
    assert rows == [('[[1,"x"],"y"]',)]


def test_a_rewrite_list_isnt_made_variant():
    # only an array the query wrote is read: two columns, or one column alone, stay a plain list
    sql = sf.translate("SELECT ARRAY_CONSTRUCT(a, b), ARRAY_CONSTRUCT(a) FROM t").duckdb_sql
    assert "VARIANT" not in sql


# ---------------------------------------------------------------- 4: a whole number beside one with a fraction

def test_a_whole_number_keeps_its_type_beside_a_fraction():
    rows, _ = _run("SELECT ARRAY_CONSTRUCT(1, 2.5)")
    [(value,)] = rows
    assert value == [1, 2.5] and isinstance(value[0], int)  # DuckDB's one DECIMAL list made it 1.0


def test_to_json_of_a_whole_number_and_a_fraction_stops_by_name():
    # neither the array_construct nor the to_json page shows how Snowflake writes 2.5 beside 1
    _stops("SELECT TO_JSON(ARRAY_CONSTRUCT(1, 2.5))", "SFN-083")
    _stops("SELECT ARRAY_CONSTRUCT(1, 2.5)::VARCHAR", "SFN-078")


# ---------------------------------------------------------------- 5: TO_JSON inside an item, named as written

def test_to_json_inside_a_cast_or_call_is_named_as_written():
    rows, names = _run("SELECT to_json(v)::varchar, UPPER(TO_JSON(v)), TO_JSON(v), TO_JSON(v) AS j, "
                       "(SELECT TO_JSON(v)::VARCHAR) FROM (SELECT PARSE_JSON('{\"b\":1,\"a\":2}') AS v)")
    assert rows == [('{"a":2,"b":1}', '{"A":2,"B":1}', '{"a":2,"b":1}', '{"a":2,"b":1}', '{"a":2,"b":1}')]
    assert names == ["CAST(TO_JSON(v) AS VARCHAR)", "UPPER(TO_JSON(v))", "TO_JSON(v)", "j",
                     "(SELECT CAST(TO_JSON(v) AS VARCHAR))"]


def test_the_new_rules_are_in_the_catalog():
    ids = {row[0] for row in sf.CATALOG}
    assert {"SFX-085", "SFN-084"} <= ids
    assert sf.RULES_VERSION == "2026.11.23"
