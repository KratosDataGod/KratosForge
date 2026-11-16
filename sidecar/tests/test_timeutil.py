"""One rule for reading times (timeutil): a time without an offset is UTC, and a missing or unreadable
time is None, never an error. Fabric's job times have no offset and the app's own have +00:00."""

import datetime as dt

import pytest

from fabric_desktop import timeutil

UTC = dt.timezone.utc
NOON = dt.datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", [
    "2026-11-16T12:00:00Z",
    "2026-11-16T12:00:00+00:00",
    "2026-11-16T12:00:00",  # naive: Fabric's startTimeUtc / endTimeUtc
    "2026-11-16T13:00:00+01:00",
    " 2026-11-16T12:00:00Z ",
    dt.datetime(2026, 11, 16, 12, 0),  # naive datetime
    NOON,
    NOON.timestamp(),  # epoch seconds
    int(NOON.timestamp()),
])
def test_naive_aware_and_epoch_times_read_as_the_same_instant(value):
    t = timeutil.parse_time(value)
    assert t == NOON and t.tzinfo is not None


def test_fabric_seven_digit_fractions_are_read():
    assert timeutil.parse_time("2026-11-16T12:00:00.1234567") == NOON.replace(microsecond=123456)


@pytest.mark.parametrize("value", [None, "", "yesterday", "2026-13-01T00:00:00", "12:00", True, False, 0, [], {},
                                   float("nan"), 1e20])
def test_missing_or_malformed_times_are_none(value):
    assert timeutil.parse_time(value) is None


def test_iso_writes_utc_to_the_second():
    assert timeutil.iso("2026-11-16T13:00:00.9+01:00") == "2026-11-16T12:00:00+00:00"
    assert timeutil.iso("2026-11-16T12:00:00") == "2026-11-16T12:00:00+00:00"
    assert timeutil.iso("nope") is None


def test_now_iso_is_aware_utc_to_the_second():
    before = dt.datetime.now(UTC).replace(microsecond=0)
    text = timeutil.now_iso()
    assert text.endswith("+00:00") and "." not in text
    assert before <= dt.datetime.fromisoformat(text) <= dt.datetime.now(UTC)


def test_seconds_between_mixes_naive_and_aware():
    assert timeutil.seconds_between("2026-11-16T12:00:00+00:00", "2026-11-16T12:02:30") == 150.0
    assert timeutil.seconds_between("2026-11-16T12:00:00Z", "2026-11-16T11:59:00Z") == -60.0
    assert timeutil.seconds_between(None, "2026-11-16T12:00:00Z") is None
    assert timeutil.seconds_between("2026-11-16T12:00:00Z", "later") is None


def test_progress_report_keeps_a_row_with_a_naive_time():
    from fabric_desktop import progress
    assert progress._span_days(["2026-11-01T00:00:00", "2026-11-16T00:00:00+00:00", None, "bad"]) == 15

