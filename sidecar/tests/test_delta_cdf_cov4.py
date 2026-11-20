"""delta_cdf.py: a time zone-aware starting time, and the messages for delta-rs errors with fakes in place of
a Delta table."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fabric_desktop import delta_cdf


class Table:
    def __init__(self, error, version=None):
        self.error, self._version = error, version

    def metadata(self):
        return type("M", (), {"configuration": None})()

    def load_cdf(self, **arguments):
        self.arguments = arguments
        raise self.error

    def version(self):
        if self._version is None:
            raise OSError("log can't be read")
        return self._version


def test_an_aware_datetime_is_kept_as_its_instant():
    when = datetime(2026, 10, 20, 15, 0, tzinfo=timezone(timedelta(hours=2)))
    assert delta_cdf.bounds({"startingtimestamp": when}) == {"starting_timestamp": "2026-10-20T13:00:00Z"}


def test_a_naive_datetime_is_in_the_session_zone():
    seen = []

    def instant(text):
        seen.append(text)
        return datetime.fromisoformat(text).replace(tzinfo=timezone(timedelta(hours=-5)))
    assert delta_cdf.bounds({"startingversion": 0, "endingtimestamp": datetime(2026, 1, 1, 7)}, instant) == \
        {"starting_version": 0, "ending_timestamp": "2026-01-01T12:00:00Z"}
    assert seen == ["2026-01-01 07:00:00"]


def test_a_missing_version_without_a_readable_latest_leaves_it_out():
    table = Table(RuntimeError("Invalid table version: 9"))
    with pytest.raises(ValueError) as caught:
        delta_cdf.read(table, "orders", {"startingversion": 9})
    text = str(caught.value)
    assert text.startswith("[DELTA_INVALID_CDC_RANGE] CDC range from start 9 to end latest was invalid: orders has "
                           "no version 9, ")
    assert "latest version is" not in text and "DESCRIBE HISTORY orders" in text
    assert table.arguments == {"starting_version": 9}


def test_a_missing_version_names_the_latest_when_it_can():
    with pytest.raises(ValueError, match="no version 9; its latest version is 3, "):
        delta_cdf.read(Table(RuntimeError("Invalid table version: 9"), version=3), "orders", {"startingversion": 9})


def test_any_other_error_is_said_with_its_text_and_range():
    with pytest.raises(ValueError) as caught:
        delta_cdf.read(Table(OSError("  disk gone \n")), "orders",
                       {"startingtimestamp": "2026-01-01", "endingversion": "4"})
    assert str(caught.value) == ("Nothing was read from the change data feed of orders from 2026-01-01T00:00:00Z "
                                 "to 4: disk gone")
