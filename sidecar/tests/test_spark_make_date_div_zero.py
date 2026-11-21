"""make_date and div by zero answer as Spark (ANSI off) on DuckDB 1.x and 2.0 alike. CI runs this file on both."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import sql


def one(query: str) -> object:
    return next(iter(sql.run(query).to_pylist()[0].values()))


@pytest.mark.parametrize(("query", "want"), [
    ("SELECT make_date(2024, 2, 29)", dt.date(2024, 2, 29)),
    ("SELECT make_date(2023, 2, 29)", None),  # a day that doesn't exist
    ("SELECT make_date(2024, 13, 1)", None),
    ("SELECT make_date(2024, -3, 100)", None),
    ("SELECT make_date(2024, 1, 0)", None),
    ("SELECT make_date(2024, 2, NULL)", None),
    # A Spark cast inside the parts carries an ERROR(), which DuckDB 2.0 won't put inside TRY.
    ("SELECT make_date(2023, sign(7), length('ÄÖü'))", dt.date(2023, 1, 3)),
    ("SELECT make_date(2024, CAST('2' AS INT), 1)", dt.date(2024, 2, 1)),
    ("SELECT 7 div 0", None),
    ("SELECT 7 div 2", 3),
    ("SELECT pmod(length(''), (1000000 div 0))", None),  # a div inside a guarded divisor
    ("SELECT (7 div 0) % 2", None),
])
def test_answers_as_spark(query: str, want: object) -> None:
    assert one(query) == want
