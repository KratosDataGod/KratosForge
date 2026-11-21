"""Mutation-killing tests for shared_schedules.py (batch 47): the id limit, the slot deadline edge,
which schedules have an interval and the request file as written."""

from __future__ import annotations

import json

from fabric_desktop import agent_config, shared_schedules
from fabric_desktop.mailbox import CONTRACT, LocalControl

T0 = 1_800_000_000.0  # 2027-01-15T08:00:00Z


def sched(when, sid="s"):
    return agent_config.Schedule(sid, "dev", ["bronze.orders"], {}, "", when, True)


def shared(sid):
    errors: list[str] = []
    raw = {"id": sid, "env": "dev", "every_minutes": 60, "shared": True, "entities": ["bronze.orders"]}
    return agent_config._schedule(raw, 0, errors), errors


def test_the_id_limit_is_the_one_agent_toml_enforces():
    ok, errors = shared("x" * shared_schedules.MAX_ID)
    assert ok is not None and errors == []
    refused, errors = shared("x" * (shared_schedules.MAX_ID + 1))
    assert refused is None and errors and "at most 40 characters" in errors[0]


def test_only_an_every_n_minutes_schedule_has_an_interval():
    assert shared_schedules.interval({"kind": "cron", "every_minutes": 15}) == 900.0
    assert shared_schedules.interval({"kind": "daily", "every_minutes": 15, "times": ["02:00"]}) is None
    assert shared_schedules.interval({"kind": "cron"}) is None
    assert shared_schedules.next_aligned({"kind": "weekly", "every_minutes": 60}, T0) is None


def test_an_agent_at_the_deadline_itself_still_creates_the_slot(tmp_path):
    backend = LocalControl(tmp_path / "ctl")
    s = sched({"kind": "cron", "every_minutes": 60})
    _, deadline = shared_schedules.slot(s.when, T0)
    assert shared_schedules.submit(backend, s, T0, deadline, "etl-01")[1] == "created"
    assert shared_schedules.submit(backend, s, T0 + 3600, deadline + 3600.5, "etl-01")[1] == "late"


def test_the_request_is_written_as_two_space_indented_json(tmp_path):
    backend = LocalControl(tmp_path / "ctl")
    s = sched({"kind": "cron", "every_minutes": 60}, sid="dev-hourly")
    rid, outcome = shared_schedules.submit(backend, s, T0, T0 + 5, "etl-02")
    assert outcome == "created" and rid == "sched-dev-hourly-20270115T0800Z"
    body = {"contract": CONTRACT, "request_id": rid, "env": "dev", "entities": ["bronze.orders"],
            "backfill": "", "cascade": False, "deadline": "2027-01-15T09:00:00Z",
            "submitted_by": "shared schedule dev-hourly, due 2027-01-15T08:00:00Z, written by agent etl-02"}
    assert backend.read(f"requests/{rid}.json").data == json.dumps(body, indent=2).encode()
