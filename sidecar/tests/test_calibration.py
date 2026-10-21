"""FINOPS-005: Fabric CU avoided by a run on this PC comes from Fabric runs of the same workload."""

import datetime as dt

from fabric_desktop import calibration
from fabric_desktop import measurement as ms

NOW = dt.datetime(2026, 10, 18, 12, tzinfo=dt.timezone.utc)
LOCAL = {"workload_id": "meta:sales:orders", "run_id": "r9", "at": "2026-10-18T11:00:00+00:00"}


def fabric(cu_s, days_ago, classification=ms.ESTIMATED, **extra):
    at = (NOW - dt.timedelta(days=days_ago)).isoformat()
    if classification == ms.MEASURED:
        cu = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, cu_s, source="Capacity Metrics export", collected_at=at)
    else:
        cu = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, cu_s, source="Fabric job API", method="run time × CU",
                     assumptions=["1 CU"])
    return {"cu": cu, "at": at, **extra}


def test_no_baseline_is_unknown_never_zero():
    m = calibration.calibrate(LOCAL, [], now=NOW)
    assert m["classification"] == ms.UNKNOWN and m["value"] is None
    assert "No Fabric run" in m["reason"]
    assert not ms.problems(m)


def test_local_run_time_is_never_used():
    a = calibration.calibrate(dict(LOCAL, seconds=5), [fabric(600, 1)], now=NOW)
    b = calibration.calibrate(dict(LOCAL, seconds=5000), [fabric(600, 1)], now=NOW)
    assert a["value"] == b["value"] == 600
    assert any("time on this PC isn't used" in x for x in a["assumptions"])


def test_measured_baselines_win_and_the_median_is_used():
    m = calibration.calibrate(LOCAL, [fabric(100, 1), fabric(300, 2, ms.MEASURED), fabric(500, 3, ms.MEASURED),
                                      fabric(900, 4, ms.MEASURED)], now=NOW)
    assert m["classification"] == ms.ESTIMATED and m["value"] == 500
    assert "measured" in m["method"] and m["uncertainty"] is None
    assert not ms.problems(m)


def test_estimated_baselines_say_so():
    m = calibration.calibrate(LOCAL, [fabric(200, 1)], now=NOW)
    assert m["value"] == 200
    assert any("itself an estimate" in x for x in m["assumptions"])
    assert m["uncertainty"]


def test_old_unknown_and_other_version_baselines_are_left_out():
    old = calibration.calibrate(LOCAL, [fabric(200, 120)], now=NOW)
    assert old["classification"] == ms.UNKNOWN and "90 days" in old["reason"]
    unknown = {"cu": ms.unknown("fabric_job_cu", ms.CU_SECONDS, "no time"), "at": NOW.isoformat()}
    assert calibration.calibrate(LOCAL, [unknown], now=NOW)["classification"] == ms.UNKNOWN
    other = calibration.calibrate(dict(LOCAL, version="v2"), [fabric(200, 1, version="v1")], now=NOW)
    assert other["classification"] == ms.UNKNOWN and "unrecorded) version" in other["reason"]
    unrecorded = calibration.calibrate(dict(LOCAL, version="v2"), [fabric(200, 1)], now=NOW)
    assert unrecorded["classification"] == ms.UNKNOWN and "unrecorded) version" in unrecorded["reason"]
    future = calibration.calibrate(LOCAL, [fabric(200, -1)], now=NOW)
    assert future["classification"] == ms.UNKNOWN


def test_scaled_by_rows_only_when_both_sides_have_them():
    scaled = calibration.calibrate(dict(LOCAL, rows=2000), [fabric(100, 1, rows=1000)], now=NOW)
    assert scaled["value"] == 200 and "scaled" in scaled["method"]
    same = calibration.calibrate(dict(LOCAL, rows=2000), [fabric(100, 1)], now=NOW)
    assert same["value"] == 100 and any("about as much data" in x for x in same["assumptions"])


def test_cu_hours_baselines_are_converted():
    b = fabric(0, 1)
    b["cu"] = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_HOURS, 0.5, method="m", assumptions=["a"])
    assert calibration.calibrate(LOCAL, [b], now=NOW)["value"] == 1800
