"""Incident history edges the main tests don't pin down (mutation batch 139, docs/quality/mutation-testing.md):
defaults for fields an incident leaves out, when it last changed, a record of copies that can't be read,
naive times, and a report's window at its exact ends."""

import datetime as dt

from fabric_desktop.workloads import incident_history as ih
from fabric_desktop.workloads import sla

NOW = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)


def test_the_documented_limits():
    assert ih.KEEP_SENT == 50_000 and ih.REPORT_DAYS == (7, 30, 90, 365)


def test_a_snapshot_of_an_incident_that_leaves_fields_out_gets_the_defaults_and_its_opening_time():
    [row] = ih.snapshot_rows([{"id": "inc-1", "state": "open", "opened_at": "2026-11-18T10:00:00Z"}], "prod", "ws")
    assert row["source"] == "detected"
    assert row["owner"] == row["team"] == row["criticality"] == row["workload"] == row["title"] == row["resolution"] == ""
    assert row["as_of"] is not None and row["as_of"] == row["opened_at"]


def test_a_snapshot_keeps_the_fields_an_incident_has():
    inc = {"id": "inc-1", "state": "resolved", "source": "reported", "owner": "dana", "team": "data",
           "criticality": "high", "workload": "meta:s:o", "title": "Late", "resolution": "fixed",
           "opened_at": "2026-11-18T10:00:00Z", "events": [{"at": "2026-11-18T11:00:00Z", "what": "note"}]}
    [row] = ih.snapshot_rows([inc], "prod", "ws")
    assert (row["source"], row["owner"], row["team"], row["criticality"], row["workload"], row["title"],
            row["resolution"]) == ("reported", "dana", "data", "high", "meta:s:o", "Late", "fixed")
    assert row["as_of"] > row["opened_at"]


def test_an_event_without_text_is_copied_with_empty_text():
    [row] = ih.event_rows([{"id": "inc-1", "events": [{"at": "2026-11-18T10:00:00Z", "what": "opened"}]}], "prod", "ws")
    assert row["text"] == ""


def test_a_record_of_copies_that_cant_be_read_sends_everything_again(tmp_path):
    path = tmp_path / "sent.json"
    path.write_text("{not json")
    rows = [{"record_id": "a"}, {"record_id": "b"}]
    assert ih.Sent(path).new("lh", rows) == rows
    assert ih.Sent(tmp_path).new("lh", rows) == rows  # a folder where the file should be can't be read either


def test_a_naive_time_is_read_as_utc():
    assert ih._minutes("2026-11-18T10:00:00", "2026-11-18T10:30:00Z") == 30


def inc(opened, **kw):
    return {"id": "i", "priority": "P1", "state": "resolved", "opened_at": opened, "acknowledged_at": opened,
            "resolved_at": opened} | kw


def test_a_report_counts_incidents_opened_at_exactly_either_end_of_its_window():
    since = (NOW - dt.timedelta(days=7)).isoformat()
    out = ih.report([inc(since), inc(NOW.isoformat())], sla.DEFAULT_TARGETS, NOW, 7)
    assert out["total"]["opened"] == 2
    assert out["total"]["sla"]["acknowledge"]["met_percent"] == 100
