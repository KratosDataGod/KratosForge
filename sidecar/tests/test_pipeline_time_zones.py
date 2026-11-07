"""convertTimeZone, convertFromUtc and convertToUtc in a local pipeline run: Windows time zone ids (as
Fabric takes them) through CLDR's map to IANA zones, daylight saving included."""

from __future__ import annotations

import zoneinfo

import pytest

from fabric_desktop.orchestration.evaluate import ExpressionError, Scope, evaluate
from fabric_desktop.orchestration.windows_zones import WINDOWS_TO_IANA


def ev(text: str):
    return evaluate("@" + text, Scope())


@pytest.mark.parametrize("expr, want", [
    ("convertTimeZone('2018-01-01T08:00:00.0000000Z', 'UTC', 'Pacific Standard Time')", "2018-01-01T00:00:00.0000000"),
    ("convertTimeZone('2018-01-01T08:00:00.0000000Z', 'UTC', 'Pacific Standard Time', 'D')", "Monday, January 1, 2018"),
    ("convertFromUtc('2018-01-01T08:00:00.0000000Z', 'Pacific Standard Time', 'D')", "Monday, January 1, 2018"),
    ("convertToUtc('01/01/2018 00:00:00', 'Pacific Standard Time')", "2018-01-01T08:00:00.0000000Z"),
    ("convertToUtc('01/01/2018 00:00:00', 'Pacific Standard Time', 'D')", "Monday, January 1, 2018"),
    ("convertFromUtc('2018-07-01T08:00:00Z', 'Pacific Standard Time', 'HH:mm')", "01:00"),       # daylight saving
    ("convertFromUtc('2018-07-01T08:00:00Z', 'W. Europe Standard Time', 'yyyy-MM-dd HH:mm')", "2018-07-01 10:00"),
    ("convertTimeZone('2018-07-01 09:30:00', 'India Standard Time', 'Tokyo Standard Time', 'HH:mm')", "13:00"),
    ("convertFromUtc('2018-01-01T00:00:00Z', 'pacific standard time', 'HH')", "16"),  # ids ignore case
    ("formatDateTime('2018-03-05T07:04:09Z', 'M/d/yyyy h:m:s tt')", "3/5/2018 7:4:9 AM"),
])
def test_time_zone_functions(expr, want):
    assert ev(expr) == want


@pytest.mark.parametrize("expr, message", [
    ("convertFromUtc('2018-01-01T00:00:00Z', 'America/Los_Angeles')", r"needs a Windows time zone id"),
    ("convertTimeZone('2018-01-01T00:00:00Z', 'UTC', 'Mars Standard Time')", r"needs a Windows time zone id"),
])
def test_an_unknown_zone_is_refused(expr, message):
    with pytest.raises(ExpressionError, match=message):
        ev(expr)


def test_every_mapped_zone_exists_in_the_time_zone_data():
    assert len(WINDOWS_TO_IANA) > 130 and WINDOWS_TO_IANA["UTC"] == "Etc/UTC"
    assert not [z for z in WINDOWS_TO_IANA.values() if z not in zoneinfo.available_timezones()]


@pytest.mark.parametrize("fmt", ["M", "m", "h", "H", "y", "t", "g"])
def test_a_one_letter_format_is_a_standard_format_not_a_token(fmt):
    # .NET reads "M" alone as the month/day pattern ("March 15"), so a lone token must not print 3.
    with pytest.raises(ExpressionError, match="standard date format"):
        ev(f"formatDateTime('2018-03-15T13:27:36Z', '{fmt}')")


def test_the_supported_standard_formats_still_work():
    assert ev("formatDateTime('2018-03-15T13:27:36Z', 'd')") == "03/15/2018"
    assert ev("formatDateTime('2018-03-15T13:27:36Z', 's')") == "2018-03-15T13:27:36"
