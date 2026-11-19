"""Workload evaluation edges the main tests don't pin down (mutation batch 136, docs/quality/mutation-testing.md):
defaults for a run with no message or place, a load of exactly one row, lateness counted unless told
otherwise, times shown in UTC, and the one line a change becomes in a Teams message."""

import datetime as dt

from fabric_desktop.workloads import evaluate

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 18, 12, tzinfo=UTC)


def test_a_failed_run_with_no_message_or_place_gets_the_defaults():
    [s] = evaluate.signals("sales", {"orders": {}}, [{"entity": "orders", "status": "failed", "run_id": "r1"}], "prod", NOW)
    assert s["text"] == "The run failed."
    assert s["evidence"]["ran_on"] == "" and s["evidence"]["job_id"] == ""
    [s] = evaluate.signals("sales", {"orders": {}}, [{"entity": "orders", "status": "failed", "run_id": "r1",
                                                      "message": "boom", "ran_on": "pc", "job_id": "j"}], "prod", NOW)
    assert s["text"] == "boom" and s["evidence"]["ran_on"] == "pc" and s["evidence"]["job_id"] == "j"


def test_a_load_of_one_row_counts_its_rejected_rows():
    promised = [{"product": "Sales", "envs": ["prod"], "max_rejected_pct": 10}]
    load = {"status": "ok", "run_id": "r1", "rows_read": 1, "quarantined": 1}
    s = evaluate.promise_signal("orders", promised, [load], "prod", NOW)
    assert s is not None and "100.0 % of rows rejected" in s["text"]


def test_lateness_counts_unless_the_caller_says_the_entity_reports_it():
    promised = [{"product": "Sales", "envs": ["prod"], "within_hours": 1}]
    old = {"status": "ok", "run_id": "r1", "started_at": "2026-11-17T00:00:00+00:00", "seconds": 0}
    s = evaluate.promise_signal("orders", promised, [old], "prod", NOW)
    assert s is not None and s["evidence_id"].startswith("late:")
    assert evaluate.promise_signal("orders", promised, [old], "prod", NOW, skip_late=True) is None


def test_times_are_shown_in_utc_and_anything_else_as_it_is():
    assert evaluate._utc("2026-10-17T09:40:00+02:00") == "2026-10-17 07:40 UTC"
    assert evaluate._utc("2026-10-17T07:40:00") == "2026-10-17T07:40:00"  # naive: as it is
    assert evaluate._utc("not a time") == "not a time"


def inc(**kw):
    return {"kind": "run_failed", "env": "prod", "name": "orders", "title": "orders failed in Prod", "text": "boom"} | kw


def test_a_recovery_says_recovered_or_cleared_and_names_the_environment_without_a_name():
    assert evaluate.describe({"change": "recovered", "incident": inc()}) == \
        "**orders** recovered: run failed in Prod is over."
    assert evaluate.describe({"change": "recovered", "incident": inc(resolution="cleared", name="")}) == \
        "**Prod** cleared: run failed in Prod is over."


def test_an_escalation_names_who_and_falls_back_to_on_call():
    assert evaluate.describe({"change": "escalated", "incident": inc(escalated_to="dana")}) == \
        "Not acknowledged, escalated to dana: **orders failed in Prod**. boom"
    assert evaluate.describe({"change": "escalated", "incident": inc(text=None)}) == \
        "Not acknowledged, escalated to on-call: **orders failed in Prod**."


def test_an_opened_or_reopened_incident_says_so_and_names_its_owner():
    assert evaluate.describe({"change": "opened", "incident": inc()}) == "**orders failed in Prod**. boom"
    assert evaluate.describe({"change": "reopened", "incident": inc(owner="dana", text="")}) == \
        "again: **orders failed in Prod**.  Owner: dana."
    assert evaluate.describe({"change": "opened", "incident": inc(kind="agent_offline", text=None)}) == \
        "**orders failed in Prod**."


def test_a_run_waiting_for_a_decision_with_no_message_says_what_waits():
    [s] = evaluate.signals("sales", {"orders": {}}, [{"entity": "orders", "status": "needs_decision", "run_id": "r1"}],
                           "prod", NOW)
    assert s["text"] == "Schema drift or quarantined rows are waiting for review."


def test_an_objective_names_the_newest_finished_run_not_one_still_going():
    spec = {"objectives": {"success_rate": 100, "runs": 3, "envs": ["prod"]}}
    runs = [{"entity": "orders", "status": "running", "run_id": "r0"},
            {"entity": "orders", "status": "ok", "run_id": "r1"},
            {"entity": "orders", "status": "failed", "run_id": "r2"},
            {"entity": "orders", "status": "ok", "run_id": "r3"}]
    [s] = [x for x in evaluate.signals("sales", {"orders": spec}, runs, "prod", NOW) if x["kind"] == "objective"]
    assert s["evidence_id"] == "objective:r1"


def test_a_load_with_no_rejected_count_rejected_nothing():
    promised = [{"product": "Sales", "envs": ["prod"], "max_rejected_pct": 0}]
    assert evaluate.promise_signal("orders", promised, [{"status": "ok", "run_id": "r1", "rows_read": 10}], "prod", NOW) is None


def test_exactly_the_promised_share_of_rejected_rows_keeps_the_promise():
    promised = [{"product": "Sales", "envs": ["prod"], "max_rejected_pct": 10}]
    load = {"status": "ok", "run_id": "r1", "rows_read": 10, "quarantined": 1}
    assert evaluate.promise_signal("orders", promised, [load], "prod", NOW) is None


def test_the_products_a_late_table_lets_down_go_on_its_late_signal():
    spec = {"freshness": {"within_hours": 1, "envs": ["prod"]}}
    runs = [{"entity": "orders", "status": "failed", "run_id": "r2"},
            {"entity": "orders", "status": "ok", "run_id": "r1", "started_at": "2026-11-17T00:00:00+00:00", "seconds": 0}]
    promises = {"orders": [{"product": "Sales", "envs": ["prod"], "within_hours": 1}]}
    out = evaluate.signals("sales", {"orders": spec}, runs, "prod", NOW, promises=promises)
    by_kind = {s["kind"]: s for s in out}
    assert set(by_kind) == {"run_failed", "late"}
    assert by_kind["late"]["evidence"]["promise_products"] == ["Sales"]
    assert "promise_products" not in by_kind["run_failed"]["evidence"]


def test_an_agent_is_missing_from_exactly_its_stale_time():
    record = {"agent": "etl-02", "heartbeat_at": "2026-11-18T11:55:00Z", "stale_after": "2026-11-18T12:00:00Z"}
    assert len(evaluate.agent_signals([record], "prod", NOW)) == 1
    assert evaluate.agent_signals([record], "prod", NOW - dt.timedelta(seconds=1)) == []
