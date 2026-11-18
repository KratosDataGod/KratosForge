"""Kill tests for workloads/incident_history.py (docs/quality/mutation-testing.md, batch NN): the
times read, the rows copied, the record ids this PC remembers, and the report's windows, ranks and
SLA counts at their exact boundaries."""

from __future__ import annotations

import datetime as dt
import json

from fabric_desktop.optimize.shared import record_id
from fabric_desktop.workloads import incident_history as ih
from fabric_desktop.workloads import sla

UTC = dt.UTC
NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


def _inc(n, opened, state="open", acked=None, resolved=None, priority="P1", **kw):
    return {"id": f"inc-{n}", "number": f"INC-DEV-{n:04d}", "kind": "reported", "priority": priority,
            "state": state, "opened_at": opened, "acknowledged_at": acked, "resolved_at": resolved} | kw


# ---------------------------------------------------------------------------- times


def test_ts_reads_z_offsets_and_gives_none_for_nothing_or_bad_or_naive_times():
    assert ih._ts("2026-10-18T06:00:00Z") == "2026-10-18T06:00:00.000000+00:00"
    assert ih._ts("2026-10-18T08:00:00+02:00") == "2026-10-18T06:00:00.000000+00:00"
    assert ih._ts("") is None and ih._ts(None) is None
    assert ih._ts("not a time") is None
    assert ih._ts("2026-10-18T06:00:00") is None  # no time zone


def test_as_of_is_the_newest_time_of_the_events_the_open_and_the_resolve():
    inc = {"opened_at": "2026-10-18T06:00:00Z", "resolved_at": "2026-10-18T07:00:00Z",
           "events": [{"at": "2026-10-18T06:30:00Z"}, {"at": "garbage"}]}
    assert ih._as_of(inc) == "2026-10-18T07:00:00.000000+00:00"
    later = dict(inc, events=[{"at": "2026-10-18T09:00:00Z"}])
    assert ih._as_of(later) == "2026-10-18T09:00:00.000000+00:00"
    assert ih._as_of({"opened_at": "2026-10-18T06:00:00Z"}) == "2026-10-18T06:00:00.000000+00:00"
    assert ih._as_of({}) is None and ih._as_of({"events": None, "opened_at": "x"}) is None


def test_when_reads_naive_times_as_utc_and_bad_ones_as_none():
    assert ih._when("2026-10-18T06:00:00") == dt.datetime(2026, 10, 18, 6, tzinfo=UTC)
    assert ih._when("2026-10-18T06:00:00Z") == dt.datetime(2026, 10, 18, 6, tzinfo=UTC)
    plus2 = ih._when("2026-10-18T08:00:00+02:00")
    assert plus2.utcoffset() == dt.timedelta(hours=2) and plus2 == dt.datetime(2026, 10, 18, 6, tzinfo=UTC)
    assert ih._when("") is None and ih._when(None) is None and ih._when("nope") is None


def test_minutes_between_two_times_zero_when_equal_and_none_when_backwards_or_missing():
    assert ih._minutes("2026-10-18T06:00:00Z", "2026-10-18T06:30:00Z") == 30.0
    assert ih._minutes("2026-10-18T06:00:00Z", "2026-10-18T06:00:00Z") == 0.0
    assert ih._minutes("2026-10-18T06:00:01Z", "2026-10-18T06:00:00Z") is None
    assert ih._minutes(None, "2026-10-18T06:00:00Z") is None
    assert ih._minutes("2026-10-18T06:00:00Z", None) is None
    assert ih._minutes("2026-10-18T06:00:00Z", "2026-10-18T06:00:30Z") == 0.5


# ---------------------------------------------------------------------------- rows


def test_event_rows_carry_every_field_and_redact_the_text():
    inc = _inc(1, "2026-10-18T06:00:00Z", kind="detected_kind", env="ignored",
               events=[{"at": "2026-10-18T06:00:00Z", "by": "dana", "what": "noted", "text": "password=hunter2"},
                       {"at": None, "by": None, "what": "seen", "text": None}])
    first, second = ih.event_rows([inc], "prod", "w1")
    at = "2026-10-18T06:00:00.000000+00:00"
    assert first["record_id"] == record_id("event", "inc-1", at, "noted", "dana", "password=hunter2")
    assert {k: v for k, v in first.items() if k not in ("record_id", "text")} == {
        "incident_id": "inc-1", "number": "INC-DEV-0001", "env": "prod", "workspace_id": "w1",
        "kind": "detected_kind", "priority": "P1", "at": at, "by": "dana", "what": "noted"}
    assert "hunter2" not in first["text"]
    assert second["at"] is None and second["text"] == "" and second["by"] is None
    assert ih.event_rows([{"id": "x", "events": None}], "prod", "w1") == []


def test_snapshot_rows_fill_defaults_and_key_on_every_field():
    [row] = ih.snapshot_rows([{"id": "inc-9", "state": "open", "opened_at": "2026-10-18T06:00:00Z"}], "dev", "w2")
    assert row["source"] == "detected"
    assert (row["owner"], row["team"], row["criticality"], row["workload"], row["title"], row["resolution"]) == \
        ("", "", "", "", "", "")
    assert row["env"] == "dev" and row["workspace_id"] == "w2" and row["incident_id"] == "inc-9"
    assert row["opened_at"] == "2026-10-18T06:00:00.000000+00:00" and row["as_of"] == row["opened_at"]
    assert row["acknowledged_at"] is None and row["resolved_at"] is None
    rest = {k: v for k, v in row.items() if k != "record_id"}
    assert row["record_id"] == record_id("snapshot", *(rest[k] for k in sorted(rest)))
    full = {"id": "inc-9", "state": "resolved", "source": "reported", "owner": "o", "team": "t",
            "criticality": "high", "workload": "wl", "title": "T", "resolution": "fixed",
            "acknowledged_at": "2026-10-18T06:05:00Z", "resolved_at": "2026-10-18T07:00:00Z"}
    [r2] = ih.snapshot_rows([full], "dev", "w2")
    assert (r2["source"], r2["owner"], r2["team"], r2["criticality"], r2["workload"], r2["title"],
            r2["resolution"]) == ("reported", "o", "t", "high", "wl", "T", "fixed")
    assert r2["acknowledged_at"] == "2026-10-18T06:05:00.000000+00:00"
    assert r2["resolved_at"] == "2026-10-18T07:00:00.000000+00:00"


# ---------------------------------------------------------------------------- what was sent


def test_sent_remembers_ids_per_lakehouse_in_order_without_repeats(tmp_path):
    sent = ih.Sent(tmp_path / "sent.json")
    rows = [{"record_id": "a"}, {"record_id": "b"}, {"record_id": "c"}]
    assert sent.new("lh1", rows) == rows  # no file yet
    sent.add("lh1", ["a", "b"])
    sent.add("lh1", ["b", "c", "a"])
    assert json.loads((tmp_path / "sent.json").read_text()) == {"lh1": ["a", "b", "c"]}
    assert sent.new("lh1", rows + [{"record_id": "d"}]) == [{"record_id": "d"}]
    assert sent.new("lh2", rows) == rows
    sent.add("lh2", ["z"])
    assert sent._load() == {"lh1": ["a", "b", "c"], "lh2": ["z"]}


def test_sent_keeps_exactly_the_newest_50000_ids(tmp_path):
    assert ih.KEEP_SENT == 50_000
    sent = ih.Sent(tmp_path / "sent.json")
    sent.add("lh", [str(i) for i in range(50_001)])
    kept = sent._load()["lh"]
    assert len(kept) == 50_000 and kept[0] == "1" and kept[-1] == "50000"


def test_sent_reads_a_non_object_or_unreadable_file_as_empty(tmp_path):
    path = tmp_path / "sent.json"
    path.write_text("[1, 2]")
    assert ih.Sent(path)._load() == {}
    folder = tmp_path / "dir.json"
    folder.mkdir()
    assert ih.Sent(folder)._load() == {}


# ---------------------------------------------------------------------------- report


def test_spread_is_count_median_and_p90_by_nearest_rank():
    assert ih._spread([]) == {"count": 0, "median_minutes": None, "p90_minutes": None}
    assert ih._spread([7.4]) == {"count": 1, "median_minutes": 7, "p90_minutes": 7}
    assert ih._spread([2.0, 1.0]) == {"count": 2, "median_minutes": 1, "p90_minutes": 2}
    ten = [float(i) for i in range(10, 0, -1)]  # 1..10, unsorted
    assert ih._spread(ten) == {"count": 10, "median_minutes": 5, "p90_minutes": 9}
    eleven = [float(i) for i in range(1, 12)]
    assert ih._spread(eleven) == {"count": 11, "median_minutes": 6, "p90_minutes": 10}
    assert ih._spread([1.6, 2.4, 3.5]) == {"count": 3, "median_minutes": 2, "p90_minutes": 4}


def test_report_window_includes_both_ends_and_leaves_out_the_future():
    since = NOW - dt.timedelta(days=7)
    items = [_inc(1, _iso(since)), _inc(2, _iso(NOW)), _inc(3, _iso(since - dt.timedelta(seconds=1))),
             _inc(4, _iso(NOW + dt.timedelta(seconds=1))), _inc(5, None), _inc(6, "bad")]
    out = ih.report(items, sla.DEFAULT_TARGETS, NOW, 7)
    assert out["days"] == 7 and out["since"] == "2026-11-04T12:00:00Z"
    assert out["total"]["opened"] == 2


def test_report_groups_by_priority_in_order_falling_back_to_the_default_priority():
    items = [_inc(1, "2026-11-11T10:00:00Z", priority="P3"),
             _inc(2, "2026-11-11T10:00:00Z", priority=None, criticality="high"),  # default P2
             _inc(3, "2026-11-11T10:00:00Z", priority="P9")]
    out = ih.report(items, sla.DEFAULT_TARGETS, NOW, 7)
    assert [p["priority"] for p in out["priorities"]] == ["P2", "P3", "P9"]
    assert all(p["opened"] == 1 for p in out["priorities"])
    assert ih.report([], sla.DEFAULT_TARGETS, NOW, 7)["priorities"] == []


def test_report_measures_and_counts_slas_from_the_latest_state():
    items = [
        # acknowledged after 10 min, resolved after 60: both met for P1 (15 / 240)
        _inc(1, "2026-11-11T10:00:00Z", "resolved", "2026-11-11T10:10:00Z", "2026-11-11T11:00:00Z"),
        # resolved without an acknowledgement: resolving acknowledges, after 20 min (missed)
        _inc(2, "2026-11-11T10:00:00Z", "resolved", None, "2026-11-11T10:20:00Z"),
        # acknowledged, still open and inside its resolve window: ack met, resolve due (not counted)
        _inc(3, "2026-11-11T11:50:00Z", "acknowledged", "2026-11-11T11:55:00Z", "2026-11-11T11:56:00Z"),
        # open, unacknowledged, 2 h in: ack breached
        _inc(4, "2026-11-11T10:00:00Z", "open"),
        # acknowledged before it opened: not measured
        _inc(5, "2026-11-11T10:00:00Z", "acknowledged", "2026-11-11T09:00:00Z"),
    ]
    total = ih.report(items, sla.DEFAULT_TARGETS, NOW, 7)["total"]
    assert (total["opened"], total["resolved"], total["open"]) == (5, 2, 3)
    assert total["time_to_acknowledge"] == {"count": 3, "median_minutes": 10, "p90_minutes": 20}
    assert total["time_to_resolve"] == {"count": 2, "median_minutes": 20, "p90_minutes": 60}
    # item 5's ack (09:00, before it opened) is met by the SLA clock; item 3's is met too
    assert total["sla"]["acknowledge"] == {"met": 3, "missed": 1, "breached": 1, "met_percent": 60}
    assert total["sla"]["resolve"] == {"met": 2, "missed": 0, "breached": 0, "met_percent": 100}


def test_report_met_percent_rounds_and_is_none_with_nothing_judged():
    items = [_inc(1, "2026-11-11T10:00:00Z", "resolved", "2026-11-11T10:10:00Z", "2026-11-11T10:20:00Z"),
             _inc(2, "2026-11-11T10:00:00Z", "resolved", "2026-11-11T10:20:00Z", "2026-11-11T10:30:00Z"),
             _inc(3, "2026-11-11T10:00:00Z", "resolved", "2026-11-11T10:30:00Z", "2026-11-11T10:40:00Z")]
    total = ih.report(items, sla.DEFAULT_TARGETS, NOW, 7)["total"]
    assert total["sla"]["acknowledge"]["met_percent"] == 33  # 1 of 3
    p4 = ih.report([_inc(1, "2026-11-11T10:00:00Z", priority="P4")], sla.DEFAULT_TARGETS, NOW, 7)["total"]
    assert p4["sla"]["acknowledge"] == {"met": 0, "missed": 0, "breached": 0, "met_percent": None}
    assert p4["time_to_acknowledge"]["count"] == 0 and p4["open"] == 1 and p4["resolved"] == 0


def test_report_on_a_resolved_incident_with_no_resolve_time_measures_no_resolve():
    item = _inc(1, "2026-11-11T10:00:00Z", "resolved", "2026-11-11T10:05:00Z", None)
    total = ih.report([item], sla.DEFAULT_TARGETS, NOW, 7)["total"]
    assert total["time_to_resolve"]["count"] == 0 and total["time_to_acknowledge"]["count"] == 1
    assert total["resolved"] == 1 and total["open"] == 0


def test_reports_cover_a_week_a_month_a_quarter_and_a_year():
    assert ih.REPORT_DAYS == (7, 30, 90, 365)
