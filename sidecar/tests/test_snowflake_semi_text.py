"""An ARRAY, OBJECT or VARIANT as text (2026.11.21): SFX-081 writes Snowflake's JSON, SFN-078 and SFN-079 refuse
what can't match. DuckDB gave its own list form ([1, a]) where Snowflake gives JSON ([1,"a"]).

Sources: TO_VARCHAR ("For VARIANT, ARRAY, or OBJECT inputs, the output is the string containing a JSON document or
JSON elementary value"; "A string stored in VARIANT is preserved as is"), the semi-structured data types page
('Sample'::VARIANT::VARCHAR is Sample), data type conversion (ARRAY and OBJECT to VARCHAR castable, not coercible;
VARIANT coercible), ARRAY_CONSTRUCT (3::DOUBLE shown as 3.000000000000000e+00, a SQL NULL as undefined), the
semi-structured considerations page (TO_CHAR of JSON null is SQL NULL), and querying-semistructured (stored objects
list their keys sorted)."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf


def _con(setup: str = "") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    if setup:
        translation = sf.translate(setup)
        assert translation.runnable, translation.first_refusal()
        con.execute(translation.duckdb_sql)
    return con


def _run(sql: str, setup: str = ""):
    translation = sf.translate(sql)
    assert translation.runnable, translation.first_refusal()
    con = _con(setup)
    rows = con.execute(translation.duckdb_sql).fetchall()
    return rows, [d[1] for d in con.description], [d[0] for d in con.description]


def _stops(sql: str, rule: str, setup: str = "") -> None:
    translation = sf.translate(sql)
    assert translation.runnable, translation.first_refusal()
    with pytest.raises(duckdb.Error) as caught:
        _con(setup).execute(translation.duckdb_sql).fetchall()
    assert sf.stopped_by_name(caught.value) and rule in str(caught.value)


@pytest.mark.parametrize("form", ["{}::VARCHAR", "{}::STRING", "{}::TEXT", "CAST({} AS VARCHAR)", "CAST({} AS STRING)",
                                  "CAST({} AS TEXT)", "TRY_CAST({} AS VARCHAR)", "TO_VARCHAR({})", "TO_CHAR({})"])
def test_every_cast_to_text_of_an_array_is_compact_json(form):
    rows, types, _ = _run("SELECT " + form.format("ARRAY_CONSTRUCT(1, 'a')") + ", "
                          + form.format("ARRAY_CONSTRUCT(1, 2)") + ", " + form.format("ARRAY_CONSTRUCT('a', 'b')"))
    assert rows == [('[1,"a"]', "[1,2]", '["a","b"]')]
    assert types == ["VARCHAR"] * 3


def test_nested_empty_and_boolean_arrays():
    rows, types, _ = _run("SELECT ARRAY_CONSTRUCT(ARRAY_CONSTRUCT(1, 2), ARRAY_CONSTRUCT(3))::VARCHAR, "
                          "ARRAY_CONSTRUCT()::VARCHAR, ARRAY_CONSTRUCT(TRUE, FALSE)::VARCHAR, "
                          "ARRAY_CONSTRUCT('a\"b')::VARCHAR, [1, 2]::VARCHAR")
    assert rows == [("[[1,2],[3]]", "[]", "[true,false]", '["a\\"b"]', "[1,2]")]
    assert types == ["VARCHAR"] * 5


def test_an_object_as_text_sorts_its_keys_at_every_level():
    rows, types, _ = _run("SELECT OBJECT_CONSTRUCT('b', 1, 'a', 'x')::VARCHAR, TO_VARCHAR(OBJECT_CONSTRUCT()), "
                          "OBJECT_CONSTRUCT('d', ARRAY_CONSTRUCT(1, 2), 'c', OBJECT_CONSTRUCT('z', 1, 'y', 'n'))::STRING, "
                          "PARSE_JSON('{\"b\": 1, \"a\": [1,  2, {\"d\": null, \"c\": true}]}')::VARCHAR")
    assert rows == [('{"a":"x","b":1}', "{}", '{"c":{"y":"n","z":1},"d":[1,2]}',
                     '{"a":[1,2,{"c":true,"d":null}],"b":1}')]
    assert types == ["VARCHAR"] * 4


def test_a_variant_holding_text_is_the_text_without_quotes():
    # data-types-semistructured: SELECT 'Sample'::VARIANT::VARCHAR gives Sample
    rows, types, _ = _run("SELECT 'Sample'::VARIANT::VARCHAR, TO_VARCHAR(TO_VARIANT('hi')), "
                          "PARSE_JSON('\"hi\"')::VARCHAR, TO_VARIANT(ARRAY_CONSTRUCT(1, 2))::VARCHAR")
    assert rows == [("Sample", "hi", "hi", "[1,2]")]
    assert types == ["VARCHAR"] * 4


def test_json_null_cast_to_text_is_sql_null():
    # semistructured-considerations: TO_CHAR(PARSE_JSON('{ "a": null }'):a) is NULL; JSON null inside stays null
    rows, _, _ = _run("SELECT TO_CHAR(PARSE_JSON('{ \"a\": null }'):a), PARSE_JSON('null')::VARCHAR, "
                      "PARSE_JSON('[null]')::VARCHAR")
    assert rows == [(None, None, "[null]")]


_TABLE = ("CREATE TABLE t (id INT, a ARRAY, o OBJECT, v VARIANT, s VARCHAR, n INT); "
          "INSERT INTO t SELECT 1, ARRAY_CONSTRUCT(1, 'a'), OBJECT_CONSTRUCT('b', 1, 'a', PARSE_JSON('null')), "
          "PARSE_JSON('{\"z\": [1, \"q\"], \"y\": true}'), 'hey', 5; "
          "INSERT INTO t SELECT 2, ARRAY_CONSTRUCT(1.5), NULL, TO_VARIANT('str'), NULL, NULL;")


def test_columns_are_read_by_their_type_when_the_query_runs():
    rows, types, names = _run("SELECT a::VARCHAR, o::VARCHAR, v::VARCHAR, TO_VARCHAR(v), s::VARCHAR, n::VARCHAR "
                              "FROM t WHERE id = 1", _TABLE)
    assert rows == [('[1,"a"]', '{"a":null,"b":1}', '{"y":true,"z":[1,"q"]}', '{"y":true,"z":[1,"q"]}', "hey", "5")]
    assert types == ["VARCHAR"] * 6
    assert names[0] == "CAST(a AS TEXT)" and names[4] == "CAST(s AS TEXT)"  # names as before, not the rewrite's text
    rows, _, _ = _run("SELECT v::VARCHAR, o::VARCHAR FROM t WHERE id = 2", _TABLE)
    assert rows == [("str", None)]


def test_a_variant_joined_with_text_is_written_as_to_varchar_writes_it():
    rows, types, names = _run("SELECT 'x' || 'Sample'::VARIANT, CONCAT('x', PARSE_JSON('[1, 2]')), 'x' || s, "
                              "'x' || n, s || v FROM t WHERE id = 1", _TABLE)
    assert rows == [("xSample", "x[1,2]", "xhey", "x5", 'hey{"y":true,"z":[1,"q"]}')]
    assert types == ["VARCHAR"] * 5
    assert names[2:4] == ["'x' || s", "'x' || n"]
    rows, _, _ = _run("SELECT 'x' || v, 'x' || PARSE_JSON('null') FROM t WHERE id = 2", _TABLE)
    assert rows == [("xstr", None)]


def test_to_json_of_a_rewritten_value_is_named_as_written_once():
    # the column keeps the TO_JSON call's text as written (SFX-070), not the rewrite's text, named once
    rows, _, names = _run("SELECT TO_JSON(v || 'x'), TO_JSON(PARSE_JSON(v::VARCHAR)), TO_JSON(a) FROM t WHERE id = 1",
                          _TABLE)
    assert names == ["TO_JSON(v || 'x')", "TO_JSON(PARSE_JSON(CAST(v AS VARCHAR)))", "TO_JSON(a)"]
    assert rows[0][1:] == ('{"y":true,"z":[1,"q"]}', '[1,"a"]')


@pytest.mark.parametrize("sql", ["SELECT 'x' || ARRAY_CONSTRUCT(1, 2)", "SELECT CONCAT('x', ARRAY_CONSTRUCT(1, 2))",
                                 "SELECT 'x' || OBJECT_CONSTRUCT('a', 1)", "SELECT ARRAY_AGG(1) || 'x'",
                                 "SELECT 'x' || [1, 2]::ARRAY"])
def test_an_array_or_object_joined_with_text_is_refused(sql):
    translation = sf.translate(sql)
    assert not translation.runnable
    assert translation.statements[0].notes[0].id == "SFN-079"


@pytest.mark.parametrize("sql", ["SELECT 'x' || a FROM t WHERE id = 1", "SELECT 'x' || o FROM t WHERE id = 1"])
def test_an_array_or_object_column_joined_with_text_stops(sql):
    _stops(sql, "SFN-079", _TABLE)


@pytest.mark.parametrize("sql", [
    "SELECT ARRAY_CONSTRUCT(PARSE_JSON('null'), 3::DOUBLE, 4)::VARCHAR",  # the page's own: 3.000000000000000e+00
    "SELECT ARRAY_CONSTRUCT(1.5, 2)::VARCHAR",
    "SELECT ARRAY_CONSTRUCT(NULL, 1)::VARCHAR",                                 # undefined in Snowflake
    "SELECT ARRAY_CONSTRUCT(CURRENT_DATE)::VARCHAR",
    "SELECT OBJECT_CONSTRUCT('d', CURRENT_DATE)::VARCHAR",
    "SELECT OBJECT_CONSTRUCT('d', 1.5)::VARCHAR",
    "SELECT PARSE_JSON('{\"a\": 1.5}')::VARCHAR",
    "SELECT ARRAY_CONSTRUCT('x' || CHR(10))::VARCHAR",
    "SELECT ARRAY_CONSTRUCT(1)::VARCHAR(20)",
    "SELECT a::VARCHAR FROM t WHERE id = 2",
])
def test_what_cannot_match_stops_by_name(sql):
    _stops(sql, "SFN-078", _TABLE)


def test_a_window_or_lambda_value_sorts_its_outer_keys_and_stops_on_an_inner_object():
    rows, types, _ = _run("SELECT id, ARRAY_AGG(id) OVER (ORDER BY id)::VARCHAR, FIRST_VALUE(v) OVER (ORDER BY id)::VARCHAR "
                          "FROM t ORDER BY id", _TABLE)
    assert rows == [(1, "[1]", '{"y":true,"z":[1,"q"]}'), (2, "[1,2]", '{"y":true,"z":[1,"q"]}')]
    assert types == ["INTEGER", "VARCHAR", "VARCHAR"]
    rows, _, _ = _run("SELECT object_pick(parse_json('{\"b\": [2], \"a\": 1}'), k1, k2)::string "
                      "FROM (SELECT 'b' AS k1, 'a' AS k2)")
    assert rows == [('{"a":1,"b":[2]}',)]
    _stops("SELECT object_pick(parse_json('{\"a\": {\"c\": 1, \"b\": 2}}'), k)::string FROM (SELECT 'a' AS k)", "SFN-078")


def test_an_aggregate_cast_to_text():
    rows, types, _ = _run("SELECT ARRAY_AGG(id)::VARCHAR, OBJECT_AGG(s, n)::VARCHAR FROM t", _TABLE)
    assert rows == [("[1,2]", '{"hey":5}')]
    assert types == ["VARCHAR", "VARCHAR"]


def test_the_rules_are_in_the_catalog():
    rows = {row[0]: row for row in sf.CATALOG}
    assert {"SFX-081", "SFN-078", "SFN-079"} <= set(rows)
    assert sf.RULES_VERSION == "2026.11.23"
