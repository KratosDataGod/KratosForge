"""WR-270: a NULL in a T-SQL date or text function's arguments, on the local engine.

T-SQL's answer (Microsoft's T-SQL reference, the page named by each test) is the oracle: a NULL argument gives NULL,
whether it is an untyped NULL, a NULL cast to a type, or a column holding NULL. Here DATEPART(hour, NULL) and
DATEADD(day, 1, NULL) were DuckDB binder errors, DATEDIFF of a NULL date was T-SQL's overflow error, LEFT and RIGHT
with a NULL length gave the whole text, and DATENAME gave the month's short name for hour. The seeded family is in
test_tsql_rules_differential.py (NULL ...); these are the cases it can't write as one expression.
"""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop.localdata import LocalData, tsql

DOCS = "https://learn.microsoft.com/en-us/sql/t-sql/functions/"


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("tsql-nulls") / "app" / "local-data")
    e = local.engine(local.create("Nulls")["id"])
    e.execute("CREATE TABLE dbo.n (d DATE, dt DATETIME2(6), s VARCHAR(20), i INT, b BIGINT, m DECIMAL(10, 2))",
              dialect="tsql")
    e.execute("INSERT INTO dbo.n VALUES (NULL, NULL, NULL, NULL, NULL, NULL)", dialect="tsql")
    e.execute("INSERT INTO dbo.n VALUES ('2024-01-02', '2024-01-02 13:04:05', '2024-03-04 05:06:07', 2, 2, 1.55)",
              dialect="tsql")
    return e


def _one(engine, expression: str, table: bool = False) -> object:
    where = " FROM dbo.n WHERE i IS NULL" if table else ""
    return engine.execute(f"SELECT {expression} AS v{where}", dialect="tsql")["rows"][0][0]


@pytest.mark.parametrize("expression", [
    # datepart-transact-sql, datename-transact-sql: the known case
    "DATEPART(hour, NULL)", "DATENAME(hour, NULL)", "DATEPART(week, NULL)", "DATEPART(weekday, NULL)",
    "DATEPART(quarter, NULL)", "DATEPART(microsecond, NULL)", "DATENAME(month, NULL)", "DATENAME(dayofyear, NULL)",
    "DATEPART(hour, CAST(NULL AS VARCHAR(10)))", "DATEPART(weekday, CAST(NULL AS NVARCHAR(10)))",
    # dateadd-transact-sql, datetrunc-transact-sql
    "DATEADD(day, 1, NULL)", "DATEADD(hour, 1, NULL)", "DATEADD(month, 1, CAST(NULL AS VARCHAR(10)))",
    "DATETRUNC(month, NULL)", "DATETRUNC(week, NULL)", "DATETRUNC(hour, CAST(NULL AS NVARCHAR(10)))",
    # datediff-transact-sql
    "DATEDIFF(day, NULL, '2024-01-01')", "DATEDIFF(hour, '2024-01-01', CAST(NULL AS DATE))",
    "DATEDIFF(month, CAST(NULL AS DATETIME2), CAST(NULL AS DATETIME2))",
    # cast-and-convert-transact-sql: a style with a NULL value
    "CONVERT(VARCHAR(30), NULL, 126)", "CONVERT(VARCHAR(30), NULL, 121)", "CONVERT(DATE, NULL, 112)",
    "TRY_CONVERT(DATETIME2, NULL, 126)",
    # left-transact-sql, right-transact-sql
    "LEFT('abc', NULL)", "RIGHT('abc', NULL)", "LEFT('abc', CAST(NULL AS BIGINT))", "RIGHT(N'abc', CAST(NULL AS INT))",
    # round-transact-sql: length is an int, and a bigint converts to one
    "ROUND(1.55, CAST(NULL AS BIGINT))", "ROUND(1.55, CAST(NULL AS BIGINT), 1)",
])
def test_a_null_argument_gives_null(engine, expression: str) -> None:
    assert _one(engine, expression) is None


@pytest.mark.parametrize("expression", [
    "DATEPART(hour, d)", "DATEPART(weekday, dt)", "DATENAME(hour, dt)", "DATENAME(month, d)",
    "DATEADD(day, 1, d)", "DATEADD(day, i, '2024-01-01')", "DATEADD(hour, b, dt)", "DATETRUNC(month, dt)",
    "DATEDIFF(day, d, dt)", "DATEDIFF(day, '2024-01-01', d)", "DATEDIFF_BIG(second, dt, '2024-01-01')",
    "EOMONTH(d)", "EOMONTH('2024-01-02', i)", "DATEFROMPARTS(2024, i, 1)",
    "DATEPART(hour, s)", "DATEADD(day, 1, s)", "DATETRUNC(month, s)", "DATENAME(hour, s)",
    "CONVERT(VARCHAR(30), dt, 126)", "CONVERT(VARCHAR(30), d, 112)", "FORMAT(m, 'N2')", "FORMAT(d, 'yyyy-MM-dd')",
    "LEFT('abc', i)", "RIGHT('abc', b)", "LEFT(s, 2)", "SUBSTRING('abc', i, 2)", "CHARINDEX('b', s)",
    "REPLICATE('ab', b)", "STUFF(s, 1, 1, 'x')", "TRANSLATE(s, 'ab', 'xy')", "ROUND(m, 1)", "ROUND(i, b)",
])
def test_a_null_column_gives_null(engine, expression: str) -> None:
    assert _one(engine, expression, table=True) is None


def test_the_same_calls_still_answer_for_values(engine) -> None:
    rows = engine.execute("SELECT DATEDIFF(day, d, dt), LEFT('abcdef', i), RIGHT('abcdef', b), DATEPART(hour, s), "
                          "DATEADD(day, 1, s), DATETRUNC(month, s), ROUND(1.55, CAST(1 AS BIGINT)), ROUND(i, b) "
                          "FROM dbo.n WHERE i IS NOT NULL", dialect="tsql")["rows"]
    got = rows[0]
    assert got[:4] == [0, "ab", "ef", 5]
    assert str(got[4]).startswith("2024-03-05 05:06:07") and str(got[5]).startswith("2024-03-01")
    assert str(got[6]) == "1.60" and got[7] == 2


def test_stuff_with_a_null_insert_still_deletes(engine) -> None:
    # stuff-transact-sql: a NULL replaceWith_expression deletes the characters without inserting anything.
    assert _one(engine, "STUFF('abc', 2, 1, NULL)") == "ac"


def test_isnull_gives_the_other_argument(engine) -> None:
    # isnull-transact-sql: the replacement, converted to the first argument's type.
    assert _one(engine, "ISNULL(i, 7)", table=True) == 7
    assert _one(engine, "ISNULL(s, 'b')", table=True) == "b"
    assert _one(engine, "ISNULL(NULL, 'b')") == "b"


def test_datediff_past_an_int_is_still_the_overflow_error(engine) -> None:
    with pytest.raises(Exception, match="datediff function resulted in an overflow"):
        _one(engine, "DATEDIFF(millisecond, '2000-01-01', '2024-03-01')")


def test_datediff_groups_as_before(engine) -> None:
    rows = engine.execute("SELECT DATEDIFF(day, d, '2024-01-03') AS k, COUNT(*) AS c FROM dbo.n "
                          "GROUP BY DATEDIFF(day, d, '2024-01-03') ORDER BY k", dialect="tsql")["rows"]
    assert rows == [[None, 1], [1, 1]]  # T-SQL sorts NULL first


def test_a_negative_length_column_is_still_the_error(engine) -> None:
    with pytest.raises(Exception, match="Invalid length parameter passed to the LEFT function"):
        engine.execute("SELECT LEFT('abc', -i) FROM dbo.n WHERE i IS NOT NULL", dialect="tsql")


@pytest.mark.parametrize(("part", "want"), [
    # datename-transact-sql: every part but month and weekday is its number as text, unpadded.
    ("hour", "13"), ("minute", "4"), ("second", "5"), ("day", "2"), ("dayofyear", "2"), ("quarter", "1"),
    ("week", "1"), ("iso_week", "1"), ("year", "2024"), ("millisecond", "0"), ("microsecond", "0"),
    ("month", "January"), ("weekday", "Tuesday"), ("dw", "Tuesday"), ("hh", "13"), ("y", "2"), ("w", "Tuesday"),
])
def test_datename_writes_the_part_as_tsql_does(engine, part: str, want: str) -> None:
    assert _one(engine, f"DATENAME({part}, CAST('2024-01-02 13:04:05' AS DATETIME2))") == want


def test_round_of_a_decimal_to_a_column_length_is_refused_by_name(engine) -> None:
    with pytest.raises(ValueError, match="ROUND of a decimal needs its length written in the query.*Nothing ran"):
        engine.execute("SELECT ROUND(m, i) FROM dbo.n", dialect="tsql")


def test_string_agg_with_a_column_separator_is_refused_by_name(engine) -> None:
    # string-agg-transact-sql: the separator "can be literal or variable".
    with pytest.raises(ValueError, match="STRING_AGG's separator has to be text written in the query.*Nothing ran"):
        engine.execute("SELECT STRING_AGG(s, s) FROM dbo.n", dialect="tsql")


@pytest.mark.parametrize(("separator", "want"), [
    # string-agg-transact-sql: a separator that doesn't change from row to row runs, however it is written.
    ("CHAR(13) + CHAR(10)", "a\r\nb"), ("CHAR(44)", "a,b"), ("CAST('|' AS VARCHAR(1))", "a|b"), ("', ' + ''", "a, b"),
])
def test_string_agg_with_a_separator_worked_out_from_text_runs(separator: str, want: str) -> None:
    con = duckdb.connect()
    try:
        out = tsql.translate(f"SELECT STRING_AGG(s, {separator}) WITHIN GROUP (ORDER BY s) "
                             "FROM (VALUES ('b'), ('a')) AS v(s)")[0][0]
        assert con.execute(out).fetchall() == [(want,)]
    finally:
        con.close()


@pytest.mark.parametrize("separator", ["(SELECT MAX(s) FROM dbo.n)", "CASE WHEN RAND() < 2 THEN ',' END",
                                       "LEFT(CAST(NEWID() AS VARCHAR(36)), 1)", "s + ','"])
def test_string_agg_with_a_separator_that_changes_by_row_is_refused_by_name(engine, separator: str) -> None:
    with pytest.raises(ValueError, match="STRING_AGG's separator has to be text written in the query"):
        engine.execute(f"SELECT STRING_AGG(s, {separator}) FROM dbo.n", dialect="tsql")


@pytest.mark.parametrize(("expression", "want"), [
    ("ROUND(1.55, 1 + 0)", "1.60"), ("ROUND(1.55, LEN('a'))", "1.60"),
    ("ROUND(1.55, CAST(1 AS BIGINT) + 0)", "1.60"),
])
def test_round_of_a_decimal_to_a_length_worked_out_from_numbers_runs(engine, expression: str, want: str) -> None:
    assert _one(engine, expression) == want


@pytest.mark.parametrize("length", ["(SELECT MAX(i) FROM dbo.n)", "CAST(RAND() * 2 AS INT)"])
def test_round_of_a_decimal_to_a_length_that_changes_by_row_is_refused_by_name(engine, length: str) -> None:
    with pytest.raises(ValueError, match="ROUND of a decimal needs its length written in the query"):
        engine.execute(f"SELECT ROUND(m, {length}) FROM dbo.n", dialect="tsql")


@pytest.mark.parametrize(("expression", "kind"), [
    # cast-and-convert-transact-sql: CONVERT of NULL is NULL of the type asked for, whatever the style.
    ("CONVERT(DATE, NULL, 112)", "DATE"), ("TRY_CONVERT(DATETIME2, NULL, 126)", "TIMESTAMP"),
    ("CONVERT(VARCHAR(30), NULL, 126)", "VARCHAR"),
])
def test_convert_of_null_with_a_style_keeps_the_type(expression: str, kind: str) -> None:
    out = tsql.translate(f"SELECT {expression}")[0][0]
    assert duckdb.sql(f"SELECT typeof(x) FROM ({out}) AS t(x)").fetchall() == [(kind,)]


def _size(expression: str) -> int:
    return len(tsql.translate(f"SELECT {expression} FROM dbo.n")[0][-1])


@pytest.mark.parametrize("wrap", [
    "DATEADD(day, DATEDIFF(day, {}, '2024-01-01'), '2024-01-01')",
    "LEFT({}, i)",
    "DATENAME(hour, DATEADD(day, 1, {}))",
])
def test_nested_calls_grow_the_sql_linearly(wrap: str) -> None:
    sizes = []
    for depth in range(2, 7):  # from 2: the first call may bind its argument where a column needed no binding
        expression = "dt"
        for _ in range(depth):
            expression = wrap.format(expression)
        sizes.append(_size(expression))
    steps = [b - a for a, b in zip(sizes, sizes[1:], strict=False)]
    assert max(steps) <= 1.2 * min(steps), sizes
