"""WR-219: local T-SQL's JSON functions give a Fabric Warehouse's answers, or say where they differ.

Each expected value is from T-SQL's reference on Microsoft Learn (ISJSON, JSON_VALUE, JSON_QUERY, JSON_OBJECT,
JSON_ARRAY), whose "Applies to" includes Warehouse in Microsoft Fabric. ISJSON with no type is 1 only for an object
or array. JSON_VALUE and JSON_QUERY give NULL in lax mode (the default) where strict mode is an error. JSON_ARRAY
leaves NULLs out by default, and JSON_OBJECT keeps them. What the local engine can't run the same way (OPENJSON,
FOR JSON, the ON NULL clauses, JSON_ARRAYAGG and JSON_OBJECTAGG, JSON_MODIFY, JSON_PATH_EXISTS) is refused by
name, and what may still differ (a number's written form, the spaces in a JSON_QUERY fragment, JSON that bends the
standard) runs with a note.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("json") / "app" / "local-data")
    e = local.engine(local.create("Json")["id"])
    e.execute("CREATE TABLE dbo.docs (id INT, j VARCHAR(8000))", dialect="tsql")
    e.execute("""INSERT INTO dbo.docs VALUES (1, '{"a":1,"o":{"b":2},"l":[1,2]}'), (2, '[1]'), (3, 'bad'), """
              "(4, NULL)", dialect="tsql")
    yield e
    local.close_all()


def one(engine, sql):
    r = engine.execute(sql, dialect="tsql")
    return r["rows"][0][0]


def notes(engine, sql):
    return (engine.execute(sql, dialect="tsql").get("translation") or {}).get("notes") or []


# (T-SQL expression, T-SQL's answer)
CASES = [
    # ISJSON: an object or array is JSON; a scalar needs VALUE or SCALAR; NULL in, NULL out
    ("""ISJSON('{"a":1}')""", 1),
    ("ISJSON('[1,2]')", 1),
    ("ISJSON('1')", 0),
    ("""ISJSON('"x"')""", 0),
    ("ISJSON('test string')", 0),
    ("ISJSON(NULL)", None),
    ("""ISJSON('{"a":1}', OBJECT)""", 1),
    ("ISJSON('[1]', OBJECT)", 0),
    ("ISJSON('[1]', ARRAY)", 1),
    ("ISJSON('true', VALUE)", 1),
    ("ISJSON('test string', VALUE)", 0),
    ("""ISJSON('"test string"', SCALAR)""", 1),
    ("ISJSON('1.5', SCALAR)", 1),
    ("ISJSON('true', SCALAR)", 0),
    # JSON_VALUE: a scalar's text; lax gives NULL for what isn't one
    ("""JSON_VALUE('{"a":1}', '$.a')""", "1"),
    ("""JSON_VALUE('{"a":"x"}', '$.a')""", "x"),
    ("""JSON_VALUE('{"a":true}', '$.a')""", "true"),
    ("""JSON_VALUE('{"a":null}', '$.a')""", None),
    ("""JSON_VALUE('{"a":{"b":1}}', '$.a')""", None),
    ("""JSON_VALUE('{"a":[1,2]}', '$.a')""", None),
    ("""JSON_VALUE('{"a":[1,2]}', '$.a[1]')""", "2"),
    ("""JSON_VALUE('{"a":{"b":[10,20]}}', '$.a.b[0]')""", "10"),
    ("""JSON_VALUE('{"a b":1}', '$."a b"')""", "1"),
    ("""JSON_VALUE('{"a''b":1}', '$."a''b"')""", "1"),  # a key with an apostrophe, lax and strict
    ("""JSON_VALUE('{"a''b":1}', 'strict $."a''b"')""", "1"),
    ("""JSON_VALUE('{"a\\"b":1}', '$."a\\"b"')""", "1"),
    ("""JSON_VALUE('{"a\\\\b":4}', '$."a\\\\b"')""", "4"),  # a key with a backslash
    ("""JSON_VALUE('{"a\\\\b":4}', 'strict $."a\\\\b"')""", "4"),
    ("""JSON_VALUE('{"a":[1,2]}', 'strict $.a[1]')""", "2"),
    ("""JSON_VALUE('{"a":1}', '$.b')""", None),
    ("""JSON_VALUE('{"a":1}', 'lax $.b')""", None),
    ("""JSON_VALUE('{"a":1}', 'strict $.a')""", "1"),
    ("""JSON_VALUE('{"a":null}', 'strict $.a')""", None),
    ("JSON_VALUE(NULL, '$.a')", None),
    ("""JSON_VALUE('{"a":"' + REPLICATE('x', 4001) + '"}', '$.a')""", None),
    ("""LEN(JSON_VALUE('{"a":"' + REPLICATE('x', 4000) + '"}', '$.a'))""", 4000),
    # JSON_QUERY: an object or array; lax gives NULL for a scalar or a missing path
    ("""JSON_QUERY('{"a":{"b":1}}', '$.a')""", '{"b":1}'),
    ("""JSON_QUERY('{"a":[1,2]}', '$.a')""", "[1,2]"),
    ("""JSON_QUERY('{"a":1}', '$.a')""", None),
    ("""JSON_QUERY('{"a":1}', '$.b')""", None),
    ("""JSON_QUERY('{"a":{"b":1}}')""", '{"a":{"b":1}}'),
    ("""JSON_QUERY('{"a":{"b":1}}', 'strict $.a')""", '{"b":1}'),
    ("JSON_QUERY(NULL, '$.a')", None),
    # JSON_OBJECT keeps NULL as null; JSON_ARRAY leaves it out
    ("JSON_OBJECT('name':'value', 'type':1)", '{"name":"value","type":1}'),
    ("JSON_OBJECT('a': NULL)", '{"a":null}'),
    ("JSON_OBJECT()", "{}"),
    ("JSON_ARRAY('a', 1, 'b', NULL)", '["a",1,"b"]'),
    ("JSON_ARRAY(NULL)", "[]"),
    ("JSON_ARRAY()", "[]"),
    ("""JSON_ARRAY('a"b')""", '["a\\"b"]'),
    # a JSON function inside another nests as JSON, not as quoted text
    ("JSON_ARRAY(1, JSON_ARRAY(2, NULL), JSON_OBJECT('k': 'v'))", '[1,[2],{"k":"v"}]'),
    ("""JSON_OBJECT('a': JSON_ARRAY(1), 'b': JSON_QUERY('{"x":[1]}', '$.x'))""", '{"a":[1],"b":[1]}'),
]


@pytest.mark.parametrize(("expr", "want"), CASES)
def test_json_functions_give_tsqls_answers(engine, expr, want):
    assert one(engine, f"SELECT {expr}") == want


def test_isjson_and_json_value_read_a_column_row_by_row(engine):
    rows = engine.execute("SELECT id, ISJSON(j), ISJSON(j, VALUE) FROM dbo.docs ORDER BY id", dialect="tsql")["rows"]
    assert [list(r) for r in rows] == [[1, 1, 1], [2, 1, 1], [3, 0, 0], [4, None, None]]
    rows = engine.execute("SELECT id, JSON_VALUE(j, '$.a'), JSON_QUERY(j, '$.o') FROM dbo.docs WHERE id IN (1, 4) "
                          "ORDER BY id", dialect="tsql")["rows"]
    assert [list(r) for r in rows] == [[1, "1", '{"b":2}'], [4, None, None]]
    assert one(engine, "SELECT COUNT(*) FROM dbo.docs WHERE ISJSON(j) = 1 AND JSON_VALUE(j, '$.a') = '1'") == 1


@pytest.mark.parametrize(("sql", "words"), [
    # strict mode is an error where lax gives NULL
    ("""SELECT JSON_VALUE('{"a":1}', 'strict $.b')""", r"JSON_VALUE: the property at strict path '\$\.b' doesn't exist"),
    ("""SELECT JSON_VALUE('{"a":{"b":1}}', 'strict $.a')""", r"isn't a scalar\. Use JSON_QUERY instead"),
    ("""SELECT JSON_VALUE('{"a":"' + REPLICATE('x', 4001) + '"}', 'strict $.a')""", "longer than 4000 characters"),
    ("""SELECT JSON_QUERY('{"a":1}', 'strict $.a')""", r"isn't an object or array\. Use JSON_VALUE instead"),
    ("""SELECT JSON_QUERY('{"a":1}', 'strict $.b')""", r"JSON_QUERY: the property at strict path '\$\.b' doesn't"),
    # JSON text that isn't JSON is an error, in lax mode too
    ("SELECT JSON_VALUE('not json', '$.a')", "Malformed JSON"),
    ("SELECT JSON_VALUE(j, '$.a') FROM dbo.docs", "Malformed JSON"),
    ("""SELECT JSON_VALUE('{"a":1}', 'bogus $.a')""", r"'bogus \$\.a' isn't a JSON path"),
    ("""SELECT JSON_VALUE('{"a":1}', 'foo')""", r"'foo' isn't a JSON path"),
    ("""SELECT JSON_VALUE('{"a":1}', 'lax foo')""", r"'lax foo' isn't a JSON path"),
    ("""SELECT ISJSON('{"a":1}', bogus)""", "ISJSON's second argument is VALUE, ARRAY, OBJECT or SCALAR"),
])
def test_what_t_sql_makes_an_error_is_an_error(engine, sql, words):
    with pytest.raises(Exception, match=words):
        engine.execute(sql, dialect="tsql")


@pytest.mark.parametrize(("sql", "words"), [
    ("""SELECT * FROM OPENJSON('{"a":1,"b":"x"}')""", "OPENJSON isn't mapped yet"),
    ("""SELECT * FROM OPENJSON('{"a":1}') WITH (a INT '$.a')""", "OPENJSON isn't mapped yet"),
    ("SELECT 1 AS a, 'x' AS b FOR JSON PATH", "FOR JSON PATH isn't mapped yet"),
    ("SELECT 1 AS a FOR JSON AUTO", "FOR JSON AUTO isn't mapped yet"),
    ("SELECT 1 AS a FOR JSON PATH, WITHOUT_ARRAY_WRAPPER", "FOR JSON PATH isn't mapped yet"),
    ("SELECT JSON_OBJECT('a': NULL ABSENT ON NULL)", "ABSENT ON NULL in JSON_OBJECT or JSON_ARRAY isn't mapped yet"),
    ("SELECT JSON_ARRAY(1, NULL NULL ON NULL)", "NULL ON NULL in JSON_OBJECT or JSON_ARRAY isn't mapped yet"),
    ("SELECT JSON_ARRAYAGG(id) FROM dbo.docs", "JSON_ARRAYAGG and JSON_OBJECTAGG aren't mapped yet"),
    ("SELECT JSON_OBJECTAGG('a': id) FROM dbo.docs", "JSON_ARRAYAGG and JSON_OBJECTAGG aren't mapped yet"),
    ("""SELECT JSON_MODIFY('{"a":1}', '$.a', 2)""", "JSON_MODIFY isn't mapped yet"),
    ("""SELECT JSON_PATH_EXISTS('{"a":1}', '$.a')""", "JSON_PATH_EXISTS isn't mapped yet"),
    ("""SELECT JSON_VALUE('{"a":[{"b":1}]}', '$.a[*].b')""", "JSON_VALUE with a wildcard, slice or filter"),
    ("""SELECT JSON_QUERY('[1]', '$[*]')""", "JSON_QUERY with a wildcard, slice or filter"),
    ("""SELECT JSON_VALUE('{"a":1}', '$..a')""", "JSON_VALUE with a wildcard, slice or filter"),
    ("SELECT JSON_VALUE(j, j) FROM dbo.docs", "JSON_VALUE with a path that isn't written out as text"),
])
def test_what_isnt_mapped_is_refused_by_name(engine, sql, words):
    with pytest.raises(ValueError, match=words):
        engine.execute(sql, dialect="tsql")


def test_the_words_in_a_string_or_a_name_are_left_alone(engine):
    assert one(engine, "SELECT 'FOR JSON PATH'") == "FOR JSON PATH"
    assert one(engine, "SELECT 'ABSENT ON NULL'") == "ABSENT ON NULL"
    assert one(engine, "SELECT [json] FROM (SELECT 1 AS [json]) AS x") == 1


@pytest.mark.parametrize(("sql", "words"), [
    ("""SELECT JSON_VALUE('{"a":1.50}', '$.a')""", "JSON_VALUE gives a JSON number in the local engine's form"),
    ("""SELECT JSON_QUERY('{"a":[1, 2]}', '$.a')""", "JSON_QUERY gives the object or array without the spaces"),
    ("""SELECT ISJSON('{"a":1}')""", "ISJSON counts JSON with a trailing comma, or NaN, as valid here"),
    ("SELECT JSON_ARRAY(CAST(1.50 AS DECIMAL(3,2)))", "write a decimal in its shortest form"),
    ("SELECT JSON_OBJECT('a': 1)", "write a decimal in its shortest form"),
])
def test_what_may_still_differ_runs_with_a_note(engine, sql, words):
    assert any(words in n for n in notes(engine, sql))


def test_the_documented_differences_are_what_runs(engine):
    assert one(engine, """SELECT JSON_VALUE('{"a":1.50}', '$.a')""") == "1.5"  # T-SQL: 1.50
    assert one(engine, """SELECT JSON_QUERY('{"a":[1, 2]}', '$.a')""") == "[1,2]"  # T-SQL: not checked
    assert one(engine, """SELECT ISJSON('{"a":1,}')""") == 1  # T-SQL: 0


def test_a_strict_or_lax_path_reaches_duckdb_as_a_plain_path():
    texts, _ = translate("SELECT JSON_VALUE(j, 'strict $.a'), JSON_QUERY(j, 'lax $.o') FROM dbo.docs",
                         {"dbo": {"docs": {"j": "VARCHAR"}}})
    assert "'strict $" not in texts[0] and "'lax $" not in texts[0] and "'$.a'" in texts[0] and "'$.o'" in texts[0]
