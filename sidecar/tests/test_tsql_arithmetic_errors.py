"""WR-247: local T-SQL's arithmetic and conversion errors in SQL Server's numbers and words, and NULL in + and CONCAT.

SQL Server says "Msg 8115 ... Arithmetic overflow error converting expression to data type int." for an int that
overflows (Microsoft's ABS page shows it for ABS(-2147483648) of an int), 245 "Conversion failed when converting the
varchar value 'x' to data type int." (Microsoft's CAST and CONVERT page), 8114 "Error converting data type varchar to
numeric.", 241 "Conversion failed when converting date and/or time from character string.", 248 for whole-number
text too big for an int and 232 for POWER of an int. Where SQL Server's words depend on what DuckDB's message doesn't
say (a constant cast to a smaller type, which is 220), DuckDB's words stay, and no number is guessed.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql_errors

INT_OVERFLOW = "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type int."
BIGINT_OVERFLOW = "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type bigint."
NUMERIC_OVERFLOW = "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type numeric."
BY_ZERO = "Msg 8134, Level 16: Divide by zero error encountered."
DATE_TEXT = "Msg 241, Level 16: Conversion failed when converting date and/or time from character string."


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Arithmetic")["id"])
    # A warehouse table has no tinyint, so the table is made in DuckDB's words; T-SQL reads its UTINYINT as tinyint
    e.execute("CREATE TABLE t (i INTEGER, b BIGINT, ti UTINYINT, si SMALLINT, d DECIMAL(38, 0), s VARCHAR, "
              "ns VARCHAR, ni INTEGER, z INTEGER)")
    e.execute("INSERT INTO t VALUES (2147483647, 9223372036854775807, 1, 1, "
              "99999999999999999999999999999999999999, 'x', NULL, NULL, 0)", dialect="tsql")
    e.execute("INSERT INTO t VALUES (2147483647, 1, 255, 32767, 1, '1.5', NULL, NULL, 0)", dialect="tsql")
    yield e
    data.close_all()


def error(engine, sql):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    return str(caught.value)


def rows(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"]


@pytest.mark.parametrize("sql, says", [
    ("SELECT 2147483647 + 1", INT_OVERFLOW),
    ("SELECT i + 1 FROM t", INT_OVERFLOW),
    ("SELECT i * 2 FROM t", INT_OVERFLOW),
    ("SELECT -i - 2 FROM t", INT_OVERFLOW),
    ("SELECT ABS(CAST(-2147483648 AS INT))", INT_OVERFLOW),
    ("SELECT -(-i - 1) FROM t", INT_OVERFLOW),
    ("SELECT -(-2147483647 - 1)", INT_OVERFLOW),  # DuckDB 2.0 gave 2147483648, a BIGINT
    ("SELECT SUM(i) FROM t", INT_OVERFLOW),  # T-SQL's SUM of an int is an int; DuckDB's was 4294967294
    ("SELECT SUM(i) OVER () FROM t", INT_OVERFLOW),
    ("SELECT SUM(i / 1) FROM t", INT_OVERFLOW),
    ("SELECT ROUND(2147483647, -1)", INT_OVERFLOW),
    ("SELECT b + 1 FROM t WHERE b > 1", BIGINT_OVERFLOW),
    ("SELECT CAST(9223372036854775807 AS BIGINT) + 1", BIGINT_OVERFLOW),
    ("SELECT SUM(b) FROM t", BIGINT_OVERFLOW),
    ("SELECT d + 1 FROM t WHERE d > 1", NUMERIC_OVERFLOW),
    ("SELECT d * 10 FROM t WHERE d > 1", NUMERIC_OVERFLOW),
    ("SELECT CAST(i AS SMALLINT) FROM t",
     "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type smallint."),
    ("SELECT CAST(i AS TINYINT) FROM t",
     "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type tinyint."),
    ("SELECT POWER(2, 31)", "Msg 232, Level 16: Arithmetic overflow error for type int, value = 2147483648.000000."),
    ("SELECT POWER(10, 10)", "Msg 232, Level 16: Arithmetic overflow error for type int, value = 10000000000.000000."),
])
def test_an_overflow_is_8115_in_sql_servers_words(engine, sql, says):
    assert error(engine, sql) == says


@pytest.mark.parametrize("sql, answer", [
    # A whole number too big for an int is a decimal in T-SQL, not a bigint
    ("SELECT 9223372036854775807 + 1", "9223372036854775808"),
    ("SELECT 4294967296 * 4294967296", "18446744073709551616"),
    ("SELECT ABS(-2147483648)", "2147483648"),  # DuckDB 2.0 read -2147483648 as an INT and overflowed
    ("SELECT -2147483648 - 1", "-2147483649"),
    ("SELECT 3000000000 / 7", "428571428.571428"),
    ("SELECT SUM(ti) FROM t", 256),  # tinyint's total is an int
    ("SELECT SUM(si) FROM t", 32768),
    ("SELECT -i FROM t WHERE ti = 1", -2147483647),
    ("SELECT POWER(2, 30)", 1073741824),
])
def test_what_fits_is_the_value(engine, sql, answer):
    got = rows(engine, sql)[0][0]
    assert str(got) == str(answer), got


def test_sum_keeps_an_int_and_groups_as_before(engine):
    engine.execute("CREATE TABLE g (k VARCHAR(1), v INT)", dialect="tsql")
    engine.execute("INSERT INTO g VALUES ('a', 1), ('a', 2), ('b', 5)", dialect="tsql")
    assert rows(engine, "SELECT k, SUM(v) FROM g GROUP BY k HAVING SUM(v) > 2 ORDER BY SUM(v)") == [["a", 3], ["b", 5]]
    assert rows(engine, "SELECT SUM(DISTINCT v) FROM g") == [[8]]
    assert rows(engine, "SELECT SUM(v) FROM g WHERE v > 9") == [[None]]


@pytest.mark.parametrize("sql", [
    "SELECT 1 / 0", "SELECT 1.5 / 0", "SELECT CAST(1 AS DECIMAL(5, 2)) / 0", "SELECT 5 % 0", "SELECT i % z FROM t",
    "SELECT i / z FROM t", "SELECT 1.0 / z FROM t", "SELECT CAST(1 AS FLOAT) / z FROM t",
])
def test_divide_and_modulo_by_zero_are_8134(engine, sql):
    assert error(engine, sql) == BY_ZERO


@pytest.mark.parametrize("sql, says", [
    ("SELECT CAST('x' AS INT)", "Msg 245, Level 16: Conversion failed when converting the varchar value 'x' to data type int."),
    ("SELECT CAST('1.5' AS INT)",
     "Msg 245, Level 16: Conversion failed when converting the varchar value '1.5' to data type int."),
    ("SELECT CONVERT(INT, 'x')", "Msg 245, Level 16: Conversion failed when converting the varchar value 'x' to data type int."),
    ("SELECT CAST(s AS INT) FROM t WHERE ti = 1",
     "Msg 245, Level 16: Conversion failed when converting the varchar value 'x' to data type int."),
    ("SELECT CAST('abc' AS BIGINT)",
     "Msg 245, Level 16: Conversion failed when converting the varchar value 'abc' to data type bigint."),
    ("SELECT CAST('abc' AS TINYINT)",
     "Msg 245, Level 16: Conversion failed when converting the varchar value 'abc' to data type tinyint."),
    ("SELECT CAST(N'abc' AS INT)",
     "Msg 245, Level 16: Conversion failed when converting the nvarchar value 'abc' to data type int."),
    ("SELECT 'abc' + 1", "Msg 245, Level 16: Conversion failed when converting the varchar value 'abc' to data type int."),
    ("SELECT i FROM t WHERE i = 'abc'",
     "Msg 245, Level 16: Conversion failed when converting the varchar value 'abc' to data type int."),
    ("SELECT CAST('3000000000' AS INT)",
     "Msg 248, Level 16: The conversion of the varchar value '3000000000' overflowed an int column."),
    ("SELECT CAST('abc' AS DECIMAL(5, 2))", "Msg 8114, Level 16: Error converting data type varchar to numeric."),
    ("SELECT CAST('' AS DECIMAL(5, 2))", "Msg 8114, Level 16: Error converting data type varchar to numeric."),
    ("SELECT CAST('abc' AS NUMERIC)", "Msg 8114, Level 16: Error converting data type varchar to numeric."),
    ("SELECT CAST('abc' AS FLOAT)", "Msg 8114, Level 16: Error converting data type varchar to float."),
    ("SELECT CAST('abc' AS REAL)", "Msg 8114, Level 16: Error converting data type varchar to real."),
    ("SELECT CAST('abc' AS DATE)", DATE_TEXT),
    ("SELECT CONVERT(DATE, 'nope')", DATE_TEXT),
    ("SELECT CAST('2024-02-30' AS DATE)", DATE_TEXT),
    ("SELECT CAST('2024-13-45' AS DATETIME2)", DATE_TEXT),
    ("SELECT CONVERT(DATETIME2, 'abc', 120)", DATE_TEXT),
    ("SELECT CAST('abc' AS DATETIME)", DATE_TEXT),
    ("SELECT CAST('abc' AS TIME)", DATE_TEXT),
    ("SELECT CAST('25:00' AS TIME)", DATE_TEXT),
])
def test_a_failed_conversion_has_sql_servers_number_and_words(engine, sql, says):
    assert error(engine, sql) == says


def test_try_cast_and_valid_text_still_convert(engine):
    assert rows(engine, "SELECT TRY_CAST('x' AS INT), TRY_CAST('3000000000' AS INT), CAST(' 7 ' AS INT), "
                        "CAST('-2147483648' AS INT), ' 5 ' + 1, CAST('' AS INT)") == [[None, None, 7, -2147483648, 6, 0]]


@pytest.mark.parametrize("sql", [
    "SELECT CAST(300 AS TINYINT)",  # a constant: SQL Server says 220, "Arithmetic overflow error for data type ..."
    "SELECT CAST(40000 AS SMALLINT)",
    "SELECT CAST(2147483648 AS INT)",
    "SELECT CAST(123.45 AS DECIMAL(4, 2))",  # "converting numeric to data type numeric", named by the source
    "SELECT CAST(1000 AS DECIMAL(5, 2))",
    "SELECT CAST('99999' AS DECIMAL(5, 2))",  # too big rather than not a number
])
def test_what_isnt_certain_keeps_duckdbs_words_and_no_number(engine, sql):
    said = error(engine, sql)
    assert not said.startswith("Msg "), said
    assert said.startswith("Conversion Error:"), said


def test_the_translators_own_messages_are_numbered_and_lose_duckdbs_prefix():
    raised = "Invalid Input Error: Arithmetic overflow error converting expression to data type int."
    assert tsql_errors.warehouse(raised, "", "select") == raised.split(": ", 1)[1]
    assert tsql_errors.numbered(raised.split(": ", 1)[1]) == INT_OVERFLOW
    # someone's own error() text that isn't one of SQL Server's keeps DuckDB's words
    assert tsql_errors.warehouse("Invalid Input Error: nope", "", "select") is None


def test_a_datetime_date_that_isnt_there_is_not_241():
    # datetime and smalldatetime say 242 for a date that isn't there; the translator's range check names them
    sql = ("SELECT CASE WHEN x THEN ERROR('The conversion of a varchar data type to a datetime data type resulted in an "
           "out-of-range value.') END")
    assert tsql_errors.warehouse('Conversion Error: timestamp field value out of range: "2024-02-30"', sql, "select") \
        is None
    assert tsql_errors.warehouse('Conversion Error: invalid timestamp field format: "abc", expected', sql, "select") \
        == DATE_TEXT.split(": ", 1)[1]


def test_overflow_in_a_script_names_the_statement(engine):
    said = error(engine, "SELECT 1; SELECT SUM(i) FROM t")
    assert said == f"Statement 2 of 2 (SELECT) failed: {INT_OVERFLOW} Statement 1 ran, and what it changed is kept.", said


@pytest.mark.parametrize("sql, answer", [
    ("SELECT 'a' + NULL", None),
    ("SELECT 'a' + ns FROM t WHERE ti = 1", None),
    ("SELECT s + ns FROM t WHERE ti = 1", None),
    ("SELECT 1 + NULL", None),
    ("SELECT 1 + ni FROM t WHERE ti = 1", None),
    ("SELECT CONCAT('a', NULL, 'b')", "ab"),
    ("SELECT CONCAT('a', ns) FROM t WHERE ti = 1", "a"),
    ("SELECT CONCAT(NULL, NULL)", ""),
    ("SELECT CONCAT_WS('-', 'a', NULL, 'b')", "a-b"),
    ("SELECT CONCAT_WS('-', 'a', ns, 'b') FROM t WHERE ti = 1", "a-b"),
])
def test_plus_with_null_is_null_and_concat_skips_it(engine, sql, answer):
    assert rows(engine, sql) == [[answer]]
