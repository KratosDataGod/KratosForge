"""WR-707: where a date or time meets text with no CAST, and GETDATE() told from SYSDATETIME().

These tests cover:
- STRING_AGG of a date or time, which writes each value as a CAST to text does;
- ISNULL with text first, which has text's type, so the date is written as a CAST writes it; ISNULL with a date
  first and a text column, refused by name;
- COALESCE and CASE, which take the date's type in T-SQL: a string written in the query is read as a date, as
  there, and a text column is refused by name;
- UNION of a text column with a date column, refused by name;
- GETDATE() (datetime) and SYSDATETIME() (datetime2(7)) marked as they are read, so neither a comment, a string,
  the same function twice nor their order mislabels one.
"""

from __future__ import annotations

import re

import pytest

from fabric_desktop.localdata import LocalData

DT = "CAST('2024-01-02 13:04:05' AS DATETIME)"
DATETIME_TEXT = re.compile(r"[A-Z][a-z]{2} [ 1-3]\d \d{4} [ 1]\d:\d\d[AP]M")
DATETIME2_TEXT = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}0")


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("DatetimeImplicit")["id"])
    e.execute("CREATE TABLE dbo.t (id INT, d DATE, a DATETIME2(6), s VARCHAR(20))", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, '2024-01-02', '2024-01-02 13:04:05', 'abc'), "
              "(2, '2024-03-05', '2024-03-05 00:00:00', NULL)", dialect="tsql")
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def date_rows(engine, sql):
    """The rows, and that the column is a date: the answer's rows show a date as its ISO text."""
    result = engine.execute(sql, dialect="tsql")
    assert result["columns"][0]["type"] == "date32[day]", result["columns"]
    return [list(r) for r in result["rows"]]


def refused(engine, sql, words):
    with pytest.raises(ValueError, match=re.escape(words)):
        engine.execute(sql, dialect="tsql")


def test_string_agg_of_a_datetime(engine):
    assert rows(engine, f"SELECT STRING_AGG({DT}, ',') AS v FROM dbo.t") == [["Jan  2 2024  1:04PM,Jan  2 2024  1:04PM"]]


def test_string_agg_of_a_date_in_order(engine):
    assert rows(engine, "SELECT STRING_AGG(d, ';') WITHIN GROUP (ORDER BY d DESC) AS v FROM dbo.t") == \
        [["2024-03-05;2024-01-02"]]


def test_string_agg_of_a_datetime2_column_has_6_digits_and_a_note(engine):
    result = engine.execute("SELECT STRING_AGG(a, ',') WITHIN GROUP (ORDER BY a) AS v FROM dbo.t", dialect="tsql")
    assert result["rows"] == [["2024-01-02 13:04:05.000000,2024-03-05 00:00:00.000000"]]
    assert any("6 digits of a second" in n for n in result["translation"]["notes"])


def test_string_agg_of_text_is_as_it_was(engine):
    assert rows(engine, "SELECT STRING_AGG(s, ',') AS v FROM dbo.t") == [["abc"]]


def test_isnull_with_text_first_writes_the_date_as_text(engine):
    assert rows(engine, f"SELECT ISNULL(CAST(NULL AS VARCHAR(30)), {DT}) AS v") == [["Jan  2 2024  1:04PM"]]
    assert rows(engine, f"SELECT ISNULL(CAST(NULL AS VARCHAR(6)), {DT}) AS v") == [["Jan  2"]]
    assert rows(engine, "SELECT ISNULL(s, d) AS v FROM dbo.t ORDER BY id") == [["abc"], ["2024-03-05"]]


def test_isnull_with_a_date_first_reads_a_written_string_as_a_date(engine):
    assert date_rows(engine, "SELECT ISNULL(CAST(NULL AS DATE), '2024-05-06') AS v") == [["2024-05-06"]]


def test_isnull_with_a_date_first_and_a_text_column_is_refused(engine):
    refused(engine, "SELECT ISNULL(d, s) AS v FROM dbo.t", "ISNULL of text and a date or time isn't mapped yet")


def test_coalesce_reads_a_written_string_as_the_date(engine):
    assert date_rows(engine, "SELECT COALESCE(NULL, CAST(NULL AS DATE), '2024-05-06') AS v") == [["2024-05-06"]]


def test_coalesce_of_a_date_and_a_text_column_is_refused(engine):
    refused(engine, "SELECT COALESCE(d, s) AS v FROM dbo.t", "COALESCE of text and a date or time isn't mapped yet")


def test_case_reads_a_written_string_as_the_date(engine):
    assert date_rows(engine, "SELECT CASE WHEN id = 1 THEN d ELSE '2024-05-06' END AS v FROM dbo.t ORDER BY id") == \
        [["2024-01-02"], ["2024-05-06"]]


def test_case_of_a_date_and_a_text_column_is_refused(engine):
    refused(engine, "SELECT CASE WHEN id = 1 THEN d ELSE s END AS v FROM dbo.t",
            "a CASE of text and a date or time isn't mapped yet")


@pytest.mark.parametrize("sql", [
    "SELECT s FROM dbo.t UNION ALL SELECT d FROM dbo.t",
    "SELECT d FROM dbo.t UNION SELECT s FROM dbo.t",
    f"SELECT '2024-01-01' UNION SELECT {DT}",
    "SELECT id, s FROM dbo.t EXCEPT SELECT id, a FROM dbo.t",
])
def test_a_set_operation_of_text_and_a_date_is_refused(engine, sql):
    with pytest.raises(ValueError, match="of a column of text and a date or time isn't mapped yet"):
        engine.execute(sql, dialect="tsql")


def test_a_union_of_dates_or_of_text_runs(engine):
    assert date_rows(engine, "SELECT d FROM dbo.t WHERE id = 1 UNION SELECT d FROM dbo.t WHERE id = 1") == \
        [["2024-01-02"]]
    assert rows(engine, "SELECT 'x' AS v UNION ALL SELECT s FROM dbo.t WHERE id = 1") == [["x"], ["abc"]]


# GETDATE() and SYSDATETIME(), marked as they are read
def clocks(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"][0]


def test_both_in_one_query_either_order(engine):
    a, b = clocks(engine, "SELECT CAST(GETDATE() AS VARCHAR(30)) AS a, CAST(SYSDATETIME() AS VARCHAR(30)) AS b")
    assert DATETIME_TEXT.fullmatch(a) and DATETIME2_TEXT.fullmatch(b), (a, b)
    a, b = clocks(engine, "SELECT CAST(SYSDATETIME() AS VARCHAR(30)) AS a, CAST(GETDATE() AS VARCHAR(30)) AS b")
    assert DATETIME2_TEXT.fullmatch(a) and DATETIME_TEXT.fullmatch(b), (a, b)


def test_a_name_in_a_comment_doesnt_mislabel_one(engine):
    a, b = clocks(engine, "SELECT /* SYSDATETIME() */ CAST(GETDATE() AS VARCHAR(30)) AS a, -- GETDATE()\n"
                          "CAST(SYSDATETIME() AS VARCHAR(30)) AS b")
    assert DATETIME_TEXT.fullmatch(a) and DATETIME2_TEXT.fullmatch(b), (a, b)


def test_a_name_in_a_string_doesnt_mislabel_one(engine):
    s, a, b = clocks(engine, "SELECT 'SYSDATETIME() GETDATE()' AS s, CAST(GETDATE() AS VARCHAR(30)) AS a, "
                             "CAST(SYSDATETIME() AS VARCHAR(30)) AS b")
    assert s == "SYSDATETIME() GETDATE()"
    assert DATETIME_TEXT.fullmatch(a) and DATETIME2_TEXT.fullmatch(b), (a, b)


def test_each_repeated(engine):
    row = clocks(engine, "SELECT CAST(SYSDATETIME() AS VARCHAR(30)), CAST(GETDATE() AS VARCHAR(30)), "
                         "CAST(SYSDATETIME() AS VARCHAR(30)), CAST(GETDATE() AS VARCHAR(30)), "
                         "CONCAT(GETDATE(), '|', SYSDATETIME())")
    assert DATETIME2_TEXT.fullmatch(row[0]) and DATETIME2_TEXT.fullmatch(row[2]), row
    assert DATETIME_TEXT.fullmatch(row[1]) and DATETIME_TEXT.fullmatch(row[3]), row
    left, right = row[4].split("|")
    assert DATETIME_TEXT.fullmatch(left) and DATETIME2_TEXT.fullmatch(right), row


def test_lower_case_and_after_a_go(engine):
    a, b = clocks(engine, "SELECT 1 AS x\nGO\nSELECT CAST(sysdatetime() AS VARCHAR(30)) AS a, "
                          "CAST(getdate() AS VARCHAR(30)) AS b")
    assert DATETIME2_TEXT.fullmatch(a) and DATETIME_TEXT.fullmatch(b), (a, b)


def test_current_timestamp_and_the_utc_functions(engine):
    row = clocks(engine, "SELECT CAST(CURRENT_TIMESTAMP AS VARCHAR(30)), CAST(GETUTCDATE() AS VARCHAR(30)), "
                         "CAST(SYSUTCDATETIME() AS VARCHAR(30))")
    assert DATETIME_TEXT.fullmatch(row[0]) and DATETIME_TEXT.fullmatch(row[1]) and DATETIME2_TEXT.fullmatch(row[2])
