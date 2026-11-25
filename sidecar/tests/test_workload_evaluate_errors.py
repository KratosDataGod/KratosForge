"""Evaluation paths no other test reaches: a freshness promise made only for other environments,
and one that holds or has nothing to judge yet."""

import datetime as dt

from fabric_desktop.workloads import evaluate

NOW = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)
OLD_LOAD = {
    "entity": "orders",
    "status": "ok",
    "run_id": "r1",
    "started_at": "2026-11-10T00:00:00+00:00",
    "seconds": 0,
}


def test_a_freshness_promise_for_other_environments_raises_nothing_here():
    spec = {"freshness": {"within_hours": 1, "envs": ["prod"]}}
    assert evaluate.signals("sales", {"orders": spec}, [OLD_LOAD], "dev", NOW) == []
    [late] = evaluate.signals("sales", {"orders": spec}, [OLD_LOAD], "prod", NOW)
    assert late["kind"] == "late"


def test_a_late_table_in_an_environment_its_promise_skips_still_reports_a_product_promise():
    spec = {"freshness": {"within_hours": 1, "envs": ["prod"]}}
    promises = {"orders": [{"product": "Sales", "envs": ["dev"], "within_hours": 1}]}
    [s] = evaluate.signals(
        "sales", {"orders": spec}, [OLD_LOAD], "dev", NOW, promises=promises
    )
    assert s["kind"] == "promise" and s["evidence"]["products"] == ["Sales"]


def test_a_table_loaded_in_time_or_never_loaded_here_is_not_reported_late():
    spec = {"freshness": {"within_hours": 24, "envs": ["prod"]}}
    fresh = OLD_LOAD | {"started_at": "2026-11-18T11:00:00+00:00"}
    assert evaluate.signals("sales", {"orders": spec}, [fresh], "prod", NOW) == []
    assert evaluate.signals("sales", {"orders": spec}, [], "prod", NOW) == []
    skipped = {"entity": "orders", "status": "skipped", "run_id": "r2"}
    assert evaluate.signals("sales", {"orders": spec}, [skipped], "prod", NOW) == []
