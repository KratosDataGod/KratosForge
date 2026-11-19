"""ESI-302: versioned baselines from comparable Fabric runs, and before and after validation that only
verifies on measured CU, equivalent inputs and matching outputs."""

from __future__ import annotations

import json
import math

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import baselines as bl

WL, DEF, SCOPE = "ws-1/nb-sales", "def-v1", "lh-1:sales/2026"
START, END = "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z"


def cu(v, kind=ms.MEASURED, run_id="r", unit=ms.CU_HOURS):
    if kind == ms.MEASURED:
        return ms.make(ms.MEASURED, "fabric_job_cu", unit, v, run_id=run_id, source="Capacity Metrics export",
                       collected_at="2026-11-01T00:00:00Z")
    if kind == ms.ESTIMATED:
        return ms.make(ms.ESTIMATED, "fabric_job_cu", unit, v, run_id=run_id, method="run time x configured CUs",
                       assumptions=["8 CUs configured"])
    if kind == ms.UNKNOWN:
        return ms.unknown("fabric_job_cu", unit, "not in the export", run_id=run_id)
    return ms.make(ms.SCENARIO, "fabric_job_cu", unit, v, run_id=run_id, method="as if", assumptions=["x"])


def fr(rid, v, day=10, kind=ms.MEASURED, month=10, **kw):
    r = {"run_id": rid, "workload_key": WL, "definition_version": DEF, "input_scope": SCOPE,
         "at": f"2026-{month:02d}-{day:02d}T09:00:00Z", "cu": cu(v, kind, rid), "ran": "fabric"}
    return r | kw


def base(runs=None, **kw):
    runs = runs if runs is not None else [fr("a", 10, 3), fr("b", 12, 10), fr("c", 9, 20)]
    args = {"definition_version": DEF, "input_scope": SCOPE, "window_start": START, "window_end": END} | kw
    return bl.baseline(WL, runs, **args)


def after(rid, v, day=5, kind=ms.MEASURED, **kw):
    return fr(rid, v, day, kind, month=11, **kw)


AFTERS = [after("x", 6, 3), after("y", 7, 4), after("z", 5, 5)]


# ---------- baselines ----------

def test_a_baseline_is_the_median_of_comparable_measured_runs():
    b = base()
    assert b["cu"]["value"] == pytest.approx(10.0) and b["cu"]["classification"] == ms.MEASURED
    assert b["statistic"] == "median" and b["row"]["runs"] == 3
    assert b["cu"]["unit"] == ms.CU_HOURS and b["cu"]["workload_id"] == WL
    assert json.loads(b["row"]["measurement_ids"]) == sorted(r["cu"]["id"] for r in
                                                             [fr("a", 10), fr("b", 12), fr("c", 9)])
    assert ms.problems(b["cu"]) == []


def test_cu_seconds_are_converted_and_one_outlier_does_not_move_the_median():
    runs = [fr("a", 36000, 3) | {"cu": cu(36000, run_id="a", unit=ms.CU_SECONDS)}, fr("b", 11, 4), fr("c", 500, 5)]
    assert base(runs)["cu"]["value"] == pytest.approx(11.0)


def test_ids_are_stable_and_the_same_baseline_on_two_pcs_is_one_version():
    a, b = base(), base(list(reversed([fr("a", 10, 3), fr("b", 12, 10), fr("c", 9, 20)])))
    assert a["baseline_id"] == b["baseline_id"] and a["baseline_version"] == b["baseline_version"]
    assert a["row"] == b["row"] and a["cu"]["id"] == b["cu"]["id"]
    # One baseline id per workload; a different figure is a different version.
    c = base([fr("a", 10, 3), fr("b", 13, 10), fr("c", 9, 20)])
    assert c["baseline_id"] == a["baseline_id"] and c["baseline_version"] != a["baseline_version"]


def test_the_same_run_read_twice_counts_once_but_two_figures_for_it_are_refused():
    b = base([fr("a", 10, 3), fr("a", 10, 3), fr("b", 12, 10), fr("c", 9, 20)])
    assert b["row"]["runs"] == 3
    with pytest.raises(ValueError, match="Run a appears twice"):
        base([fr("a", 10, 3), fr("a", 11, 3), fr("b", 12, 10), fr("c", 9, 20)])


def test_too_few_runs_give_unknown_with_the_reason_never_zero():
    b = base([fr("a", 10, 3), fr("b", 12, 10)])
    assert b["cu"]["classification"] == ms.UNKNOWN and b["cu"]["value"] is None
    assert "Only 2 usable Fabric runs; at least 3" in b["cu"]["reason"]
    assert b["row"]["runs"] == 2
    assert base([fr("a", 10, 3), fr("b", 12, 10)], min_runs=2)["cu"]["value"] == pytest.approx(11.0)
    with pytest.raises(ValueError, match="min_runs"):
        base(min_runs=0)


def test_empty_runs_and_missing_keys_are_refused():
    with pytest.raises(ValueError, match="at least one Fabric run"):
        base([])
    with pytest.raises(ValueError, match="definition version"):
        base(definition_version="  ")
    with pytest.raises(ValueError, match="input scope"):
        base(input_scope=None)
    with pytest.raises(ValueError, match="workload"):
        bl.baseline("", [fr("a", 1)], definition_version=DEF, input_scope=SCOPE, window_start=START, window_end=END)
    with pytest.raises(ValueError, match="end must be after"):
        base(window_start=END, window_end=START)
    with pytest.raises(ValueError, match="isn't a time"):
        base(window_start="soon")


@pytest.mark.parametrize("bad", [math.nan, math.inf, -1.0])
def test_nan_infinite_and_negative_figures_are_refused_naming_the_run(bad):
    r = fr("b", 1, 10)
    r["cu"] = dict(r["cu"], value=bad)  # past measurement.make, as a hand-built row could be
    with pytest.raises(ValueError, match="run b must be a finite CU figure"):
        base([fr("a", 10, 3), r, fr("c", 9, 20)])


def test_scenario_figures_are_refused():
    with pytest.raises(ValueError, match="Scenario figure"):
        base([fr("a", 10, 3), fr("b", 12, 10, kind=ms.SCENARIO), fr("c", 9, 20)])


def test_a_time_in_seconds_is_never_a_baseline():
    r = fr("b", 1, 10) | {"cu": ms.make(ms.MEASURED, "duration", ms.SECONDS, 600, run_id="b", source="job API",
                                        collected_at="2026-10-10")}
    with pytest.raises(ValueError, match="time in seconds, not CU"):
        base([fr("a", 10, 3), r, fr("c", 9, 20)])


def test_runs_that_dont_count_are_left_out_with_the_reason():
    runs = [fr("a", 10, 3), fr("b", 12, 10), fr("c", 9, 20),
            fr("d", 99, 11, definition_version="def-v0"),
            fr("e", 99, 12, input_scope="lh-1:sales/2025"),
            fr("f", 99, 13, workload_key="ws-1/nb-other"),
            fr("g", 99, 14, ran="pc"),
            fr("h", 99, 5, month=9),
            fr("i", 99, 1, month=11),  # the window's end is exclusive
            fr("j", 99, 15, input_scope=None),
            fr("k", 99, 16, kind=ms.UNKNOWN),
            fr("l", 99, 17, at=None)]
    b = base(runs)
    assert b["cu"]["value"] == pytest.approx(10.0) and b["row"]["runs"] == 3
    why = {x["run_id"]: x["reason"] for x in b["excluded"]}
    assert why == {"d": "A different definition version (def-v0 instead of def-v1).",
                   "e": "A different input scope (lh-1:sales/2025 instead of lh-1:sales/2026).",
                   "f": "A different workload (ws-1/nb-other instead of ws-1/nb-sales).",
                   "g": "It didn't run in Fabric.", "h": "It ran outside the window.", "i": "It ran outside the window.",
                   "j": "The input scope wasn't recorded, so it can't be matched.", "k": "Its CU isn't known.",
                   "l": "It has no run time, so it can't be placed in the window."}


def test_measured_runs_are_used_alone_when_there_are_enough():
    runs = [fr("a", 10, 3), fr("b", 12, 10), fr("c", 9, 20), fr("d", 50, 21, kind=ms.ESTIMATED)]
    b = base(runs)
    assert b["cu"]["classification"] == ms.MEASURED and b["cu"]["value"] == pytest.approx(10.0)
    assert {x["run_id"] for x in b["excluded"]} == {"d"}


def test_with_too_few_measured_runs_the_baseline_is_estimated():
    runs = [fr("a", 10, 3), fr("b", 12, 10, kind=ms.ESTIMATED), fr("c", 9, 20, kind=ms.ESTIMATED)]
    b = base(runs)
    assert b["cu"]["classification"] == ms.ESTIMATED and b["cu"]["value"] == pytest.approx(10.0)
    assert any("2 of the 3 runs' CU is an estimate" in a for a in b["cu"]["assumptions"])
    assert "8 CUs configured" in b["cu"]["assumptions"] and ms.problems(b["cu"]) == []


def test_comparability_needs_workload_definition_and_input_scope_never_time():
    a = {"workload_key": WL, "definition_version": DEF, "input_scope": SCOPE, "at": "2026-10-01T00:00:00Z"}
    assert bl.comparable(a, dict(a, at="2026-12-25T00:00:00Z")) == {"comparable": True, "reason": ""}
    for key in ("workload_key", "definition_version", "input_scope"):
        out = bl.comparable(a, dict(a, **{key: "other"}))
        assert out["comparable"] is False and "A different" in out["reason"]
        assert bl.comparable(a, dict(a, **{key: None}))["comparable"] is False
    # Two baselines compare the same way.
    x, y = base(), base(input_scope="lh-1:sales/2025", runs=[fr(r, 1, 3 + i, input_scope="lh-1:sales/2025")
                                                             for i, r in enumerate("abc")])
    assert bl.comparable(x, y)["reason"] == "A different input scope (lh-1:sales/2025 instead of lh-1:sales/2026)."


# ---------- versions (scenario 12) ----------

def test_scenario_12_a_definition_change_makes_a_new_version_that_supersedes_the_old():
    old = base()
    new_runs = [fr("a", 10, 3), fr("b", 12, 10), fr("c", 9, 20)] + [
        fr(r, 4 + i, 5 + i, month=11, definition_version="def-v2") for i, r in enumerate("pqr")]
    new = bl.baseline(WL, new_runs, definition_version="def-v2", input_scope=SCOPE, window_start=END,
                      window_end="2026-12-01T00:00:00Z", previous=old)
    # The old runs ran the old definition, so they don't count.
    assert new["cu"]["value"] == pytest.approx(5.0) and new["row"]["runs"] == 3
    assert {x["run_id"] for x in new["excluded"]} == {"a", "b", "c"}
    assert new["baseline_id"] == old["baseline_id"] and new["baseline_version"] != old["baseline_version"]
    # The old version isn't edited: a new row for it says what replaced it.
    assert old["row"]["superseded_by"] is None
    sup = new["supersedes"]
    assert sup["superseded_by"] == new["baseline_version"] and sup["baseline_version"] == old["baseline_version"]
    assert {k: v for k, v in sup.items() if k not in ("superseded_by", "at")} == {
        k: v for k, v in old["row"].items() if k not in ("superseded_by", "at")}
    assert sup["at"] == new["row"]["at"]
    rows = [old["row"], new["row"], sup]
    assert bl.current(rows, WL) == new["row"]
    assert bl.current([old["row"]], WL) == old["row"] and bl.current(rows, "ws-9/x") is None
    # Replacing an already replaced version, or another workload's, is refused.
    with pytest.raises(ValueError, match="already replaced"):
        base(min_runs=2, previous=sup)
    with pytest.raises(ValueError, match="same workload"):
        bl.baseline("ws-1/other", [fr("a", 1, workload_key="ws-1/other")], definition_version=DEF, input_scope=SCOPE,
                    window_start=START, window_end=END, previous=old)
    # The same version again replaces nothing.
    assert base(previous=old)["supersedes"] is None


def test_two_versions_in_effect_at_once_are_refused():
    a, b = base(), base(min_runs=2)
    with pytest.raises(ValueError, match="2 baseline versions in effect"):
        bl.current([a["row"], b["row"]], WL)


def test_runs_attributed_under_the_new_version_name_it():
    new = base()
    run = {"execution_id": "e1", "workload_key": WL, "baseline_version": new["baseline_version"],
           "at": "2026-11-02T00:00:00Z", "ran": at.FABRIC}
    out = at.attribute(run, new["cu"])
    assert out["baseline_version"] == new["baseline_version"] and out["baseline_cu_hours"] == pytest.approx(10.0)
    assert out["ledger_ready"] is True


# ---------- validation ----------

def test_measured_lower_after_with_matching_outputs_is_verified():
    v = bl.validate("opt-1", base(), AFTERS, outputs_match=True)
    assert v["result"] == bl.VERIFIED and v["moves_to"] == "verified"
    assert v["after"]["value"] == pytest.approx(6.0) and v["after"]["classification"] == ms.MEASURED
    c = v["change"]
    assert c["metric"] == bl.REDUCTION and c["value"] == pytest.approx(4.0) and c["classification"] == ms.MEASURED
    assert c["change_cu_hours"] == pytest.approx(-4.0) and c["direction"] == "reduction"
    assert v["row"]["before_measurement_id"] == base()["cu"]["id"] and v["row"]["after_measurement_id"] == v["after"]["id"]
    assert v["row"]["outputs_match"] is True and v["row"]["baseline_version"] == base()["baseline_version"]
    assert ms.problems(v["after"]) == [] and ms.problems({k: c[k] for k in c if k not in ("direction",
                                                                                         "change_cu_hours")}) == []


def test_outputs_not_measurable_verifies_only_when_stated():
    v = bl.validate("opt-1", base(), AFTERS)
    assert v["result"] == bl.VALIDATING and "weren't compared" in v["reason"]
    v = bl.validate("opt-1", base(), AFTERS, outputs_not_measurable="The notebook writes only to a log table.")
    assert v["result"] == bl.VERIFIED and v["row"]["outputs_match"] is None
    assert "writes only to a log table" in v["reason"]


def test_outputs_differ_is_failed():
    v = bl.validate("opt-1", base(), AFTERS, outputs_match=False)
    assert v["result"] == bl.FAILED and v["moves_to"] == "validating" and "outputs differ" in v["reason"]
    assert v["row"]["outputs_match"] is False


def test_a_higher_after_is_a_regression_back_to_validating():
    v = bl.validate("opt-1", base(), [after("x", 12, 3), after("y", 14, 4), after("z", 13, 5)], outputs_match=True)
    assert v["result"] == bl.REGRESSED and v["moves_to"] == "validating"
    c = v["change"]
    assert c["metric"] == bl.INCREASE and c["value"] == pytest.approx(3.0) and c["change_cu_hours"] == pytest.approx(3.0)


def test_no_change_is_verified_with_a_zero_reduction():
    v = bl.validate("opt-1", base(), [after("x", 10, 3), after("y", 10, 4), after("z", 10, 5)], outputs_match=True)
    assert v["result"] == bl.VERIFIED and v["change"]["value"] == 0 and v["change"]["direction"] == "none"


@pytest.mark.parametrize("which", ["before", "after"])
def test_estimated_inputs_never_verify(which):
    est = [fr(r, v, d, kind=ms.ESTIMATED) for r, v, d in (("a", 10, 3), ("b", 12, 10), ("c", 9, 20))]
    before = base(est) if which == "before" else base()
    afters = AFTERS if which == "before" else [after(r, v, d, kind=ms.ESTIMATED) for r, v, d in
                                               (("x", 6, 3), ("y", 7, 4), ("z", 5, 5))]
    v = bl.validate("opt-1", before, afters, outputs_match=True)
    assert v["result"] == bl.VALIDATING and "only an estimate" in v["reason"]
    assert v["change"]["classification"] == ms.ESTIMATED and v["change"]["value"] == pytest.approx(4.0)
    assert ms.problems({k: v["change"][k] for k in v["change"] if k not in ("direction", "change_cu_hours")}) == []


def test_too_few_after_runs_stay_validating():
    v = bl.validate("opt-1", base(), AFTERS[:2], outputs_match=True)
    assert v["result"] == bl.VALIDATING and "Only 2 usable after runs" in v["reason"]
    assert v["change"]["classification"] == ms.UNKNOWN and v["change"]["value"] is None


def test_an_unknown_before_stays_validating_never_zero():
    before = base([fr("a", 10, 3)])
    v = bl.validate("opt-1", before, AFTERS, outputs_match=True)
    assert v["result"] == bl.VALIDATING and "before baseline isn't known" in v["reason"]
    assert v["change"]["value"] is None and v["change"]["change_cu_hours"] is None


def test_after_runs_must_be_on_the_same_inputs_and_after_the_change():
    afters = AFTERS + [after("w", 1, 6, input_scope="lh-1:sales/sample"), after("v", 1, 7, workload_key="ws-1/x"),
                       after("u", 1, 8, definition_version="def-v9"), fr("t", 1, 25)]
    v = bl.validate("opt-1", base(), afters, outputs_match=True)
    why = {x["run_id"]: x["reason"] for x in v["excluded"]}
    assert why["w"].startswith("A different input scope") and why["v"].startswith("A different workload")
    assert why["u"].startswith("A different definition version") and why["t"] == "It ran before the change was in place."
    assert v["after"]["value"] == pytest.approx(6.0) and v["result"] == bl.VERIFIED
    # Only incomparable after runs: nothing is compared.
    only = bl.validate("opt-1", base(), [after("w", 1, d, input_scope="sample") for d in (3, 4, 5)], outputs_match=True)
    assert only["result"] == bl.VALIDATING and only["change"]["value"] is None


def test_an_optimization_that_changes_the_definition_compares_its_new_version():
    afters = [after(r, 6, d, definition_version="def-v2") for r, d in (("x", 3), ("y", 4), ("z", 5))]
    assert bl.validate("opt-1", base(), afters, outputs_match=True)["result"] == bl.VALIDATING
    v = bl.validate("opt-1", base(), afters, outputs_match=True, after_definition_version="def-v2")
    assert v["result"] == bl.VERIFIED and v["definition_version"] == "def-v2"


def test_implemented_at_and_before_runs_are_respected():
    v = bl.validate("opt-1", base(), AFTERS + [after("q", 1, 2)], outputs_match=True,
                    implemented_at="2026-11-03T00:00:00Z")
    assert {x["run_id"]: x["reason"] for x in v["excluded"]} == {"q": "It ran before the change was in place."}
    # A run in the before baseline can't also be an after run.
    v = bl.validate("opt-1", base(), AFTERS + [fr("a", 10, 3)], outputs_match=True, implemented_at=START)
    assert {x["run_id"] for x in v["excluded"]} == {"a"}


def test_a_time_only_after_is_refused_as_a_cu_change():
    secs = [after(r, 1, d) | {"cu": ms.make(ms.MEASURED, "duration", ms.SECONDS, 60, run_id=r, source="job API",
                                            collected_at="2026-11-05")} for r, d in (("x", 3), ("y", 4), ("z", 5))]
    with pytest.raises(ValueError, match="time in seconds, not CU"):
        bl.validate("opt-1", base(), secs, outputs_match=True)
    with pytest.raises(ValueError, match="time in seconds"):
        bl.change(base()["cu"], ms.make(ms.MEASURED, "duration", ms.SECONDS, 60, source="job API",
                                        collected_at="2026-11-05"))
    with pytest.raises(ValueError, match="CU seconds or CU hours"):
        bl.change(base()["cu"], ms.make(ms.MEASURED, "share", ms.PERCENT, 5, source="x", collected_at="2026-11-05"))


def test_validation_inputs_are_checked():
    with pytest.raises(ValueError, match="optimization_id"):
        bl.validate(" ", base(), AFTERS)
    with pytest.raises(ValueError, match="outputs_match"):
        bl.validate("opt-1", base(), AFTERS, outputs_match="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one after run"):
        bl.validate("opt-1", base(), [])
    with pytest.raises(ValueError, match="build it with baselines.baseline"):
        bl.validate("opt-1", {"cu": base()["cu"]}, AFTERS)
    with pytest.raises(ValueError, match="Scenario"):
        bl.validate("opt-1", base(), [after("x", 1, kind=ms.SCENARIO)])


def test_validation_ids_are_stable_and_change_with_the_evidence():
    a = bl.validate("opt-1", base(), AFTERS, outputs_match=True)
    b = bl.validate("opt-1", base(), list(reversed(AFTERS)), outputs_match=True)
    assert a["row"] == b["row"]
    c = bl.validate("opt-1", base(), AFTERS + [after("w", 6, 6)], outputs_match=True)
    assert c["row"]["validation_id"] != a["row"]["validation_id"]
    assert c["row"]["after_measurement_id"] != a["row"]["after_measurement_id"]
    assert bl.validate("opt-1", base(), AFTERS, outputs_match=False)["row"]["validation_id"] != a["row"]["validation_id"]


def test_the_rows_match_the_lakehouse_tables():
    from fabric_desktop.optimize import schema
    old = base()
    new = base(min_runs=2, previous=old)
    assert set(old["row"]) == set(schema.TABLES_V2["baseline"])
    assert set(new["supersedes"]) == set(schema.TABLES_V2["baseline"])
    v = bl.validate("opt-1", old, AFTERS, outputs_match=True)
    assert set(v["row"]) == set(schema.TABLES_V2["validation"])
    assert v["row"]["result"] in bl.RESULTS
