"""Measurements (measurement.py), the paths the other tests leave: a measurement with no metric, a
reconciliation state that isn't one, and a collection time that isn't a time."""

from __future__ import annotations

from fabric_desktop import measurement as ms


def _measured(**changes):
    m = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, 2.0, source="Capacity Metrics export",
                collected_at="2026-11-01T00:00:00Z")
    return m | changes


def test_a_valid_measurement_has_no_problems():
    assert ms.problems(_measured()) == []


def test_a_measurement_without_a_metric_says_so():
    assert ms.problems(_measured(metric="")) == ["metric is missing"]


def test_a_reconciliation_state_that_isnt_one_is_named():
    [problem] = ms.problems(_measured(reconciliation="sort of"))
    assert problem.startswith("reconciliation must be one of ")


def test_a_collection_time_that_isnt_a_time_is_named():
    assert ms.problems(_measured(collected_at="yesterday-ish")) == ["collected_at must be a time"]
