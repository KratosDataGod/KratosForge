"""Mutation-killing tests for measurement.py (batch 43): limits, rounding, ids, freshness and the wording of totals."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import measurement as ms

AT = "2026-01-01T00:00:00Z"


def measured(value, unit=ms.CU_SECONDS, **kw):
    return ms.make(ms.MEASURED, "cu", unit, value, source="jobs", collected_at=AT, **kw)


def test_an_unknown_unit_is_named_whichever_side_it_is_on():
    with pytest.raises(ValueError, match="unknown unit 'furlongs'"):
        ms.convert(1, "furlongs", ms.CU_HOURS)
    with pytest.raises(ValueError, match="unknown unit 'parsecs'"):
        ms.convert(1, ms.CU_HOURS, "parsecs")


def test_a_time_with_its_own_offset_keeps_it():
    t = ms.parse_time("2026-01-01T10:00:00+02:00")
    assert t.utcoffset() == dt.timedelta(hours=2)
    assert ms.parse_time("2026-01-01T10:00:00").utcoffset() == dt.timedelta(0)


def test_a_utilization_of_exactly_100_percent_is_valid():
    assert ms.make(ms.MEASURED, "util", ms.PERCENT, 100, source="m", collected_at=AT)["value"] == 100
    with pytest.raises(ValueError, match="over 100%"):
        ms.make(ms.MEASURED, "util", ms.PERCENT, 100.5, source="m", collected_at=AT)


@pytest.mark.parametrize("period", [{"start": "nope", "end": AT}, {"start": AT, "end": "nope"}])
def test_either_end_of_a_period_must_be_a_time(period):
    m = dict(measured(1), period=period)
    assert "period start and end must be times" in ms.problems(m)


def test_a_value_is_rounded_to_six_places():
    assert measured(1.23456789)["value"] == 1.234568


def test_the_id_is_a_fixed_hash_of_what_identifies_the_measurement():
    m = measured(5, run_id="r1")
    assert m["id"] == "m-06f0a627331e0e9a"
    assert len(m["id"]) == 18


def test_freshness_without_a_collection_time_is_never_stale():
    m = ms.make(ms.ESTIMATED, "cu", ms.CU_SECONDS, 1, method="x", assumptions=["a"])
    assert ms.freshness(m, 0) == {"age_seconds": None, "stale": False, "text": "Not collected from a source"}


def test_an_observation_exactly_at_its_maximum_age_is_fresh():
    now = dt.datetime(2026, 1, 1, 0, 1, tzinfo=dt.timezone.utc)
    assert ms.freshness(measured(1), 60, now)["stale"] is False
    assert ms.freshness(measured(1), 59, now)["stale"] is True


def test_an_age_never_goes_below_zero():
    at = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    assert ms.freshness(measured(1), None, at)["age_seconds"] == 0
    assert ms.freshness(measured(1), None, at - dt.timedelta(hours=1))["age_seconds"] == 0


def test_an_unknown_measurement_changes_unit_without_a_value():
    m = ms.unknown("cu", ms.CU_SECONDS, "no data")
    out = ms.to_unit(m, ms.CU_HOURS)
    assert out["unit"] == ms.CU_HOURS and out["value"] is None


def test_converted_values_and_totals_are_rounded_to_six_places():
    assert ms.to_unit(measured(1), ms.CU_HOURS)["value"] == 0.000278
    assert ms.total([measured(1)], "cu", ms.CU_HOURS)["value"] == 0.000278


def test_the_incomplete_reason_counts_parts_in_plain_words():
    gone = ms.unknown("cu", ms.CU_SECONDS, "no data")
    assert ms.total([gone], "cu", ms.CU_SECONDS)["reason"] == "1 of 1 part not known; the total covers the rest"
    assert ms.total([gone, measured(2)], "cu", ms.CU_SECONDS)["reason"] == (
        "1 of 2 parts not known; the total covers the rest")
