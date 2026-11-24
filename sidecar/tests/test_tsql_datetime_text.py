"""WR-707: a date or time written as text with no style, as T-SQL writes each type.

These tests cover:
- datetime and smalldatetime: style 0, ``mon dd yyyy hh:miAM`` with the day and the hour padded with a space;
  GETDATE(), GETUTCDATE() and CURRENT_TIMESTAMP are datetime;
- date: ``yyyy-mm-dd``; time(n) and datetime2(n): n digits of a second, 7 with no n; SYSDATETIME() and
  SYSUTCDATETIME() are datetime2(7);
- CAST, CONVERT and TRY_CONVERT with no style, to VARCHAR(n), VARCHAR(MAX), NVARCHAR and CHAR(n), and the cut to
  a length that is too short;
- CONVERT's styles 0 and 100, and the styles already checked, which still give their text;
- CONCAT and CONCAT_WS, which write a date as a CAST does, and + of text and a date, refused by name;
- a table's datetime2 and time columns, written with 6 digits, with a note.
"""

from __future__ import annotations

import re

import pytest

from fabric_desktop.localdata import LocalData, tsql

DT = "CAST('2024-01-02 13:04:05.123' AS DATETIME)"


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("DatetimeText")["id"])
    yield e
    local.close_all()


def one(engine, expression):
    return engine.execute(f"SELECT {expression} AS v", dialect="tsql")["rows"][0][0]


# (T-SQL expression, its text)
TEXTS = [
    (f"CAST({DT} AS VARCHAR(30))", "Jan  2 2024  1:04PM"),
    (f"CAST({DT} AS VARCHAR)", "Jan  2 2024  1:04PM"),
    (f"CAST({DT} AS NVARCHAR(30))", "Jan  2 2024  1:04PM"),
    (f"CAST({DT} AS VARCHAR(MAX))", "Jan  2 2024  1:04PM"),
    (f"CAST({DT} AS CHAR(22))", "Jan  2 2024  1:04PM   "),
    (f"CONVERT(VARCHAR(30), {DT})", "Jan  2 2024  1:04PM"),
    (f"TRY_CONVERT(VARCHAR(30), {DT})", "Jan  2 2024  1:04PM"),
    ("CAST(CAST('2024-12-25 00:04' AS DATETIME) AS VARCHAR(30))", "Dec 25 2024 12:04AM"),
    ("CAST(CAST('2024-12-25 12:00' AS DATETIME) AS VARCHAR(30))", "Dec 25 2024 12:00PM"),
    ("CAST(CAST('2024-12-25 23:59:59.999' AS DATETIME) AS VARCHAR(30))", "Dec 26 2024 12:00AM"),
    ("CAST(CAST('2024-11-30 10:15:29.998' AS SMALLDATETIME) AS VARCHAR(30))", "Nov 30 2024 10:15AM"),
    ("CAST(CAST('2024-11-30 10:15:29.999' AS SMALLDATETIME) AS VARCHAR(30))", "Nov 30 2024 10:16AM"),
    (f"CAST(DATEADD(day, 9, {DT}) AS VARCHAR(30))", "Jan 11 2024  1:04PM"),
    ("CAST(CAST('2024-01-02' AS DATE) AS VARCHAR(30))", "2024-01-02"),
    ("CAST(CAST('13:04:05.1234567' AS TIME) AS VARCHAR(30))", "13:04:05.1234560"),
    ("CAST(CAST('13:04:05.12' AS TIME(3)) AS VARCHAR(30))", "13:04:05.120"),
    ("CAST(CAST('13:04:05' AS TIME(0)) AS VARCHAR(30))", "13:04:05"),
    ("CAST(CAST('2024-01-02 13:04:05' AS DATETIME2) AS VARCHAR(30))", "2024-01-02 13:04:05.0000000"),
    ("CAST(CAST('2024-01-02 13:04:05.5' AS DATETIME2(2)) AS VARCHAR(30))", "2024-01-02 13:04:05.50"),
    ("CONVERT(VARCHAR(30), CAST('2024-01-02 13:04:05' AS DATETIME2))", "2024-01-02 13:04:05.0000000"),
    ("CAST(CAST(NULL AS DATETIME) AS VARCHAR(30))", None),
]


@pytest.mark.parametrize("expression, text", TEXTS)
def test_text_of_each_type(engine, expression, text):
    assert one(engine, expression) == text


@pytest.mark.parametrize("expression, text", [
    (f"CAST({DT} AS VARCHAR(11))", "Jan  2 2024"),
    (f"CAST({DT} AS VARCHAR(5))", "Jan  "),
    ("CAST(CAST('2024-01-02 13:04:05' AS DATETIME2) AS VARCHAR(19))", "2024-01-02 13:04:05"),
    ("CAST(CAST('2024-01-02' AS DATE) AS VARCHAR(4))", "2024"),
    ("CONVERT(VARCHAR(10), CAST('2024-01-02 13:04:05' AS DATETIME2), 120)", "2024-01-02"),
])
def test_too_short_a_length_cuts_the_text(engine, expression, text):
    assert one(engine, expression) == text


@pytest.mark.parametrize("call, length", [("GETDATE()", 19), ("GETUTCDATE()", 19), ("CURRENT_TIMESTAMP", 19),
                                          ("SYSDATETIME()", 27), ("SYSUTCDATETIME()", 27)])
def test_the_clock_functions_are_their_own_types(engine, call, length):
    text = one(engine, f"CAST({call} AS VARCHAR(30))")
    assert len(text) == length, text
    if length == 19:
        assert re.fullmatch(r"[A-Z][a-z]{2} [ 1-3]\d \d{4} [ 1]\d:\d\d[AP]M", text), text
    else:
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}0", text), text


def test_getdate_and_sysdatetime_in_one_query_keep_their_own_types(engine):
    row = engine.execute("SELECT CAST(GETDATE() AS VARCHAR(30)) AS a, CAST(SYSDATETIME() AS VARCHAR(30)) AS b",
                         dialect="tsql")["rows"][0]
    assert len(row[0]) == 19 and len(row[1]) == 27


@pytest.mark.parametrize("style", [0, 100])
@pytest.mark.parametrize("value, text", [
    (DT, "Jan  2 2024  1:04PM"),
    ("CAST('2024-01-02 13:04:05' AS DATETIME2)", "Jan  2 2024  1:04PM"),
    ("CAST('2024-01-02' AS DATE)", "Jan  2 2024 12:00AM"),
])
def test_convert_styles_0_and_100(engine, style, value, text):
    assert one(engine, f"CONVERT(VARCHAR(30), {value}, {style})") == text


@pytest.mark.parametrize("style, text", [
    (101, "01/02/2024"), (103, "02/01/2024"), (112, "20240102"), (120, "2024-01-02 13:04:05"),
    (121, "2024-01-02 13:04:05.123"), (126, "2024-01-02T13:04:05.123"),
])
def test_the_checked_styles_still_give_their_text(engine, style, text):
    assert one(engine, f"CONVERT(VARCHAR(30), {DT}, {style})") == text


def test_concat_writes_a_date_as_cast_does(engine):
    assert one(engine, f"CONCAT('at ', {DT}, ' on ', CAST('2024-01-02' AS DATE), ' ', "
                       "CAST('2024-01-02 03:04:05' AS DATETIME2(1)), ' ', CAST('01:02' AS TIME(0)))") == \
        "at Jan  2 2024  1:04PM on 2024-01-02 2024-01-02 03:04:05.0 01:02:00"


def test_concat_ws_writes_a_date_as_cast_does(engine):
    assert one(engine, f"CONCAT_WS('|', {DT}, 1)") == "Jan  2 2024  1:04PM|1"


def test_text_added_to_text_of_a_date_joins_them(engine):
    assert one(engine, f"CAST({DT} AS VARCHAR(20)) + '!'") == "Jan  2 2024  1:04PM!"


@pytest.mark.parametrize("expression", ["'x' + CAST('2024-01-02' AS DATE)", f"'1' + {DT}",
                                        "CAST('01:02' AS TIME) + 'x'"])
def test_plus_of_text_and_a_date_is_refused_by_name(expression):
    with pytest.raises(ValueError, match=re.escape("+ of text and a date or time")):
        tsql.translate(f"SELECT {expression}")


def test_a_tables_datetime2_and_time_columns_have_6_digits_with_a_note(engine):
    engine.execute("CREATE TABLE dbo.e (a DATETIME2(6), t TIME(6), d DATE)", dialect="tsql")
    engine.execute("INSERT INTO dbo.e VALUES ('2024-01-02 13:04:05', '01:02:03', '2024-01-02')", dialect="tsql")
    result = engine.execute("SELECT CAST(a AS VARCHAR(30)) AS a, CAST(t AS VARCHAR(20)) AS t, "
                            "CAST(d AS VARCHAR(10)) AS d FROM dbo.e", dialect="tsql")
    assert result["rows"] == [["2024-01-02 13:04:05.000000", "01:02:03.000000", "2024-01-02"]]
    assert any("6 digits of a second" in n for n in result["translation"]["notes"])


def test_datetimeoffset_is_still_refused_by_name(engine):
    with pytest.raises(ValueError, match="datetimeoffset isn't mapped yet"):
        engine.execute("SELECT CAST(CAST('2024-01-02 13:04:05 +01:00' AS DATETIMEOFFSET) AS VARCHAR(40)) AS v",
                       dialect="tsql")
