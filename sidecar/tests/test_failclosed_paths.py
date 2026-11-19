"""The audit fail-closed rule holds on every path that runs Prod work, not only the app's own calls.
A call aimed at Prod, given a confirmation, an approval, a typed name or an override, runs only once its
"started" line is written (audit.fails_closed). So does a PC schedule aimed at Prod (run by Task
Scheduler through the CLI, not through server.handle) and an agent's Prod job. A new parameter that names
an environment must be one audit reads, so a new Prod path can't skip the rule."""

from __future__ import annotations

import inspect
import re

import pytest

from fabric_desktop import audit, server
from fabric_desktop.agent import Agent
from fabric_desktop.service import Service

from test_agent import setup  # noqa: F401 - setup is a fixture
from test_schedules import svc  # noqa: F401 - svc is a fixture

DAILY = {"kind": "daily", "times": ["06:00"]}


class Broken:
    """An audit log that can't be written (a full disk, a locked file)."""

    def record(self, *a, **k):
        raise OSError("disk full")


def test_a_target_given_as_an_object_counts_when_it_names_prod():
    for params in ({"target": {"kind": "meta", "env": "prod"}}, {"target": {"kind": "meta", "env": " PROD "}},
                   {"target": {"kind": "meta", "envs": ["dev", "prod"]}}):
        assert audit.fails_closed("add_pc_schedule", params), params
    for params in ({"target": {"kind": "meta", "env": "dev"}}, {"target": {"kind": "notebook", "name": "prod"}},
                   {"target": {}}):
        assert not audit.fails_closed("add_pc_schedule", params), params


def test_a_prod_pc_schedule_doesnt_run_when_its_audit_line_cant_be_written(svc, monkeypatch):  # noqa: F811
    ran = []
    monkeypatch.setattr(type(svc), "_run_meta_here", lambda self, env, names: ran.append(env) or {"status": "ok"})
    prod = svc.pc_schedules.add({"kind": "meta", "env": "prod"}, DAILY)["id"]
    dev = svc.pc_schedules.add({"kind": "meta", "env": "dev"}, DAILY)["id"]
    svc._audit_log = Broken()
    row = svc.run_schedule(prod)
    assert row["status"] == "failed" and "audit log couldn't be written, so this didn't run" in row["message"]
    assert ran == []
    assert svc.pc_schedules.runs()[0]["status"] == "failed"  # Monitor shows why it didn't run
    assert svc.run_schedule(dev)["status"] == "ok" and ran == ["dev"]  # a Dev run still runs, as before


def test_a_prod_pc_schedule_writes_its_started_line_first(svc, monkeypatch):  # noqa: F811
    order = []
    monkeypatch.setattr(type(svc), "_run_meta_here", lambda self, env, names: order.append("ran") or {"status": "ok"})
    real = svc.audit.record
    monkeypatch.setattr(svc.audit, "record", lambda action, outcome, *a, **k: order.append((action, outcome)) or real(action, outcome, *a, **k))
    sid = svc.pc_schedules.add({"kind": "meta", "env": "prod", "entities": ["silver.orders"]}, DAILY)["id"]
    svc.run_schedule(sid)
    assert order[:2] == [("run_schedule", "started"), "ran"]
    started = [e for e in svc.audit.recent(10) if e["action"] == "run_schedule" and e["outcome"] == "started"]
    assert started and started[0]["target"]["target"]["env"] == "prod"


def test_a_prod_pc_schedule_skipped_for_overlap_leaves_no_started_line(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(svc), "_run_meta_here", lambda self, env, names: pytest.fail("it mustn't run"))
    monkeypatch.setattr(type(svc), "_lock", lambda self, sid, info: None)  # the last run is still going
    sid = svc.pc_schedules.add({"kind": "meta", "env": "prod"}, DAILY)["id"]
    assert svc.run_schedule(sid)["status"] == "skipped"
    assert not [e for e in svc.audit.recent(10) if e["action"] == "run_schedule"]


@pytest.mark.parametrize("result, outcome", [({"status": "ok", "message": "2 ran"}, "ok"),
                                             ({"status": "failed", "message": "silver.orders failed"}, "error")])
def test_a_prod_pc_schedule_records_how_it_went_after_its_started_line(svc, monkeypatch, result, outcome):  # noqa: F811
    monkeypatch.setattr(type(svc), "_run_meta_here", lambda self, env, names: dict(result))
    sid = svc.pc_schedules.add({"kind": "meta", "env": "prod"}, DAILY)["id"]
    svc.run_schedule(sid)
    lines = [e for e in svc.audit.recent(10) if e["action"] == "run_schedule"]
    assert [e["outcome"] for e in lines] == [outcome, "started"]  # newest first
    assert (lines[0].get("error") or "") == ("" if outcome == "ok" else "silver.orders failed")
    dev = svc.pc_schedules.add({"kind": "meta", "env": "dev"}, DAILY)["id"]
    svc.run_schedule(dev)
    assert len([e for e in svc.audit.recent(10) if e["action"] == "run_schedule"]) == 2  # Dev: as before


def _prod_agent(setup):  # noqa: F811
    import dataclasses
    return Agent(dataclasses.replace(setup, environments=["dev", "prod"]))


def test_an_agents_prod_job_waits_in_the_queue_when_its_audit_line_cant_be_written(setup, monkeypatch):  # noqa: F811
    agent = _prod_agent(setup)
    ran = []
    monkeypatch.setattr(agent.runtime, "run_environment",
                        lambda env, *a, **k: ran.append(env) or [{"entity": "silver.orders", "status": "ok", "rows_written": 1}])
    job = agent.enqueue("prod")
    claimed = agent.queue.claim(setup.name, setup.job_lease_seconds)
    agent.audit = Broken()
    agent.run_job(claimed)
    after = agent.queue.get(job["id"])
    assert ran == [] and after["state"] in ("queued", "dead")
    assert "audit log couldn't be written, so this didn't run" in after["error"]


def test_an_agents_prod_job_writes_its_started_line_before_it_runs(setup, monkeypatch):  # noqa: F811
    agent = _prod_agent(setup)
    order = []
    monkeypatch.setattr(agent.runtime, "run_environment",
                        lambda env, *a, **k: order.append("ran") or [{"entity": "silver.orders", "status": "ok", "rows_written": 1}])
    real = agent.audit.record
    monkeypatch.setattr(agent.audit, "record", lambda action, outcome, *a, **k: order.append((action, outcome)) or real(action, outcome, *a, **k))
    agent.enqueue("prod")
    agent.run_job(agent.queue.claim(setup.name, setup.job_lease_seconds))
    assert order[:2] == [("agent_job", "started"), "ran"]
    order.clear()
    agent.enqueue("dev")
    agent.run_job(agent.queue.claim(setup.name, setup.job_lease_seconds))
    assert order[0] == "ran"  # a Dev job records how it went afterwards, as before


# Parameter names that look like they name an environment but don't: each says what it is instead.
NOT_AN_ENVIRONMENT = {
    "target_workspace_name": "a workspace's display name, shown to the person; never matched against prod",
}


def test_every_parameter_that_names_an_environment_is_one_audit_reads():
    """A new Prod path takes its environment under a name audit.fails_closed reads (env, envs, target),
    or is reviewed here, so a call aimed at Prod can't run unrecorded under another name."""
    looks = re.compile(r"(^|_)(env|envs|environment|environments|stage|stages|target|targets|tier|to_env|from_env)(_|$)")
    unread = sorted({(name, method) for method in server.METHODS if audit.audited(method)
                     for name in inspect.signature(getattr(Service, method)).parameters
                     if looks.search(name) and name not in audit._PROD_PARAMS and name not in NOT_AN_ENVIRONMENT})
    assert unread == [], "read it in audit._PROD_PARAMS, or say in NOT_AN_ENVIRONMENT what it is instead"


@pytest.mark.parametrize("method", sorted(m for m in server.METHODS if audit.audited(m)))
def test_every_audited_call_aimed_at_prod_fails_closed(method):
    params = inspect.signature(getattr(Service, method)).parameters
    for name in set(params) & set(audit._PROD_PARAMS):
        assert audit.fails_closed(method, {name: "prod"}), (method, name)
        assert audit.fails_closed(method, {name: ["prod"]}), (method, name)
    for name in set(params) & audit.GUARD_PARAMS:
        assert audit.fails_closed(method, {name: True}), (method, name)
