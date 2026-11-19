"""Queue view edges the main tests don't pin down (mutation batch 146, docs/quality/mutation-testing.md):
times at exactly now and at the end of the window, the order of the states, defaults for missing
names, and a shared schedule only one agent holds."""

import datetime as dt

from fabric_desktop.workloads.queue import build

UTC = dt.timezone.utc
NOW = dt.datetime(2027, 1, 15, 8, 0, tzinfo=UTC)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def agent(name, live=True, **kw):
    beat = NOW - (dt.timedelta(seconds=30) if live else dt.timedelta(minutes=20))
    return {"kind": "agent_status", "agent": name, "state": "running", "heartbeat_at": iso(beat),
            "stale_after": iso(beat + dt.timedelta(minutes=3)), **kw}


def pc_local(t):
    return t.astimezone().replace(tzinfo=None).isoformat()


def test_a_live_agents_schedule_due_now_is_next_with_no_note():
    rec = agent("etl-01", schedules=[{"id": "h", "next": iso(NOW - dt.timedelta(minutes=1)), "entities": ["x"]},
                                     {"id": "n", "next": iso(NOW + dt.timedelta(hours=1)), "entities": ["y"]}])
    rows = build({"prod": [rec]}, [], [], [], {}, NOW)
    assert [(r["state"], r["note"]) for r in rows] == [("next", ""), ("next", "")]


def test_an_offline_agents_schedule_at_exactly_now_is_missed_and_a_later_one_notes_the_last_report():
    rec = agent("etl-02", live=False, schedules=[{"id": "h", "next": iso(NOW), "entities": ["x"]},
                                                 {"id": "l", "next": iso(NOW + dt.timedelta(hours=1)), "entities": ["y"]}])
    rows = build({"prod": [rec]}, [], [], [], {}, NOW)
    assert [(r["state"], r["what"], r["note"]) for r in rows] == [
        ("missed", "x", "Runs once when etl-02 is back"), ("next", "y", "From its last report")]


def test_the_window_ends_at_exactly_24_hours_by_default():
    end = NOW + dt.timedelta(hours=24)
    rec = agent("etl-01", schedules=[{"id": "edge", "next": iso(end), "entities": ["x"]},
                                     {"id": "after", "next": iso(end + dt.timedelta(minutes=30)), "entities": ["y"]}])
    pc = [{"id": "p1", "next_run": pc_local(end), "target": {"kind": "meta", "env": "dev", "entities": []}},
          {"id": "p2", "next_run": pc_local(end + dt.timedelta(minutes=30)), "target": {"kind": "meta", "env": "dev"}}]
    rows = build({"prod": [rec]}, pc, [], [], {}, NOW)
    assert sorted(r["owner"] for r in rows) == ["Windows Task Scheduler", "etl-01's schedule edge"]


def test_a_pc_schedule_that_doesnt_say_enabled_runs_and_keeps_its_env_and_empty_text():
    pc = [{"id": "p1", "next_run": pc_local(NOW + dt.timedelta(hours=1)),
           "target": {"kind": "meta", "env": "dev", "entities": ["silver_orders"]}}]
    [row] = build({}, pc, [], [], {}, NOW)
    assert (row["env"], row["what"], row["text"]) == ("dev", "silver_orders", "")


def test_fabric_rows_without_names_say_what_they_are():
    runs = [{"status": "InProgress", "workspace_id": "ws"}]
    scheds = [{"enabled": True, "workspace_id": "ws"}]
    run, sched = build({}, [], runs, scheds, {"ws": "test"}, NOW)
    assert run["where"]["name"] == "Fabric" and run["started_by"] == "Run from this app" and run["what"] == "Fabric job"
    assert (sched["what"], sched["where"]["name"], sched["text"], sched["note"]) == ("Fabric item", "Fabric", "", "")


def test_a_shared_schedule_held_by_one_agent_is_one_row():
    sched = {"id": "h", "next": iso(NOW + dt.timedelta(minutes=5)), "entities": ["x"], "shared": True}
    [row] = build({"dev": [agent("a", schedules=[sched])]}, [], [], [], {}, NOW)
    assert row["state"] == "next" and row["what"] == "x" and row["where"]["names"] == ["a"]


def test_a_shared_schedule_past_due_with_a_live_agent_is_next_and_at_exactly_now_with_none_is_missed():
    past = {"id": "h", "next": iso(NOW - dt.timedelta(minutes=5)), "entities": [], "shared": True}
    [row] = build({"dev": [agent("a", schedules=[past])]}, [], [], [], {}, NOW)
    assert row["state"] == "next"
    at_now = past | {"next": iso(NOW)}
    [row] = build({"dev": [agent("a", live=False, schedules=[at_now])]}, [], [], [], {}, NOW)
    assert row["state"] == "missed"


def test_rows_sort_by_state_then_time_then_name():
    live = agent("live", running=[{"entities": ["zz"], "started_at": iso(NOW)}], counts={"queued": 1},
                 schedules=[{"id": "b", "next": iso(NOW + dt.timedelta(hours=1)), "entities": ["aa"]},
                            {"id": "c", "next": iso(NOW + dt.timedelta(hours=2)), "entities": ["a0"]},
                            {"id": "d", "next": iso(NOW - dt.timedelta(minutes=30)), "entities": ["ab"]}])
    off = agent("off", live=False, running=[{"entities": ["aa"], "started_at": iso(NOW)}],
                schedules=[{"id": "m", "next": iso(NOW - dt.timedelta(minutes=10)), "entities": ["zy"]}])
    rows = build({"prod": [live, off]}, [], [], [], {}, NOW)
    assert [(r["state"], r["what"]) for r in rows] == [
        ("running", "zz"), ("unknown", "aa"), ("queued", "1 job"), ("missed", "zy"), ("next", "ab"), ("next", "aa"),
        ("next", "a0")]
