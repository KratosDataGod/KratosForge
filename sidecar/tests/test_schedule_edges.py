"""Schedule edge cases for the job agent: runs missed while the PC was off, and the two daylight-saving
changes a year (local_schedules.next_run, which agent.tick_schedules uses).

The daylight-saving tests set the process's time zone to America/New_York (changes on 2026-03-08 and
2026-11-01) with time.tzset, which Windows doesn't have, so they run on the Linux job only.
"""

import datetime as dt
import itertools
import os
import time

import pytest

from fabric_desktop import agent_config
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue
from fabric_desktop.local_schedules import next_run

from test_agent import Clock, config_text, setup  # noqa: F401 - setup is a fixture


@pytest.fixture
def new_york():
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset, which Windows doesn't have")
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def _runs(config, start: dt.datetime, count: int) -> list[dt.datetime]:
    """The next ``count`` runs from ``start``, each worked out from the one before, as the agent does."""
    out, t = [], start.timestamp()
    for _ in range(count):
        nxt = next_run(config, dt.datetime.fromtimestamp(t))
        out.append(nxt)
        t = nxt.timestamp()
    return out


def _gaps_hours(start: dt.datetime, runs: list[dt.datetime]) -> list[float]:
    times = [start.timestamp()] + [r.timestamp() for r in runs]
    return [(b - a) / 3600 for a, b in itertools.pairwise(times)]


def test_an_hourly_schedule_runs_every_hour_when_the_clocks_go_back(new_york):
    # 2026-11-01: 02:00 EDT becomes 01:00 EST, so 01:00 happens twice. Stepping by wall time went
    # from the first 01:00 to 02:00, two hours later, and skipped the repeated hour.
    hourly = {"type": "Cron", "interval": 60, "startDateTime": "2026-10-01T00:00:00"}
    start = dt.datetime(2026, 11, 1, 0, 0)
    runs = _runs(hourly, start, 4)
    assert _gaps_hours(start, runs) == [1.0, 1.0, 1.0, 1.0]
    assert [(r.hour, r.fold) for r in runs] == [(1, 0), (1, 1), (2, 0), (3, 0)]


def test_from_inside_the_repeated_hour_the_next_run_is_still_to_come(new_york):
    # At 01:15 the second time round, stepping by wall time named 01:30 the first time round, a
    # moment already past, so the run was due at once.
    every_15 = {"type": "Cron", "interval": 15, "startDateTime": "2026-10-01T00:00:00"}
    now = dt.datetime(2026, 11, 1, 1, 15, fold=1)
    nxt = next_run(every_15, now)
    assert (nxt.hour, nxt.minute, nxt.fold) == (1, 30, 1)
    assert nxt.timestamp() - now.timestamp() == 15 * 60
    # and at 01:45 the first time round, the next one is 01:00 the second time, not 02:00
    nxt = next_run(every_15, dt.datetime(2026, 11, 1, 1, 45))
    assert (nxt.hour, nxt.minute, nxt.fold) == (1, 0, 1)


@pytest.mark.parametrize("interval", [5, 15, 45, 90, 120, 240])
def test_every_run_is_still_to_come_and_none_waits_more_than_an_hour_extra(new_york, interval):
    # The wall-time grid stays as configured; a repeated hour adds runs, it never drops them or
    # names a moment already past. A grid with no time in that hour waits that hour on top.
    cron = {"type": "Cron", "interval": interval, "startDateTime": "2026-10-01T00:00:00"}
    for minutes in range(0, 300, 7):
        start = dt.datetime(2026, 10, 31, 23, 0) + dt.timedelta(minutes=minutes)
        gaps = _gaps_hours(start, _runs(cron, start, max(8, 240 // interval)))
        assert min(gaps) > 0 and max(gaps) <= interval / 60 + 1, (start, gaps)


def test_an_hourly_schedule_skips_only_the_hour_that_doesnt_exist_when_the_clocks_go_forward(new_york):
    # 2026-03-08: 02:00 EST becomes 03:00 EDT. The 02:00 run is the same moment as 03:00.
    hourly = {"type": "Cron", "interval": 60, "startDateTime": "2026-03-01T00:00:00"}
    start = dt.datetime(2026, 3, 8, 0, 0)
    runs = _runs(hourly, start, 4)
    assert _gaps_hours(start, runs) == [1.0, 1.0, 1.0, 1.0]


def test_a_daily_time_that_doesnt_exist_runs_an_hour_later_and_a_repeated_one_runs_once(new_york):
    at_0230 = {"type": "Daily", "times": ["02:30"]}
    [spring] = _runs(at_0230, dt.datetime(2026, 3, 8, 0, 0), 1)
    assert dt.datetime.fromtimestamp(spring.timestamp()) == dt.datetime(2026, 3, 8, 3, 30)  # the same day
    at_0130 = {"type": "Daily", "times": ["01:30"]}
    start = dt.datetime(2026, 11, 1, 0, 0)
    first, second = _runs(at_0130, start, 2)
    assert (first.date(), second.date()) == (dt.date(2026, 11, 1), dt.date(2026, 11, 2))
    assert _gaps_hours(start, [first, second]) == [1.5, 25.0]  # once on the day the hour repeats


def test_runs_missed_while_the_pc_was_off_run_once_then_the_schedule_carries_on(tmp_path, setup):  # noqa: F811
    path = tmp_path / "s.toml"
    path.write_text(config_text(setup.project, tmp_path / "home",
                                '[[schedule]]\nid = "morning"\nenv = "dev"\ndaily = ["06:00"]'), encoding="utf-8")
    cfg = agent_config.load(path)
    clock = Clock(dt.datetime(2026, 10, 20, 5, 0).timestamp())
    agent = Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=clock), clock=clock)
    assert agent.tick_schedules() == []
    assert agent.queue.schedule_next("morning") == dt.datetime(2026, 10, 20, 6, 0).timestamp()
    clock.t = dt.datetime(2026, 10, 23, 9, 15).timestamp()  # off for three days and a bit
    restarted = Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=clock), clock=clock)
    assert restarted.tick_schedules() == ["morning"]  # once, not once per missed morning
    assert restarted.tick_schedules() == []
    assert restarted.queue.counts()["queued"] == 1
    assert restarted.queue.schedule_next("morning") == dt.datetime(2026, 10, 24, 6, 0).timestamp()
