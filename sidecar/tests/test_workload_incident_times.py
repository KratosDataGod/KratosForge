"""Stored incident times (workloads/incidents.py _epoch) read the same way the SLA clocks read them:
a time with no zone is UTC on any machine, not the machine's own zone."""

import datetime as dt
import time

import pytest

from fabric_desktop.workloads import incidents


@pytest.fixture
def away_from_utc(monkeypatch):
    """Run in a zone that isn't UTC, so reading a time with no zone as local time would show."""
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset to set this process's time zone")
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


NOON_UTC = dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC).timestamp()


@pytest.mark.parametrize(
    "text", ["2026-11-18T12:00:00Z", "2026-11-18T12:00:00+00:00", "2026-11-18T12:00:00"]
)
def test_a_stored_time_with_or_without_a_zone_is_the_same_instant(away_from_utc, text):
    assert incidents._epoch(text) == NOON_UTC


def test_a_time_in_another_zone_keeps_its_offset(away_from_utc):
    assert incidents._epoch("2026-11-18T14:00:00+02:00") == NOON_UTC


@pytest.mark.parametrize("text", [None, "", "yesterday"])
def test_a_time_that_cant_be_read_is_zero(text):
    assert incidents._epoch(text) == 0.0
