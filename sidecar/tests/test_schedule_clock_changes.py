"""The agent's schedules through the two daylight-saving changes, a sleep, the clock going back and
the PC's time zone changing: each due time runs once, none twice and none never.

Every test ticks the agent as it runs (a minute at a time, or across a sleep) rather than asking
next_run alone, so what is checked is what runs. They set the process's time zone with time.tzset,
which Windows doesn't have, so they run on the Linux job only (test_schedule_edges.py does the same).
"""

import datetime as dt
import itertools
import os
import sqlite3
import time

import pytest

from fabric_desktop import agent_config
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue

from test_agent import Clock, config_text, setup  # noqa: F401 - setup is a fixture

MINUTE = 60.0


def _zone(name):
    os.environ["TZ"] = name
    time.tzset()


@pytest.fixture
def zone():
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset, which Windows doesn't have")
    old = os.environ.get("TZ")
    _zone("America/New_York")  # clocks forward 2026-03-08 02:00, back 2026-11-01 02:00
    yield _zone
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def _agent(tmp_path, setup, schedule, start):  # noqa: F811
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "s.toml"
    path.write_text(config_text(setup.project, tmp_path / "home", f'[[schedule]]\nid = "s"\nenv = "dev"\n{schedule}'),
                    encoding="utf-8")
    cfg = agent_config.load(path)
    clock = Clock(start.timestamp())
    return Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=clock), clock=clock), clock


def _restart(agent, clock):
    """The agent starting again on the same queue file, as after a reboot or a time-zone change."""
    return Agent(agent.config, queue=JobQueue(agent.queue.path, clock=clock), clock=clock)


def _tick_until(agent, clock, end, step=MINUTE):
    """Ticks a minute at a time up to ``end``; the local times (with the zone) at which the schedule ran."""
    ran = []
    while clock.t < end.timestamp():
        if agent.tick_schedules():
            ran.append(time.strftime("%m-%d %H:%M %Z", time.localtime(clock.t)))
        clock.t += step
    return ran


# ---- the clocks going forward and back

def test_a_daily_schedule_runs_once_a_day_through_both_changes(tmp_path, setup, zone):  # noqa: F811
    for day, expect in [
        (dt.datetime(2026, 3, 7), ["03-07 01:30 EST", "03-08 01:30 EST", "03-09 01:30 EDT"]),
        (dt.datetime(2026, 10, 31), ["10-31 01:30 EDT", "11-01 01:30 EDT", "11-02 01:30 EST"]),  # not again at 01:30 EST
    ]:
        agent, clock = _agent(tmp_path / day.strftime("%m%d"), setup, 'daily = ["01:30"]', day)
        assert _tick_until(agent, clock, day + dt.timedelta(days=3)) == expect


def test_a_time_that_doesnt_exist_runs_once_when_the_clocks_have_gone_forward(tmp_path, setup, zone):  # noqa: F811
    day = dt.datetime(2026, 3, 7)
    agent, clock = _agent(tmp_path, setup, 'daily = ["02:30"]', day)
    assert _tick_until(agent, clock, day + dt.timedelta(days=3)) == ["03-07 02:30 EST", "03-08 03:30 EDT", "03-09 02:30 EDT"]


def test_two_times_the_change_runs_together_run_once_together(tmp_path, setup, zone):  # noqa: F811
    # 02:00 doesn't exist on 8 March, so it falls on 03:00 EDT, the same moment as the 03:00 run: one run.
    day = dt.datetime(2026, 3, 8)
    agent, clock = _agent(tmp_path, setup, 'daily = ["01:00", "02:00", "03:00"]', day)
    assert _tick_until(agent, clock, day + dt.timedelta(days=1)) == ["03-08 01:00 EST", "03-08 03:00 EDT"]


def test_a_weekly_schedule_on_the_day_of_each_change(tmp_path, setup, zone):  # noqa: F811
    for day, expect in [(dt.datetime(2026, 3, 8), ["03-08 01:30 EST", "03-08 03:30 EDT"]),
                        (dt.datetime(2026, 11, 1), ["11-01 01:30 EDT", "11-01 02:30 EST"])]:
        agent, clock = _agent(tmp_path / day.strftime("%m%d"), setup,
                              'weekly = { days = ["Sunday"], times = ["01:30", "02:30"] }', day)
        assert _tick_until(agent, clock, day + dt.timedelta(days=1)) == expect


@pytest.mark.parametrize("day, hours", [(dt.datetime(2026, 3, 8), 23), (dt.datetime(2026, 11, 1), 25)])
def test_every_n_minutes_runs_once_per_real_interval_through_the_change(tmp_path, setup, zone, day, hours):  # noqa: F811
    for minutes in (15, 60):
        agent, clock = _agent(tmp_path / str(minutes), setup, f"every_minutes = {minutes}", day)
        agent.tick_schedules()  # the first tick only works out the next time
        clock.t += MINUTE
        ran = []
        while clock.t < (day + dt.timedelta(days=1)).timestamp():
            if agent.tick_schedules():
                ran.append(clock.t)
            clock.t += MINUTE
        # The day is 23 or 25 hours long; from 00:15 (or 01:00) to the last before midnight.
        assert len(ran) == hours * 60 // minutes - 1, minutes
        assert len(set(ran)) == len(ran) and all(b - a == minutes * MINUTE for a, b in itertools.pairwise(ran))


# ---- a sleep, and the clock going back

@pytest.mark.parametrize("day", [dt.datetime(2026, 3, 8), dt.datetime(2026, 11, 1)])
def test_a_sleep_across_the_change_runs_what_it_missed_once(tmp_path, setup, zone, day):  # noqa: F811
    agent, clock = _agent(tmp_path, setup, 'daily = ["01:30", "02:30"]', day)
    agent.tick_schedules()
    clock.t = (day + dt.timedelta(hours=5)).timestamp()  # asleep from midnight to 05:00
    assert _tick_until(agent, clock, day + dt.timedelta(hours=23)) == [time.strftime("%m-%d 05:00 %Z", time.localtime(clock.t - 18 * 3600))]
    assert agent.queue.schedule_next("s") == (day + dt.timedelta(days=1, hours=1, minutes=30)).timestamp()


def test_the_clock_set_back_an_hour_after_the_change_doesnt_run_it_again(tmp_path, setup, zone):  # noqa: F811
    day = dt.datetime(2026, 11, 1)
    agent, clock = _agent(tmp_path, setup, 'daily = ["03:00"]', day)
    assert _tick_until(agent, clock, day + dt.timedelta(hours=3, minutes=1)) == ["11-01 03:00 EST"]
    clock.t -= 3600  # someone sets the clock back an hour by hand
    assert _tick_until(agent, clock, day + dt.timedelta(hours=23)) == []


# ---- the PC's time zone changing

def test_after_a_move_west_a_daily_run_keeps_its_time_of_day_and_runs_once(tmp_path, setup, zone, caplog):  # noqa: F811
    day = dt.datetime(2026, 6, 10)
    agent, clock = _agent(tmp_path, setup, 'daily = ["06:00"]', day)
    agent.tick_schedules()  # 06:00 New York worked out
    zone("America/Los_Angeles")  # the PC is moved three hours west and the agent starts again
    agent = _restart(agent, clock)
    # Was: it ran at 03:00 Los Angeles (06:00 in New York), then again at 06:00 the same day.
    assert _tick_until(agent, clock, dt.datetime(2026, 6, 11)) == ["06-10 06:00 PDT"]
    moved = [r for r in caplog.records if "time zone changed" in r.getMessage()]
    assert len(moved) == 1  # noticed once and kept, not again at every tick until the run


def test_after_a_move_east_a_run_missed_while_off_runs_once(tmp_path, setup, zone):  # noqa: F811
    zone("America/Los_Angeles")
    day = dt.datetime(2026, 6, 10)
    agent, clock = _agent(tmp_path, setup, 'daily = ["06:00"]', day)
    agent.tick_schedules()  # 06:00 Los Angeles worked out
    zone("America/New_York")  # moved east while off; starts at 10:00 New York, after 06:00 there
    clock.t = dt.datetime(2026, 6, 10, 10, 0).timestamp()
    agent = _restart(agent, clock)
    assert _tick_until(agent, clock, dt.datetime(2026, 6, 11, 7, 0)) == ["06-10 10:00 EDT", "06-11 06:00 EDT"]


def test_a_queue_file_from_before_keeps_its_schedules(tmp_path, setup, zone):  # noqa: F811
    day = dt.datetime(2026, 6, 10)
    agent, clock = _agent(tmp_path, setup, 'daily = ["06:00"]', day)
    path = agent.queue.path
    with sqlite3.connect(path) as db:  # as an older agent left it: no wall column
        db.execute("drop table schedule_state")
        db.execute("create table schedule_state (id text primary key, next_at real not null)")
        db.execute("insert into schedule_state values ('s', ?)", (dt.datetime(2026, 6, 10, 6, 0).timestamp(),))
    db.close()
    agent = _restart(agent, clock)
    assert agent.queue.schedule_wall("s") is None
    assert _tick_until(agent, clock, dt.datetime(2026, 6, 11)) == ["06-10 06:00 EDT"]
    assert agent.queue.schedule_wall("s") == "2026-06-11T06:00"
