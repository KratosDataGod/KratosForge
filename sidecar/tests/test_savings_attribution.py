"""The attribution waterfall (ESI-303): optimization and running on a PC never add up past the baseline."""

from __future__ import annotations

import itertools
import random

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at


def measured(cu_hours: float, run_id: str = "r") -> dict:
    return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, cu_hours, run_id=run_id,
                   source="Capacity Metrics export", collected_at="2026-11-01T00:00:00Z")


def estimated(cu_hours: float) -> dict:
    return ms.make(ms.ESTIMATED, "fabric_job_cu_avoided", ms.CU_HOURS, cu_hours, method="calibration",
                   assumptions=["median of 5 Fabric runs"])


def run(ran: str = at.PC, eid: str = "e1", when: str = "2026-11-10T00:00:00Z", **kw) -> dict:
    return {"execution_id": eid, "workload_key": "nb:sales", "baseline_version": "b1", "at": when, "ran": ran, **kw}


def opt(oid: str, after: dict | None, when: str = "2026-11-05T00:00:00Z", action: str = "optimize_in_place", **kw):
    return {"optimization_id": oid, "implemented_at": when, "action": action, "state": "verified", "after": after, **kw}


def layer(result: dict, name: str) -> dict:
    return next(x for x in result["layers"] if x["layer"] == name)


# ---------- the critical acceptance test (brief section 16)

def test_optimized_then_local_never_exceeds_the_baseline():
    out = at.attribute(run(), measured(10), [opt("o1", measured(6))], local_avoided=estimated(10),
                       local_basis=at.ORIGINAL, overhead=measured(0.2))
    assert layer(out, "o1")["cu_hours"] == 4 and layer(out, "o1")["classification"] == ms.MEASURED
    assert layer(out, at.LOCAL)["cu_hours"] == 6  # capped at the optimized demand, not 10
    assert out["gross_cu_hours"] == 10 and out["gross_cu_hours"] <= out["baseline_cu_hours"]
    assert out["net_cu_hours"] == pytest.approx(9.8)
    assert out["complete"] and out["ledger_ready"]


def test_a_second_desktop_syncing_the_same_run_adds_nothing():
    a = at.attribute(run(), measured(10), [opt("o1", measured(6))], local_avoided=estimated(6))
    b = at.attribute(run(), measured(10), [opt("o1", measured(6))], local_avoided=estimated(6))
    assert [x["ledger_event_id"] for x in a["layers"]] == [x["ledger_event_id"] for x in b["layers"]]
    # Rows keyed by event id: the union is the same set, so totals don't grow.
    rows = {x["ledger_event_id"]: x for x in a["layers"] + b["layers"]}
    assert sum(x["cu_hours"] for x in rows.values()) == 10


def test_a_later_assessment_of_the_same_run_recreates_no_savings():
    first = at.attribute(run(), measured(10), [opt("o1", measured(6))])
    again = at.attribute(run(), measured(10), [opt("o1", measured(6))])
    assert {x["ledger_event_id"] for x in again["layers"]} <= {x["ledger_event_id"] for x in first["layers"]}


def test_different_runs_corrections_and_pricing_versions_are_separate_events():
    base = at.attribute(run(), measured(10), [opt("o1", measured(6))])["layers"][0]["ledger_event_id"]
    other_run = at.attribute(run(eid="e2"), measured(10), [opt("o1", measured(6))])["layers"][0]["ledger_event_id"]
    corr1 = at.attribute(run(), measured(10), [opt("o1", measured(6))], corrects="x1")["layers"][0]["ledger_event_id"]
    corr2 = at.attribute(run(), measured(10), [opt("o1", measured(6))], corrects="x2")["layers"][0]["ledger_event_id"]
    priced = at.attribute(run(), measured(10), [opt("o1", measured(6))], pricing_version="p2")["layers"][0][
        "ledger_event_id"]
    assert len({base, other_run, corr1, corr2, priced}) == 5


# ---------- layers

def test_several_optimizations_take_their_share_in_order():
    opts = [opt("late", measured(3), when="2026-11-06T00:00:00Z"), opt("early", measured(7))]
    out = at.attribute(run(at.FABRIC), measured(10), opts)
    assert [(x["layer"], x["cu_hours"]) for x in out["layers"]] == [("early", 3), ("late", 4)]
    assert out["gross_cu_hours"] == 7 and out["net_cu_hours"] == 7


def test_same_day_optimizations_follow_the_action_order():
    opts = [opt("b", measured(5), action="reschedule"), opt("a", measured(8), action="eliminate")]
    assert at.attribute(run(at.FABRIC), measured(10), opts)["optimizations"] == ["a", "b"]


def test_a_run_in_fabric_has_no_pc_layer():
    out = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))], local_avoided=estimated(6))
    assert [x["layer"] for x in out["layers"]] == ["o1"] and out["gross_cu_hours"] == 4


def test_only_optimizations_in_effect_at_the_run_count():
    opts = [opt("future", measured(2), when="2026-11-20T00:00:00Z"),
            opt("reverted", measured(5), reverted_at="2026-11-08T00:00:00Z", state="reverted"),
            opt("rejected", measured(5), state="rejected"),
            opt("kept", measured(7))]
    assert at.attribute(run(at.FABRIC), measured(10), opts)["optimizations"] == ["kept"]
    # Before the revert, the reverted one did apply.
    early = at.attribute(run(at.FABRIC, when="2026-11-07T00:00:00Z"), measured(10), opts)
    assert "reverted" in early["optimizations"]


def test_a_regression_is_negative_and_gross_stays_within_bounds():
    out = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(12))])
    assert layer(out, "o1")["cu_hours"] == -2 and layer(out, "o1")["event_type"] == at.REGRESSION
    assert out["gross_cu_hours"] == 0
    pc = at.attribute(run(), measured(10), [opt("o1", measured(12))], local_avoided=estimated(12))
    assert pc["gross_cu_hours"] == 10


def test_an_unknown_after_keeps_the_total_and_defers_the_split():
    opts = [opt("o1", measured(8)), opt("o2", None, when="2026-11-06T00:00:00Z"),
            opt("o3", measured(4), when="2026-11-07T00:00:00Z")]
    out = at.attribute(run(), measured(10), opts, local_avoided=estimated(9), local_basis=at.ORIGINAL)
    assert layer(out, "o1")["cu_hours"] == 2
    assert layer(out, "o2")["classification"] == ms.UNKNOWN and layer(out, "o3")["classification"] == ms.UNKNOWN
    un = layer(out, at.UNATTRIBUTED)
    assert un["cu_hours"] == 8 and un["optimization_ids"] == ["o2", "o3"]  # capped at B minus what's split
    assert out["gross_cu_hours"] == 10 and not out["complete"]


def test_no_comparable_fabric_run_leaves_the_pc_layer_unknown_not_zero():
    out = at.attribute(run(), measured(10), local_avoided=ms.unknown("fabric_job_cu_avoided", ms.CU_SECONDS,
                                                                       "no Fabric run of this workload"))
    assert layer(out, at.LOCAL)["cu_hours"] is None and layer(out, at.LOCAL)["classification"] == ms.UNKNOWN
    assert out["gross_cu_hours"] is None and out["net_cu_hours"] is None and not out["complete"]


def test_no_baseline_makes_everything_unknown():
    out = at.attribute(run(), ms.unknown("fabric_job_cu", ms.CU_HOURS, "never ran in Fabric"),
                       [opt("o1", measured(6))], local_avoided=estimated(6))
    assert all(x["classification"] == ms.UNKNOWN for x in out["layers"])
    assert out["gross_cu_hours"] is None and not out["ledger_ready"]


def test_unknown_overhead_marks_net_incomplete():
    out = at.attribute(run(), measured(10), local_avoided=estimated(10))
    assert out["net_cu_hours"] == 10 and not out["net_includes_overhead"] and not out["complete"]


def test_classification_is_as_weak_as_the_inputs():
    out = at.attribute(run(), measured(10), [opt("o1", estimated(6))], local_avoided=estimated(6),
                       overhead=measured(0))
    assert layer(out, "o1")["classification"] == ms.ESTIMATED
    assert out["gross_classification"] == ms.ESTIMATED and out["net_classification"] == ms.ESTIMATED


def test_an_estimated_baseline_is_not_ready_for_the_ledger():
    assert not at.attribute(run(at.FABRIC), estimated(10), [opt("o1", measured(6))])["ledger_ready"]


def test_units_convert():
    out = at.attribute(run(at.FABRIC), ms.to_unit(measured(10), ms.CU_SECONDS), [opt("o1", measured(6))])
    assert layer(out, "o1")["cu_hours"] == 4


def test_scenario_figures_are_refused():
    scen = ms.make(ms.SCENARIO, "fabric_job_cu", ms.CU_HOURS, 10, method="time x CU", assumptions=["python 1 CU"])
    with pytest.raises(ValueError, match="scenario"):
        at.attribute(run(), scen)


@pytest.mark.parametrize("missing", ["execution_id", "workload_key", "baseline_version", "at", "ran"])
def test_a_run_needs_its_ids(missing):
    with pytest.raises(ValueError, match=missing):
        at.attribute({k: v for k, v in run().items() if k != missing}, measured(10))


def test_gross_never_exceeds_the_baseline_for_any_order_or_values():
    rnd = random.Random(7)
    for _ in range(300):
        b = rnd.uniform(0.1, 20)
        afters = [rnd.choice([None, rnd.uniform(0, 25)]) for _ in range(rnd.randint(0, 4))]
        opts = [opt(f"o{i}", None if a is None else measured(a), when=f"2026-11-0{i + 1}T00:00:00Z")
                for i, a in enumerate(afters)]
        for perm in itertools.islice(itertools.permutations(opts), 6):
            for ran in (at.PC, at.FABRIC):
                out = at.attribute(run(ran), measured(b), list(perm), local_avoided=estimated(rnd.uniform(0, 30)),
                                   overhead=measured(rnd.uniform(0, 1)))
                if out["gross_cu_hours"] is not None:
                    assert 0 <= out["gross_cu_hours"] <= out["baseline_cu_hours"] + 1e-9
                    assert 0 <= out["net_cu_hours"] <= out["gross_cu_hours"] + 1e-9


@pytest.mark.parametrize("bad", [-5, float("nan"), float("inf"), float("-inf"), "nan", "inf"])
def test_negative_and_non_finite_figures_are_refused(bad):
    def fig(make):
        m = make(1)
        m["value"] = bad
        return m

    with pytest.raises(ValueError, match="after of o1"):
        at.attribute(run(), measured(10), [opt("o1", fig(measured))])
    with pytest.raises(ValueError, match="baseline"):
        at.attribute(run(), fig(measured))
    with pytest.raises(ValueError, match="local avoided"):
        at.attribute(run(), measured(10), local_avoided=fig(estimated))
    with pytest.raises(ValueError, match="overhead"):
        at.attribute(run(), measured(10), local_avoided=estimated(3), overhead=fig(measured))


def test_unattributed_is_capped_at_the_baseline_after_a_regression():
    opts = [opt("o1", measured(11)), opt("o2", None, when="2026-11-06T00:00:00Z")]
    out = at.attribute(run(), measured(10), opts, local_avoided=estimated(30))
    assert layer(out, "o1")["cu_hours"] == -1
    assert layer(out, at.UNATTRIBUTED)["cu_hours"] == 10
    assert out["gross_cu_hours"] <= out["baseline_cu_hours"]


def test_a_baseline_in_cu_seconds_doesnt_round_gross_or_net_above_it():
    base = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, 1.9, run_id="r", source="Capacity Metrics export",
                   collected_at="2026-11-01T00:00:00Z")
    local = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, 1.9, method="calibration", assumptions=["median"])
    out = at.attribute(run(), base, local_avoided=local, overhead=measured(0))
    assert out["baseline_cu_hours"] < 0.000528  # 1.9 CU seconds is 0.000527777... CU hours
    assert out["net_cu_hours"] <= out["gross_cu_hours"] <= out["baseline_cu_hours"]
    assert out["gross_cu_hours"] == 0.000527
