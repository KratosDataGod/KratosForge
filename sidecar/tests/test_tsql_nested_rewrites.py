"""WR-701: wrong answers the value checks for T-SQL's documented examples found on the local engine.

- A call inside DATEDIFF wasn't rewritten: DATEDIFF's rewrite copied its arguments first, so
  DATETRUNC(week, ...) started weeks on Monday, GETUTCDATE() was refused and DATEPART(weekday, ...) of
  text stopped with DuckDB's error.
- YEAR(0), MONTH(0) and DAY(0) stopped with DuckDB's error. T-SQL reads a whole number as days from
  1900-01-01 (Microsoft's YEAR page: ``SELECT YEAR(0), MONTH(0), DAY(0)`` gives 1900, 1, 1).
- REVERSE(1234) stopped with DuckDB's error. T-SQL reverses the number's text (Microsoft's REVERSE page).
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    yield local.engine(local.create("WR-701")["id"])
    local.close_all()


def rows(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"]


@pytest.mark.parametrize("sql, want", [
    # 2024-03-06 is a Wednesday; T-SQL's week starts on Sunday 2024-03-03, two days after 2024-03-01.
    ("SELECT DATEDIFF(day, '2024-03-01', DATETRUNC(week, CAST('2024-03-06' AS date)))", 2),
    ("SELECT DATEDIFF(day, '2024-01-01', DATEADD(day, DATEPART(weekday, '2024-03-05'), CAST('2024-01-01' AS date)))", 3),
    ("SELECT DATEDIFF(minute, GETDATE(), GETUTCDATE())", 0),
    ("SELECT DATEDIFF(minute, SYSDATETIME(), SYSUTCDATETIME())", 0),
    ("SELECT DATEDIFF_BIG(minute, CURRENT_TIMESTAMP, GETUTCDATE())", 0),
    ("SELECT DATEDIFF(week, DATEADD(day, 1, CAST('2024-01-05' AS date)), '2024-01-07')", 1),
])
def test_a_call_inside_datediff_is_rewritten_too(engine, sql, want):
    assert rows(engine, sql) == [[want]]


def test_a_whole_number_as_a_date_counts_days_from_1900(engine):
    assert rows(engine, "SELECT YEAR(0), MONTH(0), DAY(0)") == [[1900, 1, 1]]
    assert rows(engine, "SELECT DAY(-1), YEAR(-1), DAY(31), MONTH(31)") == [[31, 1899, 1, 2]]
    assert rows(engine, "SELECT DATEDIFF(day, 0, 1)") == [[1]]


def test_reverse_of_a_number_reverses_its_text(engine):
    assert rows(engine, "SELECT REVERSE(1234), REVERSE('abc'), REVERSE(''), REVERSE(NULL)") == [["4321", "cba", "", None]]
