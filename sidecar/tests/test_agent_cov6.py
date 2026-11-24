"""The headless agent (agent.py), the paths the other tests leave: the incident ages that can't be saved, a control
folder held once, the shortcut check's project, scan and listing failures and a crashed check, schedules edited
in agent.toml whose waiting runs can't be cancelled or need no change, a schedule with no next run, a lease lost
with no one waiting on it, a Prod job whose audit line can't be written on its last attempt, a retry narrowed
for a job that's gone, an alert Teams may have taken, and signals installed at start. Teams, Fabric and OneLake
are stand-ins; nothing runs a metadata environment."""

from __future__ import annotations

import dataclasses
import threading

import pytest

from fabric_desktop import agent as agent_mod
from fabric_desktop import agent_config, shortcut_watch
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue
from fabric_desktop.mailbox import ControlFolderInUse, LocalControl
from test_agent import Clock, config_text, setup  # noqa: F401 - setup is a fixture
from test_shortcut_watch import world  # noqa: F401 - world is a fixture

HOOK = "https://example.webhook.office.com/hook"


def _messages(caplog, level="WARNING"):
    return [r.getMessage() for r in caplog.records if r.levelname == level]


def test_incident_ages_that_cant_be_saved_are_only_logged(setup, monkeypatch, caplog):  # noqa: F811
    agent = Agent(setup)
    agent._incident_seen = {"dev": {"i1": (agent.monotonic(), 5.0)}}

    def full(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(agent_mod.jsonfile, "write_json", full)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent._save_incident_clock()
    assert "couldn't save the incident ages" in _messages(caplog)


def test_a_control_folder_is_held_once(setup, monkeypatch, tmp_path):  # noqa: F811
    agent = Agent(setup)
    held = []
    monkeypatch.setattr(agent_mod, "hold_control_folder", lambda folder, info: held.append(folder) or "handle")
    agent._hold_folders = True
    backend = LocalControl(tmp_path / "control")
    agent._hold(backend)
    agent._hold(LocalControl(tmp_path / "control"))
    assert held == [(tmp_path / "control").resolve()]
    assert agent._held == {str((tmp_path / "control").resolve()): "handle"}


def test_a_project_that_cant_be_read_skips_that_environments_shortcuts(world, monkeypatch, caplog):  # noqa: F811
    agent = world["make"]()

    def unreadable(env):
        raise ValueError("project.toml is broken")
    monkeypatch.setattr(agent.runtime, "environment_config", unreadable)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        assert agent.check_shortcuts() == {"prod": {"skipped": "the project can't be read"}}
    assert "couldn't read the project for the shortcut check" in _messages(caplog)
    assert world["scans"] == 0 and world["posts"] == []


def test_a_scan_that_fails_skips_that_environment(world, monkeypatch, caplog):  # noqa: F811
    agent = world["make"]()

    def broken(*a):
        raise ConnectionError("Fabric unreachable")
    monkeypatch.setattr(shortcut_watch, "scan", broken)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        assert agent.check_shortcuts() == {"prod": {"skipped": "the check failed"}}
    assert "couldn't check the shortcuts" in _messages(caplog)
    assert world["posts"] == []


def test_a_scan_that_couldnt_list_some_shortcuts_logs_each_problem(world, monkeypatch, caplog):  # noqa: F811
    agent = world["make"]()
    real = shortcut_watch.scan
    monkeypatch.setattr(shortcut_watch, "scan", lambda *a: (real(*a)[0], ["LH_Other: 403 Forbidden"]))
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        out = agent.check_shortcuts()
    assert out["prod"] == {"checked": 1, "broken": 0}
    assert [r.fields["error"] for r in caplog.records if r.getMessage() == "couldn't list some shortcuts"] == [
        "LH_Other: 403 Forbidden"]


def test_a_shortcut_check_that_crashes_is_logged_and_the_agent_goes_on(setup, monkeypatch, caplog):  # noqa: F811
    agent = Agent(setup)

    def crash():
        raise RuntimeError("unexpected")
    monkeypatch.setattr(agent, "check_shortcuts", crash)
    with caplog.at_level("ERROR", logger="fabric_desktop.agent"):
        thread = agent.start_shortcut_check()
        thread.join(10)
    assert not thread.is_alive()
    assert "shortcut check crashed" in _messages(caplog, "ERROR")


def _scheduled(tmp_path, setup, schedules):  # noqa: F811
    path = tmp_path / "scheduled.toml"
    path.write_text(config_text(setup.project, tmp_path / "home2", schedules), encoding="utf-8")
    return Agent(agent_config.load(path))


HOURLY = """
[[schedule]]
id = "dev-hourly"
env = "dev"
every_minutes = 60
"""


def test_a_removed_schedules_run_that_cant_be_cancelled_isnt_counted(tmp_path, setup, monkeypatch):  # noqa: F811
    agent = _scheduled(tmp_path, setup, "")
    agent.queue.set_schedule_next("gone", 1.0, None, "spec")
    job = agent.queue.enqueue("meta", {"env": "dev"}, source="schedule:gone", dedupe_key="schedule:gone")
    monkeypatch.setattr(agent.queue, "cancel", lambda job_id, reason: False)  # it started meanwhile
    out = agent.reconcile_schedules()
    assert out["forgotten"] == ["gone"] and out["cancelled"] == []
    assert agent.queue.get(job["id"])["state"] == "queued"


def test_a_waiting_run_already_as_configured_isnt_changed(tmp_path, setup, monkeypatch):  # noqa: F811
    agent = _scheduled(tmp_path, setup, HOURLY)
    want = {"env": "dev", "entities": [], "backfill": "", "cascade": False}
    agent.queue.enqueue("meta", want, source="schedule:dev-hourly", dedupe_key="schedule:dev-hourly")
    changed = []
    monkeypatch.setattr(agent.queue, "set_payload", lambda *a: changed.append(a) or True)
    assert agent.reconcile_schedules()["updated"] == []
    assert changed == []


def test_a_schedule_with_no_next_run_keeps_none(tmp_path, setup, monkeypatch):  # noqa: F811
    agent = _scheduled(tmp_path, setup, HOURLY)
    monkeypatch.setattr(agent_mod, "next_run", lambda config, now: None)
    assert agent.tick_schedules() == []
    assert agent.queue.schedule_next("dev-hourly") is None


def test_a_lost_lease_with_no_one_waiting_on_it_just_stops_the_heartbeat(setup):  # noqa: F811
    agent = Agent(dataclasses.replace(setup, job_lease_seconds=0.03))
    beats = []
    agent.queue.heartbeat = lambda *a, **kw: beats.append(1) or False
    done = threading.Event()
    beat = threading.Thread(target=agent._heartbeat, args=({"id": "j1"}, done, None))
    beat.start()
    beat.join(10)
    assert not beat.is_alive() and beats == [1] and not done.is_set()


def test_a_prod_job_whose_audit_line_cant_be_written_on_its_last_attempt_alerts(setup, monkeypatch):  # noqa: F811
    agent = Agent(dataclasses.replace(setup, max_attempts=1))
    monkeypatch.setattr(agent_mod, "fails_closed", lambda action, params: True)

    def refused(*a, **kw):
        raise OSError("audit folder is read-only")
    monkeypatch.setattr(agent.audit, "record", refused)
    ran, alerts = [], []
    monkeypatch.setattr(agent.runtime, "run_environment", lambda *a, **kw: ran.append(a))
    monkeypatch.setattr(agent, "_alert", lambda job, error, crashed=False: alerts.append((job["id"], error)))
    job = agent.enqueue("dev")
    agent.run_job(agent.queue.claim(setup.name, setup.job_lease_seconds))
    assert ran == [] and agent.queue.get(job["id"])["state"] == "dead"
    [(jid, error)] = alerts
    assert jid == job["id"] and "OSError: audit folder is read-only" in error


def test_narrowing_a_job_thats_gone_does_nothing(setup):  # noqa: F811
    agent = Agent(setup)
    agent._narrow("no-such-job", ["silver.orders"])
    assert agent.queue.get("no-such-job") is None


def test_an_alert_teams_may_have_taken_isnt_sent_again(setup, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setenv("FD_TEST_HOOK", HOOK)
    clock = Clock()
    cfg = dataclasses.replace(setup, teams_webhook_env="FD_TEST_HOOK")
    agent = Agent(cfg, queue=JobQueue(cfg.home / "queue.sqlite3", clock=clock, backoff_seconds=0), clock=clock)

    def timed_out(url, title, lines):
        raise TimeoutError(f"no answer from {url}")
    monkeypatch.setattr(agent, "_post", timed_out)
    monkeypatch.setattr(agent_mod, "settle_failed", lambda box, item, exc, problem: True)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent._notify("a title", ["a line"], job_id="j1")
    [logged] = [r.fields for r in caplog.records
                if r.getMessage() == "couldn't confirm the alert reached Teams; it may have arrived, so it isn't sent again"]
    assert logged["job_id"] == "j1" and HOOK not in logged["error"]
    assert "couldn't post the alert to Teams; it goes with the next try" not in _messages(caplog)


def test_signals_are_installed_before_the_control_folders_are_held(setup, monkeypatch):  # noqa: F811
    agent = Agent(setup)
    installed = []
    monkeypatch.setattr(agent_mod.signal, "signal", lambda sig, handler: installed.append((sig, handler)))

    def in_use():
        raise ControlFolderInUse("etl-02 works in it")
    monkeypatch.setattr(agent, "hold_control_folders", in_use)
    with pytest.raises(ControlFolderInUse):
        agent.run_forever()
    assert [s for s, _ in installed] == [agent_mod.signal.SIGINT, agent_mod.signal.SIGTERM]
    installed[0][1]()
    assert agent.stopping.is_set(), "a signal stops the agent"
