"""savings/planner_inputs: the edges the earlier tests left open (mutation batch 481). Each refusal of a bad
input in its own words, the per-run label at its boundaries, rounding to 6 places, and a validated optimization
without runs placed by its workload."""

from __future__ import annotations

import copy

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import accruals as ac
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import baselines as bl
from fabric_desktop.savings import planner_inputs as pi
from tests.test_savings_planner_inputs import WL, cu, events, proposal, validation


@pytest.fixture
def world():
    p1, p2 = proposal("nb-2"), proposal("nb-3")
    o1, o2 = p1["optimization_id"], p2["optimization_id"]
    vals = [validation(o1, [6, 7, 5]), validation(o2, [8, 8, 9], kind=ms.ESTIMATED)]
    acc = [events(o1, cu(6), "e1"), events(o1, cu(6), "e2", when="2026-11-11T00:00:00Z"),
           events(o2, cu(8, ms.ESTIMATED), "e3")]
    return {"opts": [p1, p2], "vals": vals, "acc": acc, "o1": o1, "o2": o2}


def shape(w):
    return pi.demand_inputs(w["vals"], w["acc"], w["opts"])


def refused(w, match, *, vals=None, acc=None, opts=None):
    with pytest.raises(ValueError, match=match):
        pi.demand_inputs(w["vals"] if vals is None else vals, w["acc"] if acc is None else acc,
                         w["opts"] if opts is None else opts)


# ---------- refusals

def test_optimizations_that_arent_a_list_or_have_bad_links_are_refused(world):
    refused(world, "^The optimizations must be a list of proposals", vals=[], acc=[], opts={"x": 1})
    bad = copy.deepcopy(world["opts"][0])
    bad["links"] = [5]
    refused(world, "has links that aren't optimization_link rows", vals=[], acc=[], opts=[bad])


def _val(world, **change):
    v = copy.deepcopy(world["vals"][0])
    for key, value in change.items():
        where, _, field = key.partition("__")
        if field:
            v[where][field] = value
        else:
            v[where] = value
    return v


@pytest.mark.parametrize("change, match", [
    ({"change": None}, "Each validation must be a result of baselines.validate"),
    ({"row__optimization_id": ""}, "has no optimization_id, so it can't be matched"),
    ({"result": "maybe"}, "has result 'maybe'; it must be one of"),
    ({"row__at": None}, "has no time, so the latest can't be told"),
    ({"change__classification": ms.SCENARIO}, "has a change classified 'scenario'"),
    ({"change__change_cu_hours": None}, "has a change that is known without a figure"),
    ({"change__change_cu_hours": "-1"}, "has a change of '-1'; it must be a finite number"),
])
def test_bad_validations_are_refused_by_what_is_wrong(world, change, match):
    refused(world, match, vals=[_val(world, **change)], acc=[])


def test_a_verified_change_of_exactly_the_tolerance_is_no_change(world):
    v = _val(world, change__change_cu_hours=pi._TOL)
    [info] = [o for o in pi.demand_inputs([v], [], world["opts"])["optimizations"]
              if o["optimization_id"] == world["o1"]]
    assert info["label"] == pi.NO_CHANGE


def test_an_event_of_no_named_optimization_says_none(world):
    e = copy.deepcopy(world["acc"][0]["events"][0]) | {"optimization_id": ""}
    refused(world, "is of optimization none, which isn't among", acc=[e])


def test_an_accrual_below_1_cu_hour_is_still_an_accrual(world):
    e = copy.deepcopy(world["acc"][0]["events"][0]) | {"cu_hours": 0.5}
    v = pi.demand_inputs(world["vals"], [e], world["opts"])["capacities"][0]["verified"]
    assert v["reduction_cu_hours"] == 0.5


# ---------- the latest validation

def test_of_two_validations_at_one_time_with_one_result_the_first_is_kept(world):
    first = world["vals"][0]
    second = copy.deepcopy(first)
    second["row"]["validation_id"] = "va-second"
    second["change"]["change_cu_hours"] = -1.0
    out = pi.demand_inputs([first, second], [], world["opts"])
    [info] = [o for o in out["optimizations"] if o["optimization_id"] == world["o1"]]
    assert info["validation_id"] == first["row"]["validation_id"]


# ---------- the per-run change

def _v(result, signed, cls=ms.MEASURED, reason=None, top=None):
    change = {"change_cu_hours": signed, "classification": cls}
    if reason is not None:
        change["reason"] = reason
    v = {"result": result, "row": {"validation_id": "va"}, "change": change}
    if top is not None:
        v["reason"] = top
    return v


def test_a_per_run_increase_is_one_past_the_tolerance_whatever_the_result():
    assert pi._per_run("o", _v(bl.VALIDATING, 1.0))["label"] == pi.INCREASE
    assert pi._per_run("o", _v(bl.VALIDATING, pi._TOL))["label"] == pi.NO_CHANGE
    assert pi._per_run("o", _v(bl.VALIDATING, -1.0))["label"] == pi.REDUCTION
    assert pi._per_run("o", _v(bl.REGRESSED, -1.0))["label"] == pi.INCREASE


def test_an_unknown_per_run_change_gives_the_change_reason_then_the_validation_reason():
    assert pi._per_run("o", _v(bl.VALIDATING, None, ms.UNKNOWN, reason="r1", top="r2"))["reason"] == "r1"
    assert pi._per_run("o", _v(bl.VALIDATING, None, ms.UNKNOWN, top="r2"))["reason"] == "r2"
    assert pi._per_run("o", _v(bl.VALIDATING, None, ms.UNKNOWN))["reason"] == "Its change isn't known."


# ---------- totals

def test_totals_round_to_6_places_and_count_measured_events():
    b = pi._bucket(pi.VERIFIED_INPUT)
    b["events"] = [{"ledger_event_id": "a", "cu_hours": 0.1234567, "classification": ms.MEASURED},
                   {"ledger_event_id": "b", "cu_hours": -0.7654321, "classification": ms.MEASURED},
                   {"ledger_event_id": "c", "cu_hours": 0.25, "classification": ms.ESTIMATED}]
    out = pi._finish(b)
    assert out["reduction_cu_hours"] == 0.373457 and out["increase_cu_hours"] == 0.765432
    assert out["demand_change_cu_hours"] == 0.391975
    assert (out["measured_events"], out["estimated_events"]) == (2, 1)


def test_a_verified_input_with_no_events_is_unknown(world):
    world["acc"] = []
    assert shape(world)["capacities"][0]["verified"]["classification"] == ms.UNKNOWN


# ---------- what isn't counted

def test_a_layer_that_isnt_an_optimizations_is_named_by_its_attribution(world):
    run = {"execution_id": "pc1", "workload_key": WL, "baseline_version": "b1", "at": "2026-11-10T00:00:00Z",
           "ran": at.PC}
    local = ms.make(ms.ESTIMATED, "x", ms.CU_HOURS, 7, method="calibration", assumptions=["a"])
    result = ac.accrue(at.attribute(run, cu(10), [], local_avoided=local), scope={"capacity_id": "cap-1"})
    [e] = result["events"]
    world["acc"].append(result)
    [x] = [x for x in shape(world)["excluded"] if x["id"] == e["ledger_event_id"]]
    assert x["reason"].startswith(f"It is a {e['attribution']} layer, not an optimization's demand change.")
    assert e["attribution"]


def test_a_validation_without_runs_is_placed_by_the_workload_its_baseline_names(world):
    world["acc"] = []
    v = world["vals"][0]
    v["change"].pop("workload_id", None)
    v["before"] = {"workload_id": WL}
    [w] = shape(world)["capacities"][0]["workloads"]
    assert w["workload_key"] == WL and w["verified"]["optimization_ids"] == [world["o1"]]


def test_a_validation_without_runs_or_a_workload_is_unplaced_with_the_reason(world):
    world["acc"] = []
    for v in world["vals"]:
        v["change"].pop("workload_id", None)
        v.pop("before", None)
    out = shape(world)
    assert out["capacities"] == []
    assert {x["reason"] for x in out["unplaced"]} == {"It doesn't name its workload."}


def test_a_regressed_validation_without_runs_is_listed_as_a_regression(world):
    p3 = proposal("nb-4")
    o3 = p3["optimization_id"]
    world["opts"].append(p3)
    world["vals"].append(validation(o3, [12, 13, 12]))
    assert world["vals"][-1]["result"] == bl.REGRESSED
    world["acc"] = []
    cap = shape(world)["capacities"][0]
    assert cap["estimated"]["regressions"] == [o3] and cap["verified"]["regressions"] == []
