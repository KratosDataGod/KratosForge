"""The date functions a local pipeline run gained from the coverage list: addToTime, subtractFromTime,
getFutureTime, getPastTime, startOfHour, startOfMonth, dayOfMonth, dayOfWeek, dayOfYear, ticks, and
month-first dates as the reference writes them."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fabric_desktop.orchestration.evaluate import ExpressionError, Scope, evaluate

NOW = datetime(2026, 1, 31, 10, 15, 0, tzinfo=timezone.utc)


def ev(text: str):
    return evaluate("@" + text, Scope(now=lambda: NOW))


@pytest.mark.parametrize("expr, want", [
    ("addToTime('2018-01-01T00:00:00Z', 1, 'Day')", "2018-01-02T00:00:00.0000000Z"),
    ("addToTime('2018-01-01T00:00:00Z', 1, 'Week', 'yyyy-MM-dd')", "2018-01-08"),
    ("addToTime('2018-01-31T00:00:00Z', 1, 'month')", "2018-02-28T00:00:00.0000000Z"),  # the month's last day
    ("addToTime('2020-02-29T00:00:00Z', 1, 'Year')", "2021-02-28T00:00:00.0000000Z"),
    ("addToTime('2018-01-01T00:00:00Z', 90, 'Second')", "2018-01-01T00:01:30.0000000Z"),
    ("subtractFromTime('2018-01-02T00:00:00Z', 1, 'Day')", "2018-01-01T00:00:00.0000000Z"),
    ("subtractFromTime('2018-03-31T00:00:00Z', 1, 'Month')", "2018-02-28T00:00:00.0000000Z"),
    ("subtractFromTime('2018-01-01T05:00:00Z', 2, 'Hour', 'HH:mm')", "03:00"),
    ("startOfHour('2018-03-15T13:30:30Z')", "2018-03-15T13:00:00.0000000Z"),
    ("startOfMonth('2018-03-15T13:30:30Z', 'yyyy-MM-dd')", "2018-03-01"),
    ("dayOfMonth('2018-03-15T13:27:36Z')", 15),
    ("dayOfWeek('2018-03-18T00:00:00Z')", 0),   # a Sunday
    ("dayOfWeek('2018-03-15T13:27:36Z')", 4),
    ("dayOfYear('2018-12-31T00:00:00Z')", 365),
    ("ticks('0001-01-01T00:00:00Z')", 0),
    ("ticks('1970-01-01T00:00:00Z')", 621355968000000000),
    ("formatDateTime('03/15/2018 12:00:00', 'yyyy-MM-ddTHH:mm:ss')", "2018-03-15T12:00:00"),
    ("formatDateTime('03/15/2018', 'yyyy-MM-dd')", "2018-03-15"),
    ("getFutureTime(1, 'Month')", "2026-02-28T10:15:00.0000000Z"),
    ("getPastTime(5, 'Minute', 'HH:mm')", "10:10"),
])
def test_date_functions(expr, want):
    assert ev(expr) == want


@pytest.mark.parametrize("expr, message", [
    ("addToTime('2018-01-01T00:00:00Z', 1, 'Fortnight')", r"Second, Minute, Hour, Day, Week, Month or Year"),
    ("addToTime('9999-12-01T00:00:00Z', 1, 'Month')", r"out of range"),
    ("subtractFromTime('0001-01-01T00:00:00Z', 1, 'Day')", r"out of range"),
    ("dayOfMonth('15/03/2018')", r"isn't a date and time this PC can read"),
])
def test_date_functions_refuse_what_they_cant_do(expr, message):
    with pytest.raises(ExpressionError, match=message):
        ev(expr)
