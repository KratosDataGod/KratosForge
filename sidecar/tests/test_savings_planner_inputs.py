"""ESI-602: verified and estimated optimization demand changes as separate capacity planner inputs,
never merged, unknown kept unknown, and never enough on their own to right-size a capacity."""

from __future__ import annotations

import copy

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.opportunities import model as om
from fabric_desktop.savings import accruals as ac
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import baselines as bl
from fabric_desktop.savings import links as lk
from fabric_desktop.savings import planner_inputs as pi

WL, DEF, SCOPE = "ws-1/nb-sales", "def-v1", "lh-1:sales/2026"


def cu(v, kind=ms.MEASURED, run_id="r"):
    if kind == ms.MEASURED:
        return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, v, run_id=run_id, source="Capacity Metrics export",
                       collected_at="2026-11-01T00:00:00Z")
    if kind == ms.ESTIMATED:
        return ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_HOURS, v, run_id=run_id, method="run time x CUs",
                       assumptions=["8 CUs configured"])
    return ms.unknown("fabric_job_cu", ms.CU_HOURS, "not in the export", run_id=run_id)


def fr(rid, v, day, month=10, kind=ms.MEASURED, wl=WL):
    return {"run_id": rid, "workload_key": wl, "definition_version": DEF, "input_scope": SCOPE,
            "at": f"2026-{month:02d}-{day:02d}T09:00:00Z", "cu": cu(v, kind, rid), "ran": "fabric"}


def base(wl=WL):
    return bl.baseline(wl, [fr("a", 10, 3, wl=wl), fr("b", 12, 10, wl=wl), fr("c", 9, 20, wl=wl)],
                       definition_version=DEF, input_scope=SCOPE, window_start="2026-10-01T00:00:00Z",
                       window_end="2026-11-01T00:00:00Z")


def validation(oid, values, kind=ms.MEASURED, wl=WL, **kw):
    runs = [fr(f"x{i}", v, 3 + i, month=11, kind=kind, wl=wl) for i, v in enumerate(values)]
    return bl.validate(oid, base(wl), runs, **({"outputs_match": True} | kw))


def proposal(item="nb-2", capacity="cap-1"):
    wl = {"workspace_id": "ws-1", "item_id": item, "type": "Notebook", "name": "Orders"}
    o = om.make(rule="OE-DUP-NB", category="duplicate_processing", action="eliminate", title="Duplicate",
                artifacts=[om.artifact(wl, "change")], current="x", problem="y",
                evidence=[om.evidence("code", "same", "definition")], recommendation="z", alternatives=[],
                effort="low", risk="low", confidence="proven", validation=["compare"], rollback="restore",
                source="analyzer", next_step={"kind": "open"})
    return lk.from_opportunity(o, at="2026-11-01T00:00:00Z", capacity_id=capacity)


def events(oid, after, eid="e1", capacity="cap-1", wl=WL, baseline=None, when="2026-11-10T00:00:00Z"):
    run = {"execution_id": eid, "workload_key": wl, "baseline_version": "b1", "at": when, "ran": at.FABRIC}
    opt = {"optimization_id": oid, "implemented_at": "2026-11-02T00:00:00Z", "action": "optimize_in_place",
           "state": "validating", "after": after}
    scope = {"capacity_id": capacity} if capacity else {}
    return ac.accrue(at.attribute(run, baseline or cu(10), [opt]), scope=scope)


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


# ---------- two inputs, verified first, never merged

def test_verified_and_estimated_are_separate_inputs_verified_first(world):
    out = shape(world)
    assert out["inputs"] == ["verified", "estimated"] and out["contract"] == pi.CONTRACT
    [cap] = out["capacities"]
    assert list(k for k in cap if k in pi.INPUTS) == ["verified", "estimated"]
    v, e = cap["verified"], cap["estimated"]
    assert v["optimization_ids"] == [world["o1"]] and e["optimization_ids"] == [world["o2"]]
    assert v["classification"] == ms.MEASURED and e["classification"] == ms.ESTIMATED
    assert v["demand_change_cu_hours"] == -8 and v["reduction_cu_hours"] == 8 and v["runs"] == 2
    assert e["demand_change_cu_hours"] == -2
    assert not any("total" in k or "combined" in k for k in cap)  # nothing holds their sum
    [wl] = cap["workloads"]
    assert wl["workload_key"] == WL and wl["verified"]["demand_change_cu_hours"] == -8


def test_only_measured_accruals_of_verified_optimizations_are_verified(world):
    world["acc"].append(events(world["o1"], cu(6, ms.ESTIMATED), "e4"))
    cap = shape(world)["capacities"][0]
    assert cap["verified"]["event_ids"] == sorted(e["ledger_event_id"] for a in world["acc"][:2] for e in a["events"])
    assert cap["estimated"]["optimization_ids"] == sorted([world["o1"], world["o2"]])


def test_a_validating_optimization_with_measured_runs_is_estimated(world):
    o1 = world["o1"]
    world["vals"][0] = validation(o1, [6, 7, 5], outputs_match=None)  # outputs not compared: Validating
    assert world["vals"][0]["result"] == bl.VALIDATING
    out = shape(world)
    cap = out["capacities"][0]
    assert o1 in cap["estimated"]["optimization_ids"] and cap["verified"]["events"] == 0
    assert cap["verified"]["demand_change_cu_hours"] is None and "No accrued runs" in cap["verified"]["reason"]
    [info] = [o for o in out["optimizations"] if o["optimization_id"] == o1]
    assert info["inputs"] == ["estimated"] and info["result"] == bl.VALIDATING


def test_a_not_validated_optimization_feeds_the_estimated_input(world):
    world["vals"] = []
    out = shape(world)
    assert out["capacities"][0]["verified"]["events"] == 0
    assert all(o["result"] == pi.NOT_VALIDATED and o["per_run_change_cu_hours"] is None for o in out["optimizations"])


# ---------- regressions and unknowns

def test_a_regression_is_an_increase_labelled_as_one(world):
    p3 = proposal("nb-4")
    o3 = p3["optimization_id"]
    world["opts"].append(p3)
    world["vals"].append(validation(o3, [12, 13, 12]))
    assert world["vals"][-1]["result"] == bl.REGRESSED
    world["acc"].append(events(o3, cu(12), "e5"))
    out = shape(world)
    e = out["capacities"][0]["estimated"]
    assert e["regressions"] == [o3] and e["increase_cu_hours"] == 2
    assert e["demand_change_cu_hours"] == 0.0  # +2 from the regression, -2 from o2
    [info] = [o for o in out["optimizations"] if o["optimization_id"] == o3]
    assert info["label"] == pi.INCREASE and info["per_run_change_cu_hours"] > 0
    assert out["capacities"][0]["verified"]["regressions"] == []


def test_unknown_runs_leave_the_change_unknown_never_zero_or_averaged(world):
    world["acc"].append(events(world["o2"], None, "e6"))
    e = shape(world)["capacities"][0]["estimated"]
    assert e["unknown_events"] == 1 and e["demand_change_cu_hours"] is None
    assert e["reduction_cu_hours"] is None and e["classification"] == ms.UNKNOWN
    assert "isn't known" in e["reason"] and "averaged" in e["reason"]


def test_an_unknown_validation_change_stays_unknown(world):
    o2 = world["o2"]
    world["vals"][1] = validation(o2, [8], kind=ms.MEASURED)  # too few after runs
    out = shape(world)
    [info] = [o for o in out["optimizations"] if o["optimization_id"] == o2]
    assert info["per_run_change_cu_hours"] is None and info["label"] == pi.UNKNOWN_CHANGE and info["reason"]


# ---------- right-sizing is the planner's job

def test_nothing_is_ever_sufficient_for_right_sizing(world):
    out = shape(world)
    places = [out, *out["capacities"], *(w for c in out["capacities"] for w in c["workloads"])]
    assert all(p["sufficient_for_right_sizing"] is False for p in places)
    assert all("peak" in p["right_sizing_reason"] and "concurrency" in p["right_sizing_reason"]
               and "throttling" in p["right_sizing_reason"] for p in places)
    text = repr(out).lower()
    assert "sku'" not in text and "recommend" not in str(list(out)) and out["written"] is False


# ---------- placement and exclusions

def test_capacity_comes_from_the_event_or_else_the_single_linked_capacity(world):
    world["acc"].append(events(world["o2"], cu(8, ms.ESTIMATED), "e7", capacity="cap-2"))
    world["acc"].append(events(world["o1"], cu(6), "e8", capacity=None))
    out = shape(world)
    assert [c["capacity_id"] for c in out["capacities"]] == ["cap-1", "cap-2"]
    assert out["capacities"][0]["verified"]["runs"] == 3  # e8 placed on o1's linked capacity


def test_an_event_with_no_capacity_is_unplaced_with_a_reason(world):
    p = proposal("nb-9", capacity=None)
    world["opts"].append(p)
    world["acc"].append(events(p["optimization_id"], cu(6), "e9", capacity=None))
    out = shape(world)
    [u] = out["unplaced"]
    assert u["optimization_id"] == p["optimization_id"] and "capacity" in u["reason"]


def test_local_layers_and_failed_optimizations_are_excluded_with_reasons(world):
    run = {"execution_id": "pc1", "workload_key": WL, "baseline_version": "b1", "at": "2026-11-10T00:00:00Z",
           "ran": at.PC}
    local = ms.make(ms.ESTIMATED, "x", ms.CU_HOURS, 7, method="calibration", assumptions=["a"])
    world["acc"].append(ac.accrue(at.attribute(run, cu(10), [], local_avoided=local), scope={"capacity_id": "cap-1"}))
    world["vals"][1] = validation(world["o2"], [8, 8, 9], kind=ms.ESTIMATED, outputs_match=False)
    out = shape(world)
    kinds = {(x["kind"], x["optimization_id"]) for x in out["excluded"]}
    assert ("event", None) in kinds and ("event", world["o2"]) in kinds and ("validation", world["o2"]) in kinds
    assert all(x["reason"].endswith(".") for x in out["excluded"])
    assert world["o2"] not in out["capacities"][0]["estimated"]["optimization_ids"]


def test_a_validated_optimization_without_runs_still_shows_its_per_run_change(world):
    world["acc"] = []
    out = shape(world)
    v = out["capacities"][0]["workloads"][0]["verified"]
    assert v["demand_change_cu_hours"] is None and v["per_run_changes"][0]["label"] == pi.REDUCTION
    assert v["per_run_changes"][0]["per_run_change_cu_hours"] < 0


# ---------- determinism and purity

def test_same_input_any_order_gives_the_same_output_and_changes_nothing(world):
    before = copy.deepcopy(world)
    a = shape(world)
    assert world == before
    b = pi.demand_inputs(list(reversed(world["vals"])), list(reversed(world["acc"])), list(reversed(world["opts"])))
    assert a == b


def test_the_same_event_twice_counts_once(world):
    world["acc"].append(copy.deepcopy(world["acc"][0]))
    assert shape(world)["capacities"][0]["verified"]["runs"] == 2


def test_the_latest_validation_wins(world):
    o1 = world["o1"]
    later = validation(o1, [12, 13, 12], at="2026-12-01T00:00:00Z")
    world["vals"].append(later)
    cap = shape(world)["capacities"][0]
    assert cap["verified"]["events"] == 0 and o1 in cap["estimated"]["regressions"]


# ---------- refusals in full sentences

def _bad_event(w, **change):
    e = copy.deepcopy(w["acc"][0]["events"][0]) | change
    return pi.demand_inputs(w["vals"], [e], w["opts"])


@pytest.mark.parametrize("change, match", [
    ({"ledger_event_id": None}, "no ledger_event_id"),
    ({"corrects": "abc"}, "is a correction"),
    ({"event_type": "reversal"}, "only accruals and regressions"),
    ({"classification": ms.SCENARIO}, "Measured, Estimated or Unknown"),
    ({"cu_hours": None}, "a known event has a figure"),
    ({"cu_hours": float("nan")}, "finite number"),
    ({"cu_hours": -1.0}, "A regression avoids less than 0"),
    ({"workload_key": ""}, "no workload_key"),
    ({"optimization_id": "opt-nope"}, "isn't among the optimization records"),
])
def test_bad_events_are_refused(world, change, match):
    with pytest.raises(ValueError, match=match):
        _bad_event(world, **change)


def test_two_figures_for_one_event_are_refused(world):
    e = copy.deepcopy(world["acc"][0]["events"][0])
    with pytest.raises(ValueError, match="appears twice with different figures"):
        pi.demand_inputs(world["vals"], world["acc"] + [e | {"cu_hours": 1.0, "value": 1.0}], world["opts"])


def test_bad_validations_are_refused(world):
    v = copy.deepcopy(world["vals"][0])
    with pytest.raises(ValueError, match="isn't among the optimization records"):
        pi.demand_inputs([v], [], world["opts"][1:])
    clash = copy.deepcopy(v)
    clash["result"] = clash["row"]["result"] = bl.VALIDATING
    with pytest.raises(ValueError, match="two validations at .* with different results"):
        pi.demand_inputs([v, clash], [], world["opts"])
    fake = copy.deepcopy(world["vals"][1])
    fake["result"] = fake["row"]["result"] = bl.VERIFIED
    with pytest.raises(ValueError, match="says Verified, but its change isn't"):
        pi.demand_inputs([fake], [], world["opts"])
    with pytest.raises(ValueError, match="must be a list"):
        pi.demand_inputs(None, [], world["opts"])  # type: ignore[arg-type]


def test_bad_optimizations_are_refused(world):
    with pytest.raises(ValueError, match="proposal from savings/links.py"):
        pi.demand_inputs([], [], [{"optimization_id": "x"}])
    with pytest.raises(ValueError, match="no optimization_id"):
        pi.demand_inputs([], [], [{"record": {}, "links": []}])
