"""Failures the agent used to keep to its log: a message dropped after three days unsent, a shared
scheduled run nobody requested, and a queue file set aside at start. Each now says so where an
admin looks (a warning, the status record, or one Teams alert)."""

import dataclasses
import json

from fabric_desktop import agent_status, shared_schedules
from fabric_desktop.agent import Agent
from fabric_desktop.agent_config import Schedule
from fabric_desktop.jobqueue import JobQueue
from fabric_desktop.mailbox import LocalControl

from test_agent import Clock, setup  # noqa: F401 - setup is a fixture

HOOK = "https://example.webhook.office.com/hook"


def _agent(cfg, clock, monkeypatch, queue=None):
    monkeypatch.setenv("FD_TEST_HOOK", HOOK)
    cfg = dataclasses.replace(cfg, teams_webhook_env="FD_TEST_HOOK")
    agent = Agent(cfg, queue=queue or JobQueue(cfg.home / "queue.sqlite3", clock=clock, backoff_seconds=0), clock=clock)
    sent = []
    monkeypatch.setattr(agent, "_post", lambda url, title, lines: sent.append((title, lines)))
    return agent, sent


def _fields(caplog, message):
    return [r.fields for r in caplog.records if r.getMessage() == message]


# ---- 1: a message dropped unsent

def test_a_message_dropped_after_three_days_is_logged_and_counted(setup, monkeypatch, caplog):  # noqa: F811
    clock = Clock()
    agent, sent = _agent(setup, clock, monkeypatch)
    agent._alert_outbox().add("old alert", ["line"], "ConnectionError: no network")
    clock.t += 3 * 86400 + 1
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent.send_waiting_alerts()
    assert not sent and agent._alert_outbox().pending() == []
    [logged] = _fields(caplog, "Teams or flow messages waited three days without being sent and were dropped")
    assert logged == {"dropped": 1, "outbox": "job_alerts.sqlite3"}
    assert agent.messages_dropped == 1
    assert agent_status.record(agent, "dev")["messages_dropped"] == 1


def test_a_message_within_three_days_is_sent_and_nothing_is_counted(setup, monkeypatch, caplog):  # noqa: F811
    clock = Clock()
    agent, sent = _agent(setup, clock, monkeypatch)
    agent._alert_outbox().add("recent alert", ["line"], "ConnectionError: no network")
    clock.t += 3 * 86400 - 60
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent.send_waiting_alerts()
    assert [t for t, _ in sent] == ["Delayed: recent alert"]
    assert agent.messages_dropped == 0 and "were dropped" not in caplog.text
    assert agent_status.record(agent, "dev")["messages_dropped"] == 0


# ---- 2: a shared scheduled run nobody requested

def _shared(entities=("bronze.orders",)):
    return Schedule("dev-hourly", "dev", list(entities), {}, "", {"kind": "cron", "every_minutes": 60}, True)


def test_a_shared_run_nobody_requested_sends_one_alert(setup, monkeypatch, caplog, tmp_path):  # noqa: F811
    clock = Clock()
    agent, sent = _agent(setup, clock, monkeypatch)
    agent._controls["dev"] = LocalControl(tmp_path / "ctl")
    s, due = _shared(), clock.t
    with caplog.at_level("INFO", logger="fabric_desktop.agent"):
        agent._submit_shared(s, due, due + 2 * 3600)  # the agent was away past the slot's deadline
        agent._submit_shared(s, due, due + 2 * 3600 + 60)  # the same due time again: no second alert
    [(title, lines)] = sent
    assert title == "KratosForge agent etl-01: a shared Dev run was missed"
    assert lines[0].startswith(f"Schedule dev-hourly, due {shared_schedules.iso(due)}: no agent requested it")
    assert lines[-1] == "To run it now: fabric-desktop agent enqueue dev bronze.orders"
    [logged] = _fields(caplog, "shared scheduled run missed: no agent requested it before its deadline")
    assert logged["schedule"] == "dev-hourly" and logged["due"] == shared_schedules.iso(due)
    assert (tmp_path / "ctl" / "requests").exists() is False  # still never written late


def test_a_shared_run_another_agent_requested_is_not_an_alert(setup, monkeypatch, caplog, tmp_path):  # noqa: F811
    clock = Clock()
    agent, sent = _agent(setup, clock, monkeypatch)
    backend = LocalControl(tmp_path / "ctl")
    agent._controls["dev"] = backend
    s, due = _shared(), clock.t
    rid, outcome = shared_schedules.submit(backend, s, due, due + 5, "etl-02")  # etl-02 was there on time
    assert outcome == "created"
    with caplog.at_level("INFO", logger="fabric_desktop.agent"):
        agent._submit_shared(s, due, due + 2 * 3600)
    assert not sent
    [logged] = _fields(caplog, "shared schedule past its deadline; not requested")
    assert logged["request_id"] == rid


def test_a_shared_run_whose_mailbox_cant_be_read_is_alerted_as_unchecked(setup, monkeypatch, caplog):  # noqa: F811
    clock = Clock()
    agent, sent = _agent(setup, clock, monkeypatch)

    class Unreadable:
        def read(self, key):
            raise OSError("OneLake can't be reached")
    agent._controls["dev"] = Unreadable()
    s = _shared(entities=())
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent._submit_shared(s, clock.t, clock.t + 2 * 3600)
    [(title, lines)] = sent
    assert "couldn't be read to check whether another agent ran it" in lines[0]
    assert lines[-1] == "To run it now: fabric-desktop agent enqueue dev"
    assert _fields(caplog, "couldn't check whether another agent requested the shared schedule")
    assert "dev" not in agent._controls  # opened again next time, not kept broken


def test_a_missed_shared_run_without_a_webhook_is_still_logged(setup, monkeypatch, caplog, tmp_path):  # noqa: F811
    clock = Clock()
    agent = Agent(setup, queue=JobQueue(setup.home / "queue.sqlite3", clock=clock), clock=clock)
    agent._controls["dev"] = LocalControl(tmp_path / "ctl")
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent._submit_shared(_shared(), clock.t, clock.t + 2 * 3600)
    assert _fields(caplog, "shared scheduled run missed: no agent requested it before its deadline")


# ---- 4: a queue file set aside at start

def test_a_queue_file_set_aside_at_start_sends_one_alert(setup, monkeypatch, caplog):  # noqa: F811
    clock = Clock()
    setup.home.mkdir(parents=True, exist_ok=True)
    (setup.home / "queue.sqlite3").write_bytes(b"this is not a database" * 100)
    queue = JobQueue(setup.home / "queue.sqlite3", clock=clock, backoff_seconds=0)
    assert queue.set_aside is not None
    agent, sent = _agent(setup, clock, monkeypatch, queue=queue)
    agent.stopping.set()
    agent.step()
    agent.step()
    [(title, lines)] = [m for m in sent if "queue file" in m[0]]
    assert title == "KratosForge agent etl-01: its job queue file couldn't be read"
    assert lines[0] == f"It was moved to {queue.set_aside.name} in the agent's home, and the agent started with an empty queue."
    assert str(setup.home) not in json.dumps(lines)  # the file's name only, not the machine's paths


def test_a_queue_alert_teams_didnt_take_waits_for_the_next_try(setup, monkeypatch, caplog):  # noqa: F811
    clock = Clock()
    setup.home.mkdir(parents=True, exist_ok=True)
    (setup.home / "queue.sqlite3").write_bytes(b"this is not a database" * 100)
    queue = JobQueue(setup.home / "queue.sqlite3", clock=clock, backoff_seconds=0)
    agent, _ = _agent(setup, clock, monkeypatch, queue=queue)

    def down(url, title, lines):
        raise ConnectionError(f"can't reach {url}")
    monkeypatch.setattr(agent, "_post", down)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent._alert_queue_set_aside()
    [waiting] = agent._alert_outbox().pending()
    assert waiting["title"] == "KratosForge agent etl-01: its job queue file couldn't be read"
    [logged] = _fields(caplog, "couldn't post the alert to Teams; it goes with the next try")
    assert HOOK not in logged["error"] and logged["moved_to"] == queue.set_aside.name
