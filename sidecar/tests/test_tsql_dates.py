"""WR-208: local T-SQL dates and times give T-SQL's documented answers at their edges.

DATEADD, DATEDIFF and DATEDIFF_BIG at month-end, the leap day and the week's start; EOMONTH; DATEFROMPARTS;
FORMAT; and CONVERT and TRY_CONVERT with styles 101, 103, 112, 120, 121 and 126, both ways. Each value is
what Microsoft Learn's T-SQL reference gives. A date-and-time is a warehouse's datetime2(6): six digits of
fraction. A style that hasn't been checked runs with a note, never silently.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import CHECKED_DATE_STYLES


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("dates") / "app" / "local-data")
    e = local.engine(local.create("Dates")["id"])
    e.execute("CREATE TABLE dbo.d AS SELECT DATE '2024-01-31' AS jan31, DATE '2024-02-29' AS leap, "
              "DATE '2023-01-31' AS jan31b, TIMESTAMP '2024-02-29 13:04:05.123' AS ts, "
              "TIMESTAMP '2024-02-29 13:04:05' AS ts0, DATE '2024-01-06' AS sat, DATE '2024-01-07' AS sun")
    return e


def tsql(engine, sql):
    return engine.execute(f"SELECT {sql} AS v FROM dbo.d", dialect="tsql")


# (T-SQL expression, T-SQL's answer as text; None for NULL; "ERROR:<words>" for an error that has to say them)
CASES = [
 ("DATEADD(month, 1, jan31)", "2024-02-29"),
 ("DATEADD(month, 1, jan31b)", "2023-02-28"),
 ("DATEADD(year, 1, leap)", "2025-02-28"),
 ("DATEADD(month, -1, DATEFROMPARTS(2024, 3, 31))", "2024-02-29"),
 ("DATEADD(day, 1, DATEFROMPARTS(2024, 2, 28))", "2024-02-29"),
 ("DATEADD(quarter, 1, DATEFROMPARTS(2024, 11, 30))", "2025-02-28"),
 ("DATEADD(week, 1, leap)", "2024-03-07"),
 ("DATEADD(hour, 11, ts0)", "2024-03-01 00:04:05"),
 ("DATEADD(minute, -1, DATEFROMPARTS(2024, 3, 1))", "ERROR:"),
 ("DATEDIFF(year, DATEFROMPARTS(2023, 12, 31), DATEFROMPARTS(2024, 1, 1))", "1"),
 ("DATEDIFF(month, jan31, DATEFROMPARTS(2024, 2, 1))", "1"),
 ("DATEDIFF(month, DATEFROMPARTS(2024, 2, 1), jan31)", "-1"),
 ("DATEDIFF(day, DATEFROMPARTS(2024, 2, 28), DATEFROMPARTS(2024, 3, 1))", "2"),
 ("DATEDIFF(week, sat, sun)", "1"),
 ("DATEDIFF(week, sun, DATEFROMPARTS(2024, 1, 13))", "0"),
 ("DATEDIFF(quarter, DATEFROMPARTS(2024, 3, 31), DATEFROMPARTS(2024, 4, 1))", "1"),
 ("DATEDIFF(hour, CAST('2024-01-01 23:59:00' AS DATETIME2), CAST('2024-01-02 00:00:00' AS DATETIME2))", "1"),
 ("DATEDIFF(minute, CAST('2024-01-01 23:59:59' AS DATETIME2), CAST('2024-01-02 00:00:00' AS DATETIME2))", "1"),
 ("DATEDIFF(second, CAST('2024-01-01 00:00:00.9' AS DATETIME2), CAST('2024-01-01 00:00:01.1' AS DATETIME2))", "1"),
 ("DATEDIFF(dayofyear, DATEFROMPARTS(2024, 2, 28), DATEFROMPARTS(2024, 3, 1))", "2"),
 ("DATEDIFF(millisecond, DATEFROMPARTS(2024, 1, 1), DATEFROMPARTS(2024, 2, 1))", "ERROR:overflow"),
 ("DATEDIFF_BIG(millisecond, DATEFROMPARTS(2024, 1, 1), DATEFROMPARTS(2024, 2, 1))", "2678400000"),
 ("DATEDIFF_BIG(second, DATEFROMPARTS(1900, 1, 1), DATEFROMPARTS(2024, 2, 29))", "3918153600"),
 ("EOMONTH(DATEFROMPARTS(2024, 2, 10))", "2024-02-29"),
 ("EOMONTH(jan31, 1)", "2024-02-29"),
 ("EOMONTH(DATEFROMPARTS(2023, 2, 10))", "2023-02-28"),
 ("EOMONTH(DATEFROMPARTS(2024, 12, 15), -12)", "2023-12-31"),
 ("EOMONTH(ts)", "2024-02-29"),
 ("DATEFROMPARTS(2024, 2, 29)", "2024-02-29"),
 ("DATEFROMPARTS(2023, 2, 29)", "ERROR:"),
 ("DATEFROMPARTS(2024, NULL, 1)", None),
 ("CONVERT(VARCHAR(10), leap, 101)", "02/29/2024"),
 ("CONVERT(VARCHAR(10), leap, 103)", "29/02/2024"),
 ("CONVERT(VARCHAR(8), leap, 112)", "20240229"),
 ("CONVERT(VARCHAR(30), leap, 120)", "2024-02-29"),
 ("CONVERT(VARCHAR(30), ts, 120)", "2024-02-29 13:04:05"),
 ("CONVERT(VARCHAR(30), ts, 121)", "2024-02-29 13:04:05.123000"),
 ("CONVERT(VARCHAR(30), ts, 126)", "2024-02-29T13:04:05.123000"),
 ("CONVERT(VARCHAR(30), ts0, 126)", "2024-02-29T13:04:05"),
 ("CONVERT(VARCHAR(30), ts, 101)", "02/29/2024"),
 ("CONVERT(DATE, '02/29/2024', 101)", "2024-02-29"),
 ("CONVERT(DATE, '29/02/2024', 103)", "2024-02-29"),
 ("CONVERT(DATE, '20240229', 112)", "2024-02-29"),
 ("CONVERT(DATETIME2, '2024-02-29 13:04:05', 120)", "2024-02-29 13:04:05"),
 ("CONVERT(DATETIME2, '2024-02-29 13:04:05.123', 121)", "2024-02-29 13:04:05.123000"),
 ("CONVERT(DATETIME2, '2024-02-29T13:04:05.123', 126)", "2024-02-29 13:04:05.123000"),
 ("TRY_CONVERT(DATE, '31/02/2024', 103)", None),
 ("TRY_CONVERT(DATE, '02/30/2024', 101)", None),
 ("TRY_CONVERT(DATE, '2023-02-29', 120)", None),
 ("CONVERT(DATE, '2023-02-29', 120)", "ERROR:"),
 ("TRY_CONVERT(DATE, '20240229', 112)", "2024-02-29"),
 ("TRY_CONVERT(DATETIME2, '02/30/2024', 101)", None),
 ("TRY_CONVERT(DATETIME, '30/02/2024', 103)", None),
 ("TRY_CONVERT(DATETIME, 'xx', 101)", None),
 ("TRY_CONVERT(DATETIME2, '20240230', 112)", None),
 ("TRY_CONVERT(DATETIME2, '2024-02-30 13:04:05', 120)", None),
 ("TRY_CONVERT(DATETIME2, '29/02/2024', 103)", "2024-02-29 00:00:00"),
 ("CONVERT(DATETIME2, '29/02/2024', 103)", "2024-02-29 00:00:00"),
 ("CONVERT(DATETIME, '02/29/2024', 101)", "2024-02-29 00:00:00"),
 ("CONVERT(DATETIME2, '2024-02-29', 120)", "2024-02-29 00:00:00"),
 ("CONVERT(DATETIME2, '30/02/2024', 103)", "ERROR:"),
 ("FORMAT(leap, 'yyyy-MM-dd')", "2024-02-29"),
 ("FORMAT(leap, 'dddd, MMMM d, yyyy')", "Thursday, February 29, 2024"),
 ("FORMAT(ts, 'yyyy-MM-ddTHH:mm:ss.fff')", "2024-02-29T13:04:05.123"),
 ("FORMAT(ts, 'MM/dd/yy h:mm tt')", "02/29/24 1:04 PM"),
 ("FORMAT(DATEFROMPARTS(2024, 1, 7), 'ddd')", "Sun"),
]

VALUES = [c for c in CASES if c[1] is None or not c[1].startswith("ERROR:")]
ERRORS = [c for c in CASES if c[1] is not None and c[1].startswith("ERROR:")]


@pytest.mark.parametrize(("sql", "want"), VALUES, ids=[c[0] for c in VALUES])
def test_each_date_function_gives_tsqls_answer(engine, sql, want):
    r = tsql(engine, sql)
    got = r["rows"][0][0]
    assert (None if got is None else str(got)) == want
    assert not (r.get("translation") or {}).get("notes"), "a checked case runs without a caveat"


@pytest.mark.parametrize(("sql", "want"), ERRORS, ids=[c[0] for c in ERRORS])
def test_what_tsql_refuses_is_refused_here_too(engine, sql, want):
    with pytest.raises(Exception, match=f"(?i){want[len('ERROR:'):]}"):
        tsql(engine, sql)


def test_datediff_past_an_int_says_so_as_tsql_does(engine):
    with pytest.raises(Exception, match="The datediff function resulted in an overflow"):
        tsql(engine, "DATEDIFF(millisecond, DATEFROMPARTS(2024, 1, 1), DATEFROMPARTS(2024, 2, 1))")
    r = tsql(engine, "DATEDIFF(millisecond, DATEFROMPARTS(2024, 1, 1), DATEFROMPARTS(2024, 1, 2))")
    assert r["rows"] == [[86400000]] and r["columns"][0]["type"] == "int32"  # an int, as in T-SQL
    big = tsql(engine, "DATEDIFF_BIG(millisecond, DATEFROMPARTS(2024, 1, 1), DATEFROMPARTS(2024, 2, 1))")
    assert big["columns"][0]["type"] == "int64"


# WR-731: these ran with an "unchecked style" note; now each is written as Learn's style table shows, with no note,
# and text read with a style other than the checked ones is refused by name.
@pytest.mark.parametrize("sql, want", [("CONVERT(VARCHAR(30), ts, 104)", "29.02.2024"),
                                       ("TRY_CONVERT(VARCHAR(30), leap, 107)", "Feb 29, 2024")])
def test_a_style_from_learns_table_runs_without_a_note(engine, sql, want):
    r = tsql(engine, sql)
    assert r["rows"] == [[want]] and not (r.get("translation") or {}).get("notes")


def test_text_read_with_an_unchecked_style_is_refused(engine):
    with pytest.raises(ValueError, match="CONVERT of text to a date with style 104 isn't mapped yet"):
        tsql(engine, "CONVERT(DATE, '29.02.2024', 104)")


def test_a_number_with_a_style_gets_no_date_note(engine):
    r = tsql(engine, "CONVERT(VARCHAR(10), 7, 1)")
    assert r["rows"] == [["7"]] and not (r.get("translation") or {}).get("notes")


def test_every_checked_style_is_value_checked_above():
    used = {s for sql, _ in CASES for s in CHECKED_DATE_STYLES if sql.endswith(f", {s})")}
    assert used == set(CHECKED_DATE_STYLES) - {"0"}, "a style listed as checked needs a case above"
