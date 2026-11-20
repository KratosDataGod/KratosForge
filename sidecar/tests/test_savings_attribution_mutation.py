"""Mutation-testing kills for savings/attribution.py (docs/quality/mutation-testing.md, batch 93): the
waterfall's refusals, its tie and boundary cases, and the six-decimal rounding of every figure."""

from __future__ import annotations

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at


def cu(value, cls=ms.MEASURED):
    return {"value": value, "unit": ms.CU_HOURS, "classification": cls}


def run(ran=at.PC, when="2026-11-10T00:00:00Z", **kw):
    return {"execution_id": "e1", "workload_key": "nb:sales", "baseline_version": "b1", "at": when, "ran": ran, **kw}


def opt(oid, after, when="2026-11-05T00:00:00Z", **kw):
    return {"optimization_id": oid, "implemented_at": when, "action": "optimize_in_place", "state": "verified",
            "after": after, **kw}


def layer(out, name):
    return next(x for x in out["layers"] if x["layer"] == name)


# ---- what it refuses, each with its own words

def test_a_scenario_figure_is_refused_as_a_scenario():
    with pytest.raises(ValueError, match="scenario figure isn't evidence"):
        at.attribute(run(), cu(10, ms.SCENARIO))


def test_a_classification_it_doesnt_know_is_refused():
    with pytest.raises(ValueError, match="Unknown classification 'guess'"):
        at.attribute(run(), cu(10, "guess"))


def test_a_run_time_that_isnt_a_time_is_refused():
    with pytest.raises(ValueError, match="Not a time: 'soon'"):
        at.attribute(run(when="soon"), cu(10))


def test_a_run_that_ran_somewhere_else_is_refused():
    with pytest.raises(ValueError, match="ran is fabric or pc"):
        at.attribute(run(ran="cloud"), cu(10))


def test_a_local_basis_it_doesnt_know_is_refused():
    with pytest.raises(ValueError, match="local_basis is original or optimized"):
        at.attribute(run(), cu(10), local_basis="guess")


# ---- without a baseline

def test_a_fabric_run_without_a_baseline_has_no_local_layer():
    out = at.attribute(run(ran=at.FABRIC), None, [opt("o1", cu(6))])
    assert [x["layer"] for x in out["layers"]] == ["o1"]
    out = at.attribute(run(ran=at.PC), None, [opt("o1", cu(6))])
    assert [x["layer"] for x in out["layers"]] == ["o1", at.LOCAL]


# ---- ties and boundaries

def test_an_optimization_implemented_at_the_very_moment_of_the_run_applies():
    when = "2026-11-10T00:00:00Z"
    assert [o["optimization_id"] for o in at.in_effect([opt("o1", cu(6), when=when)], when)] == ["o1"]


def test_an_optimization_reverted_at_the_very_moment_of_the_run_doesnt_apply():
    when = "2026-11-10T00:00:00Z"
    assert at.in_effect([opt("o1", cu(6), reverted_at=when)], when) == []


def test_an_optimization_that_changed_nothing_is_an_accrual_of_zero_not_a_regression():
    out = at.attribute(run(ran=at.FABRIC), cu(10), [opt("o1", cu(10))])
    o1 = layer(out, "o1")
    assert (o1["event_type"], o1["cu_hours"], o1["reason"]) == (at.ACCRUAL, 0, "")


def test_a_saving_under_one_cu_hour_is_still_an_accrual():
    out = at.attribute(run(ran=at.FABRIC), cu(10), [opt("o1", cu(9.5))])
    o1 = layer(out, "o1")
    assert (o1["event_type"], o1["cu_hours"], o1["reason"]) == (at.ACCRUAL, 0.5, "")


def test_a_regression_under_one_cu_hour_is_a_regression():
    out = at.attribute(run(ran=at.FABRIC), cu(10), [opt("o1", cu(10.5))])
    o1 = layer(out, "o1")
    assert o1["event_type"] == at.REGRESSION and o1["reason"] == "Used more after this change than before it"


def test_this_pc_avoiding_exactly_what_remains_isnt_marked_capped():
    out = at.attribute(run(), cu(10), [opt("o1", cu(6))], local_avoided=cu(6, ms.ESTIMATED), overhead=cu(0))
    assert layer(out, at.LOCAL)["cu_hours"] == 6 and layer(out, at.LOCAL)["reason"] == ""
    out = at.attribute(run(), cu(10), [opt("o1", cu(6))], local_avoided=cu(7, ms.ESTIMATED), overhead=cu(0))
    assert layer(out, at.LOCAL)["reason"] == "Capped at what the optimized workload needs"


# ---- complete, and the rounding

def test_an_unknown_layer_leaves_the_result_incomplete_even_with_the_overhead_known():
    out = at.attribute(run(), cu(10), [opt("o1", cu(6))], local_avoided=None, overhead=cu(0.2))
    assert out["complete"] is False and out["net_includes_overhead"] is True


def test_every_figure_is_rounded_to_six_decimals():
    out = at.attribute(run(), cu(10), [opt("o1", cu(9.1234564))], local_avoided=cu(0.0000004, ms.ESTIMATED),
                       overhead=cu(0.1234564))
    assert layer(out, "o1")["cu_hours"] == 0.876544
    assert layer(out, at.LOCAL)["cu_hours"] == 0.0
    assert out["gross_cu_hours"] == 0.876544
    assert out["overhead_cu_hours"] == 0.123456
    assert out["net_cu_hours"] == 0.753088


def test_gross_capped_at_an_unrounded_baseline_is_rounded_too():
    # The layers round up to 0.123457, past the baseline, so gross is the baseline itself, rounded.
    out = at.attribute(run(), cu(0.1234567), [opt("o1", cu(0.0))], local_avoided=cu(5), overhead=cu(0))
    assert layer(out, "o1")["cu_hours"] == 0.123457
    assert out["gross_cu_hours"] == 0.123457 and out["net_cu_hours"] == 0.123457
