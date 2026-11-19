"""Incident SLA edges the main tests don't pin down (mutation batch 138, docs/quality/mutation-testing.md):
the default targets, the longest target a policy can set, and each clock at its exact boundary."""

import datetime as dt

from fabric_desktop.workloads import sla

OPENED = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)


def test_the_default_targets_and_the_longest_one():
    assert sla.DEFAULT_TARGETS == {
        "P1": {"acknowledge_minutes": 15, "resolve_minutes": 240},
        "P2": {"acknowledge_minutes": 30, "resolve_minutes": 240},
        "P3": {"acknowledge_minutes": 240, "resolve_minutes": 2880},
        "P4": {"acknowledge_minutes": None, "resolve_minutes": None},
    }
    assert sla.LONGEST == 525_600


def test_a_target_from_one_minute_to_a_year_is_taken_and_beyond_is_ignored():
    table, problems = sla.targets({"P1": {"acknowledge_minutes": 1, "resolve_minutes": sla.LONGEST}})
    assert table["P1"] == {"acknowledge_minutes": 1, "resolve_minutes": 525_600} and problems == []
    table, problems = sla.targets({"P1": {"acknowledge_minutes": -1, "resolve_minutes": sla.LONGEST + 1}})
    assert table["P1"] == sla.DEFAULT_TARGETS["P1"] and len(problems) == 2


def inc(**kw):
    return {"priority": "P1", "state": "open", "opened_at": "2026-11-18T12:00:00Z"} | kw


def test_an_incident_with_no_opening_time_has_no_clocks():
    out = sla.status(inc(opened_at=None), sla.DEFAULT_TARGETS, OPENED)
    assert out == {"priority": "P1", "acknowledge": {"state": "none", "due": None},
                   "resolve": {"state": "none", "due": None}, "state": "none"}


def test_acknowledged_at_exactly_the_due_time_is_met():
    out = sla.status(inc(acknowledged_at="2026-11-18T12:15:00Z"), sla.DEFAULT_TARGETS, OPENED + dt.timedelta(hours=1))
    assert out["acknowledge"]["state"] == "met"
    out = sla.status(inc(acknowledged_at="2026-11-18T12:15:01Z"), sla.DEFAULT_TARGETS, OPENED + dt.timedelta(hours=1))
    assert out["acknowledge"]["state"] == "missed"


def test_at_exactly_the_due_time_an_open_clock_is_not_yet_breached():
    at_due = sla.status(inc(), sla.DEFAULT_TARGETS, OPENED + dt.timedelta(minutes=15))["acknowledge"]
    assert at_due["state"] == "at_risk" and at_due["left_minutes"] == 0
    later = sla.status(inc(), sla.DEFAULT_TARGETS, OPENED + dt.timedelta(minutes=15, seconds=1))["acknowledge"]
    assert later["state"] == "breached"


def test_at_risk_from_less_than_a_quarter_of_the_window_left_and_minutes_left_are_whole():
    table = {"P1": {"acknowledge_minutes": 100, "resolve_minutes": None}}
    quarter = sla.status(inc(), table, OPENED + dt.timedelta(minutes=75))["acknowledge"]
    assert quarter["state"] == "due" and quarter["left_minutes"] == 25
    less = sla.status(inc(), table, OPENED + dt.timedelta(minutes=75, seconds=1))["acknowledge"]
    assert less["state"] == "at_risk" and less["left_minutes"] == 24
    hour = sla.status(inc(), {"P1": {"acknowledge_minutes": 600, "resolve_minutes": None}},
                      OPENED + dt.timedelta(minutes=1))["acknowledge"]
    assert hour["left_minutes"] == 599
