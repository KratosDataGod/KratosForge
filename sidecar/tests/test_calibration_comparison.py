"""ESI-201: a Fabric run counts as a baseline only when the workload, definition version and input
scope match and its CU is valid; its data volume is checked when both sides recorded it.

Scenario 12 (baseline version change) and 13 (missing Fabric comparison) of the plan's test matrix.
"""

import datetime as dt

import pytest

from fabric_desktop import calibration
from fabric_desktop import measurement as ms

NOW = dt.datetime(2026, 11, 20, 12, tzinfo=dt.timezone.utc)
RUN = {"workload_key": "meta:sales:orders", "definition_version": "v2", "input_scope": "orders/2026-11",
       "run_id": "local-1", "at": "2026-11-19T10:00:00+00:00"}


def fabric(rid, cu_s, days_ago=1, classification=ms.MEASURED, **extra):
    at = (NOW - dt.timedelta(days=days_ago)).isoformat()
    if classification == ms.MEASURED:
        cu = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, cu_s, source="Capacity Metrics export",
                     collected_at=at, run_id=rid)
    else:
        cu = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, cu_s, source="Fabric job API",
                     method="run time × CU", assumptions=["1 CU"], run_id=rid)
    keys = {k: RUN[k] for k in calibration.COMPARISON_KEYS}
    return {"run_id": rid, "at": at, "cu": cu, "ran": "fabric", **keys, **extra}


# Comparability ---------------------------------------------------------------------------------------


def test_each_key_must_match_and_an_unrecorded_one_matches_nothing():
    base = fabric("f1", 100)
    assert calibration.comparable(RUN, base) == {"comparable": True, "reason": ""}
    assert calibration.comparable(RUN, dict(base, workload_key="meta:sales:returns"))["reason"] == \
        "A different workload (meta:sales:returns instead of meta:sales:orders)."
    assert calibration.comparable(RUN, dict(base, input_scope="orders/2026-10"))["reason"] == \
        "A different input scope (orders/2026-10 instead of orders/2026-11)."
    assert calibration.comparable(RUN, dict(base, definition_version=None))["reason"] == \
        "The definition version wasn't recorded, so it can't be matched."
    assert not calibration.comparable(dict(RUN, input_scope=""), base)["comparable"]


@pytest.mark.parametrize("cu, reason", [
    (ms.unknown("fabric_job_cu", ms.CU_SECONDS, "no time"), "Its CU isn't known."),
    (ms.make(ms.SCENARIO, "fabric_job_cu", ms.CU_SECONDS, 100, method="m", assumptions=["a"]), "Scenario"),
    (ms.make(ms.MEASURED, "run_time", ms.SECONDS, 100, source="s", collected_at="2026-11-19"), "run time is never CU"),
    ({"classification": ms.MEASURED, "unit": ms.CU_SECONDS, "value": -5, "metric": "fabric_job_cu"}, "isn't valid"),
    ({"classification": ms.MEASURED, "unit": ms.CU_SECONDS, "value": float("nan"), "metric": "x"}, "isn't valid"),
    (None, "It has no CU figure."),
])
def test_invalid_metrics_dont_count(cu, reason):
    c = calibration.check_baseline(RUN, dict(fabric("f1", 100), cu=cu), now=NOW)
    assert not c["counts"] and reason in c["reason"]


def test_runs_not_in_fabric_too_old_or_in_the_future_dont_count():
    assert calibration.check_baseline(RUN, fabric("f1", 100, ran="local"), now=NOW)["reason"] == \
        "It didn't run in Fabric."
    assert "more than 90 days ago" in calibration.check_baseline(RUN, fabric("f1", 100, 91), now=NOW)["reason"]
    assert calibration.check_baseline(RUN, fabric("f1", 100, -1), now=NOW)["reason"] == "Its run time is in the future."


# Data volume -----------------------------------------------------------------------------------------


def test_volume_within_the_tolerance_passes_and_outside_it_fails():
    assert calibration.VOLUME_TOLERANCE == 0.25
    edge = calibration.volume_check(dict(RUN, rows=1000), fabric("f1", 100, rows=1250))
    assert edge["status"] == calibration.PASSED
    low = calibration.volume_check(dict(RUN, rows=1000), fabric("f1", 100, rows=750))
    assert low["status"] == calibration.PASSED
    over = calibration.check_baseline(dict(RUN, rows=1000), fabric("f1", 100, rows=1251), now=NOW)
    assert not over["counts"] and over["volume"]["status"] == calibration.FAILED
    assert "1251 input rows" in over["reason"] and "25%" in over["reason"]


def test_bytes_are_checked_as_well_as_rows():
    out = calibration.volume_check(dict(RUN, rows=1000, bytes=10_000), fabric("f1", 100, rows=1000, bytes=50_000))
    assert out["status"] == calibration.FAILED and "input bytes" in out["reason"]
    ok = calibration.volume_check(dict(RUN, bytes=10_000), fabric("f1", 100, bytes=11_000))
    assert ok["status"] == calibration.PASSED and ok["reason"] == "Its input bytes are within 25% of this run's."


def test_an_unrecorded_volume_means_the_check_didnt_run_never_that_it_passed():
    one_side = calibration.volume_check(dict(RUN, rows=1000), fabric("f1", 100))
    assert one_side["status"] == calibration.NOT_RUN and "couldn't run" in one_side["reason"]
    out = calibration.calibrate_comparable(RUN, [fabric("f1", 100)], now=NOW)
    assert out["counted"] == ["f1"]
    assert any("data volume check couldn't run for 1 of the 1" in a for a in out["cu"]["assumptions"])
    assert not any("within 25%" in a for a in out["cu"]["assumptions"])


def test_zero_rows_only_matches_zero_rows():
    assert calibration.volume_check(dict(RUN, rows=0), fabric("f1", 100, rows=0))["status"] == calibration.PASSED
    assert calibration.volume_check(dict(RUN, rows=0), fabric("f1", 100, rows=1))["status"] == calibration.FAILED


@pytest.mark.parametrize("bad", [-1, float("nan"), "lots", True])
def test_a_bad_volume_is_refused_naming_the_run(bad):
    with pytest.raises(ValueError, match="input rows of Fabric run f1 must be a finite number"):
        calibration.volume_check(RUN | {"rows": 10}, fabric("f1", 100, rows=bad))
    with pytest.raises(ValueError, match="data volume tolerance"):
        calibration.volume_check(RUN, fabric("f1", 100), tolerance=-0.1)


# Selection and the figure ----------------------------------------------------------------------------


def test_only_comparable_runs_are_used_and_each_left_out_one_says_why():
    runs = [fabric("f1", 100), fabric("f2", 200, 2), fabric("f3", 300, 3),
            fabric("f4", 9000, definition_version="v1"), fabric("f5", 9000, input_scope="orders/all")]
    out = calibration.calibrate_comparable(RUN, runs, now=NOW)
    assert out["counted"] == ["f1", "f2", "f3"]
    assert out["cu"]["value"] == 200 and out["cu"]["classification"] == ms.ESTIMATED
    assert {x["run_id"]: x["reason"] for x in out["excluded"]} == {
        "f4": "A different definition version (v1 instead of v2).",
        "f5": "A different input scope (orders/all instead of orders/2026-11)."}
    assert any("definition version (v2) and input scope (orders/2026-11)" in a for a in out["cu"]["assumptions"])
    assert not ms.problems(out["cu"])


def test_the_same_fabric_run_read_twice_counts_once_and_two_figures_are_refused():
    out = calibration.calibrate_comparable(RUN, [fabric("f1", 100), fabric("f1", 100), fabric("f2", 400, 2)], now=NOW)
    assert out["counted"] == ["f1", "f2"] and out["cu"]["value"] == 250
    with pytest.raises(ValueError, match="Fabric run f1 appears twice with different CU figures"):
        calibration.calibrate_comparable(RUN, [fabric("f1", 100), fabric("f1", 300)], now=NOW)
    with pytest.raises(ValueError, match="Fabric run 1 in the list has no run_id"):
        calibration.calibrate_comparable(RUN, [dict(fabric("f1", 100), run_id="")], now=NOW)


def test_existing_calibrate_is_unchanged():
    legacy = calibration.calibrate({"workload_id": "w", "run_id": "r", "at": NOW.isoformat()},
                                   [{"cu": fabric("f1", 100)["cu"], "at": NOW.isoformat()}], now=NOW)
    assert legacy["value"] == 100 and len(legacy["assumptions"]) == 4


# Scenario 13: missing Fabric comparison ---------------------------------------------------------------


def test_scenario_13_no_fabric_run_is_unknown_with_its_reason_never_zero():
    out = calibration.calibrate_comparable(RUN, [], now=NOW)
    assert out["cu"]["classification"] == ms.UNKNOWN and out["cu"]["value"] is None
    assert out["cu"]["reason"] == "No Fabric run of this workload to compare with."
    assert out["counted"] == [] and not ms.problems(out["cu"])


def test_scenario_13_no_comparable_run_names_each_run_and_why():
    runs = [fabric("f1", 100, definition_version="v1"), fabric("f2", 100, definition_version="v1"),
            fabric("f3", 100, cu=ms.unknown("fabric_job_cu", ms.CU_SECONDS, "no time"))]
    cu = calibration.calibrate_comparable(RUN, runs, now=NOW)["cu"]
    assert cu["classification"] == ms.UNKNOWN and cu["value"] is None
    assert cu["reason"] == ("No Fabric run counts as a baseline for this run. A different definition version "
                            "(v1 instead of v2). (runs f1, f2) Its CU isn't known. (run f3)")


def test_scenario_13_a_later_fabric_run_adds_a_new_row_and_the_old_one_stays():
    first = calibration.calibrate_comparable(RUN, [], now=NOW)["cu"]
    old = dict(first)
    executions = [{"run": RUN, "measurement": first}]
    later = [fabric("f9", 600, 0)]
    out = calibration.enrich(executions, later, now=NOW)
    assert out["still_unknown"] == []
    [row] = out["rows"]
    assert row["run_id"] == "local-1" and row["previous_measurement_id"] == first["id"]
    assert row["measurement"]["id"] != first["id"] and row["measurement"]["value"] == 600
    assert row["measurement"]["classification"] == ms.ESTIMATED and row["counted"] == ["f9"]
    assert first == old  # the Unknown row isn't changed
    # once a known row is recorded, running it again adds nothing
    again = calibration.enrich(executions + [{"run": RUN, "measurement": row["measurement"]}], later, now=NOW)
    assert again == {"rows": [], "still_unknown": []}


def test_enrich_leaves_a_run_unknown_with_the_reason_when_still_nothing_counts():
    first = calibration.calibrate_comparable(RUN, [], now=NOW)["cu"]
    out = calibration.enrich([{"run": RUN, "measurement": first}], [fabric("f1", 100, definition_version="v1")],
                             now=NOW)
    assert out["rows"] == []
    assert out["still_unknown"] == [{"run_id": "local-1", "reason": "No Fabric run counts as a baseline for this "
                                     "run. A different definition version (v1 instead of v2). (run f1)"}]


def test_enrich_refuses_rows_that_dont_belong():
    first = calibration.calibrate_comparable(RUN, [], now=NOW)["cu"]
    with pytest.raises(ValueError, match="Execution 1 in the list has no run_id"):
        calibration.enrich([{"run": {}, "measurement": first}], [], now=NOW)
    with pytest.raises(ValueError, match="belongs to run other"):
        calibration.enrich([{"run": RUN, "measurement": dict(first, run_id="other")}], [], now=NOW)
    with pytest.raises(ValueError, match="is of 'fabric_job_cu', not fabric_job_cu_avoided"):
        calibration.enrich([{"run": RUN, "measurement": dict(first, metric="fabric_job_cu")}], [], now=NOW)


# Scenario 12: baseline version change -----------------------------------------------------------------


def test_scenario_12_a_definition_change_makes_older_baselines_not_count():
    v1 = [fabric(f"f{d}", 900, d, definition_version="v1") for d in (3, 4, 5)]
    before = calibration.calibrate_comparable(dict(RUN, definition_version="v1"), v1, now=NOW)
    assert before["cu"]["value"] == 900
    # the definition changes to v2: the v1 runs no longer count, and nothing is filled in from them
    after = calibration.calibrate_comparable(RUN, v1, now=NOW)
    assert after["cu"]["classification"] == ms.UNKNOWN and after["cu"]["value"] is None
    assert "A different definition version (v1 instead of v2). (runs f3, f4, f5)" in after["cu"]["reason"]
    # a v2 Fabric run arrives: only it counts, and the earlier Unknown run gets a new row
    v2 = fabric("g1", 300, 1)
    now_known = calibration.calibrate_comparable(RUN, v1 + [v2], now=NOW)
    assert now_known["counted"] == ["g1"] and now_known["cu"]["value"] == 300
    out = calibration.enrich([{"run": RUN, "measurement": after["cu"]}], v1 + [v2], now=NOW)
    assert [r["measurement"]["value"] for r in out["rows"]] == [300]
    assert out["rows"][0]["previous_measurement_id"] == after["cu"]["id"]


@pytest.mark.parametrize(("mine", "theirs"), [(3, "3"), ("3", " 3 "), (" v2", "v2")])
def test_a_version_written_differently_counts_and_agrees_with_the_figure(mine, theirs):
    run = RUN | {"definition_version": mine}
    out = calibration.calibrate_comparable(run, [fabric("f1", 100, definition_version=theirs)], now=NOW)
    assert out["counted"] == ["f1"] and out["cu"]["value"] is not None, out


def test_enrich_refuses_an_unknown_row_without_an_id():
    first = calibration.calibrate_comparable(RUN, [], now=NOW)["cu"]
    first = {k: v for k, v in first.items() if k != "id"}
    with pytest.raises(ValueError, match="has no id"):
        calibration.enrich([{"run": RUN, "measurement": first}], [fabric("f1", 100)], now=NOW)
