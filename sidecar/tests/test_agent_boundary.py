"""agent.py at its exact edges (mutation batch 163, a boundary re-score of batch 17,
docs/quality/mutation-testing.md): a schedule exactly its longest wait away is kept, the clock moving
exactly a status beat isn't a wake, and the agent claims exactly as many jobs as it has workers. No
test reaches Fabric, OneLake, Teams or the network."""

from __future__ import annotations

import dataclasses
import logging

import pytest

from fabric_desktop import agent_config
from fabric_desktop.agent import Agent, _longest_wait
from fabric_desktop.meta.templates import init_project

from test_agent import Clock, config_text

SCHEDULE = """
[[schedule]]
id = "dev-hourly"
env = "dev"
entities = ["silver.orders"]
every_minutes = 60
"""


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def cfg(tmp_path):
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "agent.toml"
    path.write_text(config_text(project, tmp_path / "agent-home", SCHEDULE), encoding="utf-8")
    return agent_config.load(path)


def test_a_schedule_exactly_its_longest_wait_away_is_kept(cfg, caplog):
    clock = Clock()
    a = Agent(cfg, clock=clock)
    [s] = a.config.schedules
    due = clock.t + _longest_wait(s.when)
    a.queue.set_schedule_next(s.id, due, None)
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.agent"):
        assert a.tick_schedules() == []
    assert a.queue.schedule_next(s.id) == due and "further off than it can be" not in caplog.text
    a.queue.set_schedule_next(s.id, due + 1, None)  # a second more: the clock went back
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.agent"):
        a.tick_schedules()
    assert a.queue.schedule_next(s.id) != due + 1 and "further off than it can be" in caplog.text


def test_the_clock_moving_exactly_a_status_beat_and_a_poll_is_not_a_wake(cfg):
    clock = Clock(0.0)  # small times, so the difference is exactly the beat
    a = Agent(cfg, clock=clock)
    beat = a.config.status_seconds + a.config.poll_seconds
    a._woke()  # the first pass only notes the time
    clock.t = beat
    assert clock.t - 0.0 == beat and a._woke() is False
    clock.t = 2 * beat + 0.5
    assert a._woke() is True


class _Running:
    def done(self):
        return False


def test_the_agent_claims_exactly_as_many_jobs_as_it_has_workers(cfg, monkeypatch):
    a = Agent(dataclasses.replace(cfg, concurrency=2), clock=Clock())
    a._running = {"job-0": _Running()}  # one worker busy, one free
    claimed = []

    def claim(name, lease):
        claimed.append(len(claimed) + 1)
        return {"id": f"job-{len(claimed)}", "claim_id": "c"}

    monkeypatch.setattr(a.queue, "claim", claim)
    monkeypatch.setattr(a._pool, "submit", lambda fn, job: _Running())
    try:
        a.step()
    finally:
        a._pool.shutdown(wait=False)
    assert claimed == [1] and len(a._running) == 2
