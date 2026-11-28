"""Five bugs the run_as and overlap error tests found, each fixed with the test that pins it."""

import datetime as dt

import pytest

from fabric_desktop.workloads import overlap, run_as

NOW = dt.datetime(2027, 1, 15, 8, 0, tzinfo=dt.timezone.utc)


def agent(name, schedules=None, beat="2027-01-15T07:59:30Z", **kw):
    return {
        "kind": "agent_status",
        "agent": name,
        "state": "running",
        "heartbeat_at": beat,
        "stale_after": "2027-01-15T08:01:30Z",
        "host": "vm",
        "schedules": schedules,
        **kw,
    }


# Bug 1: overlap crashed (AttributeError) on an agent's schedules that aren't a list of objects.
@pytest.mark.parametrize(
    "schedules", [[None], ["nightly"], "nightly", {"id": "nightly"}]
)
def test_schedules_that_arent_a_list_of_objects_are_skipped_rather_than_crashing(
    schedules,
):
    known, unknown = overlap.placements({"prod": [agent("a", schedules)]}, [], NOW)
    assert known == []


# Bug 2: a schedule time whose describing raises something other than ValueError/TypeError/KeyError
# escaped overlap._text and crashed the agents list.
@pytest.mark.parametrize(
    "when",
    [
        {"kind": "daily", "times": [600]},
        {"kind": "daily", "times": [None]},
        {"kind": "cron", "every_minutes": float("inf")},
    ],
)
def test_a_schedule_time_with_values_of_the_wrong_type_has_empty_text(when):
    [row], _ = overlap.placements(
        {"prod": [agent("a", [{"id": "s", "entities": ["x"], "when": when}])]}, [], NOW
    )
    assert row["text"] == ""


# Bug 3: the same agent in two records of one environment showed that environment twice.
def test_the_same_agent_twice_in_one_environment_names_that_environment_once():
    out = run_as.build("", {"prod": [agent("a"), agent("a")]}, [], {}, NOW)
    [row] = [r for r in out["rows"] if r["place"]["kind"] == "agent"]
    assert row["env"] == "prod" and "Prod, Prod" not in row["boundary"]


# Bug 4: heartbeats were compared as text, so an offset time or a junk value beat a newer readable one.
# (fleet() in agents.py compares parsed times; all three now share agents.newer.)
@pytest.mark.parametrize("old_beat", ["2027-01-15T09:00:00+02:00", 99999])
def test_the_newest_heartbeat_by_time_speaks_for_an_agent(old_beat):
    older = agent(
        "a", [{"id": "old", "entities": ["x"]}], beat=old_beat, identity="user"
    )
    newer = agent(
        "a",
        [{"id": "new", "entities": ["x"]}],
        beat="2027-01-15T07:59:30Z",
        identity="managed_identity",
    )
    known, _ = overlap.placements({"prod": [older, newer]}, [], NOW)
    assert [k["schedule"] for k in known] == ["new"]
    [row] = [
        r
        for r in run_as.build("", {"prod": [older, newer]}, [], {}, NOW)["rows"]
        if r["place"]["kind"] == "agent"
    ]
    assert row["runs_as"]["kind"] == "managed_identity"


# Bug 5: a table listed twice in one schedule was reported twice in the overlap.
def test_a_table_listed_twice_is_reported_once():
    out = overlap.overlaps(
        {
            "prod": [
                agent("a", [{"id": "s", "entities": ["x", "x"]}]),
                agent("b", [{"id": "t", "entities": []}]),
            ]
        },
        [],
        NOW,
    )
    [o] = out["overlaps"]
    assert o["entities"] == ["x"]


# Run as keeps the first record when heartbeats tie or neither can be read; overlap lets the later one win.
@pytest.mark.parametrize("beat", ["2027-01-15T07:59:30Z", "not a time"])
def test_run_as_keeps_the_first_record_when_heartbeats_tie_or_cant_be_read(beat):
    first = agent("a", beat=beat, identity="user")
    second = agent("a", beat=beat, identity="managed_identity")
    rows = run_as.build("", {"prod": [first, second]}, [], {}, NOW)["rows"]
    [row] = [r for r in rows if r["place"]["kind"] == "agent"]
    assert row["runs_as"]["kind"] == "user"


@pytest.mark.parametrize("beat", ["2027-01-15T07:59:30Z", "not a time"])
def test_overlap_takes_the_later_record_when_heartbeats_tie_or_cant_be_read(beat):
    first = agent("a", [{"id": "first", "entities": ["x"]}], beat=beat)
    second = agent("a", [{"id": "second", "entities": ["x"]}], beat=beat)
    known, _ = overlap.placements({"prod": [first, second]}, [], NOW)
    assert [k["schedule"] for k in known] == ["second"]


def test_a_table_listed_twice_in_a_pc_schedule_is_reported_once():
    pc = [
        {
            "id": "p",
            "enabled": True,
            "target": {"kind": "meta", "env": "prod", "entities": ["x", "x"]},
        }
    ]
    out = overlap.overlaps(
        {"prod": [agent("b", [{"id": "t", "entities": []}])]}, pc, NOW
    )
    [o] = out["overlaps"]
    assert o["entities"] == ["x"]
