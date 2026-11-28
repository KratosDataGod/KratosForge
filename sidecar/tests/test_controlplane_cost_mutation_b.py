"""Mutation batch 490: the environment cost summary's period edges, times given as datetimes or
in another time zone, and the sources, methods and assumptions each sync row carries."""
from __future__ import annotations

import datetime as dt
import json

from fabric_desktop import measurement as ms
from fabric_desktop.controlplane import cost

NOW = dt.datetime(2026, 11, 20, 12, tzinfo=dt.timezone.utc)
EAST = dt.timezone(dt.timedelta(hours=5))
WEST = dt.timezone(dt.timedelta(hours=-5))


def row(at, value=1.0, **extra):
    return {"installation_id": "pc-a", "workload_key": "optimize.sync.analysis", "metric": "optimize_sync_seconds",
            "classification": ms.MEASURED, "value": value, "unit": ms.SECONDS, "collected_at": at, **extra}


def seconds(rows, **kw):
    return cost.summary(rows, now=NOW, **kw)["sync_overhead"]["optimize_sync_seconds"]


def test_the_period_includes_its_first_and_last_moment():
    start = NOW - dt.timedelta(days=30)
    assert seconds([row(start.isoformat()), row(NOW.isoformat(), 2.0)])["value"] == 3.0


def test_the_period_is_30_days_unless_said():
    got = cost.summary([], now=NOW)
    assert got["period"] == {"start": (NOW - dt.timedelta(days=30)).isoformat(), "end": NOW.isoformat(), "days": 30}


def test_without_a_time_given_the_period_ends_now():
    before = dt.datetime.now(dt.timezone.utc)
    end = dt.datetime.fromisoformat(cost.summary([])["period"]["end"])
    assert before <= end <= dt.datetime.now(dt.timezone.utc)


def test_a_row_time_given_as_a_datetime_counts_with_or_without_a_zone():
    aware = NOW - dt.timedelta(days=1)
    assert seconds([row(aware), row(aware.replace(tzinfo=None), 2.0)])["value"] == 3.0


def test_a_row_time_in_another_zone_is_read_in_that_zone():
    later = (NOW + dt.timedelta(hours=1)).astimezone(WEST)  # 08:00 in -05:00 is after NOW, not before it
    assert seconds([row(later), row(later.isoformat())])["parts"] == 0
    earlier = (NOW - dt.timedelta(days=30, hours=1)).astimezone(EAST)  # an hour before the period starts
    assert seconds([row(earlier), row(earlier.isoformat())])["parts"] == 0


def test_a_row_time_that_isnt_text_or_a_datetime_is_skipped():
    assert seconds([row(12345), row((NOW - dt.timedelta(days=1)).isoformat())])["parts"] == 1


def test_each_rows_source_method_and_assumptions_are_kept():
    got = seconds([row((NOW - dt.timedelta(days=1)).isoformat(), source="Optimize sync on this PC", method="timed",
                       assumptions=json.dumps(["one sync per run"]))])
    assert got["sources"] == ["Optimize sync on this PC"] and got["methods"] == ["timed"]
    assert got["assumptions"] == ["one sync per run"]


def test_net_is_rounded_to_six_places():
    side = {"classification": ms.MEASURED, "unit": ms.CURRENCY, "currency": "USD"}
    assert cost.net({**side, "value": 0.1234567}, {**side, "value": 0.0})["value"] == 0.123457
