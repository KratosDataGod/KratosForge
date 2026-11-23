"""The agent (agent.py): the remaining edges. Incident evaluation when an environment's control area,
agent records or Teams fail; escalation; shortcut checks that can't read a project or scan; jobs the
schedules no longer name; a lease lost with nobody waiting; an alert that may have reached Teams; and
signal handlers at start. Teams, Fabric and OneLake are fakes; nothing leaves this PC."""

from __future__ import annotations

import dataclasses
import threading
from types import SimpleNamespace

import pytest
import requests

from fabric_desktop import agent as agent_mod
from fabric_desktop import agent_config, jsonfile, shortcut_watch
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue
from fabric_desktop.mailbox import ControlFolderInUse, LocalControl
from fabric_desktop.workloads.notify import Routing

from test_agent import Clock, config_text, setup  # noqa: F401 - setup is a fixture
from test_incidents import agent_setup  # noqa: F401 - a fixture
from test_shortcut_watch import world  # noqa: F401 - a fixture

HOOK = "https://example.webhook.office.com/hook"


def messages(caplog, level="WARNING"):
    return [r.getMessage() for r in caplog.records if r.levelname == level and r.name == "fabric_desktop.agent"]


# ---- incidents


def incident_agent(cfg, monkeypatch):
    monkeypatch.setenv("TEST_INCIDENT_HOOK", HOOK)
    a = Agent(cfg, clock=Clock())
    monkeypatch.setattr(a.runtime, "meta_runs", lambda env, limit: [])
    posted = []
    monkeypatch.setattr(a, "_post", lambda url, title, lines: posted.append(title))
    return a, posted


def test_incident_ages_that_cant_be_saved_are_logged_and_kept_for_this_run(agent_setup, monkeypatch, caplog):  # noqa: F811
    a, _ = incident_agent(agent_setup, monkeypatch)
    a._incident_seen = {"dev": {"i1": (a.monotonic(), 30.0)}}

    def full(*args, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(jsonfile, "write_json", full)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        a._save_incident_clock()
    assert messages(caplog) == ["couldn't save the incident ages"]
    assert a._incident_seen["dev"]["i1"][1] == 30.0


def test_an_environment_without_a_control_area_is_skipped_and_logged(agent_setup, monkeypatch, caplog):  # noqa: F811
    a, _ = incident_agent(agent_setup, monkeypatch)

    def no_control(env):
        raise OSError("LH_Meta unreachable")
    monkeypatch.setattr(a, "_control", no_control)
    a._controls["dev"] = object()
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        assert a.evaluate_incidents() == {}
    assert "couldn't open the incident list" in messages(caplog) and "dev" not in a._controls


def test_unreadable_agent_records_leave_their_incidents_and_escalation_still_runs(agent_setup, monkeypatch, caplog,  # noqa: F811
                                                                                    tmp_path):
    a, _ = incident_agent(agent_setup, monkeypatch)
    monkeypatch.setattr(a, "_control", lambda env: LocalControl(tmp_path / "ctl"))

    def unreadable(backend):
        raise OSError("records unreadable")
    monkeypatch.setattr(agent_mod, "read_status", unreadable)
    monkeypatch.setattr(a, "incident_routing", lambda: Routing([], None, 600.0, "oncall"))
    escalated = []
    monkeypatch.setattr(agent_mod.IncidentLog, "escalate",
                        lambda self, after, to, by: escalated.append((after, to, by)) or [])
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        assert a.evaluate_incidents() == {"dev": []}
    assert "couldn't read the agent records" in messages(caplog)
    assert escalated == [(600.0, "oncall", "agent etl-01")]


def test_incidents_teams_didnt_take_are_logged_for_the_next_try(agent_setup, monkeypatch, caplog, tmp_path):  # noqa: F811
    a, _ = incident_agent(agent_setup, monkeypatch)
    monkeypatch.setattr(a, "_control", lambda env: LocalControl(tmp_path / "ctl"))
    monkeypatch.setattr(agent_mod, "send", lambda *args, **kw: {"queued": 2, "error": "ConnectionError: offline"})
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        a.evaluate_incidents()
    assert "couldn't post incidents to Teams; they'll go with the next try" in messages(caplog)


def test_a_route_whose_address_cant_be_used_gets_no_messages(agent_setup, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setenv("TEST_INCIDENT_HOOK", HOOK)
    monkeypatch.setenv("FD_ROUTE_HOOK", "http://not-secure.example.com/hook")
    route = {"name": "finance", "webhook_env": "FD_ROUTE_HOOK", "teams": "", "owners": [], "criticality": [],
             "kinds": [], "envs": []}
    cfg = dataclasses.replace(agent_setup, incidents=dataclasses.replace(agent_setup.incidents, routes=(route,)))
    a = Agent(cfg, clock=Clock())
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        routing = a.incident_routing()
    assert [r.name for r in routing.routes] == ["default"]
    assert "an incident route's address can't be used; its messages go nowhere" in messages(caplog)


# ---- shortcut checks


def test_a_project_that_cant_be_read_skips_the_shortcut_check(world, monkeypatch):  # noqa: F811
    a = world["make"]()

    def broken(cfg):
        raise ValueError("project.toml is broken")
    monkeypatch.setattr(shortcut_watch, "env_workspaces", broken)
    assert a.check_shortcuts() == {"prod": {"skipped": "the project can't be read"}}
    assert world["posts"] == []


def test_a_scan_that_fails_skips_the_environment_and_problems_are_logged(world, monkeypatch, caplog):  # noqa: F811
    a = world["make"]()

    def down(*args):
        raise ConnectionError("Fabric isn't answering")
    monkeypatch.setattr(shortcut_watch, "scan", down)
    assert a.check_shortcuts() == {"prod": {"skipped": "the check failed"}}
    monkeypatch.setattr(shortcut_watch, "scan", lambda *args: ([], ["LH_Gold: 403"]))
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        assert a.check_shortcuts() == {"prod": {"checked": 0, "broken": 0}}
    assert "couldn't list some shortcuts" in messages(caplog)


def test_a_shortcut_check_that_crashes_is_logged_and_the_agent_goes_on(world, monkeypatch, caplog):  # noqa: F811
    a = world["make"]()

    def crash():
        raise RuntimeError("bug")
    monkeypatch.setattr(a, "check_shortcuts", crash)
    with caplog.at_level("ERROR", logger="fabric_desktop.agent"):
        a.start_shortcut_check().join(timeout=30)
    assert "shortcut check crashed" in messages(caplog, "ERROR")


# ---- schedules and jobs


def sched_agent(tmp_path, setup, schedules=""):  # noqa: F811
    path = tmp_path / "s.toml"
    path.write_text(config_text(setup.project, tmp_path / "home", schedules), encoding="utf-8")
    cfg = agent_config.load(path)
    clock = Clock()
    return Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=clock), clock=clock)


HOURLY = '[[schedule]]\nid = "s"\nenv = "dev"\nevery_minutes = 60\n'


def test_a_queued_run_of_an_unchanged_schedule_is_left_as_it_is(tmp_path, setup):  # noqa: F811
    a = sched_agent(tmp_path, setup, HOURLY)
    s = a.config.schedules[0]
    job = a.enqueue(s.env, s.entities, s.backfill, source="schedule:s", dedupe_key="schedule:s")
    assert a.reconcile_schedules()["updated"] == []
    assert a.queue.get(job["id"])["payload"] == job["payload"]


def test_a_run_of_a_removed_schedule_that_cant_be_cancelled_is_not_listed(tmp_path, setup, monkeypatch):  # noqa: F811
    a = sched_agent(tmp_path, setup)
    a.queue.set_schedule_next("gone", a.clock() + 60, None)  # kept from when agent.toml named it
    a.enqueue("dev", [], "", source="schedule:gone", dedupe_key="schedule:gone")
    monkeypatch.setattr(a.queue, "cancel", lambda job_id, why: False)  # it started meanwhile
    out = a.reconcile_schedules()
    assert out["forgotten"] == ["gone"] and out["cancelled"] == []


def test_a_lost_lease_with_nobody_waiting_ends_the_heartbeat(tmp_path, setup, monkeypatch, caplog):  # noqa: F811
    a = sched_agent(tmp_path, setup)
    a.config = dataclasses.replace(a.config, job_lease_seconds=0.03)
    monkeypatch.setattr(a.queue, "heartbeat", lambda *args, **kw: False)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        a._heartbeat({"id": "j1"}, threading.Event(), None)  # returns: the lease is gone
    assert messages(caplog) == ["lost the job's lease"]


def test_a_prod_job_whose_audit_line_cant_be_written_and_has_no_tries_left_is_alerted(tmp_path, setup, monkeypatch):  # noqa: F811
    a = sched_agent(tmp_path, setup)
    monkeypatch.setattr(agent_mod, "fails_closed", lambda what, params: True)

    def unwritable(*args):
        raise OSError("audit folder read-only")
    monkeypatch.setattr(a.audit, "record", unwritable)
    monkeypatch.setattr(a.queue, "retry_later", lambda *args, **kw: {"state": "dead", "error": "not recorded"})
    alerts = []
    monkeypatch.setattr(a, "_alert", lambda job, error, crashed=False: alerts.append((job["id"], error)))
    a.run_job({"id": "j1", "payload": {"env": "dev", "entities": []}, "attempts": 3, "source": "cli"})
    assert alerts == [("j1", "not recorded")]


def test_narrowing_a_job_that_is_gone_changes_nothing(tmp_path, setup, monkeypatch):  # noqa: F811
    a = sched_agent(tmp_path, setup)
    changed = []
    monkeypatch.setattr(a.queue, "set_payload", lambda *args: changed.append(args))
    a._narrow("no-such-job", ["bronze.orders"])
    assert changed == []


def test_an_alert_that_may_have_reached_teams_isnt_sent_again(tmp_path, setup, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setenv("FD_TEST_HOOK", HOOK)
    a = sched_agent(tmp_path, setup)
    a.config = dataclasses.replace(a.config, teams_webhook_env="FD_TEST_HOOK")
    posts = []

    def no_answer(url, title, lines):
        posts.append(title)
        raise requests.exceptions.ReadTimeout("no answer")
    monkeypatch.setattr(a, "_post", no_answer)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        a._notify("Job failed", ["line"], job_id="j1")
    assert posts == ["Job failed"]
    assert messages(caplog) == ["couldn't confirm the alert reached Teams; it may have arrived, so it isn't sent again"]
    assert a._alert_outbox().pending() == []


def test_signal_handlers_are_installed_at_start_and_stop_the_agent(tmp_path, setup, monkeypatch):  # noqa: F811
    a = sched_agent(tmp_path, setup)
    handlers = {}
    monkeypatch.setattr(agent_mod, "signal", SimpleNamespace(SIGINT=2, SIGTERM=15,
                                                             signal=lambda sig, h: handlers.__setitem__(sig, h)))

    def in_use():
        raise ControlFolderInUse("another agent holds it")
    monkeypatch.setattr(a, "hold_control_folders", in_use)
    with pytest.raises(ControlFolderInUse):
        a.run_forever()
    assert sorted(handlers) == [2, 15]
    handlers[15]()
    assert a.stopping.is_set()


def test_a_control_folder_already_held_is_held_once(tmp_path, setup, monkeypatch):  # noqa: F811
    a = sched_agent(tmp_path, setup)
    a._hold_folders = True
    held = []
    monkeypatch.setattr(agent_mod, "hold_control_folder", lambda path, info: held.append(path) or SimpleNamespace())
    backend = LocalControl(tmp_path / "ctl")
    a._hold(backend)
    a._hold(backend)
    assert len(held) == 1

