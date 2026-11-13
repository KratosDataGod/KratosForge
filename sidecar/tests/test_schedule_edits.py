"""agent.toml edited between starts: a schedule whose timing changed, one removed, one whose tables
changed while a run of it is queued. Each due time runs once, as configured now, never twice and never
at a time agent.toml no longer names. agent.toml is read only when the agent starts."""

import datetime as dt

from fabric_desktop import agent_config
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue

from test_agent import Clock, config_text, setup  # noqa: F401 - setup is a fixture

MINUTE = 60.0


def _cfg(tmp_path, setup, schedules):  # noqa: F811
    path = tmp_path / "s.toml"
    path.write_text(config_text(setup.project, tmp_path / "home", schedules), encoding="utf-8")
    return agent_config.load(path)


def _start(cfg, clock):
    agent = Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=clock), clock=clock)
    agent.reconcile_schedules()
    return agent


def _ticks(agent, clock, minutes):
    fired = []
    for _ in range(minutes):
        fired += [(s, clock.t) for s in agent.tick_schedules()]
        clock.t += MINUTE
    return fired


DAILY_2 = '[[schedule]]\nid = "s"\nenv = "dev"\ndaily = ["02:00"]\n'
DAILY_6 = '[[schedule]]\nid = "s"\nenv = "dev"\ndaily = ["06:00"]\n'


def test_a_schedule_moved_to_a_new_time_runs_at_the_new_time_only(tmp_path, setup):  # noqa: F811
    clock = Clock(dt.datetime(2026, 11, 10, 0, 30).timestamp())
    agent = _start(_cfg(tmp_path, setup, DAILY_2), clock)
    agent.tick_schedules()  # next run worked out: 02:00 tonight
    agent = _start(_cfg(tmp_path, setup, DAILY_6), clock)  # edited to 06:00, then started again
    fired = _ticks(agent, clock, 8 * 60)
    assert [dt.datetime.fromtimestamp(t).strftime("%H:%M") for _, t in fired] == ["06:00"]


def test_an_unchanged_schedule_keeps_its_next_run_across_a_restart(tmp_path, setup):  # noqa: F811
    clock = Clock(dt.datetime(2026, 11, 10, 0, 30).timestamp())
    agent = _start(_cfg(tmp_path, setup, DAILY_2), clock)
    agent.tick_schedules()
    clock.t = dt.datetime(2026, 11, 10, 3, 0).timestamp()  # down over 02:00
    agent = _start(_cfg(tmp_path, setup, DAILY_2), clock)
    assert agent.reconcile_schedules() == {"retimed": [], "forgotten": [], "cancelled": [], "updated": []}
    assert agent.tick_schedules() == ["s"]  # the missed run, once


def test_a_removed_schedule_has_its_waiting_run_cancelled_and_starts_afresh_if_added_back(tmp_path, setup):  # noqa: F811
    clock = Clock(dt.datetime(2026, 11, 10, 1, 59).timestamp())
    agent = _start(_cfg(tmp_path, setup, DAILY_2), clock)
    _ticks(agent, clock, 3)  # 02:00 fires and waits in the queue (no worker ran)
    [job] = agent.queue.queued_with_key("schedule:s")
    agent = _start(_cfg(tmp_path, setup, ""), clock)  # removed
    assert agent.queue.get(job["id"])["state"] == "cancelled"
    assert agent.queue.get(job["id"])["error"] == "its schedule was removed from agent.toml"
    assert agent.queue.schedule_next("s") is None
    clock.t = dt.datetime(2026, 11, 20, 1, 0).timestamp()  # added back ten days later
    agent = _start(_cfg(tmp_path, setup, DAILY_2), clock)
    assert agent.tick_schedules() == []  # no catching up for the days it was gone
    fired = _ticks(agent, clock, 120)
    assert [dt.datetime.fromtimestamp(t).strftime("%H:%M") for _, t in fired] == ["02:00"]


def test_an_edited_schedule_runs_its_waiting_first_run_once_with_the_new_tables(tmp_path, setup):  # noqa: F811
    clock = Clock(dt.datetime(2026, 11, 10, 1, 59).timestamp())
    agent = _start(_cfg(tmp_path, setup, DAILY_2 + 'entities = ["bronze.orders"]\n'), clock)
    _ticks(agent, clock, 3)
    [job] = agent.queue.queued_with_key("schedule:s")
    agent = _start(_cfg(tmp_path, setup, DAILY_2 + 'entities = ["bronze.customers"]\n'), clock)
    [same] = agent.queue.queued_with_key("schedule:s")
    assert same["id"] == job["id"] and same["payload"]["entities"] == ["bronze.customers"]
    assert agent.tick_schedules() == []  # the same timing: nothing more queued, and not twice


def test_a_retry_of_an_edited_schedule_keeps_the_tables_it_still_has_to_finish(tmp_path, setup):  # noqa: F811
    clock = Clock(dt.datetime(2026, 11, 10, 1, 59).timestamp())
    agent = _start(_cfg(tmp_path, setup, DAILY_2 + 'entities = ["bronze.orders", "bronze.customers"]\n'), clock)
    _ticks(agent, clock, 3)
    claimed = agent.queue.claim("w", 60)
    agent.queue.retry_later(claimed["id"], "w", "throttled")
    agent.queue.set_payload(claimed["id"], claimed["payload"] | {"entities": ["bronze.customers"]})
    agent = _start(_cfg(tmp_path, setup, DAILY_2 + 'entities = ["bronze.orders"]\n'), clock)
    assert agent.queue.get(claimed["id"])["payload"]["entities"] == ["bronze.customers"]


def test_a_running_job_of_a_removed_schedule_is_left_to_finish(tmp_path, setup):  # noqa: F811
    clock = Clock(dt.datetime(2026, 11, 10, 1, 59).timestamp())
    agent = _start(_cfg(tmp_path, setup, DAILY_2), clock)
    _ticks(agent, clock, 3)
    running = agent.queue.claim("w", 600)
    agent = _start(_cfg(tmp_path, setup, ""), clock)
    assert agent.queue.get(running["id"])["state"] == "running"


def test_a_queue_from_before_this_change_is_read_as_unchanged(tmp_path, setup):  # noqa: F811
    clock = Clock(dt.datetime(2026, 11, 10, 0, 30).timestamp())
    cfg = _cfg(tmp_path, setup, DAILY_2)
    queue = JobQueue(cfg.home / "q.sqlite3", clock=clock)
    queue.set_schedule_next("s", dt.datetime(2026, 11, 10, 2, 0).timestamp())  # no spec kept
    agent = Agent(cfg, queue=queue, clock=clock)
    assert agent.reconcile_schedules()["retimed"] == []
    assert agent.queue.schedule_specs()["s"] is not None
