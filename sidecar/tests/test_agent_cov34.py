"""agent.py: a schedule whose timing has no next run (local_schedules.next_run gives None)."""

from fabric_desktop import agent as agent_mod
from fabric_desktop import agent_config
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue

from test_agent import Clock, config_text


def _agent(tmp_path, clock):
    project = tmp_path / "proj"
    from fabric_desktop.meta.templates import init_project
    init_project(project, "3", "Chris", True)
    path = tmp_path / "s.toml"
    path.write_text(config_text(project, tmp_path / "h", '[[schedule]]\nid = "q"\nenv = "dev"\nevery_minutes = 15'),
                    encoding="utf-8")
    cfg = agent_config.load(path)
    return Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=clock), clock=clock)


def test_a_schedule_with_no_next_run_keeps_no_time_and_others_still_tick(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    clock = Clock(1_800_000_000.0)
    agent = _agent(tmp_path, clock)
    asked = []

    def no_next(config, now):
        asked.append(config)
        return None

    monkeypatch.setattr(agent_mod, "next_run", no_next)
    assert agent.tick_schedules() == []
    assert asked  # the timing was asked for its next run
    assert agent.queue.schedule_next("q") is None  # nothing kept: there is no next time
    clock.t += 3600
    assert agent.tick_schedules() == []  # still nothing due, and nothing queued
    assert agent.queue.queued_with_key("schedule:q") == []
    assert len(asked) == 2
