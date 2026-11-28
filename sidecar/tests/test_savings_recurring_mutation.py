"""Period and projection edges the main tests don't pin down (mutation batch 192,
docs/quality/mutation-testing.md): a mix of measured and estimated runs, the window's edges and the
per-run figure's rounding."""

from __future__ import annotations

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import recurring as rc

NOW = "2026-11-30T00:00:00Z"


def _cu(cls: str, v: float) -> dict:
    if cls == ms.MEASURED:
        return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, v, run_id="r", source="Capacity Metrics export",
                       collected_at="2026-11-01T00:00:00Z")
    return ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_HOURS, v, method="calibration", assumptions=["median"])


def result(eid: str, day: int, avoided: float = 6, cls: str = ms.MEASURED, baseline: str = "b1") -> dict:
    run = {"execution_id": eid, "workload_key": "nb:sales", "baseline_version": baseline,
           "at": f"2026-11-{day:02d}T09:00:00Z", "ran": at.PC}
    return at.attribute(run, _cu(ms.MEASURED, 10), local_avoided=_cu(cls, avoided), overhead=_cu(ms.MEASURED, 0))


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_a_period_with_a_measured_and_an_estimated_run_is_estimated_whichever_comes_first(order):
    runs = [result("e1", 3, cls=ms.MEASURED), result("e2", 4, cls=ms.ESTIMATED)]
    [month] = rc.by_period([runs[i] for i in order], "month")
    assert month["gross_classification"] == ms.ESTIMATED and month["net_classification"] == ms.ESTIMATED


def test_a_window_of_one_day_projects_and_a_window_of_zero_is_refused():
    runs = [result(f"e{i}", 29, avoided=1 + i) for i in range(3)]
    assert rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW,
                      window_days=1)["state"] == "projected"
    with pytest.raises(ValueError, match="window_days"):
        rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW, window_days=0)


def test_a_rebaselined_projection_has_no_runs_of_its_own():
    p = rc.project([result("e1", 20, baseline="b0")], workload_key="nb:sales", baseline_version="b1", now=NOW)
    assert p["state"] == "rebaselined" and p["runs"] == 0 and p["older_baseline_runs"] == 1


def test_the_per_run_figure_is_rounded_to_six_places():
    runs = [result("e1", 20, avoided=1), result("e2", 21, avoided=0), result("e3", 22, avoided=0)]
    p = rc.project(runs, workload_key="nb:sales", baseline_version="b1", now=NOW)
    assert p["net_cu_hours_per_run"] == 0.333333
