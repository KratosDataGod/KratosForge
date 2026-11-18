"""Kill tests for workloads/evaluate.py (docs/quality/mutation-testing.md, batch NN): the signals
from the run log, product promises at their exact limits, agents past their stale time, the
"can't check" signal and the one-line Teams messages."""

from __future__ import annotations

import datetime as dt

from fabric_desktop.errors import NotSetUp
from fabric_desktop.workloads import evaluate
from fabric_desktop.workloads.classify import classify

UTC = dt.UTC
NOW = dt.datetime(2026, 10, 16, 9, 0).astimezone()


def _run(entity="a", status="ok", run_id="r1", hours_ago=1.0, **kw):
    return {"entity": entity, "status": status, "run_id": run_id,
            "started_at": (NOW - dt.timedelta(hours=hours_ago)).isoformat(), "seconds": 0} | kw


# ---------------------------------------------------------------------------- run_failed and needs_decision


def test_a_failed_run_signal_carries_every_field():
    run = _run(status="failed", run_id="r7", message="KeyError: 'region'", ran_on="srv-1", job_id="j9")
    [s] = evaluate.signals("sales", {"a": {"owner": "dana@contoso.com"}}, [run], "prod", now=NOW, team="Data")
    assert s == {"workload": "meta:sales:a", "name": "a", "owner": "dana@contoso.com", "team": "Data",
                 "criticality": "", "kind": "run_failed", "evidence_id": "r7", "title": "a failed in Prod",
                 "text": "KeyError: 'region'", "classification": classify("KeyError: 'region'"),
                 "evidence": {"source": "run log", "run_id": "r7", "started_at": run["started_at"],
                              "ran_on": "srv-1", "job_id": "j9"}}


def test_a_failed_run_without_a_message_or_host_says_so_plainly():
    [s] = evaluate.signals("sales", {"a": {}}, [_run(status="failed")], "test", now=NOW)
    assert s["text"] == "The run failed." and s["title"] == "a failed in Test"
    assert s["evidence"]["ran_on"] == "" and s["evidence"]["job_id"] == ""


def test_a_needs_decision_run_signal_and_its_default_text():
    [s] = evaluate.signals("sales", {"a": {}}, [_run(status="needs_decision", run_id="r2")], "prod", now=NOW)
    assert s["kind"] == "needs_decision" and s["evidence_id"] == "r2"
    assert s["title"] == "a needs a decision in Prod"
    assert s["text"] == "Schema drift or quarantined rows are waiting for review."
    assert s["evidence"]["run_id"] == "r2" and "classification" not in s
    [m] = evaluate.signals("sales", {"a": {}}, [_run(status="needs_decision", message="drift")], "prod", now=NOW)
    assert m["text"] == "drift"


def test_only_the_newest_finished_run_counts():
    runs = [_run(status="running", run_id="r3"), _run(status="skipped", run_id="r4"),
            _run(status="failed", run_id="r2"), _run(status="ok", run_id="r1")]
    [s] = evaluate.signals("sales", {"a": {}}, runs, "prod", now=NOW)
    assert s["evidence_id"] == "r2"
    assert evaluate.signals("sales", {"a": {}}, [_run(status="running")], "prod", now=NOW) == []
    assert evaluate.signals("sales", {"a": {}}, [_run(status="failed", entity="b")], "prod", now=NOW) == []


def test_placeholder_owners_are_dropped_whatever_their_case_or_spaces():
    [s] = evaluate.signals("sales", {"a": {"owner": "  TBD "}}, [_run(status="failed")], "prod", now=NOW)
    assert s["owner"] == ""
    [s] = evaluate.signals("sales", {"a": {"owner": None}}, [_run(status="failed")], "prod", now=NOW)
    assert s["owner"] == ""


def test_a_declared_criticality_wins_over_an_assigned_one():
    run = [_run(status="failed")]
    [s] = evaluate.signals("sales", {"a": {"objectives": {"criticality": "low"}}}, run, "prod", now=NOW,
                           criticality={"a": "high"})
    assert s["criticality"] == "low"
    [s] = evaluate.signals("sales", {"a": {"objectives": "not a table"}}, run, "prod", now=NOW,
                           criticality={"a": "high"})
    assert s["criticality"] == "high"


def test_now_defaults_to_the_current_time():
    fresh = [dict(_run(), started_at=dt.datetime.now().astimezone().isoformat())]
    spec = {"a": {"freshness": {"within_hours": 1, "envs": ["prod"]}}}
    assert evaluate.signals("sales", spec, fresh, "prod") == []


# ---------------------------------------------------------------------------- late


def test_a_late_signal_carries_its_evidence():
    spec = {"freshness": {"within_hours": 2, "envs": ["prod"]}}
    old = _run(hours_ago=5)
    [s] = evaluate.signals("sales", {"a": spec}, [old], "prod", now=NOW)
    assert s["kind"] == "late" and s["title"] == "a is late in Prod"
    done = (NOW - dt.timedelta(hours=5)).isoformat()
    assert s["evidence_id"] == f"late:{done}"
    assert s["text"] == "last updated 5 h ago, promised every 2 h"
    assert s["evidence"] == {"source": "freshness promise", "expected": s["evidence"]["expected"],
                             "last_ok": done, "due_at": (NOW - dt.timedelta(hours=3)).isoformat()}
    assert "promise_products" not in s["evidence"]
    assert evaluate.signals("sales", {"a": spec}, [_run(hours_ago=1)], "prod", now=NOW) == []


def test_a_late_table_names_only_the_products_late_in_this_environment():
    spec = {"freshness": {"within_hours": 2, "envs": ["prod"]}}
    promises = {"a": [{"product": "Sales", "envs": ["prod"], "within_hours": 3},
                      {"product": "Weekly", "envs": ["prod"], "within_hours": 100},
                      {"product": "TestOnly", "envs": ["test"], "within_hours": 1},
                      {"product": "NoEnvs", "within_hours": 1},
                      {"product": "Rows", "envs": ["prod"], "max_rejected_pct": 50},
                      {"product": "ByTime", "envs": ["prod"], "by": "00:01"}]}
    old = _run(hours_ago=30)
    got = evaluate.signals("sales", {"a": spec}, [old], "prod", now=NOW, promises=promises)
    assert [s["kind"] for s in got] == ["late"]
    assert got[0]["evidence"]["promise_products"] == ["Sales", "ByTime"]


def test_a_table_late_in_another_environment_still_reports_its_products():
    spec = {"freshness": {"within_hours": 2, "envs": ["test"]}}
    promises = {"a": [{"product": "Sales", "envs": ["prod"], "within_hours": 3}]}
    got = evaluate.signals("sales", {"a": spec}, [_run(hours_ago=30)], "prod", now=NOW, promises=promises)
    assert [s["kind"] for s in got] == ["promise"]


# ---------------------------------------------------------------------------- product promises


def test_rejected_rows_exactly_at_the_limit_keep_the_promise():
    promised = [{"product": "P", "envs": ["prod"], "max_rejected_pct": 2}]
    at = _run(rows_read=1000, quarantined=20)
    assert evaluate.promise_signal("a", promised, [at], "prod", NOW) is None
    over = _run(rows_read=1000, quarantined=21, run_id="r5")
    s = evaluate.promise_signal("a", promised, [over], "prod", NOW)
    assert s == {"kind": "promise", "evidence_id": "rejected:r5", "title": "P broke its promise in Prod (a)",
                 "text": "P: 2.1 % of rows rejected in the newest load, promised under 2 %. Readers of the product rely on it.",
                 "evidence": {"source": "product promise", "products": ["P"],
                              "reasons": ["P: 2.1 % of rows rejected in the newest load, promised under 2 %"]}}


def test_rejected_rows_use_the_newest_load_and_count_missing_numbers_as_none():
    promised = [{"product": "P", "envs": ["prod"], "max_rejected_pct": 0}]
    failed_then_ok = [_run(status="failed", run_id="r9", rows_read=10, quarantined=10),
                      _run(status="needs_decision", run_id="r8", rows_read=10, quarantined=1)]
    assert evaluate.promise_signal("a", promised, failed_then_ok, "prod", NOW)["evidence_id"] == "rejected:r8"
    assert evaluate.promise_signal("a", promised, [_run(rows_read=10)], "prod", NOW) is None  # no quarantined
    assert evaluate.promise_signal("a", promised, [_run(quarantined=5)], "prod", NOW) is None  # no rows_read
    assert evaluate.promise_signal("a", promised, [_run(rows_read=1, quarantined=1, status="failed")],
                                   "prod", NOW) is None  # no load
    assert evaluate.promise_signal("a", [{"product": "P", "envs": ["prod"]}], [_run(rows_read=1, quarantined=1)],
                                   "prod", NOW) is None  # no limit


def test_lateness_is_left_out_when_skipped_and_promises_elsewhere_are_ignored():
    promised = [{"product": "P", "envs": ["prod"], "within_hours": 1}]
    old = [_run(hours_ago=5)]
    s = evaluate.promise_signal("a", promised, old, "prod", NOW)
    assert s["evidence_id"].startswith("late:") and s["text"].startswith("P: last updated 5 h ago")
    assert evaluate.promise_signal("a", promised, old, "prod", NOW, skip_late=True) is None
    assert evaluate.promise_signal("a", promised, old, "test", NOW) is None
    assert evaluate.promise_signal("a", [{"product": "P", "within_hours": 1}], old, "prod", NOW) is None
    assert evaluate.promise_signal("a", promised, [_run(hours_ago=0.5)], "prod", NOW) is None


def test_several_products_share_one_signal_with_sorted_unique_evidence():
    promised = [{"product": "B", "envs": ["prod"], "within_hours": 1},
                {"product": "A", "envs": ["prod"], "within_hours": 2}]
    s = evaluate.promise_signal("a", promised, [_run(hours_ago=5)], "prod", NOW)
    assert s["title"] == "B, A broke their promise in Prod (a)"
    assert s["evidence_id"].count("late:") == 1 and "|" not in s["evidence_id"]
    assert s["evidence"]["products"] == ["B", "A"] and len(s["evidence"]["reasons"]) == 2


def test_a_broken_promise_signal_in_signals_carries_the_base_fields():
    promises = {"a": [{"product": "P", "envs": ["prod"], "max_rejected_pct": 1}]}
    [s] = evaluate.signals("sales", {"a": {}}, [_run(rows_read=100, quarantined=50)], "prod", now=NOW,
                           promises=promises, team="T")
    assert s["kind"] == "promise" and s["workload"] == "meta:sales:a" and s["team"] == "T"


# ---------------------------------------------------------------------------- objectives


def test_an_objective_below_target_is_one_signal_with_its_numbers():
    runs = [_run(status="running", run_id="x")] + [_run(status="failed", run_id="r0")] + \
        [_run(run_id=f"r{i}") for i in range(1, 10)]
    spec = {"objectives": {"success_rate": 95, "runs": 10, "envs": ["prod"]}}
    [s] = [x for x in evaluate.signals("sales", {"a": spec}, runs, "prod", now=NOW) if x["kind"] == "objective"]
    assert s["evidence_id"] == "objective:r0"
    assert s["title"] == "a is below its success objective in Prod"
    assert s["text"] == "9 of the last 10 runs succeeded (90 %); the objective is 95 % of the last 10 runs succeed."
    assert s["evidence"]["source"] == "run log" and s["evidence"]["status"] == "breached"
    assert [x for x in evaluate.signals("sales", {"a": spec}, runs, "test", now=NOW) if x["kind"] == "objective"] == []
    met = {"objectives": {"success_rate": 90, "runs": 10, "envs": ["prod"]}}
    assert [x for x in evaluate.signals("sales", {"a": met}, runs, "prod", now=NOW) if x["kind"] == "objective"] == []


def test_an_objective_from_an_assigned_criticality():
    runs = [_run(status="failed", run_id="r0")] + [_run(run_id=f"r{i}") for i in range(1, 10)]
    got = evaluate.signals("sales", {"a": {}}, runs, "prod", now=NOW, criticality={"a": "critical"})
    assert [x["kind"] for x in got] == ["run_failed", "objective"]
    assert got[1]["criticality"] == "critical"


# ---------------------------------------------------------------------------- agents


def _status(name, heartbeat, stale_after, state="running", schedules=()):
    return {"agent": name, "host": f"srv-{name}", "version": "0.21.0", "state": state,
            "heartbeat_at": heartbeat, "stale_after": stale_after, "schedules": list(schedules)}


def test_an_agent_exactly_at_its_stale_time_is_offline_and_one_second_before_is_not():
    now = dt.datetime(2026, 10, 17, 7, 43, tzinfo=UTC)
    rec = _status("etl-02", "2026-10-17T07:40:00Z", "2026-10-17T07:43:00Z")
    [s] = evaluate.agent_signals([rec], "prod", now)
    assert s == {"kind": "agent_offline", "workload": "agent:etl-02", "name": "etl-02", "owner": "", "team": "",
                 "criticality": "", "evidence_id": "heartbeat:2026-10-17T07:40:00Z",
                 "title": "Agent etl-02 stopped reporting in Prod",
                 "text": "Its last heartbeat was 2026-10-17 07:40 UTC; it was due again by 2026-10-17 07:43 UTC.",
                 "evidence": {"source": "agents/etl-02.json", "host": "srv-etl-02",
                              "heartbeat_at": "2026-10-17T07:40:00Z", "stale_after": "2026-10-17T07:43:00Z",
                              "version": "0.21.0"}}
    assert evaluate.agent_signals([rec], "prod", now - dt.timedelta(seconds=1)) == []


def test_an_offline_agent_names_its_schedules_that_have_an_id():
    now = dt.datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
    rec = _status("etl-02", "2026-10-17T07:40:00Z", "2026-10-17T07:43:00Z",
                  schedules=[{"id": "nightly"}, {"id": ""}, {}, {"id": "hourly"}])
    [s] = evaluate.agent_signals([rec], "test", now)
    assert s["text"].endswith(" Its Test schedules (nightly, hourly) don't run while it is down.")
    rec["schedules"] = None
    [s] = evaluate.agent_signals([rec], "test", now)
    assert s["text"].endswith("07:43 UTC.")


def test_agents_skipped_nameless_self_stopped_unreadable_or_naive():
    now = dt.datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
    late = ("2026-10-17T07:00:00Z", "2026-10-17T07:03:00Z")
    records = [_status("", *late), _status(None, *late), _status("me", *late), _status("s", *late, state="stopped"),
               _status("bad", "x", "nope"), _status("naive", "x", "2026-10-17T07:03:00"),
               _status("missing", "x", None)]
    assert evaluate.agent_signals(records, "prod", now, me="me") == []
    [s] = evaluate.agent_signals([_status("me", *late)], "prod", now)  # without me, it counts
    assert s["name"] == "me"


def test_agent_signals_default_to_now():
    past = _status("old", "2000-01-01T00:00:00Z", "2000-01-01T00:03:00Z")
    future = _status("new", "2999-01-01T00:00:00Z", "2999-01-01T00:03:00Z")
    assert [s["name"] for s in evaluate.agent_signals([past, future], "prod")] == ["old"]


def test_utc_writes_aware_times_in_utc_and_leaves_anything_else_as_it_is():
    assert evaluate._utc("2026-10-17T09:40:00+02:00") == "2026-10-17 07:40 UTC"
    assert evaluate._utc("2026-10-17T07:40:00") == "2026-10-17T07:40:00"
    assert evaluate._utc("soon") == "soon"
    assert evaluate._utc(None) == "None"


# ---------------------------------------------------------------------------- can't check


def test_an_environment_not_set_up_opens_nothing_and_says_why():
    assert evaluate.unreadable("test", NotSetUp("No workspace is assigned.")) == ([], "No workspace is assigned.")


def test_an_unreadable_environment_is_one_cant_evaluate_signal():
    signals, message = evaluate.unreadable("prod", PermissionError("no"))
    assert message == ""
    assert signals == [evaluate.cant_evaluate("prod", "unauthorized",
                                              "Your sign-in can't read this. Ask for access, or sign in with an account that has it.")]


def test_cant_evaluate_names_the_state_in_lower_case_or_as_it_is():
    s = evaluate.cant_evaluate("prod", "offline", "Couldn't reach Fabric.")
    assert s == {"kind": "cant_evaluate", "workload": None, "name": "", "evidence_id": "offline",
                 "title": "Can't check Prod: offline",
                 "text": "Couldn't reach Fabric. Nothing is known about its workloads until it can be read again.",
                 "evidence": {"source": "evaluation", "state": "offline"}}
    assert evaluate.cant_evaluate("test", "throttled", "m")["title"] == "Can't check Test: fabric asked to slow down"
    assert evaluate.cant_evaluate("test", "Weird", "m")["title"] == "Can't check Test: weird"


# ---------------------------------------------------------------------------- Teams lines


def _change(change, **inc):
    base = {"kind": "run_failed", "name": "a", "env": "prod", "title": "a failed in Prod", "text": "boom", "owner": ""}
    return {"change": change, "incident": base | inc}


def test_describe_a_recovery_or_a_clearing():
    assert evaluate.describe(_change("recovered")) == "**a** recovered: run failed in Prod is over."
    assert evaluate.describe(_change("recovered", resolution="cleared")) == "**a** cleared: run failed in Prod is over."
    assert evaluate.describe(_change("recovered", name="", kind="odd")) == "**Prod** recovered: odd in Prod is over."


def test_describe_an_escalation():
    assert evaluate.describe(_change("escalated", escalated_to="dana")) == \
        "Not acknowledged, escalated to dana: **a failed in Prod**. boom"
    assert evaluate.describe(_change("escalated", text=None)) == \
        "Not acknowledged, escalated to on-call: **a failed in Prod**."


def test_describe_an_opening_a_reopening_and_the_owner():
    assert evaluate.describe(_change("opened")) == "**a failed in Prod**. boom"
    assert evaluate.describe(_change("reopened", owner="dana")) == "again: **a failed in Prod**. boom Owner: dana."
    assert evaluate.describe(_change("opened", text="")) == "**a failed in Prod**."


def test_a_load_of_one_row_is_judged_on_its_rejected_rows():
    promised = [{"product": "P", "envs": ["prod"], "max_rejected_pct": 50}]
    s = evaluate.promise_signal("a", promised, [_run(rows_read=1, quarantined=1, run_id="r3")], "prod", NOW)
    assert s["evidence_id"] == "rejected:r3" and "100.0 % of rows rejected" in s["text"]


def test_a_late_table_that_also_failed_puts_the_products_on_the_late_signal():
    spec = {"freshness": {"within_hours": 2, "envs": ["prod"]}}
    promises = {"a": [{"product": "Sales", "envs": ["prod"], "within_hours": 3}]}
    runs = [_run(status="failed", run_id="r2", hours_ago=1), _run(hours_ago=30)]
    failed, late = evaluate.signals("sales", {"a": spec}, runs, "prod", now=NOW, promises=promises)
    assert failed["kind"] == "run_failed" and "promise_products" not in failed["evidence"]
    assert late["kind"] == "late" and late["evidence"]["promise_products"] == ["Sales"]
