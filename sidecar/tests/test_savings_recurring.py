"""ESI-304: period figures group per-run accruals, and the monthly benefit is a projection that is
always Estimated and never written."""

from __future__ import annotations

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import recurring as rc

NOW = "2026-11-30T00:00:00Z"


def measured(v: float) -> dict:
    return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, v, run_id="r", source="Capacity Metrics export",
                   collected_at="2026-11-01T00:00:00Z")


def estimated(v: float) -> dict:
    return ms.make(ms.ESTIMATED, "fabric_job_cu_avoided", ms.CU_HOURS, v, method="calibration",
                   assumptions=["median of 5 Fabric runs"])


def result(eid: str, day: int, *, avoided: float | None = 6, overhead: float | None = 1, baseline: str = "b1",
           workload: str = "nb:sales", month: int = 11) -> dict:
    run = {"execution_id": eid, "workload_key": workload, "baseline_version": baseline,
           "at": f"2026-{month:02d}-{day:02d}T09:00:00Z", "ran": at.PC}
    local = estimated(avoided) if avoided is not None else ms.unknown("fabric_job_cu_avoided", ms.CU_HOURS, "none")
    return at.attribute(run, measured(10), local_avoided=local,
                        overhead=measured(overhead) if overhead is not None else None)


# ---------- periods

def test_periods_group_runs_and_a_run_from_two_devices_counts_once():
    runs = [result("e1", 3), result("e2", 4), result("e1", 3), result("e3", 2, month=12)]
    months = rc.by_period(runs, "month")
    assert [(m["period"], m["runs"], m["gross_cu_hours"], m["net_cu_hours"]) for m in months] == [
        ("2026-11", 2, 12, 10), ("2026-12", 1, 6, 5)]
    assert months[0]["gross_classification"] == ms.ESTIMATED and months[0]["complete"]


def test_days_and_weeks():
    runs = [result("e1", 2), result("e2", 3), result("e3", 9)]
    assert [d["period"] for d in rc.by_period(runs, "day")] == ["2026-11-02", "2026-11-03", "2026-11-09"]
    assert [(w["period"], w["runs"]) for w in rc.by_period(runs, "week")] == [("2026-W45", 2), ("2026-W46", 1)]


def test_unknown_runs_are_counted_beside_not_as_zero():
    [m] = rc.by_period([result("e1", 3), result("e2", 4, avoided=None)])
    assert m["runs"] == 2 and m["unknown_runs"] == 1 and m["gross_cu_hours"] == 6 and not m["complete"]


def test_unknown_overhead_leaves_the_period_incomplete():
    [m] = rc.by_period([result("e1", 3), result("e2", 4, overhead=None)])
    assert m["net_cu_hours"] == 11 and not m["complete"]


def test_two_different_figures_for_one_run_are_refused():
    with pytest.raises(ValueError, match="two different attributions"):
        rc.by_period([result("e1", 3), result("e1", 3, avoided=2)])


def test_an_unknown_period_is_refused():
    with pytest.raises(ValueError, match="period"):
        rc.by_period([], "year")


# ---------- projection

def test_projection_is_estimated_from_recent_runs_and_frequency():
    runs = [result(f"e{d}", d) for d in (5, 10, 15, 20, 25, 29)]  # 6 runs in 30 days, net 5 each
    out = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW)
    p = out["projection"]
    assert out["state"] == "projected" and out["written"] is False
    assert p["classification"] == ms.ESTIMATED and p["unit"] == ms.CU_HOURS
    assert p["value"] == pytest.approx(5 * 6 * rc.DAYS_PER_MONTH / 30)
    assert out["net_cu_hours_per_run"] == 5 and out["inputs_classification"] == ms.ESTIMATED


def test_even_measured_inputs_project_as_estimated():
    run = {"workload_key": "nb:sales", "baseline_version": "b1", "ran": at.FABRIC}
    opt = {"optimization_id": "o1", "implemented_at": "2026-10-01T00:00:00Z", "action": "optimize_in_place",
           "state": "verified", "after": measured(6)}
    runs = [at.attribute(run | {"execution_id": f"e{d}", "at": f"2026-11-{d:02d}T00:00:00Z"}, measured(10), [opt])
            for d in (10, 20, 25)]
    out = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW)
    assert out["inputs_classification"] == ms.MEASURED and out["projection"]["classification"] == ms.ESTIMATED


def test_retirement_stops_the_projection():
    runs = [result(f"e{d}", d) for d in (5, 10, 15)]
    out = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW, retired_at="2026-11-20T00:00:00Z")
    assert out["state"] == "stopped" and out["projection"]["value"] is None
    assert out["projection"]["classification"] == ms.UNKNOWN
    # Retirement planned for later doesn't stop it yet.
    later = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW, retired_at="2026-12-20T00:00:00Z")
    assert later["state"] == "projected"


def test_a_workload_that_stopped_running_projects_nothing():
    out = rc.project([result("e1", 5, month=9)], workload_key="nb:sales", baseline_version="b1", now=NOW)
    assert out["state"] == "stopped" and out["runs"] == 0 and out["projection"]["value"] is None


def test_a_new_baseline_version_doesnt_project_from_old_runs():
    old = [result(f"e{d}", d, baseline="b1") for d in (5, 10, 15, 20)]
    out = rc.project(old, workload_key="nb:sales", baseline_version="b2", now=NOW)
    assert out["state"] == "rebaselined" and out["older_baseline_runs"] == 4 and out["projection"]["value"] is None
    mixed = old + [result(f"n{d}", d, baseline="b2", avoided=2) for d in (22, 24, 26)]
    out = rc.project(mixed, workload_key="nb:sales", baseline_version="b2", now=NOW)
    assert out["state"] == "projected" and out["runs"] == 3 and out["older_baseline_runs"] == 4
    assert out["net_cu_hours_per_run"] == 1  # only the new baseline's runs (2 avoided - 1 overhead)


def test_too_few_runs_is_unknown_with_its_reason():
    runs = [result("e1", 10), result("e2", 12), result("e3", 14, avoided=None)]
    out = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW)
    assert out["state"] == "too_few_runs" and "at least 3" in out["reason"]
    assert out["projection"]["classification"] == ms.UNKNOWN


def test_other_workloads_and_runs_outside_the_window_are_left_out():
    runs = [result(f"e{d}", d) for d in (10, 15, 20)] + [result("x1", 12, workload="nb:other"),
                                                       result("old", 20, month=10)]
    out = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW)
    assert out["runs"] == 3


def test_unknown_overhead_is_named_in_the_assumptions():
    runs = [result("e1", 10), result("e2", 12), result("e3", 14, overhead=None)]
    out = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW)
    assert any("overhead is unknown" in a for a in out["projection"]["assumptions"])


def test_bad_windows_are_refused():
    with pytest.raises(ValueError):
        rc.project([], workload_key="w", baseline_version="b1", now=NOW, window_days=0)
    with pytest.raises(ValueError):
        rc.project([], workload_key="w", baseline_version="b1", now=NOW, min_runs=0)
