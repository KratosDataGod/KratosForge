"""WR-276: text + NULL, integers of different sizes, NCHAR, STRING_ESCAPE, and T-SQL built-ins refused by name.

Each expected answer is the one Microsoft Learn's T-SQL reference gives (the page is named on each test):

- "+ (String Concatenation)": with CONCAT_NULL_YIELDS_NULL ON, a warehouse's setting, text + NULL is NULL.
- "Data type precedence": "the data type with the lower precedence is first converted to the data type with the
  higher precedence", and "For an operator combining operand expressions having the same data type, the result of the
  operation has that data type." So a smallint + an int literal is an int, and smallint + smallint a smallint, which
  overflows with error 8115 in smallint's name.
- "- (Unary Negative)": "an unsigned tinyint expression is promoted to a signed smallint result."
- "NCHAR": the Unicode character for 0 through 1114111 with a supplementary-character collation; NULL outside it.
- "STRING_ESCAPE": the JSON special characters and CHAR(0) to CHAR(31), as its tables list them.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate

SMALLINT_OVERFLOW = "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type smallint."
TINYINT_OVERFLOW = "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type tinyint."
INT_OVERFLOW = "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type int."
COLUMNS = {"dbo": {"t": {"i": "INTEGER", "ti": "UTINYINT", "si": "SMALLINT", "s": "VARCHAR", "b": "BIGINT"}}}


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    data = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = data.engine(data.create("Sizes")["id"])
    # a warehouse table has no tinyint, so the table is made in DuckDB's words; T-SQL reads UTINYINT as tinyint
    e.execute("CREATE TABLE t (i INTEGER, ti UTINYINT, si SMALLINT, s VARCHAR, b BIGINT)")
    e.execute("INSERT INTO t VALUES (2147483647, 255, 32767, 'x', 1)", dialect="tsql")
    yield e
    data.close_all()


def rows(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"]


def error(engine, sql):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    return str(caught.value)


# + (String Concatenation): NULL, of the text's type, where DuckDB stopped with "No function matches '+(VARCHAR, NULL)'"
@pytest.mark.parametrize("sql", [
    "SELECT s + NULL FROM dbo.t", "SELECT NULL + s FROM dbo.t", "SELECT s + (NULL) FROM dbo.t",
    "SELECT s + CAST(NULL AS VARCHAR) FROM dbo.t", "SELECT s + NULL + 'x' FROM dbo.t", "SELECT 'x' + s + NULL FROM dbo.t",
    "SELECT i + NULL FROM dbo.t",
])
def test_text_plus_null_is_null(engine, sql):
    assert rows(engine, sql) == [[None]]


def test_text_plus_null_is_text(engine):
    assert rows(engine, "SELECT ISNULL(s + NULL, 'none') FROM dbo.t") == [["none"]]
    assert "||" in translate("SELECT s + NULL FROM dbo.t", COLUMNS)[0][0]


# Data type precedence: the smaller integer is converted to the larger, so these are ints
@pytest.mark.parametrize("sql, want", [
    ("SELECT si + 1 FROM dbo.t", 32768),
    ("SELECT si * 2 FROM dbo.t", 65534),
    ("SELECT si - (-1) FROM dbo.t", 32768),
    ("SELECT -si - 2 FROM dbo.t", -32769),
    ("SELECT si / 2 FROM dbo.t", 16383),
    ("SELECT ti + 1 FROM dbo.t", 256),
    ("SELECT ti * 1000 FROM dbo.t", 255000),
    ("SELECT CAST(32767 AS SMALLINT) + 1", 32768),
    ("SELECT CAST(255 AS TINYINT) + 1", 256),
    ("SELECT i + CAST(3000000000 AS BIGINT) FROM dbo.t", 5147483647),  # the int converted to the bigint
    ("SELECT SUM(si + 1) FROM dbo.t", 32768),
    # - (Unary Negative): -tinyint is a smallint (DuckDB 1.5 wrapped -255 to 1 as a UTINYINT)
    ("SELECT -ti FROM dbo.t", -255),
    ("SELECT -ti - ti FROM dbo.t", -510),
])
def test_a_smaller_integer_is_converted_to_the_larger(engine, sql, want):
    assert rows(engine, sql) == [[want]]


def test_an_int_column_plus_a_whole_number_past_int_is_a_decimal(engine):
    # Constants: a whole number past int is a decimal (WR-247), so the int is converted to it
    assert rows(engine, "SELECT i + 3000000000 FROM dbo.t") == [["5147483647"]]


def test_the_result_has_tsqls_type(engine):
    made = engine.execute("SELECT si + 1 AS a, si - si + si AS b, -ti AS c FROM dbo.t", dialect="tsql")
    assert [c["type"] for c in made["columns"]] == ["int32", "int16", "int16"]


# Same type: the result keeps it, and past it is 8115 in that type's name
@pytest.mark.parametrize("sql, says", [
    ("SELECT si + si FROM dbo.t", SMALLINT_OVERFLOW),
    ("SELECT CAST(32767 AS SMALLINT) + CAST(1 AS SMALLINT)", SMALLINT_OVERFLOW),
    ("SELECT si + ti FROM dbo.t", SMALLINT_OVERFLOW),  # the tinyint converted to the smallint
    ("SELECT -(-si - CAST(1 AS SMALLINT)) FROM dbo.t", SMALLINT_OVERFLOW),
    ("SELECT ti + ti FROM dbo.t", TINYINT_OVERFLOW),
    ("SELECT ti - ti - ti FROM dbo.t", TINYINT_OVERFLOW),
    ("SELECT i + 1 FROM dbo.t", INT_OVERFLOW),
    ("SELECT -si - 2147483647 FROM dbo.t", INT_OVERFLOW),
])
def test_two_of_one_type_keep_it_and_overflow_in_its_name(engine, sql, says):
    assert error(engine, sql) == says


# NCHAR: carried as CHAR is, over 0 to 1114111
@pytest.mark.parametrize("sql, want", [
    ("SELECT NCHAR(65) AS x", "A"),
    ("SELECT NCHAR(248)", "ø"),  # example A: ø
    ("SELECT NCHAR(143743)", "\U0002317f"),  # the page's preferred way to write a supplementary character
    ("SELECT NCHAR(1114111)", "\U0010ffff"),
    ("SELECT NCHAR(1114112)", None),
    ("SELECT NCHAR(-1)", None),
    ("SELECT NCHAR(65.9)", "A"),  # made an int first, toward zero
    ("SELECT NCHAR('66')", "B"),
    ("SELECT NCHAR(NULL)", None),
    ("SELECT NCHAR(si - 32702) FROM dbo.t", "A"),
    ("SELECT NCHAR(i) FROM dbo.t", None),
    ("SELECT NCHAR(UNICODE(SUBSTRING(N'København', 2, 1)))", "ø"),  # example A
    ("SELECT NCHAR(65) + 'b'", "Ab"),  # text, so + joins it
    ("SELECT NCHAR(ti) + 'b' FROM dbo.t", "ÿb"),
])
def test_nchar(engine, sql, want):
    assert rows(engine, sql) == [[want]]


def test_nchar_reads_its_code_once():
    text = translate("SELECT NCHAR(si + RAND()) FROM dbo.t", COLUMNS)[0][0]
    assert text.upper().count("RANDOM()") == 1


def test_nchar_of_half_a_surrogate_pair_is_refused_by_name(engine):
    with pytest.raises(ValueError, match="NCHAR of 55296 to 57343"):
        engine.execute("SELECT NCHAR(55296)", dialect="tsql")
    assert "NCHAR of 55296 to 57343" in error(engine, "SELECT NCHAR(55296 + ti - 255) FROM dbo.t")


# STRING_ESCAPE(text, 'json')
@pytest.mark.parametrize("sql, want", [
    # example A, with its tabs and new line written as CHAR
    ("SELECT STRING_ESCAPE('\\' + CHAR(9) + '/' + CHAR(10) + '\\\\' + CHAR(9) + '\"' + CHAR(9), 'json')",
     "\\\\\\t\\/\\n\\\\\\\\\\t\\\"\\t"),
    ("SELECT STRING_ESCAPE(CHAR(0) + CHAR(1) + CHAR(8) + CHAR(12) + CHAR(13) + CHAR(31) + CHAR(127) + 'é', 'json')",
     "\\u0000\\u0001\\b\\f\\r\\u001f\x7fé"),
    ("SELECT STRING_ESCAPE(NULL, 'json')", None),
    ("SELECT STRING_ESCAPE(s, 'json') FROM dbo.t", "x"),
    ("SELECT STRING_ESCAPE('a/', 'json') + 'b'", "a\\/b"),  # text, so + joins it
    ("SELECT LEN(STRING_ESCAPE(REPLICATE('ab/', 30), 'json'))", 120),  # nvarchar(max): not cut to 30 characters
])
def test_string_escape(engine, sql, want):
    assert rows(engine, sql) == [[want]]


def test_string_escape_of_another_type_is_refused_by_name(engine):
    with pytest.raises(ValueError, match="STRING_ESCAPE runs here with its type written as 'json'"):
        engine.execute("SELECT STRING_ESCAPE('a', 'xml')", dialect="tsql")


# T-SQL built-ins that aren't carried yet: refused by name, with or without the engine's function list
@pytest.mark.parametrize("sql, name", [
    ("SELECT ISDATE('2024-01-01')", "ISDATE"),
    ("SELECT DIFFERENCE('a', 'b')", "DIFFERENCE"),
    ("SELECT FORMATMESSAGE('x %s', 'y')", "FORMATMESSAGE"),
    ("SELECT TODATETIMEOFFSET(SYSUTCDATETIME(), '+01:00')", "TODATETIMEOFFSET"),
    ("SELECT CURRENT_TIMEZONE()", "CURRENT_TIMEZONE"),
    ("SELECT CURRENT_TIMEZONE_ID()", "CURRENT_TIMEZONE_ID"),
    ("SELECT JARO_WINKLER_SIMILARITY('Colour', 'Color')", "JARO_WINKLER_SIMILARITY"),  # T-SQL's is an int, 97
    ("SELECT EDIT_DISTANCE('a', 'b')", "EDIT_DISTANCE"),
    ("SELECT DATETIME2FROMPARTS(2024, 1, 2, 3, 4, 5, 0, 0)", "DATETIME2FROMPARTS"),
    ("SELECT JSON_MODIFY('{}', '$.a', 1)", "JSON_MODIFY"),
    ("SELECT HASHBYTES('NOPE', 'a')", "HASHBYTES"),
    ("SELECT LEFT_SHIFT(1, 2)", "LEFT_SHIFT"),
    ("SELECT DB_NAME()", "DB_NAME"),
    ("SELECT SYSDATETIMEOFFSET()", "SYSDATETIMEOFFSET"),
])
def test_an_uncarried_builtin_is_refused_by_name(engine, sql, name):
    for run in (lambda: translate(sql, COLUMNS), lambda: engine.execute(sql, dialect="tsql")):
        with pytest.raises(ValueError, match=f"on the local engine: {name} isn't mapped yet") as caught:
            run()
        assert "Catalog Error" not in str(caught.value)


@pytest.mark.parametrize("sql, name, count", [
    ("SELECT CHOOSE(1)", "CHOOSE", 1),
    ("SELECT QUOTENAME('a', '[', 'x')", "QUOTENAME", 3),
    ("SELECT NCHAR(65, 66)", "NCHAR", 2),
    ("SELECT STRING_ESCAPE('a')", "STRING_ESCAPE", 1),
    ("SELECT PARSENAME('a')", "PARSENAME", 1),
])
def test_a_builtin_with_the_wrong_number_of_arguments_is_refused(sql, name, count):
    with pytest.raises(ValueError, match=f"{name} can't take {count} argument"):
        translate(sql, COLUMNS)

