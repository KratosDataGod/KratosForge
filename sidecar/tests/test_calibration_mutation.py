"""Mutation-testing follow-ups for calibration.py (docs/quality/mutation-testing.md, batch 38).

Each test pins a behaviour that a surviving mutant changed: the age window's edges, the five newest
baselines, the version check's message, scaling by rows only above 0, and the wording of the result.
"""

import datetime as dt

import pytest

from fabric_desktop import calibration
from fabric_desktop import measurement as ms

NOW = dt.datetime(2026, 10, 18, 12, tzinfo=dt.timezone.utc)
LOCAL = {"workload_id": "meta:sales:orders", "run_id": "r9", "at": "2026-10-18T11:00:00+00:00"}


def fabric(cu_s, at, classification=ms.MEASURED, source="Capacity Metrics export", **extra):
    if classification == ms.MEASURED:
        cu = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, cu_s, source=source, collected_at=at)
    else:
        cu = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, cu_s, source=source, method="run time × CU",
                     assumptions=["1 CU"])
    return {"cu": cu, "at": at.isoformat(), **extra}


def ago(**kw):
    return NOW - dt.timedelta(**kw)


def test_a_run_at_this_very_moment_counts():
    m = calibration.calibrate(LOCAL, [fabric(300, NOW)], now=NOW)
    assert m["value"] == 300


def test_a_run_exactly_at_the_age_limit_counts_and_one_just_past_it_doesnt():
    assert calibration.calibrate(LOCAL, [fabric(300, ago(days=1))], now=NOW, max_age_days=1)["value"] == 300
    past = calibration.calibrate(LOCAL, [fabric(300, ago(days=1, seconds=0.5))], now=NOW, max_age_days=1)
    assert past["classification"] == ms.UNKNOWN and "last 1 days" in past["reason"]


def test_a_measured_baseline_without_a_value_is_left_out():
    broken = {"cu": {"classification": ms.MEASURED, "value": None}, "at": ago(days=1).isoformat()}
    assert calibration.calibrate(LOCAL, [broken, fabric(250, ago(days=2))], now=NOW)["value"] == 250


def test_a_version_with_no_baselines_at_all_says_there_is_nothing_to_compare():
    m = calibration.calibrate(dict(LOCAL, version="v2"), [], now=NOW)
    assert m["classification"] == ms.UNKNOWN
    assert m["reason"] == "No Fabric run of this workload to compare with."


def test_only_the_five_newest_runs_are_used():
    # newest first: 10, 20, 30, 40, 50 then two old 1000s; the median of the newest five is 30
    runs = [fabric(v, ago(days=d)) for d, v in [(1, 10), (2, 20), (3, 30), (4, 40), (5, 50), (6, 1000), (7, 1000)]]
    m = calibration.calibrate(LOCAL, runs, now=NOW)
    assert m["value"] == 30
    assert "Based on the measured Fabric job CU of 5 Fabric runs of this workload (2026-10-13 to 2026-10-17)" \
        in m["assumptions"][0]


def test_one_run_is_named_in_the_singular():
    m = calibration.calibrate(LOCAL, [fabric(300, ago(days=1))], now=NOW)
    assert m["assumptions"][0].startswith("Based on the measured Fabric job CU of 1 Fabric run of this workload (")
    two = calibration.calibrate(LOCAL, [fabric(300, ago(days=1)), fabric(300, ago(days=2))], now=NOW)
    assert two["assumptions"][0].startswith("Based on the measured Fabric job CU of 2 Fabric runs of this")


@pytest.mark.parametrize("local_rows, base_rows", [(0, 10), (10, 0)])
def test_rows_scale_only_when_both_sides_read_more_than_none(local_rows, base_rows):
    m = calibration.calibrate(dict(LOCAL, rows=local_rows), [fabric(100, ago(days=1), rows=base_rows)], now=NOW)
    assert m["value"] == 100 and "scaled" not in m["method"]


def test_one_row_on_each_side_still_scales():
    m = calibration.calibrate(dict(LOCAL, rows=1), [fabric(100, ago(days=1), rows=1)], now=NOW)
    assert m["value"] == 100 and m["method"].endswith(", scaled by rows read")


def test_the_baselines_sources_are_named():
    m = calibration.calibrate(LOCAL, [fabric(100, ago(days=1)), fabric(100, ago(days=2), source="Fabric job API")],
                              now=NOW)
    assert m["source"] == "Capacity Metrics export; Fabric job API"
    run = fabric(100, ago(days=1))
    run["cu"] = dict(run["cu"], source=None)  # a baseline recorded without one
    assert calibration.calibrate(LOCAL, [run], now=NOW)["source"] == "Fabric runs of this workload"


def test_three_measured_runs_are_firm_but_three_estimated_or_two_measured_are_rough():
    three = [fabric(100, ago(days=d)) for d in (1, 2, 3)]
    assert calibration.calibrate(LOCAL, three, now=NOW)["uncertainty"] is None
    estimated = [fabric(100, ago(days=d), ms.ESTIMATED) for d in (1, 2, 3)]
    assert calibration.calibrate(LOCAL, estimated, now=NOW)["uncertainty"]
    assert calibration.calibrate(LOCAL, three[:2], now=NOW)["uncertainty"]
