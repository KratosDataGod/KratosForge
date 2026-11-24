"""SLA targets and clocks (workloads/sla.py) on input the other tests don't send: a policy entry that
isn't an object, timestamps that can't be read, timestamps in another zone, and timestamps with no zone."""

import datetime as dt

import pytest

from fabric_desktop.workloads import sla

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 18, 13, tzinfo=UTC)


def inc(**kw):
    return {"priority": "P1", "state": "open", "opened_at": "2026-11-18T12:00:00Z"} | kw


def test_a_priority_whose_targets_arent_an_object_is_ignored_and_the_rest_still_apply():
    table, problems = sla.targets({"P1": 30, "P2": {"acknowledge_minutes": 5}})
    assert table["P1"] == sla.DEFAULT_TARGETS["P1"]
    assert table["P2"] == {"acknowledge_minutes": 5, "resolve_minutes": 240}
    assert problems == ["incident_sla.P1 isn't an object; ignored."]


@pytest.mark.parametrize("opened", ["yesterday", "2026-13-40T00:00:00Z", 12345])
def test_an_opening_time_that_cant_be_read_has_no_clocks_rather_than_an_error(opened):
    out = sla.status(inc(opened_at=opened), sla.DEFAULT_TARGETS, NOW)
    assert out["state"] == "none" and out["acknowledge"] == {
        "state": "none",
        "due": None,
    }


def test_an_acknowledgement_time_that_cant_be_read_leaves_the_clock_running():
    out = sla.status(inc(acknowledged_at="not a time"), sla.DEFAULT_TARGETS, NOW)
    assert out["acknowledge"] == {
        "state": "breached",
        "due": "2026-11-18T12:15:00Z",
        "over_minutes": 45,
    }


def test_an_opening_time_in_another_zone_is_measured_in_utc():
    out = sla.status(
        inc(opened_at="2026-11-18T14:00:00+02:00"), sla.DEFAULT_TARGETS, NOW
    )
    assert (
        out["acknowledge"]["due"] == "2026-11-18T12:15:00Z"
        and out["acknowledge"]["state"] == "breached"
    )
    assert out["resolve"] == {
        "state": "due",
        "due": "2026-11-18T16:00:00Z",
        "left_minutes": 180,
    }


@pytest.mark.parametrize(
    "field, value, ack",
    [
        (
            "opened_at",
            "2026-11-18T12:00:00",
            {"state": "breached", "due": "2026-11-18T12:15:00Z", "over_minutes": 45},
        ),
        (
            "opened_at",
            "2026-11-18",
            {"state": "breached", "due": "2026-11-18T00:15:00Z", "over_minutes": 765},
        ),
        (
            "acknowledged_at",
            "2026-11-18T12:05:00",
            {"state": "met", "due": "2026-11-18T12:15:00Z"},
        ),
    ],
)
def test_a_timestamp_with_no_zone_is_read_as_utc(field, value, ack):
    assert (
        sla.status(inc(**{field: value}), sla.DEFAULT_TARGETS, NOW)["acknowledge"]
        == ack
    )
