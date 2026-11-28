"""Agent status records built from plain stand-ins: which jobs and schedules a record lists and in what order,
the incident and shortcut summaries, the counters an older agent leaves out, and how publish retries.
Mutation batch 56."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fabric_desktop import agent_status


class Queue:
    def __init__(self, jobs=()):
        self.jobs = list(jobs)
        self.limits = []

    def for_env(self, env, limit):
        self.limits.append(limit)
        return list(self.jobs)

    def counts(self, env):
        return {"queued": 0}

    def schedule_next(self, sid):
        return None


def _pipeline(**kw):
    base = {"enabled": True, "environments": ("dev",), "entities": (), "allow_backfill": False, "require_run": False}
    return SimpleNamespace(**{**base, **kw})


def _incidents(**kw):
    base = {"enabled": True, "seconds": 300, "quiet_hours": "", "quiet_except": (), "escalate_after_minutes": 60,
            "escalate_to": "", "routes": ()}
    return SimpleNamespace(**{**base, **kw})


def _agent(jobs=(), pipeline=None, shortcut_health=None, **extra):
    cfg = SimpleNamespace(name="etl-01", owner="", auth={"type": "user"}, environments=("dev",), status_seconds=60,
                          concurrency=1, pipeline=pipeline or _pipeline(), incidents=_incidents(), max_queued=100,
                          schedules=(), shortcut_health=shortcut_health)
    return SimpleNamespace(config=cfg, queue=Queue(jobs), clock=lambda: 1_700_000_000.0, runtime=None,
                           started_at=None, **extra)


def _finished(i, finished_at, error=None):
    return {"id": f"j{i}", "state": "succeeded", "source": "manual", "finished_at": finished_at, "error": error,
            "attempts": 1, "payload": {}, "started_at": None}


def test_the_record_reads_up_to_200_jobs_and_lists_the_10_newest_finished_first():
    jobs = [_finished(i, 1_700_000_000.0 + i) for i in range(12)] + [_finished(99, None)]
    agent = _agent(jobs)
    recent = agent_status.record(agent, "dev")["recent"]
    assert agent.queue.limits == [200]
    assert agent_status.RECENT == 10
    assert [r["job_id"] for r in recent] == [f"j{i}" for i in range(11, 1, -1)]


def test_a_job_with_no_finish_time_sorts_as_the_oldest():
    jobs = [_finished(1, None), _finished(2, 1.0)]
    assert [r["job_id"] for r in agent_status.record(_agent(jobs), "dev")["recent"]] == ["j2", "j1"]


def test_an_error_is_cut_to_300_characters_and_an_empty_one_reads_as_none():
    jobs = [_finished(1, 2.0, "e" * 301), _finished(2, 1.0, "")]
    recent = agent_status.record(_agent(jobs), "dev")["recent"]
    assert recent[0]["error"] == "e" * 300 and recent[1]["error"] is None


def test_the_pipeline_is_enabled_only_for_its_own_environments():
    assert agent_status.record(_agent(), "dev")["pipeline"]["enabled"] is True
    assert agent_status.record(_agent(pipeline=_pipeline(environments=("prod",))), "dev")["pipeline"]["enabled"] is False
    assert agent_status.record(_agent(pipeline=_pipeline(enabled=False)), "dev")["pipeline"]["enabled"] is False


def test_counters_an_older_agent_leaves_out_read_as_zero_and_waiting_as_unknown():
    r = agent_status.record(_agent(), "dev")
    assert r["messages_dropped"] == 0 and r["schedules_deferred"] == 0 and r["messages_waiting"] is None
    r = agent_status.record(_agent(messages_dropped=2, schedules_deferred=3, messages_waiting=lambda: 4), "dev")
    assert (r["messages_dropped"], r["schedules_deferred"], r["messages_waiting"]) == (2, 3, 4)


def test_the_incident_summary_lists_routes_without_the_webhook_variable():
    inc = _incidents(enabled=1, quiet_except=("critical",), routes=(
        {"name": "on-call", "teams": ("data",), "webhook_env": "TEAMS_HOOK", "criticality": "high"},))
    got = agent_status.incidents_summary(inc)
    assert got["enabled"] is True and got["quiet_except"] == ["critical"]
    assert got["routes"] == [{"name": "on-call", "teams": ["data"], "criticality": "high"}]
    assert agent_status.record(_agent(), "dev")["incidents"]["seconds"] == 300


def test_shortcut_health_is_on_only_when_enabled_for_this_environment():
    on = SimpleNamespace(enabled=True, environments=("dev",), minutes=60)
    assert agent_status.shortcut_health_summary(SimpleNamespace(shortcut_health=on), "dev") == {
        "enabled": True, "minutes": 60}
    assert agent_status.shortcut_health_summary(SimpleNamespace(shortcut_health=on), "prod") == {
        "enabled": False, "minutes": 60}
    off = SimpleNamespace(enabled=False, environments=("dev",), minutes=30)
    assert agent_status.shortcut_health_summary(SimpleNamespace(shortcut_health=off), "dev") == {
        "enabled": False, "minutes": 30}
    assert agent_status.shortcut_health_summary(SimpleNamespace(), "dev") == {"enabled": False, "minutes": None}
    assert agent_status.record(_agent(shortcut_health=on), "dev")["shortcut_health"]["enabled"] is True


class Backend:
    def __init__(self, create=False, current=None, replace=False):
        self._create, self._current, self._replace = create, current, replace
        self.calls = []

    def create(self, key, data):
        self.calls.append(("create", key, data))
        return self._create

    def read(self, key):
        self.calls.append(("read", key))
        return self._current

    def replace(self, key, data, etag):
        self.calls.append(("replace", key, etag))
        return self._replace


def test_publish_writes_two_space_json_under_agents():
    b = Backend(create=True)
    agent_status.publish(b, "etl-01", {"a": 1})
    assert b.calls == [("create", "agents/etl-01.json", json.dumps({"a": 1}, indent=2).encode())]


def test_publish_replaces_with_the_current_etag_and_tries_three_times():
    b = Backend(current=SimpleNamespace(etag="e1"), replace=True)
    agent_status.publish(b, "etl-01", {})
    assert [c[0] for c in b.calls] == ["create", "read", "replace"] and b.calls[2][2] == "e1"
    gone = Backend(current=None)
    with pytest.raises(RuntimeError, match="kept changing"):
        agent_status.publish(gone, "etl-01", {})
    assert [c[0] for c in gone.calls] == ["create", "read"] * 3
