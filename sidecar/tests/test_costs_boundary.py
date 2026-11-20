"""Boundary kill tests for costs (mutation testing, batch 168)."""
from __future__ import annotations

import datetime as dt

from fabric_desktop import costs

NOW = dt.datetime(2026, 10, 15, 12, tzinfo=dt.timezone.utc)
AT = "2026-10-15T10:00:00+00:00"
CFG = costs.settings({"python_cu": 2, "spark_cu": 8, "price_per_cu_hour": 0.5})


def test_a_run_of_zero_seconds_has_a_known_time():
    rows = costs.activities([{"entity": "orders", "env": "prod", "started_at": AT, "run_id": "r1", "seconds": 0}], [],
                            [{"job": "load", "seconds": 0, "finished_at": AT}], CFG, now=NOW)
    assert [r["seconds"] for r in rows] == [0.0, 0.0]


def test_a_run_exactly_the_period_ago_is_in_the_report():
    since = (NOW - dt.timedelta(days=7)).isoformat()
    rows = costs.activities([{"entity": "orders", "env": "prod", "started_at": since, "run_id": "r1", "seconds": 30}],
                            [], [], CFG, now=NOW)
    assert costs.report(rows, CFG, 7, NOW)["total"]["runs"] == 1
