"""Agent status edges the main tests don't pin down (mutation batch 134, docs/quality/mutation-testing.md):
records that aren't status records, the exact moment an agent goes offline, and which record speaks for
an agent when heartbeats tie or arrive out of order."""

import datetime as dt
import json
import types

from fabric_desktop.workloads.agents import agent_state, fleet, read_status

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 18, 12, tzinfo=UTC)


class Backend:
    def __init__(self, files):
        self.files = files

    def list(self, folder):
        return list(self.files)

    def read(self, key):
        name = key.split("/", 1)[1]
        data = self.files.get(name)
        return None if data is None else types.SimpleNamespace(data=data)


def test_only_status_records_that_are_objects_are_read():
    backend = Backend({
        "a.json": json.dumps({"kind": "agent_status", "agent": "a"}).encode(),
        "b.json": json.dumps({"kind": "something_else", "agent": "b"}).encode(),
        "c.json": json.dumps(["agent_status"]).encode(),
        "d.json": b"{not json",
        "e.json": None,
    })
    assert [r["agent"] for r in read_status(backend)] == ["a"]


def test_an_agent_is_offline_from_exactly_its_stale_time():
    record = {"stale_after": "2026-11-18T12:00:00Z"}
    assert agent_state(record, NOW) == "offline"
    assert agent_state(record, NOW - dt.timedelta(seconds=1)) == "live"


def rec(env_host, beat):
    return {"agent": "etl", "host": env_host, "heartbeat_at": beat, "stale_after": "2026-11-18T13:00:00Z"}


def test_an_older_heartbeat_read_later_doesnt_speak_for_the_agent():
    [row] = fleet({"prod": [rec("new", "2026-11-18T11:59:00Z")], "dev": [rec("old", "2026-11-18T11:00:00Z")]},
                  NOW, "1.0.0")
    assert row["host"] == "new" and set(row["envs"]) == {"prod", "dev"}


def test_a_tied_heartbeat_keeps_the_first_record_read():
    [row] = fleet({"prod": [rec("first", "2026-11-18T11:59:00Z")], "dev": [rec("second", "2026-11-18T11:59:00Z")]},
                  NOW, "1.0.0")
    assert row["host"] == "first"
