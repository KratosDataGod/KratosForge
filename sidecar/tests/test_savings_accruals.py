"""Execution ledger accruals (ESI-203): per-run events, the measured-baseline gate, and nothing written."""

from __future__ import annotations

import random

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.optimize import schema
from fabric_desktop.savings import accruals as ac
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import corrections as co


def measured(cu_hours: float, unit: str = ms.CU_HOURS) -> dict:
    return ms.make(ms.MEASURED, "fabric_job_cu", unit, cu_hours, source="Capacity Metrics export",
                   collected_at="2026-11-01T00:00:00Z")


def estimated(cu_hours: float) -> dict:
    return ms.make(ms.ESTIMATED, "fabric_job_cu_avoided", ms.CU_HOURS, cu_hours, method="calibration",
                   assumptions=["median of 5 Fabric runs"])


def unknown() -> dict:
    return ms.unknown("fabric_job_cu", ms.CU_HOURS, "not measured yet")


def run(ran: str = at.PC, eid: str = "e1", when: str = "2026-11-10T00:00:00Z") -> dict:
    return {"execution_id": eid, "workload_key": "nb:sales", "baseline_version": "b1", "at": when, "ran": ran}


def opt(oid: str, after: dict | None, when: str = "2026-11-05T00:00:00Z") -> dict:
    return {"optimization_id": oid, "implemented_at": when, "action": "optimize_in_place", "state": "verified",
            "after": after}


def pc_run(baseline: dict | None = None, local: dict | None = None, overhead: dict | None = None, **kw) -> dict:
    return at.attribute(run(**kw), measured(10) if baseline is None else baseline, [],
                        local_avoided=estimated(7) if local is None else local, overhead=overhead)


# ---------- the shape: fdopt_savings v2 columns

def test_a_pc_run_is_one_local_accrual_shaped_as_the_ledger_row():
    a = ac.accrue(pc_run(overhead=measured(0.5)), scope={"workspace_id": "w1", "capacity_id": "c1",
                                                          "artifact_key": "w1/nb1"})
    [e] = a["events"]
    assert set(ac.COLUMNS) <= set(e)  # every fdopt_savings v2 column
    assert set(e) - set(ac.COLUMNS) == set(ac.NO_COLUMN)
    assert set(ac.COLUMNS) == set(schema.expected(2)["fdopt_savings"])
    assert e["event_type"] == at.ACCRUAL and e["layer"] == at.LOCAL and e["attribution"] == ac.LOCAL_EXECUTION
    assert e["value"] == e["cu_hours"] == 7 and e["classification"] == ms.ESTIMATED
    assert e["category"] == "estimated_cu" and e["unit"] == ms.CU_HOURS and e["metric"] == at.METRIC
    assert e["record_id"] == e["savings_id"] == e["ledger_event_id"]
    assert e["corrects"] is None and e["money"] is None and e["currency"] is None
    assert e["period_start"] == e["period_end"] == "2026-11-10T00:00:00+00:00"  # one execution, one time
    assert (e["workspace_id"], e["capacity_id"], e["artifact_key"]) == ("w1", "c1", "w1/nb1")
    assert all(e[k] is None for k in ac.PROVENANCE_FROM_WRITER)  # filled by a writer, and nothing here writes
    assert (a["gross_cu_hours"], a["overhead_cu_hours"], a["net_cu_hours"]) == (7, 0.5, 6.5)
    assert a["net_complete"] and a["writable"] and a["ledger_ready"] and a["reason"] == ""
    assert a["written"] is False


def test_optimization_regression_and_unattributed_layers_keep_their_kinds():
    a = ac.accrue(at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6)),
                                                               opt("o2", measured(7), "2026-11-06T00:00:00Z")]))
    by = {e["layer"]: e for e in a["events"]}
    assert by["o1"]["attribution"] == ac.OPTIMIZATION and by["o1"]["optimization_id"] == "o1"
    assert by["o2"]["event_type"] == at.REGRESSION and by["o2"]["cu_hours"] == -1
    assert [e["layer"] for e in a["events"]] == ["o1", "o2"]  # waterfall order

    b = ac.accrue(at.attribute(run(), measured(10), [opt("o1", None)], local_avoided=estimated(9),
                               local_basis=at.ORIGINAL))
    by = {e["layer"]: e for e in b["events"]}
    assert by["o1"]["cu_hours"] is None and by["o1"]["classification"] == ms.UNKNOWN  # never 0
    assert by["o1"]["category"] is None and by["o1"]["value"] is None
    assert by[at.UNATTRIBUTED]["attribution"] == at.UNATTRIBUTED
    assert by[at.UNATTRIBUTED]["optimization_ids"] == ["o1"] and by[at.UNATTRIBUTED]["cu_hours"] == 9


# ---------- acceptance

def test_net_is_never_more_than_gross():
    a = ac.accrue(pc_run(overhead=measured(20)))
    assert a["net_cu_hours"] == 0 <= a["gross_cu_hours"] == 7


def test_unknown_overhead_marks_net_incomplete():
    a = ac.accrue(pc_run(overhead=None))
    assert a["overhead_cu_hours"] is None
    assert a["net_cu_hours"] == a["gross_cu_hours"] == 7  # shown as gross
    assert not a["net_complete"] and not a["net_includes_overhead"]


def test_resync_adds_nothing():
    first = ac.accrue(pc_run(overhead=measured(0.5)))
    again = ac.accrue(pc_run(overhead=measured(0.5)))  # a second PC syncing the same run
    assert first["events"] == again["events"]
    ledger = ac.unsynced(first["events"], [])
    assert ledger == first["events"]
    assert ac.unsynced(again["events"], ledger) == []
    assert ac.unsynced(first["events"] + again["events"], []) == first["events"]  # twice in one batch


def test_a_changed_figure_on_resync_is_a_correction_not_a_second_accrual():
    ledger = ac.accrue(pc_run())["events"]
    later = pc_run(local=estimated(6))
    with pytest.raises(ValueError, match="Run e1 already has layer local.*correction"):
        ac.unsynced(ac.accrue(later)["events"], ledger)
    fix = co.correct(ledger, later)
    assert fix[0]["corrects"] == ledger[0]["ledger_event_id"]  # the correction chains to the accrual
    assert co.reconcile(ledger + fix, [later])["reconciled"]


def test_different_runs_and_pricing_versions_get_different_ids():
    a = ac.accrue(pc_run(eid="e1"))["events"][0]["ledger_event_id"]
    b = ac.accrue(pc_run(eid="e2"))["events"][0]["ledger_event_id"]
    c = ac.accrue(at.attribute(run(), measured(10), [], local_avoided=estimated(7), pricing_version="p2"),
                  pricing_version="p2")["events"][0]
    assert len({a, b, c["ledger_event_id"]}) == 3 and c["pricing_version"] == "p2"


# ---------- the gate: nothing writable without a measured baseline

def test_an_estimated_baseline_is_shown_but_not_writable():
    a = ac.accrue(pc_run(baseline=estimated(10), overhead=measured(0.5)))
    assert not a["writable"] and not a["ledger_ready"] and a["written"] is False
    assert a["reason"].startswith("The baseline for workload nb:sales is Estimated, not measured.")
    assert a["events"] and a["gross_cu_hours"] == 7  # still computed and shown


def test_an_unknown_baseline_has_unknown_events_and_is_not_writable():
    a = ac.accrue(pc_run(baseline=unknown()))
    assert not a["writable"] and "Unknown" in a["reason"]
    assert [(e["cu_hours"], e["classification"]) for e in a["events"]] == [(None, ms.UNKNOWN)]
    assert a["gross_cu_hours"] is None and a["net_cu_hours"] is None


def test_a_result_that_claims_ready_with_an_estimated_baseline_is_refused():
    r = pc_run(baseline=estimated(10)) | {"ledger_ready": True}
    with pytest.raises(ValueError, match="Run e1 can't be accrued: it says ledger_ready is True but its baseline "
                                         "is estimated"):
        ac.accrue(r)


# ---------- bad input, refused in full sentences naming the run

def _layer0(r: dict, **kw) -> dict:
    return r | {"layers": [r["layers"][0] | kw] + r["layers"][1:]}


@pytest.mark.parametrize("change, match", [
    (lambda r: r | {"gross_cu_hours": 11.0}, "gross \\(11 CU-hours\\) is outside 0 to its baseline"),
    (lambda r: r | {"net_cu_hours": 8.0}, "net \\(8 CU-hours\\) is more than its gross"),
    (lambda r: r | {"overhead_cu_hours": -1.0}, "overhead .* is negative"),
    (lambda r: r | {"net_cu_hours": float("nan")}, "net must be a finite number"),
    (lambda r: r | {"complete": True, "overhead_cu_hours": None}, "complete with Unknown overhead"),
    (lambda r: r | {"at": "not a time"}, "isn't a time it ran"),
    (lambda r: r | {"workload_key": None}, "has no workload_key"),
    (lambda r: _layer0(r, cu_hours=12.0), "layers add up to 12 CU-hours, more than its baseline"),
    (lambda r: _layer0(r, classification=ms.SCENARIO), "scenario figure"),
    (lambda r: _layer0(r, cu_hours=0.0, classification=ms.UNKNOWN), "Unknown but has a figure"),
    (lambda r: _layer0(r, execution_id="e9"), "layer local is for run e9"),
    (lambda r: _layer0(r, ledger_event_id="x"), "isn't the one this run gives"),
    (lambda r: _layer0(r, event_type=co.CORRECTION, corrects="y", result_cu_hours=7.0,
                       result_classification=ms.ESTIMATED, layer_removed=False), "is a correction"),
    (lambda r: r | {"layers": r["layers"] * 2}, "has layer local twice"),
])
def test_bad_input_is_refused_naming_the_run(change, match):
    with pytest.raises(ValueError, match="^Run e1 can't be accrued: .*" + match):
        ac.accrue(change(pc_run(overhead=measured(0.5))))


def test_a_result_attributed_with_another_pricing_version_or_as_a_correction_is_refused():
    with pytest.raises(ValueError, match="another pricing version"):
        ac.accrue(pc_run(), pricing_version="p2")
    with pytest.raises(ValueError, match="another pricing version or as a correction"):
        ac.accrue(at.attribute(run(), measured(10), [], local_avoided=estimated(7), corrects="abc"))


def test_an_unknown_scope_key_is_refused():
    with pytest.raises(ValueError, match="Run e1 can't be accrued: the scope has tenant_id"):
        ac.accrue(pc_run(), scope={"tenant_id": "t"})


# ---------- property: accruals reconcile with the attribution they came from

def _random_attribution(rnd: random.Random, eid: str) -> dict:
    def fig(lo: float, hi: float) -> dict | None:
        pick = rnd.random()
        if pick < 0.2:
            return None
        return (measured if pick < 0.6 else estimated)(round(rnd.uniform(lo, hi), 3))

    base = rnd.choice([measured(round(rnd.uniform(1, 20), 3)), estimated(round(rnd.uniform(1, 20), 3)), unknown(),
                       measured(round(rnd.uniform(3600, 72000), 1), ms.CU_SECONDS)])
    opts = [opt(f"o{i}", fig(0, 25), when=f"2026-11-0{i + 1}T00:00:00Z") for i in range(rnd.randint(0, 3))]
    over = rnd.choice([None, measured(round(rnd.uniform(0, 3), 3)), estimated(round(rnd.uniform(0, 3), 3))])
    return at.attribute(run(rnd.choice([at.PC, at.FABRIC]), eid=eid), base, opts, local_avoided=fig(0, 30) or unknown(),
                        local_basis=rnd.choice([at.ORIGINAL, at.OPTIMIZED]), overhead=over)


def test_accruals_reconcile_with_the_attribution_for_any_run():
    rnd = random.Random(203)
    results = [_random_attribution(rnd, f"e{i}") for i in range(400)]
    accrued = [ac.accrue(r) for r in results]
    ledger: list[dict] = []
    for a in accrued:
        ledger += ac.unsynced(a["events"], ledger)
    for r in results:  # a second PC syncs everything again
        assert ac.unsynced(ac.accrue(r)["events"], ledger) == []
    report = co.reconcile(ledger, results)
    assert report["reconciled"], report["problems"] or [r for r in report["runs"] if r["status"] != "matched"]
    for r, a in zip(results, accrued, strict=True):
        assert a["writable"] == (r["baseline_classification"] == ms.MEASURED)
        assert len(a["events"]) == len(r["layers"])
        if a["gross_cu_hours"] is not None:
            assert 0 <= a["net_cu_hours"] <= a["gross_cu_hours"] <= a["baseline_cu_hours"]
        if a["overhead_cu_hours"] is None and a["events"]:
            assert not a["net_complete"]
        for e in a["events"]:
            assert (e["cu_hours"] is None) == (e["classification"] == ms.UNKNOWN)
            assert e["classification"] in (ms.MEASURED, ms.ESTIMATED, ms.UNKNOWN)
    assert len({e["ledger_event_id"] for e in ledger}) == len(ledger) == sum(len(r["layers"]) for r in results)
