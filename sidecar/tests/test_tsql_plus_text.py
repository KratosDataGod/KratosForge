"""Local T-SQL's ``+`` on text from a subquery, a CTE, an APPLY or a function, as T-SQL does it (WR-258).

T-SQL joins two strings with ``+`` and converts text to a number's type when it meets one. sqlglot gave no type to
LEFT, RIGHT, REPLACE, STUFF, FORMAT, QUOTENAME or STR, nor to a column of a CROSS or OUTER APPLY subquery, so the
``+`` stayed a ``+`` and DuckDB stopped with a binder error. Where a type truly can't be worked out (STRING_SPLIT's
``value``), the error names the problem and asks for a CAST.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql_errors

T = "FROM (SELECT 'abc' AS s, '123' AS n, 7 AS i) t"


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("tsql-plus-text") / "app" / "local-data")
    return local.engine(local.create("PlusText")["id"])


def run(engine, sql: str) -> dict:
    return engine.execute(sql, dialect="tsql")


def one(engine, sql: str):
    return run(engine, sql)["rows"][0][0]


@pytest.mark.parametrize(("sql", "expected"), [
    # derived tables and CTEs
    (f"SELECT LEFT(s, 2) + 'x' {T}", "abx"),
    ("SELECT s + 'x' FROM (SELECT LEFT('abc', 1) AS s) t", "ax"),
    ("WITH c AS (SELECT LEFT('abc', 2) AS s) SELECT s + 'x' FROM c", "abx"),
    ("WITH c(s) AS (SELECT RIGHT('abc', 2)) SELECT s + 'x' FROM c", "bcx"),
    # CROSS and OUTER APPLY
    ("SELECT x + 'a' FROM (SELECT 1 AS d) q CROSS APPLY (SELECT 'b' AS x) z", "ba"),
    ("SELECT x + 'a' FROM (SELECT 1 AS d) q OUTER APPLY (SELECT 'b' AS x) z", "ba"),
    ("SELECT LEFT(x, 1) + y FROM (SELECT 1 AS d) q CROSS APPLY (SELECT 'pq' AS x, 'r' AS y) z", "pr"),
    ("SELECT z.x + q.s FROM (SELECT 'a' AS s) q CROSS APPLY (SELECT q.s + 'b' AS x) z", "aba"),
    # functions, nested
    (f"SELECT UPPER(LEFT(s, 2)) + LOWER(RIGHT(s, 1)) {T}", "ABc"),
    (f"SELECT SUBSTRING(s, 2, 1) + LTRIM(RTRIM(' x ')) {T}", "bx"),
    (f"SELECT STUFF(s, 2, 1, 'Z') + REPLACE(s, 'a', 'A') {T}", "aZcAbc"),
    (f"SELECT QUOTENAME(LEFT(s, 2)) + STR(i, 3) {T}", "[ab]  7"),
    ("SELECT STR(5) + 'b'", "         5b"),
    (f"SELECT FORMAT(i, '00') + '%' {T}", "07%"),
    (f"SELECT CONVERT(VARCHAR(10), i) + 'x' {T}", "7x"),
    (f"SELECT CAST(i AS VARCHAR(10)) + 'x' {T}", "7x"),
    (f"SELECT REVERSE(s) + REPLICATE('-', 2) {T}", "cba--"),
    # CASE and IIF that give text
    (f"SELECT CASE WHEN i > 1 THEN 'big' ELSE 'small' END + '!' {T}", "big!"),
    (f"SELECT IIF(i > 1, 'big', 'small') + '!' {T}", "big!"),
])
def test_text_is_joined(engine, sql, expected):
    assert one(engine, sql) == expected


@pytest.mark.parametrize(("sql", "expected"), [
    (f"SELECT LEFT(n, 2) + 1 {T}", 13),
    (f"SELECT 1 + LEFT(n, 2) {T}", 13),
    ("SELECT x + 1 FROM (SELECT '5' AS x) t", 6),
    ("SELECT z.x + 1 FROM (SELECT 1 AS d) q CROSS APPLY (SELECT '41' AS x) z", 42),
    ("WITH c(s) AS (SELECT RIGHT('123', 2)) SELECT s + 1 FROM c", 24),
])
def test_a_number_outranks_text(engine, sql, expected):
    assert one(engine, sql) == expected


@pytest.mark.parametrize("sql", [
    f"SELECT LEFT(s, 1) + 1 {T}",
    "WITH c(s) AS (SELECT RIGHT('abc', 2)) SELECT s + 1 FROM c",
])
def test_text_that_is_not_a_number_stops(engine, sql):
    with pytest.raises(Exception, match="Conversion failed"):
        run(engine, sql)


@pytest.mark.parametrize("sql", [
    f"SELECT NULL + LEFT(s, 1) {T}",
    f"SELECT LEFT(s, 1) + NULL {T}",
    "SELECT z.x + 'b' FROM (SELECT 1 AS d) q OUTER APPLY (SELECT 'a' AS x WHERE 1 = 0) z",
])
def test_text_with_null_is_null(engine, sql):
    assert one(engine, sql) is None


def test_null_with_text_needs_no_note(engine):
    answer = run(engine, "SELECT NULL + 'a'")
    assert answer["rows"][0][0] is None
    assert answer["translation"]["notes"] == []


@pytest.mark.parametrize("sql", [
    "SELECT value + 'x' FROM STRING_SPLIT('a,b', ',')",
    "SELECT value + 1 FROM STRING_SPLIT('1,2', ',')",
])
def test_an_untyped_value_says_to_cast(engine, sql):
    with pytest.raises(Exception) as caught:
        run(engine, sql)
    assert str(caught.value) == tsql_errors.PLUS_UNTYPED
    assert "Binder Error" not in str(caught.value)


def test_a_cast_value_is_joined(engine):
    rows = run(engine, "SELECT CAST(value AS VARCHAR(10)) + 'x' FROM STRING_SPLIT('a,b', ',')")["rows"]
    assert rows == [["ax"], ["bx"]]


def test_text_and_function_text_join_without_a_note(engine):
    answer = run(engine, "SELECT t.s + LEFT(t.s, 2) FROM (SELECT 'abc' AS s) t")
    assert answer["rows"] == [["abcab"]]
    assert answer["translation"]["notes"] == []


@pytest.mark.parametrize("sides", ["VARCHAR, STRING_LITERAL", "INTEGER_LITERAL, VARCHAR"])
def test_duckdbs_plus_error_on_text_is_translated(sides):
    first = (f"Binder Error: No function matches the given name and argument types '+({sides})'. You might need to "
             "add explicit type casts.")
    assert tsql_errors.warehouse(first + "\n\tCandidate functions:\n\t+(TINYINT) -> TINYINT", "", "select") \
        == tsql_errors.PLUS_UNTYPED


def test_duckdbs_plus_error_on_numbers_is_left_alone():
    first = ("Binder Error: No function matches the given name and argument types '+(DATE, BOOLEAN)'. You might "
             "need to add explicit type casts.")
    assert tsql_errors.warehouse(first, "", "select") is None
