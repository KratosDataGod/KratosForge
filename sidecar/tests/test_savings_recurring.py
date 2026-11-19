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
    assert rc.DAYS_PER_MONTH == 30.4375
    assert p["value"] == pytest.approx(5 * 6 * 30.4375 / 30)
    assert out["runs_per_month"] == 6.0875
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
    assert out["complete"] is False and out["unknown_runs"] == 0  # like by_period: unknown overhead is incomplete


def test_bad_windows_are_refused():
    with pytest.raises(ValueError):
        rc.project([], workload_key="w", baseline_version="b1", now=NOW, window_days=0)
    with pytest.raises(ValueError):
        rc.project([], workload_key="w", baseline_version="b1", now=NOW, min_runs=0)


# ---------- edges

def test_a_run_whose_time_isnt_a_time_is_refused():
    bad = result("e1", 3) | {"at": "not a time"}
    with pytest.raises(ValueError, match="Not a time"):
        rc.by_period([bad])
    with pytest.raises(ValueError, match="Not a time"):
        rc.project([bad], workload_key="nb:sales", baseline_version="b1", now=NOW)
    with pytest.raises(ValueError, match="Not a time"):
        rc.project([], workload_key="nb:sales", baseline_version="b1", now="later")


def test_the_window_includes_its_end_and_excludes_its_start():
    # now - 30 days is 2026-10-31T00:00Z: a run exactly then is out, one exactly at now is in.
    edge = [result(f"e{d}", d) for d in (10, 20)]
    start = edge[0] | {"execution_id": "s", "at": "2026-10-31T00:00:00Z"}
    end = edge[0] | {"execution_id": "n", "at": NOW}
    out = rc.project(edge + [start, end], workload_key="nb:sales", baseline_version="b1", now=NOW)
    assert out["runs"] == 3
    after = edge[0] | {"execution_id": "f", "at": "2026-11-30T00:00:01Z"}
    assert rc.project(edge + [end, after], workload_key="nb:sales", baseline_version="b1", now=NOW)["runs"] == 3


def test_retired_exactly_now_stops():
    runs = [result(f"e{d}", d) for d in (5, 10, 15)]
    assert rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW, retired_at=NOW)["state"] == "stopped"
    assert rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW,
                      retired_at="2026-11-30T00:00:01Z")["state"] == "projected"


def test_a_negative_window_is_refused():
    with pytest.raises(ValueError):
        rc.project([], workload_key="w", baseline_version="b1", now=NOW, window_days=-1)


def test_a_period_is_ledger_ready_only_when_every_run_is():
    measured_run = result("e1", 3)
    estimated_baseline = measured_run | {"execution_id": "e2", "ledger_ready": False}
    [m] = rc.by_period([measured_run, estimated_baseline])
    assert m["ledger_ready"] is False
    assert rc.by_period([measured_run])[0]["ledger_ready"] is True
    assert rc.by_period([result("e3", 3, avoided=None)])[0]["ledger_ready"] is False  # nothing known


def test_too_few_runs_counts_older_baseline_runs():
    runs = [result("o1", 10, baseline="b1"), result("n1", 12, baseline="b2")]
    out = rc.project(runs, workload_key="nb:sales", baseline_version="b2", now=NOW)
    assert out["state"] == "too_few_runs" and out["runs"] == 1 and out["older_baseline_runs"] == 1


def test_figures_are_rounded_to_six_places():
    runs = [result(f"e{d}", d, avoided=1 / 3, overhead=0) for d in (10, 11, 12)]
    [m] = rc.by_period(runs)
    assert m["gross_cu_hours"] == 0.999999 and m["net_cu_hours"] == 0.999999  # each run's 0.333333, added
    out = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now="2026-11-13T00:00:00Z", window_days=7)
    assert out["net_cu_hours_per_run"] == 0.333333 and out["runs_per_month"] == 13.044643


def test_runs_with_an_unknown_figure_are_left_out_never_filled_in():
    runs = [result(f"e{d}", d) for d in (10, 12, 14)] + [result("u", 16, avoided=None)]
    out = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW)
    assert out["runs"] == 4 and out["known_runs"] == 3 and out["unknown_runs"] == 1 and out["complete"] is False
    assert out["projection"]["value"] == pytest.approx(5 * 3 * 30.4375 / 30)  # 3 runs, not 4
    assert any("1 run(s) with an unknown figure are left out" in a for a in out["projection"]["assumptions"])


def test_no_left_out_line_when_every_run_is_known():
    runs = [result(f"e{d}", d) for d in (10, 12, 14)]
    out = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW)
    assert out["complete"] is True and out["unknown_runs"] == 0
    assert not any("left out" in a for a in out["projection"]["assumptions"])


def test_periods_are_cut_in_utc():
    late = result("e1", 30) | {"at": "2026-11-30T23:30:00-06:00"}  # Chicago evening = December in UTC
    assert rc.by_period([late])[0]["period"] == "2026-12"
