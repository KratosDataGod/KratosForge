"""WR-278: T-SQL calls with a number or a type of arguments that stopped the translator with a Python error, or reached
DuckDB and stopped with its parser, binder or catalog error.

T-SQL's answer is the oracle (Microsoft's T-SQL reference, https://learn.microsoft.com/en-us/sql/t-sql/functions/, the
page named on each case): a call with a number of arguments the function's syntax doesn't take is SQL Server's
Msg 174 ("The ceiling function requires 1 argument(s).") or Msg 189 ("The round function requires 2 to 3
arguments."), a window function needs OVER, and an argument T-SQL converts on the way in (a number where it takes
text, text where it takes an int or a float) is converted as T-SQL converts it. What can't be carried is refused
by name.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql
from fabric_desktop.localdata import tsql_errors

DOCS = "https://learn.microsoft.com/en-us/sql/t-sql/functions/"


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("tsql-arity") / "app" / "local-data")
    e = local.engine(local.create("Arity")["id"])
    e.execute("CREATE TABLE dbo.t (m DECIMAL(10, 2), i INT, s VARCHAR(20))", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (123.45, 1, 'a')", dialect="tsql")
    return e


def _one(engine, sql: str) -> object:
    if not sql.upper().startswith(("SELECT", "WITH")):
        sql = f"SELECT {sql} AS v"
    return engine.execute(sql, dialect="tsql")["rows"][0][0]


def _says(engine, sql: str) -> str:
    with pytest.raises(Exception) as caught:  # noqa: PT011 - the message is what is checked
        _one(engine, sql)
    return str(caught.value)


@pytest.mark.parametrize(("sql", "says"), [
    # day-, month-, year-, eomonth-, floor-, square-, ceiling-transact-sql: each takes one value (EOMONTH an optional
    # month_to_add); these were AttributeErrors (DAY() ...) and a DuckDB parser error (CEILING(1, 2, 3))
    ("DAY()", "Msg 174, Level 16: The day function requires 1 argument(s)."),
    ("MONTH()", "Msg 174, Level 16: The month function requires 1 argument(s)."),
    ("YEAR()", "Msg 174, Level 16: The year function requires 1 argument(s)."),
    ("EOMONTH()", "Msg 189, Level 16: The eomonth function requires 1 to 2 arguments."),
    ("FLOOR()", "Msg 174, Level 16: The floor function requires 1 argument(s)."),
    ("SQUARE()", "Msg 174, Level 16: The square function requires 1 argument(s)."),
    ("CEILING(1, 2, 3)", "Msg 174, Level 16: The ceiling function requires 1 argument(s)."),
    # len-, round-, isnull-, concat-, getdate-, newid-transact-sql: these answered where SQL Server stops
    ("LEN('a', 'b')", "Msg 174, Level 16: The len function requires 1 argument(s)."),
    ("ROUND(1)", "Msg 189, Level 16: The round function requires 2 to 3 arguments."),
    ("ROUND(1, 1, 1, 1)", "Msg 189, Level 16: The round function requires 2 to 3 arguments."),
    ("ISNULL(1)", "Msg 174, Level 16: The isnull function requires 2 argument(s)."),
    ("CONCAT('a')", "Msg 189, Level 16: The concat function requires 2 to 254 arguments."),
    ("CONCAT_WS(',', 'a')", "Msg 189, Level 16: The concat_ws function requires 3 to 254 arguments."),
    ("GETDATE(1)", "Msg 174, Level 16: The getdate function requires 0 argument(s)."),
    ("NEWID(1)", "Msg 174, Level 16: The newid function requires 0 argument(s)."),
    ("IIF(1 = 1, 2)", "Msg 174, Level 16: The iif function requires 3 argument(s)."),
    ("DATEADD(day, 1)", "Msg 174, Level 16: The dateadd function requires 3 argument(s)."),
    ("DATEFROMPARTS(2024, 1)", "Msg 174, Level 16: The datefromparts function requires 3 argument(s)."),
    ("LTRIM('a', ' ', 1)", "Msg 189, Level 16: The ltrim function requires 1 to 2 arguments."),
    ("SELECT s FROM dbo.t CROSS APPLY STRING_SPLIT(s, ',', 1, 2) AS x",
     "Msg 189, Level 16: The string_split function requires 2 to 3 arguments."),
    # select-over-clause-transact-sql: a ranking or offset function takes OVER
    ("RANK()", "The function 'RANK' must have an OVER clause."),
    ("SELECT LAG(i) FROM dbo.t", "The function 'LAG' must have an OVER clause."),
])
def test_tsqls_own_error_for_a_wrong_number_of_arguments(engine, sql: str, says: str) -> None:
    assert _says(engine, sql) == says


@pytest.mark.parametrize(("sql", "says"), [
    ("GROUPING_ID()", "GROUPING_ID takes at least 1 argument"),  # grouping-id-transact-sql
    ("TRY_CAST('a', 'b')", "TRY_CAST takes one value and the type"),  # was a DuckDB catalog error
    ("CAST('a', 'b')", "CAST takes one value and the type"),
    ("CURRENT_DATE(1)", "CURRENT_DATE takes no brackets"),
    ("GROUPING(1)", "GROUPING is used only in a query with GROUP BY"),  # grouping-transact-sql
    ("SELECT GROUPING_ID(i) FROM dbo.t", "GROUPING_ID is used only in a query with GROUP BY"),
    ("SELECT PERCENTILE_CONT(0.5) FROM dbo.t", "PERCENTILE_CONT takes WITHIN GROUP"),  # percentile-cont-transact-sql
    ("ABS(CAST('2024-01-02' AS DATE))", "ABS takes a number, and this is a date"),
    ("SQRT(CAST('2024-01-02' AS DATE))", "SQRT takes a number, and this is a date"),
    ("LEFT('abc', CAST('2024-01-02' AS DATE))", "LEFT takes a whole number there, and this is a date"),
    ("SELECT SUM(s) FROM dbo.t", "SUM takes numbers, and this is text"),
    ("SELECT AVG(s) FROM dbo.t", "AVG takes numbers, and this is text"),
    ("LOWER(CAST('2024-01-02' AS DATETIME2))", "LOWER takes text, and T-SQL writes a datetime2"),
    # string-agg-transact-sql: the separator is "literal or variable"
    ("SELECT STRING_AGG(s, CAST(NEWID() AS VARCHAR(40))) FROM dbo.t", "STRING_AGG's separator has to be text"),
    ("SELECT STRING_AGG(s, MAX(s) OVER ()) FROM dbo.t", "STRING_AGG's separator has to be text"),
    ("SELECT STRING_AGG(s, (SELECT ', ')) FROM dbo.t", "STRING_AGG's separator has to be text"),
    ("SELECT STRING_AGG(s, CHAR(-(-44))) FROM dbo.t", "STRING_AGG's separator has to be text"),
    ("SELECT ROUND(m, -ABS(1)) FROM dbo.t", "ROUND of a decimal needs a length the local engine can work out"),
])
def test_refused_by_name(engine, sql: str, says: str) -> None:
    said = _says(engine, sql)
    assert says in said
    assert "Error:" not in said  # never DuckDB's own words


@pytest.mark.parametrize(("sql", "want"), [
    # data-type-conversion-database-engine: an int or a decimal converts to text implicitly, as CAST writes it
    ("LOWER(1)", "1"), ("UPPER(1.50)", "1.50"), ("REVERSE(12)", "21"), ("LTRIM(1)", "1"), ("RTRIM('a1', 1)", "a"),
    ("REPLACE(121, 1, 3)", "323"), ("LEFT(123, 2)", "12"), ("RIGHT(123, 2)", "23"), ("SUBSTRING(12345, 2, 2)", "23"),
    ("CHARINDEX(2, 123)", 2), ("STUFF(1234, 2, 1, 9)", "1934"), ("TRANSLATE(123, 1, 9)", "923"), ("ASCII(1)", 49),
    ("UNICODE(1)", 49), ("CONCAT_WS(1, 2, 3)", "213"), ("LOWER(CAST('2024-01-02' AS DATE))", "2024-01-02"),
    ("LOWER(CAST(1234567.0 AS FLOAT))", "1.23457e+006"), ("LOWER(CAST(1 AS BIT))", "1"),
    ("SELECT LOWER(i) FROM dbo.t", "1"), ("SELECT LOWER(m) FROM dbo.t", "123.45"),
    # text converts to an int for a length or a start, '' being 0 (cast-and-convert-transact-sql)
    ("LEFT('abc', '2')", "ab"), ("LEFT('abc', '')", ""),
    ("SELECT SUBSTRING('abcdef', c, c) FROM (VALUES ('2')) AS x(c)", "bc"),
    # abs-, ceiling-, floor-, round-transact-sql: text converts to a float
    ("ABS('-1.5')", 1.5), ("CEILING('1.2')", 2.0), ("FLOOR('1.7')", 1.0), ("ROUND('1.55', 1)", 1.6),
    # round-transact-sql: ROUND(748.58, -1) is 750.00; -(1) is -1, and the decimal keeps its type
    ("SELECT ROUND(m, -(1)) FROM dbo.t", "120.00"),
    # the calls with the right number of arguments still run
    ("SELECT ROW_NUMBER() OVER (ORDER BY i, m) FROM dbo.t", 1), ("SELECT LAG(i, 1, 0) OVER (ORDER BY i) FROM dbo.t", 0),
    ("SELECT FIRST_VALUE(i) IGNORE NULLS OVER (ORDER BY i) FROM dbo.t", 1),
    ("SELECT GROUPING_ID(i, s) FROM dbo.t GROUP BY i, s", 0), ("CAST(1 AS CHAR(2))", "1 "),
    ("SUBSTRING('abc', 2)", "bc"),  # substring-transact-sql: length is optional from SQL Server 2025
    ("SELECT COUNT(*) FROM dbo.t", 1), ("TRIM('a' FROM 'aba')", "b"),
])
def test_answers_as_tsql_does(engine, sql: str, want: object) -> None:
    got = _one(engine, sql)
    assert (str(got) if isinstance(want, str) else got) == want


@pytest.mark.parametrize(("sql", "says"), [
    ("ABS('a')", "Msg 8114, Level 16: Error converting data type varchar to float."),
    ("LEFT('abc', 'a')", "Conversion failed when converting the varchar value 'a' to data type int."),
    # language-elements/divide-transact-sql: -32768 / -1 doesn't fit a smallint (Msg 8115), nor -2^31 / -1 an int
    ("CAST(-32768 AS SMALLINT) / CAST(-1 AS SMALLINT)",
     "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type smallint."),
    ("CAST(-2147483648 AS INT) / CAST(-1 AS INT)",
     "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type int."),
    ("-CAST(-32768 AS SMALLINT)",
     "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type smallint."),
])
def test_tsqls_conversion_and_overflow_errors(engine, sql: str, says: str) -> None:
    assert says in _says(engine, sql)


def test_names_that_arent_calls_are_left_alone(engine) -> None:
    # A CTE, a table or an index named as a function is followed by its column list, not by arguments.
    assert _one(engine, "WITH Year (a, b) AS (SELECT 1, 2) SELECT a + b FROM Year") == 3
    engine.execute("CREATE TABLE dbo.Month (a INT, b INT)", dialect="tsql")
    engine.execute("INSERT INTO Month (a, b) VALUES (1, 2)", dialect="tsql")
    assert _one(engine, "SELECT COUNT(*) FROM dbo.Month") == 1
    assert _one(engine, "SELECT CAST(-128 AS SMALLINT) / CAST(-1 AS SMALLINT)") == 128


def test_the_numbers_are_sql_servers() -> None:
    assert tsql_errors.numbered("The left function requires 2 argument(s).").startswith("Msg 174, Level 16: ")
    assert tsql_errors.numbered("The round function requires 2 to 3 arguments.").startswith("Msg 189, Level 16: ")
    # the same division with % keeps DuckDB's words: the message doesn't say which it was
    assert tsql_errors.warehouse("Out of Range Error: Overflow in division of -32768 / -1",
                                 "SELECT a % b FROM t", "select") is None
    assert tsql_errors.warehouse("Out of Range Error: Overflow in division of -32768 / -1",
                                 "SELECT a // b, '%' FROM t", "select") == (
        "Arithmetic overflow error converting expression to data type smallint.")


def test_a_wrong_count_never_reaches_sqlglot() -> None:
    with pytest.raises(ValueError, match=r"^The day function requires 1 argument\(s\)\.$"):
        tsql.translate("SELECT DAY()")
