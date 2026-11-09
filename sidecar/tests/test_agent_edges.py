"""The agent's job loop (agent.py), the paths the main tests don't reach: a job that fails in a way a
retry won't fix, a retry that gives up, an audit line, a status record or a lease renewal that can't be
written, a job that crashes, and a key file every account can read. The project is the sample one;
nothing reaches Fabric."""

from __future__ import annotations

import sqlite3
from concurrent.futures import Future

import pytest

import fabric_desktop.agent as agent_mod
from fabric_desktop import credentials
from fabric_desktop.agent import Agent
from test_agent import finish, setup  # noqa: F401 - the fixture is used by name


class Log:
    """The agent's log, kept as (level, message, extra) for the tests to read."""

    def __init__(self):
        self.lines = []

    def _add(self, level):
        return lambda message, *a, extra=None, **k: self.lines.append((level, message, extra or {}))

    def __getattr__(self, level):
        if level in ("debug", "info", "warning", "error", "exception"):
            return self._add(level)
        raise AttributeError(level)

    def said(self, level, text):
        return any(lvl == level and text in msg for lvl, msg, _ in self.lines)


@pytest.fixture
def agent(setup):  # noqa: F811
    a = Agent(setup, log=Log())
    a.alerts = []
    a._alert = lambda job, error, crashed=False: a.alerts.append(error)
    yield a
    finish(a)


def _run(agent, results=None, error=None):
    def run_environment(*args, **kwargs):
        if error:
            raise error
        return results
    agent.runtime.run_environment = run_environment
    job = agent.enqueue("dev")
    agent.step()
    finish(agent)
    return agent.queue.get(job["id"])


def test_a_job_that_fails_in_a_way_a_retry_wont_fix_fails_and_alerts(agent):
    done = _run(agent, error=ValueError("the project's dev environment has no workspace"))
    assert done["state"] == "failed" and done["attempts"] == 1
    assert agent.alerts == ["ValueError: the project's dev environment has no workspace"]
    assert agent.log.said("error", "job failed")


def test_busy_tables_that_run_out_of_retries_alert(agent, monkeypatch):
    real = agent.queue.retry_later

    def give_up(job_id, owner, error, **kw):
        real(job_id, owner, error, **kw)
        return {"state": "dead", "error": f"gave up: {error}"}
    monkeypatch.setattr(agent.queue, "retry_later", give_up)
    _run(agent, [{"entity": "gold.fact_sales", "status": "error", "message": "is being written by etl-02",
                  "retryable": True}])
    assert agent.alerts == ["gave up: gold.fact_sales: is being written by etl-02"]


def test_a_job_runs_on_when_its_audit_line_cant_be_written(agent, monkeypatch):
    def full(*a, **k):
        raise OSError("No space left on device")
    monkeypatch.setattr(agent.audit, "record", full)
    done = _run(agent, [{"entity": "gold.fact_sales", "status": "ok", "rows_written": 3, "run_id": "r1"}])
    assert done["state"] == "succeeded" and done["result"]["run_ids"] == ["r1"]
    assert agent.log.said("warning", "couldn't write the audit line")


def test_a_job_that_crashes_doesnt_take_the_agent_down(agent, monkeypatch):
    def crash(job):
        raise RuntimeError("bug")
    monkeypatch.setattr(agent, "run_job", crash)
    agent._guarded({"id": "j1", "payload": {"env": "dev"}})
    assert agent.log.said("exception", "job crashed")


# ---- the control area and the status record


def test_a_control_area_that_cant_be_opened_is_held_later_and_its_status_isnt_published(agent, monkeypatch):
    def unreachable(env):
        raise OSError("the share is offline")
    monkeypatch.setattr(agent, "_control", unreachable)
    agent._controls["dev"] = object()
    agent.hold_control_folders()
    agent.publish_status()
    assert agent.log.said("warning", "couldn't open the control files yet")
    assert agent.log.said("warning", "couldn't publish the agent status") and "dev" not in agent._controls
    agent.release_control_folders()


def test_a_waiting_count_that_cant_be_read_is_unknown(agent, monkeypatch):
    (agent.config.home / "alerts.sqlite3").write_bytes(b"")

    def locked(path, clock=None):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(agent_mod, "Outbox", locked)
    assert agent.messages_waiting() is None
    assert agent.log.said("warning", "couldn't count the waiting messages")


def test_a_lease_that_cant_be_renewed_after_a_sleep_is_tried_again_later(agent, monkeypatch):
    running, finished = Future(), Future()
    finished.set_result(None)
    agent._running.update({"j-running": running, "j-done": finished})
    renewed = []

    def heartbeat(job_id, owner, seconds, claim=None):
        renewed.append(job_id)
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(agent.queue, "heartbeat", heartbeat)
    agent._renew_running()
    assert renewed == ["j-running"] and agent.log.said("warning", "couldn't renew the job's lease")
    agent._running.clear()


def test_an_agent_whose_key_file_every_account_can_read_says_so_at_start(agent, monkeypatch):
    monkeypatch.setattr(credentials, "key_file_open", lambda auth: True)
    agent.stopping.set()
    agent.run_forever(install_signals=False)
    assert agent.log.said("warning", credentials.KEY_FILE_OPEN)
    assert agent.log.said("info", "agent stopped")
