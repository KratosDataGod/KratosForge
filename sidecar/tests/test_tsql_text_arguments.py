"""WR-280: a number or a date where a T-SQL function takes text, and text, a bit or a date where an aggregate takes
numbers. These stopped with DuckDB's binder error ("No function matches the given name and argument types
'regexp_matches(INTEGER, STRING_LITERAL)'") where T-SQL converts the value, or gave an answer where T-SQL stops.

T-SQL's answer is the oracle (Microsoft's T-SQL reference, https://learn.microsoft.com/en-us/sql/t-sql/, the page
named on each case). data-types/data-type-conversion-database-engine: a number or a date converts implicitly to
varchar, so ``REGEXP_REPLACE(12, '1', 'z')`` replaces in '12'. What can't be carried is refused by name.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("tsql-text-args") / "app" / "local-data")
    e = local.engine(local.create("TextArgs")["id"])
    e.execute("CREATE TABLE dbo.t (i INT, b BIGINT, s SMALLINT, f FLOAT, r REAL, m DECIMAL(10, 2), "
              "w DECIMAL(25, 2), v VARCHAR(10), d DATE, d2 DATETIME2(6), bt BIT)", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (12, 2, 3, 1.5, 2.5, 3.25, 1.5, 'a', '2024-01-02', '2024-01-02 03:04:05', 1)",
              dialect="tsql")
    e.execute("CREATE TABLE dbo.n (i INT)", dialect="tsql")
    e.execute("INSERT INTO dbo.n VALUES (NULL)", dialect="tsql")
    return e


def _one(engine, sql: str) -> object:
    if not sql.upper().startswith(("SELECT", "WITH")):
        sql = f"SELECT {sql} AS v FROM dbo.t"
    return engine.execute(sql, dialect="tsql")["rows"][0][0]


def _says(engine, sql: str) -> str:
    with pytest.raises(ValueError) as caught:
        _one(engine, sql)
    return str(caught.value)


@pytest.mark.parametrize(("sql", "want"), [
    # functions/regexp-like-transact-sql, -replace, -count, -instr: string_expression and pattern_expression are
    # char, nchar, varchar or nvarchar; an int, a decimal and a date convert to their text, a float to at most 6
    # significant digits and a bit to 1 or 0 (cast-and-convert-transact-sql)
    ("IIF(REGEXP_LIKE(i, '^12$'), 1, 0)", 1),
    ("IIF(REGEXP_LIKE('x12', i), 1, 0)", 1),
    ("IIF(REGEXP_LIKE(d, '^2024-01-02$'), 1, 0)", 1),
    ("REGEXP_REPLACE(12, '1', 'z')", "z2"),
    ("REGEXP_REPLACE(m, '\\.', ',')", "3,25"),
    ("REGEXP_REPLACE(1.50, '0', 'x')", "1.5x"),  # 1.50 is a decimal(3, 2): '1.50'
    ("REGEXP_REPLACE(f, '5', '0')", "1.0"),
    ("REGEXP_REPLACE('a', 'a', bt)", "1"),
    ("REGEXP_REPLACE('a', 'a', i)", "12"),
    ("REGEXP_COUNT(i, '1')", 1),
    ("REGEXP_COUNT(b, 2)", 1),
    ("REGEXP_INSTR(d, '1')", 7),
    ("REGEXP_INSTR('aba', 'a', '2')", 3),  # start is an int: '2' converts
    # json-object-transact-sql: json_key_name is "a character expression"
    ("JSON_OBJECT(12: 1)", '{"12":1}'),
    # isjson-, json-value-transact-sql: the JSON text; '12' is no object or array, and its value is 12
    ("ISJSON(i)", 0),
    ("JSON_VALUE(i, '$')", "12"),
    # quotename-transact-sql: character_string is sysname
    ("QUOTENAME(i)", "[12]"),
    ("QUOTENAME(d)", "[2024-01-02]"),
    ("QUOTENAME(bt)", "[1]"),
    ("QUOTENAME(i) + '|'", "[12]|"),  # "Return types nvarchar(258)": + joins text
    ("REGEXP_REPLACE(i, '2', 'x') + '|'", "1x|"),
    # patindex-transact-sql: expression "is of the character string data type category"
    ("PATINDEX('%2%', i)", 2),
    ("PATINDEX('%-%', d)", 5),
    # datalength-transact-sql: "An expression of any data type": a number's bytes (data-types/int-bigint-smallint-
    # and-tinyint-transact-sql, float-and-real-transact-sql, decimal-and-numeric-transact-sql: precision 1-9 is 5
    # bytes, 10-19 9, 20-28 13, 29-38 17; bit-transact-sql; date-transact-sql 3 bytes)
    ("DATALENGTH(i)", 4), ("DATALENGTH(12)", 4), ("DATALENGTH(b)", 8), ("DATALENGTH(s)", 2), ("DATALENGTH(f)", 8),
    ("DATALENGTH(r)", 4), ("DATALENGTH(m)", 9), ("DATALENGTH(w)", 13), ("DATALENGTH(bt)", 1), ("DATALENGTH(d)", 3),
    ("DATALENGTH(CAST(1 AS TINYINT))", 1), ("DATALENGTH(CAST(1 AS DECIMAL(5, 2)))", 5),
    ("DATALENGTH(CAST(1 AS NUMERIC(38, 0)))", 17), ("DATALENGTH(i + 1)", 4), ("DATALENGTH(CAST(1.5 AS FLOAT))", 8),
    ("SELECT DATALENGTH(i) FROM dbo.n", None),  # "For a NULL value, DATALENGTH returns NULL."
    ("DATALENGTH(v)", 1),
    # bit-count-transact-sql: "BIT_COUNT(CAST (-1 AS SMALLINT))" is 16
    ("BIT_COUNT(CAST(-1 AS SMALLINT))", 16), ("BIT_COUNT(i)", 2),
])
def test_values_convert_as_tsql_converts_them(engine, sql: str, want: object) -> None:
    assert _one(engine, sql) == want


@pytest.mark.parametrize(("sql", "says"), [
    # var-transact-sql (and varp-, stdevp-, sum-, avg-, stdev-): "exact numeric or approximate numeric data type
    # category, except for the bit data type"; DuckDB stopped with its binder error, and SUM(bit) answered
    ("VAR(v)", '"Operand data type varchar is invalid for var operator."'),
    ("VARP(v)", '"Operand data type varchar is invalid for varp operator."'),
    ("STDEVP(v)", '"Operand data type varchar is invalid for stdevp operator."'),
    ("VAR(bt)", '"Operand data type bit is invalid for var operator."'),
    ("SUM(bt)", '"Operand data type bit is invalid for sum operator."'),
    ("AVG(bt)", '"Operand data type bit is invalid for avg operator."'),
    ("STDEV(bt)", '"Operand data type bit is invalid for stdev operator."'),
    ("VARP(d)", "VARP takes a number, and this is a date"),
    ("STDEVP(d2)", "STDEVP takes a number, and this is a datetime2"),
    # bit-count-transact-sql: tinyint, smallint, int, bigint, binary(n) and varbinary(n)
    ("BIT_COUNT(m)", "BIT_COUNT takes an integer or binary value"),
    ("BIT_COUNT(v)", "BIT_COUNT takes an integer or binary value"),
    ("BIT_COUNT(bt)", "BIT_COUNT takes an integer or binary value"),
    # string-agg-transact-sql: the separator is "of nvarchar or varchar type"
    ("SELECT STRING_AGG(v, 12) FROM dbo.t", "STRING_AGG's separator has to be text"),
    ("SELECT STRING_AGG(v, CAST(',' AS DATE)) FROM dbo.t", "STRING_AGG's separator has to be text"),
    # regexp-like-transact-sql: flags are characters such as 'i', 'c', 'm' and 's'
    ("IIF(REGEXP_LIKE('a', 'a', 1), 1, 0)", "REGEXP_LIKE's flags are text"),
    ("REGEXP_COUNT(d, d, d)", "REGEXP_COUNT takes a whole number there, and this is a date"),
    # a datetime2's text is CONVERT style 0 in T-SQL (cast-and-convert-transact-sql), which isn't copied here
    ("QUOTENAME(d2)", "QUOTENAME takes text, and T-SQL writes a datetime2 as text"),
    ("REGEXP_REPLACE(d2, '1', 'z')", "REGEXP_REPLACE takes text, and T-SQL writes a datetime2 as text"),
    # data-types/datetime2-transact-sql: 6 to 8 bytes by precision, which the local engine doesn't keep
    ("DATALENGTH(d2)", "DATALENGTH of this datetime2 isn't worked out here"),
    ("DATALENGTH(m * 2)", "DATALENGTH of this numeric(10, 2) isn't worked out here"),
    # jaro-winkler-similarity-transact-sql: "Return value int", JARO_WINKLER_SIMILARITY('Colour', 'Color') is 97;
    # DuckDB's function of the name gave 0.9666666666666667
    ("JARO_WINKLER_SIMILARITY('Colour', 'Color')", "JARO_WINKLER_SIMILARITY isn't mapped yet"),
    ("JARO_WINKLER_SIMILARITY(i, 'a')", "JARO_WINKLER_SIMILARITY isn't mapped yet"),
])
def test_what_tsql_stops_on_is_refused_by_name(engine, sql: str, says: str) -> None:
    message = _says(engine, sql)
    assert says in message
    assert message.startswith("This T-SQL can't be run on the local engine: ")


def test_a_number_in_a_numeric_aggregate_still_runs(engine) -> None:
    assert _one(engine, "VARP(i)") == 0.0
    assert _one(engine, "SUM(i)") == 12


def test_datalength_of_a_number_reads_its_value_once() -> None:
    texts, _ = tsql.translate("SELECT DATALENGTH(i) FROM dbo.t", {"dbo": {"t": {"i": "INTEGER"}}}, None)
    assert texts == ["SELECT CASE WHEN i IS NULL THEN CAST(NULL AS INT) ELSE 4 END FROM dbo.t"]
