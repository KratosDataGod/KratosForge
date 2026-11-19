"""Overlap edges the main tests don't pin down (mutation batch 143, docs/quality/mutation-testing.md):
a schedule that can't be described, every table on this PC, records without a name or with the same
heartbeat, and a schedule on this PC that doesn't say whether it is enabled."""

import datetime as dt

from fabric_desktop.workloads.overlap import overlaps, placements

NOW = dt.datetime(2027, 1, 15, 8, 0, tzinfo=dt.timezone.utc)
BEAT = "2027-01-15T07:59:30Z"


def agent(name, schedules, beat=BEAT):
    return {"kind": "agent_status", "agent": name, "state": "running", "heartbeat_at": beat,
            "stale_after": "2027-01-15T08:01:30Z", "schedules": schedules}


def sched(sid, entities, when=None):
    return {"id": sid, "next": "2027-01-15T09:00:00Z", "entities": entities,
            "when": when or {"kind": "cron", "every_minutes": 60}}


def test_a_schedule_that_cant_be_described_has_empty_text():
    [row], _ = placements({"prod": [agent("etl-01", [sched("a", ["x"], {"kind": "bogus"})])]}, [], NOW)
    assert row["text"] == ""


def test_an_agents_tables_against_every_table_on_this_pc_are_the_agents_tables():
    pc = {"id": "p1", "target": {"kind": "meta", "env": "prod", "entities": []}, "text": "Daily"}
    [o] = overlaps({"prod": [agent("etl-01", [sched("a", ["y", "x"])])]}, [pc], NOW)["overlaps"]
    assert o["entities"] == ["x", "y"]


def test_a_pc_schedule_that_doesnt_say_enabled_counts_and_keeps_its_text():
    pc = {"id": "p1", "target": {"kind": "meta", "env": "prod", "entities": ["x"]}, "text": "Every day at 06:00"}
    known, _ = placements({"prod": []}, [pc], NOW)
    assert [(k["schedule"], k["text"], k["entities"]) for k in known] == [("p1", "Every day at 06:00", ["x"])]
    known, _ = placements({"prod": []}, [pc | {"text": None}], NOW)
    assert known[0]["text"] == ""


def test_a_record_without_an_agent_name_is_left_out():
    known, unknown = placements({"prod": [agent("", [sched("a", ["x"])]), agent(None, [sched("b", ["x"])])]}, [], NOW)
    assert known == [] and unknown == []


def test_of_two_records_with_the_same_heartbeat_the_later_one_speaks():
    first = agent("etl-01", [sched("a", ["x"])])
    second = agent("etl-01", [sched("b", ["y"])])
    known, _ = placements({"prod": [first, second]}, [], NOW)
    assert [k["schedule"] for k in known] == ["b"]
