"""Schedules on this PC at their exact edge (mutation batch 166, a boundary re-score of batch 22,
docs/quality/mutation-testing.md): a schedule that starts exactly now runs next one interval on."""

import datetime as dt

from fabric_desktop import local_schedules


def test_a_schedule_that_starts_exactly_now_runs_next_one_interval_on():
    now = dt.datetime(2026, 11, 18, 9, 30)
    config = {"type": "Cron", "interval": 15, "startDateTime": now.isoformat()}
    assert local_schedules.next_run(config, now) == now + dt.timedelta(minutes=15)
    later = {**config, "startDateTime": (now + dt.timedelta(minutes=1)).isoformat()}
    assert local_schedules.next_run(later, now) == now + dt.timedelta(minutes=1)
