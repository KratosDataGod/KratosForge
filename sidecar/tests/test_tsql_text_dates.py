"""WR-275: text and numbers read as dates the way T-SQL reads them (DATEFORMAT mdy, us_english, as a warehouse runs).

Each expected answer is from Microsoft Learn:

- data-types/date-transact-sql, "Supported string literal formats for date": "A six-digit or eight-digit string is
  always interpreted as ymd"; the numeric `[m]m/dd/[yy]yy` (mdy), with /, - or . between the parts; and the
  alphabetical `mon [dd][,] yyyy` and `[dd] mon[,] yyyy` forms. A two-digit year takes the two digit year cutoff,
  2049 (configure-the-two-digit-year-cutoff-server-configuration-option): 49 is 2049 and 50 is 1950.
- language-elements/add-transact-sql: "can also add a number, in days, to a date"; example B, @startdate + 1.25 is
  1900-01-11 06:00:00.000 for @startdate 1900-01-10. A number cast to datetime is that many days after 1900-01-01
  (functions/cast-and-convert-transact-sql), and int to date or datetime2 is "Explicit conversion from data type
  int to date is not allowed."
- datetime2 + int is "Operand type clash: datetime2 is incompatible with int" (data-types/datetime2-transact-sql
  lists no arithmetic with numbers, unlike datetime).

DuckDB reads only ISO text, so these stopped with its "invalid date field format" or came back NULL from TRY_CAST.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop.localdata import LocalData, tsql


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("text-dates") / "app" / "local-data")
    e = local.engine(local.create("TextDates")["id"])
    e.execute("CREATE TABLE dbo.t (k INT, d DATE, ts DATETIME2(6), s VARCHAR(40))", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, '2024-01-15', '2024-01-15 10:30:00', '20240115'), "
              "(2, '2024-02-29', '2024-02-29 00:00:00', '02/29/2024 13:04'), "
              "(3, '2024-03-01', '2024-03-01 08:00:00', 'Mar 1, 2024'), "
              "(4, '2049-12-31', '2049-12-31 00:00:00', '491231'), "
              "(5, '1950-01-01', '1950-01-01 00:00:00', '1 January 1950 12:00 AM'), "
              "(6, NULL, NULL, 'abc'), (7, NULL, NULL, '20240230'), (8, NULL, NULL, '')", dialect="tsql")
    return e


def one(engine, sql: str):
    return engine.execute(f"SELECT {sql} AS v", dialect="tsql")["rows"][0][0]


D = dt.date
T = dt.datetime

# (T-SQL expression, T-SQL's answer)
LITERALS = [
    ("TRY_CONVERT(DATE, '20240115')", D(2024, 1, 15)),
    ("TRY_CAST('20240115' AS DATE)", D(2024, 1, 15)),
    ("CAST('20240115' AS DATE)", D(2024, 1, 15)),
    ("CONVERT(DATE, '20240115')", D(2024, 1, 15)),
    ("TRY_CONVERT(DATETIME, '20240115 10:30')", T(2024, 1, 15, 10, 30)),
    ("CAST('240115' AS DATE)", D(2024, 1, 15)),
    ("CAST('490101' AS DATE)", D(2049, 1, 1)),
    ("CAST('500101' AS DATE)", D(1950, 1, 1)),
    ("DATEADD(day, 1, '20240131')", T(2024, 2, 1)),
    ("DATEADD(hour, 1, '20240131 10:30')", T(2024, 1, 31, 11, 30)),
    ("DATEDIFF(day, '20240101', '20240201')", 31),
    ("YEAR('20240115')", 2024),
    ("MONTH('01/15/2024')", 1),
    ("DATEPART(day, 'Jan 15 2024')", 15),
    ("EOMONTH('20240210')", D(2024, 2, 29)),
    ("CAST('01/15/2024' AS DATE)", D(2024, 1, 15)),
    ("CAST('1/5/2024' AS DATE)", D(2024, 1, 5)),
    ("CAST('1-5-2024' AS DATE)", D(2024, 1, 5)),
    ("CAST('1.5.2024' AS DATE)", D(2024, 1, 5)),
    ("CAST('01/15/49' AS DATE)", D(2049, 1, 15)),
    ("CAST('01/15/50' AS DATE)", D(1950, 1, 15)),
    ("CAST('2024/01/15' AS DATE)", D(2024, 1, 15)),
    ("CAST('Jan 15 2024' AS DATE)", D(2024, 1, 15)),
    ("TRY_CAST('Jan 15 2024' AS DATE)", D(2024, 1, 15)),
    ("CAST('Jan 15, 2024' AS DATE)", D(2024, 1, 15)),
    ("CAST('15 Jan 2024' AS DATE)", D(2024, 1, 15)),
    ("CAST('January 15, 2024' AS DATE)", D(2024, 1, 15)),
    ("CAST('jan 15 2024 3:04PM' AS DATETIME2(0))", T(2024, 1, 15, 15, 4)),
    ("CAST('Jan 15 2024 12:00AM' AS DATETIME2(0))", T(2024, 1, 15, 0, 0)),
    ("CAST('01/15/2024 13:04:05.5' AS DATETIME2(1))", T(2024, 1, 15, 13, 4, 5, 500000)),
    ("CAST('10:30' AS DATETIME)", T(1900, 1, 1, 10, 30)),
    ("CAST('' AS DATE)", D(1900, 1, 1)),
    ("TRY_CAST('20240230' AS DATE)", None),  # no 30 February: not a date
    ("TRY_CAST('13/01/2024' AS DATE)", None),  # mdy: no month 13
    ("TRY_CAST('20240115 25:00' AS DATETIME)", None),
    ("TRY_CAST('abc' AS DATE)", None),
    ("TRY_CAST('epoch' AS DATE)", None),  # DuckDB's word for 1970-01-01; not a date in T-SQL
    # CONVERT with a style reads as the style says, as before
    ("CONVERT(DATE, '15/01/2024', 103)", D(2024, 1, 15)),
    ("CONVERT(DATE, '01/15/2024', 101)", D(2024, 1, 15)),
    ("CONVERT(DATE, '20240115', 112)", D(2024, 1, 15)),
    # + (Addition): a number of days to a datetime, a fraction being part of a day
    ("CAST('1900-01-10' AS DATETIME) + 1.25", T(1900, 1, 11, 6, 0)),
    ("CAST('2024-01-31' AS DATETIME) + 1", T(2024, 2, 1)),
    ("1 + CAST('2024-01-31' AS DATETIME)", T(2024, 2, 1)),
    ("CAST('2024-01-31' AS DATETIME) - 0.5", T(2024, 1, 30, 12, 0)),
    ("CAST('2024-01-31 10:00' AS SMALLDATETIME) + 1", T(2024, 2, 1, 10, 0)),
    ("CAST(45000 AS DATETIME)", T(2023, 3, 17)),
    ("CONVERT(DATETIME, 45000)", T(2023, 3, 17)),
    ("CAST(1.25 AS DATETIME)", T(1900, 1, 2, 6, 0)),
    ("CAST(-1 AS DATETIME)", T(1899, 12, 31)),
    ("CAST(45000 AS SMALLDATETIME)", T(2023, 3, 17)),
    ("TRY_CAST(3000000 AS DATETIME)", None),  # past 9999-12-31
]


@pytest.mark.parametrize(("expression", "want"), LITERALS)
def test_text_and_numbers_are_dates_as_tsql_reads_them(engine, expression: str, want) -> None:
    got = one(engine, expression)
    if isinstance(want, dt.datetime) and not isinstance(got, dt.datetime):
        got = dt.datetime.fromisoformat(str(got))
    elif isinstance(want, dt.date) and not isinstance(want, dt.datetime) and not isinstance(got, dt.date):
        got = dt.date.fromisoformat(str(got))
    assert got == want


@pytest.mark.parametrize("expression", ["CAST('20240230' AS DATE)", "CONVERT(DATE, '13/01/2024')",
                                        "CAST('20240115 25:00' AS DATETIME2(0))"])
def test_a_shape_with_a_date_that_isnt_there_is_tsqls_conversion_error(engine, expression: str) -> None:
    with pytest.raises(Exception, match="Conversion failed when converting date and/or time from character string"):
        one(engine, expression)


@pytest.mark.parametrize("expression", ["CAST('15-Jan-2024' AS DATE)", "TRY_CAST('2024.01' AS DATE)",
                                        "TRY_CONVERT(DATE, 'Jan 2024')", "CAST('10:30:00:123' AS DATETIME)"])
def test_another_shape_is_refused_by_name(engine, expression: str) -> None:
    # Not guessed: a TRY_ form gives NULL only for text that isn't a date.
    with pytest.raises(ValueError, match="T-SQL's reading of text in that shape isn't carried yet.*'YYYY-MM-DD'"):
        one(engine, expression)


def test_text_from_a_column_is_read_as_tsql_reads_it(engine) -> None:
    rows = engine.execute("SELECT k, TRY_CAST(s AS DATE), TRY_CONVERT(DATETIME2(0), s) FROM dbo.t WHERE k <> 6 "
                          "ORDER BY k", dialect="tsql")["rows"]
    got = [(k, str(a) if a is not None else None, str(b) if b is not None else None) for k, a, b in rows]
    assert got == [(1, "2024-01-15", "2024-01-15 00:00:00"), (2, "2024-02-29", "2024-02-29 13:04:00"),
                   (3, "2024-03-01", "2024-03-01 00:00:00"), (4, "2049-12-31", "2049-12-31 00:00:00"),
                   (5, "1950-01-01", "1950-01-01 00:00:00"), (7, None, None), (8, "1900-01-01", "1900-01-01 00:00:00")]
    # 'abc' has no digit: not a date, so NULL with TRY_CAST.
    assert engine.execute("SELECT TRY_CAST(s AS DATE) FROM dbo.t WHERE k = 6", dialect="tsql")["rows"] == [[None]]
    rows = engine.execute("SELECT YEAR(s), DATEADD(day, 1, s), DATEDIFF(day, s, '20240201') FROM dbo.t WHERE k = 2",
                          dialect="tsql")["rows"]
    assert [(r[0], str(r[1]), r[2]) for r in rows] == [(2024, "2024-03-01 13:04:00", -28)]


def test_a_column_compared_with_text(engine) -> None:
    def keys(where: str) -> list[int]:
        return [r[0] for r in engine.execute(f"SELECT k FROM dbo.t WHERE {where} ORDER BY k", dialect="tsql")["rows"]]
    assert keys("d >= '20240101' AND d < '20250101'") == [1, 2, 3]
    assert keys("d = '01/15/2024'") == [1]
    assert keys("ts >= '20240115 10:30' AND ts < 'Mar 1 2024'") == [1, 2]
    assert keys("d BETWEEN '1/1/2024' AND 'March 1, 2024'") == [1, 2, 3]
    assert keys("d IN ('20240115', '02/29/2024')") == [1, 2]
    assert keys("k < 6 AND d = s") == [1, 2, 3, 4, 5]  # s read as a date (date outranks text), its time dropped


@pytest.mark.parametrize(("expression", "words"), [
    ("ts + 1", "Operand type clash: datetime2 is incompatible with int"),
    ("d + 1", "Operand type clash: date is incompatible with int"),
    ("ts - 1.5", "Operand type clash: datetime2 is incompatible with numeric"),
    ("SYSDATETIME() + 1", "Operand type clash: datetime2 is incompatible with int"),
    ("CAST(45000 AS DATE)", "Explicit conversion from data type int to date is not allowed"),
    ("CAST(1.5 AS DATETIME2(0))", "Explicit conversion from data type numeric to datetime2 is not allowed"),
    ("TRY_CAST(k AS DATE)", "Explicit conversion from data type int to date is not allowed"),
    ("CAST(3000000 AS DATETIME)", "Arithmetic overflow error converting expression to data type datetime"),
])
def test_a_number_with_another_date_type_is_tsqls_error(engine, expression: str, words: str) -> None:
    with pytest.raises(ValueError, match=words):
        engine.execute(f"SELECT {expression} FROM dbo.t", dialect="tsql")


def test_a_number_with_a_datetime_from_a_query(engine) -> None:
    rows = engine.execute("SELECT x + 1.25, x - 1, CAST(k + 44999 AS DATETIME) FROM (SELECT k, CAST('1900-01-10' AS "
                          "DATETIME) AS x FROM dbo.t WHERE k = 1) AS q", dialect="tsql")["rows"]
    assert [tuple(str(v) for v in r) for r in rows] == [("1900-01-11 06:00:00", "1900-01-09 00:00:00",
                                                          "2023-03-17 00:00:00")]
    # GETDATE() is a datetime: + 1 is a day later.
    assert one(engine, "DATEDIFF(day, GETDATE(), GETDATE() + 1)") == 1


def test_column_text_is_read_once() -> None:
    # The rewrite binds the value in a lambda: the text of a nested call appears once.
    cols = {"dbo": {"t": {"s": "VARCHAR"}}}
    sql = tsql.translate("SELECT CAST(UPPER(LOWER(s)) AS DATE) FROM dbo.t", cols)[0][0]
    assert sql.count("LOWER(s)") == 1
