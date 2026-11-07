"""The job agent after things go wrong: a run that takes the agent down every time, and the parts of
the loop that must log a failure and carry on (a shared schedule's mailbox, pruning, a whole pass)."""

import dataclasses
import threading

from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue

from test_agent import Clock, setup  # noqa: F401 - setup is a fixture


def _logged(caplog, message):
    """The fields logged with ``message`` (logs.fields keeps them off the message text)."""
    return next(r.fields for r in caplog.records if r.getMessage() == message)


def _agent(cfg, clock, **changes):
    cfg = dataclasses.replace(cfg, **changes) if changes else cfg
    return Agent(cfg, queue=JobQueue(cfg.home / "queue.sqlite3", clock=clock, backoff_seconds=0), clock=clock)


def test_a_job_whose_worker_stops_on_every_attempt_gives_up_and_says_so(setup, monkeypatch, caplog):  # noqa: F811
    # A run that takes the agent down each time (a crash, the PC restarting) was put back again and
    # again until its attempts ran out, then went dead with only "put back in the queue" in the log
    # and no alert. Now its last recovery logs that it gave up and sends the job alert.
    clock = Clock()
    monkeypatch.setenv("FD_TEST_HOOK", "https://example.webhook.office.com/hook")
    agent = _agent(setup, clock, teams_webhook_env="FD_TEST_HOOK")
    sent = []
    monkeypatch.setattr(agent, "_post", lambda url, title, lines: sent.append((title, lines)))
    job = agent.enqueue("dev")
    agent.stopping.set()  # the passes below recover but claim nothing: the test plays each run's process
    for attempt in range(1, agent.config.max_attempts + 1):
        claimed = agent.queue.claim(f"etl-01-run-{attempt}", 60)  # each run's process dies holding it
        assert claimed["id"] == job["id"] and claimed["attempts"] == attempt
        clock.t += 61
        caplog.clear()
        with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
            agent.step()
        if attempt < agent.config.max_attempts:
            assert agent.queue.get(job["id"])["state"] == "queued"
            assert "put back in the queue" in caplog.text and not sent
    dead = agent.queue.get(job["id"])
    assert dead["state"] == "dead" and dead["error"].startswith("Gave up after 3 attempts")
    assert "job gave up: its worker stopped on every attempt" in caplog.text
    [(title, lines)] = sent
    assert title == "KratosForge agent etl-01: a Dev job didn't finish"
    assert lines[0].startswith(f"Job {job['id']}") and "stopped while running it" in lines[1]
    assert "out-of-memory kill or a crash" in lines[2] and lines[3] == f"Fix the cause, then run: fabric-desktop agent requeue {job['id']}"


def test_a_shared_schedule_whose_mailbox_cant_be_written_is_logged_and_the_rest_run(setup, monkeypatch, caplog):  # noqa: F811
    clock = Clock()
    agent = _agent(setup, clock)
    shared = type("S", (), {"id": "nightly", "env": "dev", "shared": True})()

    def unreachable(env):
        raise OSError("OneLake can't be reached")
    monkeypatch.setattr(agent, "_control", unreachable)
    agent._controls["dev"] = object()
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent._submit_shared(shared, clock.t, clock.t)
    assert "OneLake can't be reached" in _logged(caplog, "couldn't write the shared schedule's request")["error"]
    assert "dev" not in agent._controls  # opened again next time, not kept broken


def test_a_prune_that_fails_is_logged_and_tried_again_the_next_day(setup, monkeypatch, caplog):  # noqa: F811
    clock = Clock()
    agent = _agent(setup, clock)

    def locked(*_):
        raise OSError("disk full")
    monkeypatch.setattr(agent.queue, "prune", locked)
    with caplog.at_level("WARNING", logger="fabric_desktop.agent"):
        agent.step()
    assert _logged(caplog, "couldn't prune the queue")["error"] == "OSError: disk full"
    first = agent._pruned_at
    calls = []
    monkeypatch.setattr(agent.queue, "prune", lambda days: calls.append(days) or [])
    clock.t += 3600
    agent.step()
    assert calls == []  # not before a day has passed
    clock.t += 86400
    agent.step()
    assert calls == [agent.config.queue_retention_days] and agent._pruned_at > first


def test_a_pass_that_raises_is_logged_and_the_loop_carries_on_then_stops_cleanly(setup, monkeypatch, caplog):  # noqa: F811
    clock = Clock()
    agent = _agent(setup, clock, poll_seconds=0.01)
    passes = []

    def step():
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("the queue file vanished")
        if len(passes) == 3:
            agent.stop()
    monkeypatch.setattr(agent, "step", step)
    statuses = []
    monkeypatch.setattr(agent, "publish_status", lambda state="running": statuses.append(state))
    with caplog.at_level("INFO", logger="fabric_desktop.agent"):
        loop = threading.Thread(target=agent.run_forever, kwargs={"install_signals": False})
        loop.start()
        loop.join(10)
    assert not loop.is_alive() and len(passes) == 3
    [crash] = [r for r in caplog.records if r.getMessage() == "loop error; carrying on"]
    assert "the queue file vanished" in str(crash.exc_info[1])
    assert statuses == ["stopping", "stopped"] and "agent stopped" in caplog.text
