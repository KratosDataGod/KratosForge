"""Incident history error paths no other test reaches: stored times that can't be read, times in
another zone, and times with no zone at all, in the rows copied to the central lakehouse and in reports."""

import datetime as dt
import time

import pytest

from fabric_desktop.workloads import incident_history as ih
from fabric_desktop.workloads import sla

NOW = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)


@pytest.fixture
def chicago(monkeypatch):
    """Local time in Chicago, so a time read as local instead of UTC shows."""
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_a_snapshot_with_unreadable_times_keeps_none_and_dates_itself_from_the_times_it_can_read():
    inc = {
        "id": "inc-1",
        "state": "resolved",
        "opened_at": "not a time",
        "acknowledged_at": 12345,
        "resolved_at": "2026-11-18T12:00:00+02:00",
        "events": [{"at": "yesterday", "what": "note"}],
    }
    [row] = ih.snapshot_rows([inc], "prod", "ws")
    assert row["opened_at"] is None and row["acknowledged_at"] is None
    assert (
        row["resolved_at"] == "2026-11-18T10:00:00.000000+00:00"
    )  # another zone: stored as UTC
    assert row["as_of"] == row["resolved_at"]


def test_a_timeline_entry_with_an_unreadable_time_is_still_copied_without_one():
    [row] = ih.event_rows(
        [{"id": "inc-1", "events": [{"at": "soon", "what": "note", "text": "x"}]}],
        "prod",
        "ws",
    )
    assert row["at"] is None and row["what"] == "note" and row["text"] == "x"


def test_an_incident_with_no_readable_time_has_no_as_of():
    [row] = ih.snapshot_rows(
        [{"id": "inc-1", "state": "open", "opened_at": "never", "events": []}],
        "prod",
        "ws",
    )
    assert row["as_of"] is None


def test_a_report_leaves_out_what_it_cant_measure_but_still_counts_the_incident():
    opened = "2026-11-18T09:00:00+00:00"
    items = [
        {
            "id": "a",
            "priority": "P3",
            "state": "resolved",
            "opened_at": opened,
            "acknowledged_at": "garbled",
            "resolved_at": "garbled",
        },
        {"id": "b", "priority": "P3", "state": "open", "opened_at": "garbled"},
    ]
    out = ih.report(items, sla.DEFAULT_TARGETS, NOW, 7)
    assert (
        out["total"]["opened"] == 1 and out["total"]["resolved"] == 1
    )  # b has no readable opening: not in the window
    assert out["total"]["time_to_acknowledge"]["count"] == 0
    assert out["total"]["time_to_resolve"]["count"] == 0


def test_a_report_measures_times_given_in_another_zone_in_absolute_time():
    item = {
        "id": "a",
        "priority": "P3",
        "state": "resolved",
        "opened_at": "2026-11-18T10:00:00+02:00",
        "acknowledged_at": "2026-11-18T08:30:00Z",
        "resolved_at": "2026-11-18T03:00:00-06:00",
    }
    out = ih.report([item], sla.DEFAULT_TARGETS, NOW, 7)
    assert out["total"]["time_to_acknowledge"]["median_minutes"] == 30
    assert out["total"]["time_to_resolve"]["median_minutes"] == 60


def test_a_resolution_recorded_before_the_opening_is_not_measured():
    item = {
        "id": "a",
        "priority": "P3",
        "state": "resolved",
        "opened_at": "2026-11-18T10:00:00Z",
        "acknowledged_at": "2026-11-18T09:00:00Z",
        "resolved_at": "2026-11-18T09:00:00Z",
    }
    out = ih.report([item], sla.DEFAULT_TARGETS, NOW, 7)
    assert (
        out["total"]["time_to_acknowledge"]["count"] == 0
        and out["total"]["time_to_resolve"]["count"] == 0
    )


def test_times_with_no_zone_are_copied_as_utc_and_distinct_entries_stay_distinct(
    chicago,
):
    inc = {
        "id": "inc-1",
        "state": "open",
        "opened_at": "2026-11-18T10:00:00",
        "events": [
            {
                "at": "2026-11-18T10:00:00",
                "what": "note",
                "by": "dana",
                "text": "looking",
            },
            {
                "at": "2026-11-18T11:00:00",
                "what": "note",
                "by": "dana",
                "text": "looking",
            },
        ],
    }
    [snap] = ih.snapshot_rows([inc], "prod", "ws")
    assert snap["opened_at"] == "2026-11-18T10:00:00.000000+00:00"
    assert snap["as_of"] == "2026-11-18T11:00:00.000000+00:00"
    first, second = ih.event_rows([inc], "prod", "ws")
    assert [first["at"], second["at"]] == [
        "2026-11-18T10:00:00.000000+00:00",
        "2026-11-18T11:00:00.000000+00:00",
    ]
    assert first["record_id"] != second["record_id"]
