"""WR-274: T-SQL edges the mutation run of localdata/tsql.py (batch 223) found, each checked against T-SQL's documented
answer: a comparison against ALL or ANY of a subquery ignores trailing spaces; SELECT DISTINCT * and a UNION of
SELECT * over text say that 'a' and 'a ' stay apart here (WR-210); a literal count, start or length past a bigint
is T-SQL's overflow error, negated ones too; a whole number as a date is days after 1900-01-01; and a CONVERT
style from a column, or a date with a fraction of a day as a number, is refused by name."""
from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from fabric_desktop.localdata.tsql import translate

OVERFLOW = "Arithmetic overflow error converting expression to data type bigint"


def run(sql: str) -> list[tuple]:
    con = duckdb.connect()
    try:
        return con.execute(translate(sql)[0][0]).fetchall()
    finally:
        con.close()


def run_on_t(sql: str, columns: dict[str, str], rows: str) -> list[tuple]:
    con = duckdb.connect()
    try:
        con.execute("CREATE SCHEMA dbo")
        con.execute(f"CREATE TABLE dbo.t ({', '.join(f'{c} {k}' for c, k in columns.items())})")
        con.execute(f"INSERT INTO dbo.t VALUES {rows}")
        return con.execute(translate(sql, {"dbo": {"t": columns}})[0][0]).fetchall()
    finally:
        con.close()


def stops_with(sql: str, message: str) -> None:
    with pytest.raises((ValueError, duckdb.Error), match=message):
        run(sql)


# ---- ALL / ANY of a subquery: T-SQL compares text without its trailing spaces

TEXT = {"s": "VARCHAR(10)"}


@pytest.mark.parametrize(("sql", "rows"), [
    ("SELECT 1 WHERE 'a ' > ALL (SELECT s FROM dbo.t)", []),
    ("SELECT 1 WHERE 'a ' = ALL (SELECT s FROM dbo.t)", [(1,)]),
    ("SELECT 1 WHERE 'a ' <> ALL (SELECT s FROM dbo.t)", []),
    ("SELECT 1 WHERE 'a ' > ANY (SELECT s FROM dbo.t)", []),
    ("SELECT 1 WHERE 'a ' <= SOME (SELECT s FROM dbo.t)", [(1,)]),
    ("SELECT 1 WHERE 'a ' > ALL (SELECT s FROM dbo.t UNION ALL SELECT s FROM dbo.t)", []),
])
def test_a_comparison_against_all_or_any_of_a_subquery_ignores_trailing_spaces(sql, rows):
    assert run_on_t(sql, TEXT, "('a')") == rows


def test_all_of_a_subquery_of_numbers_is_still_compared_as_numbers():
    assert run_on_t("SELECT 1 WHERE 2 > ALL (SELECT n FROM dbo.t)", {"n": "INTEGER"}, "(1), (10)") == []
    assert run_on_t("SELECT 1 WHERE 20 > ALL (SELECT n FROM dbo.t)", {"n": "INTEGER"}, "(1), (10)") == [(1,)]


# ---- SELECT DISTINCT * and UNION of SELECT *: the grouping note (WR-210)

SCHEMA = {"dbo": {"t": {"s": "VARCHAR(20)", "n": "INTEGER"}, "u": {"n": "INTEGER"}}}


@pytest.mark.parametrize("sql", ["SELECT DISTINCT * FROM dbo.t", "SELECT DISTINCT t.* FROM dbo.t",
                                 "SELECT * FROM dbo.t UNION SELECT * FROM dbo.t",
                                 "SELECT * FROM dbo.t INTERSECT SELECT * FROM dbo.t"])
def test_distinct_over_a_star_with_a_text_column_has_the_grouping_note(sql):
    out, notes = translate(sql, SCHEMA)
    assert out == [sql]  # the query itself is unchanged: T-SQL shows either spelling
    assert len(notes) == 1 and notes[0].startswith("T-SQL counts text that differs only in trailing spaces")


@pytest.mark.parametrize("sql", ["SELECT DISTINCT * FROM dbo.u", "SELECT * FROM dbo.t UNION ALL SELECT * FROM dbo.t",
                                 "SELECT DISTINCT * FROM dbo.unknown"])
def test_a_star_without_text_or_without_grouping_has_no_note(sql):
    assert translate(sql, SCHEMA)[1] == []


# ---- a literal count, start or length past a bigint is T-SQL's overflow error

@pytest.mark.parametrize("sql", [
    "SELECT LEFT('abc', -10000000000000000000)",
    "SELECT LEFT('abc', -9223372036854775809)",  # a float would round it to -2**63, inside a bigint
    "SELECT RIGHT('abc', -9223372036854775809)",
    "SELECT LEFT('abc', 9223372036854775808)",
    "SELECT REPLICATE('a', -9223372036854775809)",
    "SELECT SUBSTRING('abc', -9223372036854775809, 2)",
    "SELECT SUBSTRING('abc', 9223372036854775808, 2)",
    "SELECT SUBSTRING('abc', 1, -9223372036854775809)",
])
def test_a_literal_past_a_bigint_is_t_sqls_bigint_overflow(sql):
    stops_with(sql, OVERFLOW)


def test_the_smallest_bigint_is_a_negative_length_not_an_overflow():
    stops_with("SELECT LEFT('abc', -9223372036854775808)", "Invalid length parameter passed to the LEFT function")
    assert run("SELECT LEFT('abc', 9223372036854775807)") == [("abc",)]
    assert run("SELECT REPLICATE('a', -9223372036854775808)") == [(None,)]


# ---- refused by name rather than answered wrongly

def test_a_convert_style_from_a_column_is_refused_rather_than_ignored():
    with pytest.raises(ValueError, match="CONVERT's style has to be a number written in the query"):
        translate("SELECT CONVERT(VARCHAR(30), CAST('2024-02-29' AS DATE), st) FROM dbo.t",
                  {"dbo": {"t": {"st": "INTEGER"}}})
    assert run("SELECT CONVERT(VARCHAR(30), CAST('2024-02-29' AS DATE), 101)") == [("02/29/2024",)]


# ---- a whole number as a date is that many days after 1900-01-01, a datetime, as T-SQL converts it

@pytest.mark.parametrize(("sql", "answer"), [
    ("SELECT YEAR(5)", 1900),
    ("SELECT MONTH(-1)", 12),  # 1899-12-31
    ("SELECT DAY(40)", 10),  # 1900-02-10
    ("SELECT DATEPART(year, 5)", 1900),
    ("SELECT DATEPART(weekday, 0)", 2),  # 1900-01-01 was a Monday; Sunday is 1
    ("SELECT DATENAME(month, 40)", "February"),
    ("SELECT DATENAME(weekday, 0)", "Monday"),
    ("SELECT EOMONTH(5)", dt.date(1900, 1, 31)),
    ("SELECT DATEADD(day, 1, 5)", dt.datetime(1900, 1, 7)),
    ("SELECT DATEDIFF(day, -1, 5)", 6),
    ("SELECT DATETRUNC(year, 400)", dt.datetime(1901, 1, 1)),
    ("SELECT DATEADD(day, DATEDIFF(day, 0, CAST('2024-02-29 13:00' AS DATETIME2)), 0)", dt.datetime(2024, 2, 29)),
])
def test_a_whole_number_as_a_date_is_days_after_1900(sql, answer):
    assert run(sql) == [(answer,)]


def test_a_number_as_a_date_past_a_datetime_is_t_sqls_overflow():
    stops_with("SELECT YEAR(2958464)", "Arithmetic overflow error converting expression to data type datetime")
    stops_with("SELECT YEAR(-53691)", "Arithmetic overflow error converting expression to data type datetime")
    assert run("SELECT YEAR(2958463)") == [(9999,)]
    assert run("SELECT YEAR(-53690)") == [(1753,)]


@pytest.mark.parametrize("sql", ["SELECT DAY(1.5)", "SELECT DATEPART(hour, -0.25)", "SELECT DATENAME(year, 1e1)"])
def test_a_number_with_a_fraction_of_a_day_as_a_date_is_refused_by_name(sql):
    with pytest.raises(ValueError, match=r"of a number with a fraction of a day .* isn't carried yet"):
        translate(sql)


def test_a_number_cast_to_a_number_type_is_left_as_written():
    assert translate("SELECT CAST(5 AS INT) + 1")[0] == ["SELECT CAST(5 AS INT) + 1"]
