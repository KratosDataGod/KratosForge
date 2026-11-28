"""Silent differences in local T-SQL, round 4: CHAR(n) in changes, and dates read from text.

- WR-725: an UPDATE, DELETE or MERGE reads a CHAR(n) column padded to n, as a SELECT does (WR-723), in SET's
  values, WHERE, ON and a MERGE's source, on a database table and in a Delta table's MERGE from one.
- WR-726: ``UPDATE t SET t.v = ...`` names the column with its table, as T-SQL allows; DuckDB stopped.
- WR-727, WR-728: text read as a date takes the forms SQL Server reads under us_english (data-types/
  date-transact-sql): unseparated 'yyyymmdd', 'yymmdd' and 'yyyy', m/d/y and m-d-y, two-digit years by the
  cutoff 2049, and the alphabetical forms written in the query. Before, they stopped with 241 or were NULL with
  TRY_CAST, and '24-01-02' was the year 24.
- WR-729: text written as DATEADD's date is a datetime (functions/dateadd-transact-sql), time included; it was
  read as a date, so DATEADD(hour, 1, '2024-01-01 10:00') was 01:00.
- WR-730: DATETRUNC of a time runs (functions/datetrunc-transact-sql), a date part there is error 9810, and a part
  finer than the value's digits is refused by name; DATEADD of a date part to a time is refused by name (it was the
  time unchanged); TIMEFROMPARTS is a time(precision), so its text has that many digits.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "local-data")
    e = local.engine(local.create("Silent sweep 4")["id"])
    e.execute("CREATE TABLE dbo.c (id INT, c CHAR(4), v VARCHAR(10))", dialect="tsql")
    e.execute("INSERT INTO dbo.c VALUES (1, 'x', NULL), (2, 'é', NULL), (3, NULL, NULL)", dialect="tsql")
    e.execute("CREATE TABLE dbo.w (id INT, v VARCHAR(12), c2 CHAR(6))", dialect="tsql")
    e.execute("INSERT INTO dbo.w VALUES (1, NULL, 'q'), (2, NULL, 'r')", dialect="tsql")
    e.execute("CREATE TABLE dbo.t (id INT, s VARCHAR(40))", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, '20240102'), (2, '01/02/2024'), (3, '2024-01-02 10:00'), "
              "(4, '1/2/49 10:30'), (5, '2024'), (6, NULL), (7, '240102'), (8, '2024-01-02T09:00:00')", dialect="tsql")
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def one(engine, sql):
    return rows(engine, sql)[0][0]


def run(engine, sql):
    engine.execute(sql, dialect="tsql")


# ---------- WR-725: CHAR(n) padded where a change reads it

def test_update_reads_its_own_char_column_padded(engine):
    run(engine, "UPDATE dbo.c SET v = c + '|' WHERE c + '|' = 'x   |'")
    assert rows(engine, "SELECT id, v FROM dbo.c ORDER BY id") == [[1, "x   |"], [2, None], [3, None]]


def test_update_reads_another_tables_char_column_padded(engine):
    run(engine, "UPDATE dbo.w SET v = (SELECT c FROM dbo.c WHERE id = 1) + '|' WHERE id = 1")
    run(engine, "UPDATE w SET v = x.c + '|' FROM dbo.w AS w JOIN dbo.c AS x ON x.id = w.id WHERE w.id = 2")
    assert rows(engine, "SELECT id, v FROM dbo.w ORDER BY id") == [[1, "x   |"], [2, "é  |"]]


def test_a_padded_value_too_long_for_its_column_is_truncation(engine):
    # 'x   ' + 'z' is five bytes, which char(4) can't hold
    with pytest.raises(Exception, match="String or binary data would be truncated"):
        run(engine, "UPDATE dbo.c SET c = c + 'z' WHERE id = 1")


def test_delete_reads_its_char_column_padded(engine):
    run(engine, "DELETE FROM dbo.w WHERE c2 + '|' = 'r     |'")
    assert rows(engine, "SELECT id FROM dbo.w") == [[1]]


def test_merge_reads_its_source_and_table_padded(engine):
    run(engine, "MERGE dbo.w AS t USING dbo.c AS s ON t.id = s.id "
                "WHEN MATCHED AND DATALENGTH(t.c2) = 6 THEN UPDATE SET v = CONCAT(s.c, '|') "
                "WHEN NOT MATCHED THEN INSERT (id, v, c2) VALUES (s.id, CONCAT(s.c, '#'), s.c);")
    assert rows(engine, "SELECT id, v, DATALENGTH(c2) FROM dbo.w ORDER BY id") == [
        [1, "x   |", 6], [2, "é  |", 6], [3, "#", None]]


def test_a_delta_tables_merge_reads_a_char_source_padded(engine):
    engine.execute("CREATE TABLE dbo.dw USING DELTA AS SELECT id, v FROM dbo.w", dialect="spark")
    run(engine, "MERGE dbo.dw AS t USING dbo.c AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = CONCAT(s.c, '|') "
                "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, CONCAT(s.c, '#'));")
    assert rows(engine, "SELECT id, v FROM dbo.dw ORDER BY id") == [[1, "x   |"], [2, "é  |"], [3, "#"]]


# ---------- WR-726: SET with the table's name

def test_update_set_names_the_column_with_its_table(engine):
    run(engine, "UPDATE t SET t.v = t.c2 + '|' FROM dbo.w AS t WHERE t.id = 1")
    run(engine, "UPDATE dbo.w SET dbo.w.v = 'b' WHERE id = 2")
    assert rows(engine, "SELECT id, v FROM dbo.w ORDER BY id") == [[1, "q     |"], [2, "b"]]


# ---------- WR-727, WR-728: text read as a date

@pytest.mark.parametrize("text, want", [
    ("20240102", "2024-01-02"), ("240102", "2024-01-02"), ("500102", "1950-01-02"), ("2024", "2024-01-01"),
    ("2024-01-02", "2024-01-02"), ("2024-01-02T10:00:00", "2024-01-02"), (" 2024-01-02 ", "2024-01-02"),
    ("01/02/2024", "2024-01-02"), ("1/2/2024", "2024-01-02"), ("01-02-2024", "2024-01-02"),
    ("1/2/24", "2024-01-02"), ("1/2/49", "2049-01-02"), ("1/2/50", "1950-01-02"),
    ("Jan 2 2024", "2024-01-02"), ("2 Jan 2024", "2024-01-02"), ("January 2, 2024", "2024-01-02"),
    ("2024 Jan 2", "2024-01-02"), ("Jan 2024", "2024-01-01"), ("2024 2 jan", "2024-01-02"),
    ("2 2024 JAN", "2024-01-02"), ("Jan 2024 2", "2024-01-02"),
])
def test_cast_of_text_to_date_reads_sql_servers_forms(engine, text, want):
    assert str(one(engine, f"SELECT CAST('{text}' AS DATE)")) == want
    assert str(one(engine, f"SELECT TRY_CAST('{text}' AS DATE)")) == want


@pytest.mark.parametrize("expression, want", [
    ("CAST('20240102 10:00' AS DATETIME2)", "2024-01-02 10:00:00"),
    ("CAST('20240102' AS DATETIME)", "2024-01-02 00:00:00"),
    ("CAST('01/02/2024 1:05 PM' AS DATETIME2)", "2024-01-02 13:05:00"),
    ("CAST('Jan  2 2024 10:00AM' AS DATETIME)", "2024-01-02 10:00:00"),
    ("CAST('Jan 2 2024 12:30AM' AS DATETIME2)", "2024-01-02 00:30:00"),
    ("CAST('Jan 2 2024 12:30PM' AS SMALLDATETIME)", "2024-01-02 12:30:00"),
    ("CONVERT(DATE, '20240102')", "2024-01-02"),
])
def test_text_with_a_time_reads_as_sql_server_reads_it(engine, expression, want):
    assert str(one(engine, f"SELECT {expression}")) == want


@pytest.mark.parametrize("text", ["24-01-02", "13/01/2024", "02/30/2024", "abc"])
def test_text_sql_server_doesnt_read_as_a_date_is_241(engine, text):
    with pytest.raises(Exception, match="Conversion failed when converting date and/or time from character string"):
        run(engine, f"SELECT CAST('{text}' AS DATE)")
    assert one(engine, f"SELECT TRY_CAST('{text}' AS DATE)") is None


@pytest.mark.parametrize("text", ["01.02.2024", "Jan 2 24"])
def test_text_the_reference_doesnt_settle_is_refused_by_name(engine, text):
    with pytest.raises(ValueError, match=f"Reading '{text}' as a date isn't mapped yet"):
        run(engine, f"SELECT CAST('{text}' AS DATE)")


def test_a_text_column_reads_as_a_date_in_sql_servers_forms(engine):
    got = rows(engine, "SELECT id, CAST(s AS DATE), TRY_CAST(s AS DATETIME2), DATEPART(month, s), "
                       "DATEDIFF(day, s, '20240201'), EOMONTH(s) FROM dbo.t ORDER BY id")
    assert [[str(v) if v is not None else None for v in r] for r in got] == [
        ["1", "2024-01-02", "2024-01-02 00:00:00", "1", "30", "2024-01-31"],
        ["2", "2024-01-02", "2024-01-02 00:00:00", "1", "30", "2024-01-31"],
        ["3", "2024-01-02", "2024-01-02 10:00:00", "1", "30", "2024-01-31"],
        ["4", "2049-01-02", "2049-01-02 10:30:00", "1", "-9102", "2049-01-31"],
        ["5", "2024-01-01", "2024-01-01 00:00:00", "1", "31", "2024-01-31"],
        ["6", None, None, None, None, None],
        ["7", "2024-01-02", "2024-01-02 00:00:00", "1", "30", "2024-01-31"],
        ["8", "2024-01-02", "2024-01-02 09:00:00", "1", "30", "2024-01-31"]]


def test_a_text_column_with_a_month_name_stops_by_name(engine):
    run(engine, "INSERT INTO dbo.t VALUES (9, 'Jan 2 2024')")
    with pytest.raises(Exception, match="Reading text with a month name or AM/PM, or written as m.d.y, as a date"):
        run(engine, "SELECT CAST(s AS DATE) FROM dbo.t WHERE id = 9")


@pytest.mark.parametrize("expression, want", [
    ("DATEPART(month, '01/02/2024')", 1), ("DATENAME(month, '20240301')", "March"),
    ("DATEDIFF(day, '01/01/2024', '20240201')", 31), ("str:EOMONTH('01/15/2024')", "2024-01-31"),
    ("str:DATETRUNC(month, '20240315')", "2024-03-01 00:00:00"), ("str:DATEADD(day, 1, '01/02/2024')", "2024-01-03 00:00:00"),
])
def test_date_functions_read_their_text_in_sql_servers_forms(engine, expression, want):
    as_text = expression.startswith("str:")
    got = one(engine, f"SELECT {expression.removeprefix('str:')}")
    assert (str(got) if as_text else got) == want


# ---------- WR-729: DATEADD of text is a datetime

@pytest.mark.parametrize("expression, want", [
    ("DATEADD(hour, 1, '2024-01-01 10:00')", "2024-01-01 11:00:00"),
    ("DATEADD(day, 1, '2024-01-01 10:00:00')", "2024-01-02 10:00:00"),
    ("DATEADD(millisecond, 1, '2024-01-01 10:00:00.003')", "2024-01-01 10:00:00.003000"),  # .004 rounds to .003
    ("DATEADD(millisecond, 2, '2024-01-01 10:00:00')", "2024-01-01 10:00:00.003000"),
    ("CAST(DATEADD(hour, 1, '2024-01-01 10:00') AS VARCHAR(30))", "Jan  1 2024 11:00AM"),
])
def test_dateadd_of_text_keeps_its_time_as_a_datetime(engine, expression, want):
    assert str(one(engine, f"SELECT {expression}")) == want


@pytest.mark.parametrize("text", ["2024-01-01 10:00:00.1234", "2024-01-01 +05:00"])
def test_dateadd_of_text_t_sql_stops_on_is_refused_by_name(engine, text):
    with pytest.raises(ValueError, match="isn't mapped yet. T-SQL stops when text written as DATEADD's date"):
        run(engine, f"SELECT DATEADD(day, 1, '{text}')")


def test_dateadd_of_a_microsecond_to_text_is_t_sqls_error(engine):
    with pytest.raises(Exception, match="The datepart microsecond is not supported by date function dateadd for "
                                        "data type datetime"):
        run(engine, "SELECT DATEADD(microsecond, 1, '2024-01-02 03:04:05')")


# ---------- the parts Learn gives, value-checked

@pytest.mark.parametrize("expression, want", [
    ("DATEPART(week, '2007-04-21')", 16), ("DATEPART(weekday, '2007-04-21')", 7),  # datepart's DATEFIRST 7 row
    ("DATEPART(week, 'Jan 1, 2023')", 1), ("DATEPART(week, '2024-01-07')", 2), ("DATEPART(week, '2023-12-31')", 53),
    ("DATEPART(iso_week, '2003-12-29')", 1), ("DATEPART(iso_week, '2004-01-04')", 1),  # 2004W01
    ("DATEPART(iso_week, '2021-01-03')", 53), ("DATEPART(dw, '2024-01-07')", 1),
    ("DATENAME(weekday, '2024-01-07')", "Sunday"), ("DATENAME(month, '2024-02-01')", "February"),
    ("DATEDIFF_BIG(millisecond, '0001-01-01', '9999-12-31')", 315537811200000),
])
def test_datepart_datename_and_datediff_big(engine, expression, want):
    assert one(engine, f"SELECT {expression}") == want


@pytest.mark.parametrize("part, want", [
    ("year", "2021-01-01 00:00:00"), ("quarter", "2021-10-01 00:00:00"), ("month", "2021-12-01 00:00:00"),
    ("week", "2021-12-05 00:00:00"), ("iso_week", "2021-12-06 00:00:00"), ("dayofyear", "2021-12-08 00:00:00"),
    ("day", "2021-12-08 00:00:00"), ("hour", "2021-12-08 11:00:00"), ("minute", "2021-12-08 11:30:00"),
    ("second", "2021-12-08 11:30:15"), ("millisecond", "2021-12-08 11:30:15.123000"),
    ("microsecond", "2021-12-08 11:30:15.123456"),
])
def test_datetrunc_gives_microsofts_example(engine, part, want):
    # Example A's value, to the six digits a time keeps here
    assert str(one(engine, f"SELECT DATETRUNC({part}, CAST('2021-12-08 11:30:15.123456' AS DATETIME2))")) == want


def test_datetrunc_week_starts_on_sunday(engine):
    # Example B: 2021-11-11 is a Thursday
    assert str(one(engine, "SELECT DATETRUNC(week, '2021-11-11 11:11:11.123456')")) == "2021-11-07 00:00:00"


@pytest.mark.parametrize("part, want", [
    ("hour", "10:00:00"), ("minute", "10:30:00"), ("second", "10:30:15"), ("millisecond", "10:30:15.123000"),
    ("microsecond", "10:30:15.123456"),
])
def test_datetrunc_of_a_time_is_a_time(engine, part, want):
    assert str(one(engine, f"SELECT DATETRUNC({part}, CAST('10:30:15.123456' AS TIME))")) == want


def test_datetrunc_of_a_time_to_a_date_part_is_9810(engine):
    with pytest.raises(Exception, match="Msg 9810, Level 16: The datepart year is not supported by date function "
                                        "datetrunc for data type time."):
        run(engine, "SELECT DATETRUNC(year, CAST('12:12:12.123456' AS TIME))")


@pytest.mark.parametrize("expression", [
    "DATETRUNC(millisecond, CAST('2021-12-08 11:30:15.12' AS DATETIME2(2)))",
    "DATETRUNC(microsecond, CAST('2021-12-08 11:30:15.123' AS DATETIME))",
    "DATETRUNC(millisecond, CAST('2021-12-08 11:30' AS SMALLDATETIME))",
])
def test_datetrunc_finer_than_the_values_digits_is_refused(engine, expression):
    with pytest.raises(ValueError, match="digits of a second isn't mapped yet"):
        run(engine, f"SELECT {expression}")


def test_dateadd_of_a_date_part_to_a_time_is_refused(engine):
    with pytest.raises(ValueError, match="DATEADD of day to a time isn't mapped yet"):
        run(engine, "SELECT DATEADD(day, 1, CAST('10:00' AS TIME))")
    assert str(one(engine, "SELECT DATEADD(minute, -90, CAST('00:30' AS TIME))")) == "23:00:00"


@pytest.mark.parametrize("args, want", [
    ("14, 23, 44, 5, 1", "14:23:44.5"), ("14, 23, 44, 50, 2", "14:23:44.50"), ("14, 23, 44, 500, 3", "14:23:44.500"),
    ("14, 23, 44, 0, 0", "14:23:44"),
])
def test_timefromparts_is_a_time_of_its_precision(engine, args, want):
    assert one(engine, f"SELECT CAST(TIMEFROMPARTS({args}) AS VARCHAR(20))") == want


@pytest.mark.parametrize("text, want", [
    ("10:00:00.001", "10:00:00"), ("10:00:00.002", "10:00:00.003000"), ("10:00:00.004", "10:00:00.003000"),
    ("10:00:00.005", "10:00:00.007000"), ("10:00:00.008", "10:00:00.007000"), ("10:00:00.009", "10:00:00.010000"),
    ("23:59:59.999", "NEXT"), ("23:59:59.995", "23:59:59.997000"), ("23:59:59.992", "23:59:59.993000"),
    ("23:59:59.991", "23:59:59.990000"),
])
def test_datetime_rounds_to_its_steps(engine, text, want):
    got = str(one(engine, f"SELECT CAST('2024-01-01 {text}' AS DATETIME)"))
    assert got == ("2024-01-02 00:00:00" if want == "NEXT" else f"2024-01-01 {want}")


@pytest.mark.parametrize("seconds, want", [("29.998", "10:00:00"), ("29.999", "10:01:00"), ("29", "10:00:00"),
                                           ("30", "10:01:00")])
def test_smalldatetime_rounds_on_29_999_seconds(engine, seconds, want):
    assert str(one(engine, f"SELECT CAST('2024-01-01 10:00:{seconds}' AS SMALLDATETIME)")) == f"2024-01-01 {want}"


@pytest.mark.parametrize("expression, want", [
    ("EOMONTH(CAST('2024-12-31' AS DATETIME))", "2024-12-31"), ("EOMONTH(CAST('2024-12-31' AS DATETIME), 1)", "2025-01-31"),
    ("EOMONTH(CAST('2024-12-31' AS DATETIME), -1)", "2024-11-30"), ("EOMONTH('2024-01-31', 1)", "2024-02-29"),
])
def test_eomonth_with_an_offset(engine, expression, want):
    assert str(one(engine, f"SELECT {expression}")) == want


@pytest.mark.parametrize("expression, name", [
    ("ISDATE('2024-01-01')", "ISDATE"), ("DATE_BUCKET(week, 1, CAST('2020-04-30' AS DATE))", "DATE_BUCKET"),
    ("DATETIME2FROMPARTS(2011, 8, 15, 14, 23, 44, 5, 1)", "DATETIME2FROMPARTS"),
    ("TODATETIMEOFFSET(CAST('2024-01-01' AS DATETIME2), '+05:00')", "TODATETIMEOFFSET"),
])
def test_the_date_functions_not_mapped_are_refused_by_name(engine, expression, name):
    with pytest.raises(ValueError, match=f"{name} isn't mapped yet"):
        run(engine, f"SELECT {expression}")
