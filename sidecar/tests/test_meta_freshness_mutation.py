"""Mutation-testing follow-ups for meta/freshness.py (docs/quality/mutation-testing.md, batch 38).

Pure: no project on disk. ``NOW`` is in this machine's zone, as status() works in local time.
"""

import datetime as dt

import pytest

from fabric_desktop.meta import freshness

NOW = dt.datetime(2026, 10, 15, 9, 0).astimezone()
ENVS = ("dev", "test", "prod")


def ok(started, seconds=0, **extra):
    return {"status": "ok", "started_at": started.isoformat(), "seconds": seconds, "run_id": "r1", **extra}


def test_the_promise_is_described_in_words():
    assert freshness.describe({"within_hours": 24}) == "every 24 h"
    assert freshness.describe({"by": "07:00"}) == "by 07:00 each day"
    assert freshness.describe({"within_hours": 1.5, "by": "07:00"}) == "every 1.5 h and by 07:00 each day"
    assert freshness.status({"by": "07:00"}, [], NOW)["expected"] == "by 07:00 each day"


def test_a_freshness_that_isnt_a_table_says_how_to_write_one():
    assert freshness.problems("a", {"freshness": 5}, ENVS) == ["a: [freshness] must be a table, like within_hours = 24"]


@pytest.mark.parametrize("spec", [{"within_hours": 24}, {"by": "07:00"}, {"within_hours": 0.5}, {"within_hours": 1}])
def test_either_half_of_a_promise_is_enough_and_any_hours_above_0_are_allowed(spec):
    assert freshness.problems("a", {"freshness": spec}, ENVS) == []


def test_zero_hours_is_refused():
    assert freshness.problems("a", {"freshness": {"within_hours": 0, "by": "07:00"}}, ENVS) == [
        "a: freshness within_hours must be a number of hours above 0"]


def test_the_run_ends_its_seconds_after_it_started_and_keeps_its_own_offset():
    started = dt.datetime(2026, 10, 15, 6, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    out = freshness.status({"within_hours": 24}, [ok(started, seconds=1800)], NOW)
    assert out["last_ok"] == "2026-10-15T06:30:00+02:00"


def test_a_start_without_an_offset_is_utc():
    out = freshness.status({"within_hours": 24}, [{"status": "ok", "started_at": "2026-10-15T04:00:00", "seconds": 0}],
                           NOW)
    assert out["last_ok"] == "2026-10-15T04:00:00+00:00"


def test_age_is_in_hours_to_two_places():
    out = freshness.status({"within_hours": 24}, [ok(NOW - dt.timedelta(minutes=20))], NOW)
    assert out["age_hours"] == 0.33
    assert freshness.status({"within_hours": 24}, [ok(NOW - dt.timedelta(hours=3))], NOW)["age_hours"] == 3.0
    assert freshness.status({"within_hours": 200}, [ok(NOW - dt.timedelta(hours=100))], NOW)["age_hours"] == 100.0


def test_the_due_time_is_given():
    done = NOW - dt.timedelta(hours=2)
    out = freshness.status({"within_hours": 24}, [ok(done)], NOW)
    assert out["due_at"] == (done + dt.timedelta(hours=24)).isoformat()


def test_exactly_at_the_promised_hours_is_still_fresh():
    p = {"within_hours": 2}
    assert freshness.status(p, [ok(NOW - dt.timedelta(hours=2))], NOW)["status"] == "fresh"
    assert freshness.status(p, [ok(NOW - dt.timedelta(hours=2, seconds=1))], NOW)["status"] == "late"


def test_by_a_time_is_late_from_that_minute_and_a_run_at_midnight_counts_as_today():
    p = {"by": "07:00"}
    at_seven = NOW.replace(hour=7, minute=0)
    midnight = NOW.replace(hour=0, minute=0)
    yesterday = midnight - dt.timedelta(minutes=1)
    assert freshness.status(p, [ok(yesterday)], at_seven)["status"] == "late"
    assert freshness.status(p, [ok(yesterday)], at_seven - dt.timedelta(minutes=1))["status"] == "fresh"
    assert freshness.status(p, [ok(midnight)], at_seven)["status"] == "fresh"


def test_late_tables_come_first_then_never_loaded_then_fresh():
    entities = {"a_fresh": {"freshness": {"within_hours": 24}}, "m_never": {"freshness": {"within_hours": 24}},
                "z_late": {"freshness": {"within_hours": 24}}}
    runs = [{"env": "prod", "entity": "a_fresh", **ok(NOW - dt.timedelta(hours=1))},
            {"env": "prod", "entity": "z_late", **ok(NOW - dt.timedelta(hours=30))}]
    out = freshness.report(entities, runs, ["prod"], NOW)
    assert [(x["entity"], x["status"]) for x in out] == [("z_late", "late"), ("m_never", "never"), ("a_fresh", "fresh")]


def test_the_failure_shown_is_the_latest_runs_only_when_it_didnt_succeed():
    p = {"within_hours": 24}
    fine = freshness.status(p, [ok(NOW - dt.timedelta(hours=1), message="all good")], NOW)
    assert fine["failure"] is None and fine["last_failed"] is False
    failed = {"status": "failed", "started_at": NOW.isoformat(), "run_id": "r2", "message": "boom"}
    out = freshness.status(p, [failed, ok(NOW - dt.timedelta(hours=1))], NOW)
    assert out["failure"] == "boom" and out["failed_run"] == "r2"


def test_a_run_with_no_seconds_recorded_ended_when_it_started():
    started = NOW - dt.timedelta(hours=1)
    out = freshness.status({"within_hours": 24}, [ok(started, seconds=None)], NOW)
    assert out["last_ok"] == started.isoformat()


def test_a_daily_promise_is_due_today_until_it_is_kept_then_tomorrow():
    p = {"by": "07:00"}
    six = NOW.replace(hour=6, minute=0)
    seven = NOW.replace(hour=7, minute=0)
    not_yet = freshness.status(p, [ok(six - dt.timedelta(days=1))], six)
    assert not_yet["status"] == "fresh" and not_yet["due_at"] == seven.isoformat()
    kept = freshness.status(p, [ok(NOW.replace(hour=5, minute=0))], six)
    assert kept["status"] == "fresh" and kept["due_at"] == (seven + dt.timedelta(days=1)).isoformat()
    at_midnight = freshness.status(p, [ok(NOW.replace(hour=0, minute=0))], six)  # midnight is today
    assert at_midnight["due_at"] == (seven + dt.timedelta(days=1)).isoformat()


def test_removing_a_promise_leaves_no_blank_lines_at_the_end():
    text = 'name = "a"\n\n[freshness]\nwithin_hours = 24\n\n'
    assert freshness.set_promise(text, None, None, None) == 'name = "a"\n'
    assert freshness.set_promise('name = "a"\n\n\n', 6, None, None) == 'name = "a"\n\n[freshness]\nwithin_hours = 6\n'
