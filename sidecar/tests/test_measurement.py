"""FINOPS-001: classified capacity and cost measurements, and the cost report built on them."""

from __future__ import annotations

import datetime as dt
import json
import math

import pytest
from fabric_desktop import costs
from fabric_desktop import measurement as ms

NOW = dt.datetime(2026, 10, 15, 12, tzinfo=dt.timezone.utc)
EST = {"method": "run time x CUs", "assumptions": ["1 CU"]}


# ---- schema and validation

def test_each_classification_has_its_required_evidence():
    m = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, 10, source="export.csv", collected_at=NOW,
                run_id="r1", workspace_id="w", capacity_id="c", workload_id="fabric:w:i")
    assert m["label"] == "Measured" and m["reconciliation"] == "not_applicable" and m["id"].startswith("m-")
    assert ms.make(ms.ESTIMATED, "x", ms.CU_SECONDS, 1, **EST)["classification"] == "estimated"
    assert ms.make(ms.SCENARIO, "x", ms.CU_SECONDS, 1, **EST)["classification"] == "scenario"
    assert ms.unknown("x", ms.CU_SECONDS, "no end time")["value"] is None
    with pytest.raises(ValueError, match="names the source"):
        ms.make(ms.MEASURED, "x", ms.CU_SECONDS, 1, collected_at=NOW)
    with pytest.raises(ValueError, match="says when it was collected"):
        ms.make(ms.MEASURED, "x", ms.CU_SECONDS, 1, source="s")
    # An estimate can't pass as measured: assumptions make it an estimate.
    with pytest.raises(ValueError, match="estimated, not measured"):
        ms.make(ms.MEASURED, "x", ms.CU_SECONDS, 1, source="s", collected_at=NOW, assumptions=["1 CU"])
    with pytest.raises(ValueError, match="lists its assumptions"):
        ms.make(ms.ESTIMATED, "x", ms.CU_SECONDS, 1, method="m")
    with pytest.raises(ValueError, match="says how it was worked out"):
        ms.make(ms.SCENARIO, "x", ms.CU_SECONDS, 1, assumptions=["a"])
    with pytest.raises(ValueError, match="classification must be one of"):
        ms.make("billed", "x", ms.CU_SECONDS, 1)


@pytest.mark.parametrize("value", [-1, math.nan, math.inf, "12", True])
def test_invalid_values_are_refused(value):
    with pytest.raises(ValueError):
        ms.make(ms.ESTIMATED, "x", ms.CU_SECONDS, value, **EST)


def test_unknown_has_no_value_and_says_why():
    with pytest.raises(ValueError, match="has no value"):
        ms.validate({"classification": ms.UNKNOWN, "metric": "x", "unit": ms.CU_SECONDS, "value": 0, "reason": "r"})
    with pytest.raises(ValueError, match="says why"):
        ms.make(ms.UNKNOWN, "x", ms.CU_SECONDS, None)


def test_units_and_currency_are_not_mixed_up():
    with pytest.raises(ValueError, match="needs its currency"):
        ms.make(ms.ESTIMATED, "cost", ms.CURRENCY, 1, **EST)
    with pytest.raises(ValueError, match="only money"):
        ms.make(ms.ESTIMATED, "x", ms.CU_HOURS, 1, currency="USD", **EST)
    with pytest.raises(ValueError, match="over 100%"):
        ms.make(ms.ESTIMATED, "share", ms.PERCENT, 101, **EST)
    with pytest.raises(ValueError, match="unit must be"):
        ms.make(ms.ESTIMATED, "x", "cu", 1, **EST)
    with pytest.raises(ValueError, match="ends before it starts"):
        ms.make(ms.ESTIMATED, "x", ms.CU_HOURS, 1, period_start=NOW, period_end=NOW - dt.timedelta(hours=1), **EST)


def test_ids_are_stable_for_the_same_run_and_metric():
    a = ms.make(ms.ESTIMATED, "x", ms.CU_SECONDS, 1, run_id="r", **EST)
    b = ms.make(ms.ESTIMATED, "x", ms.CU_SECONDS, 2, run_id="r", **EST)
    c = ms.make(ms.ESTIMATED, "x", ms.CU_SECONDS, 1, run_id="other", **EST)
    assert a["id"] == b["id"] != c["id"]


# ---- units

def test_cu_seconds_and_cu_hours_convert_and_nothing_else_does():
    assert ms.convert(7200, ms.CU_SECONDS, ms.CU_HOURS) == 2
    assert ms.convert(1.5, ms.CU_HOURS, ms.CU_SECONDS) == 5400
    assert ms.convert(3, ms.PERCENT, ms.PERCENT) == 3
    for a, b in [(ms.CU_HOURS, ms.CURRENCY), (ms.CU_SECONDS, ms.PERCENT), (ms.CURRENCY, ms.CU_HOURS), (ms.SECONDS, ms.CU_SECONDS)]:
        with pytest.raises(ValueError, match="measure different things"):
            ms.convert(1, a, b)
    with pytest.raises(ValueError, match="unknown unit"):
        ms.convert(1, "cu_minutes", ms.CU_HOURS)
    m = ms.to_unit(ms.make(ms.ESTIMATED, "x", ms.CU_SECONDS, 1800, **EST), ms.CU_HOURS)
    assert m["value"] == 0.5 and m["unit"] == ms.CU_HOURS


# ---- freshness

def test_old_observations_are_stale():
    m = ms.make(ms.MEASURED, "x", ms.CU_HOURS, 1, source="export", collected_at=NOW - dt.timedelta(days=3))
    assert ms.freshness(m, 86400, NOW)["stale"] and "stale" in ms.freshness(m, 86400, NOW)["text"]
    assert not ms.freshness(m, 7 * 86400, NOW)["stale"]
    assert ms.freshness(ms.make(ms.ESTIMATED, "x", ms.CU_HOURS, 1, **EST), 60, NOW)["age_seconds"] is None


# ---- totals

def test_a_total_is_as_weak_as_its_weakest_part_and_never_counts_unknown_as_zero():
    measured = ms.make(ms.MEASURED, "x", ms.CU_SECONDS, 3600, source="s", collected_at=NOW)
    est = ms.make(ms.ESTIMATED, "x", ms.CU_SECONDS, 3600, **EST)
    unk = ms.unknown("x", ms.CU_SECONDS, "still running")
    assert ms.total([measured], "x", ms.CU_HOURS)["classification"] == ms.MEASURED
    t = ms.total([measured, est, unk], "x", ms.CU_HOURS)
    assert t["classification"] == ms.ESTIMATED and t["value"] == 2 and t["unknown_parts"] == 1 and not t["complete"]
    assert t["by_classification"] == {"measured": 1, "estimated": 1, "unknown": 1} and "not known" in t["reason"]
    only_unknown = ms.total([unk], "x", ms.CU_HOURS)
    assert only_unknown["classification"] == ms.UNKNOWN and only_unknown["value"] is None
    assert ms.total([], "x", ms.CU_HOURS) | {} == ms.total([], "x", ms.CU_HOURS) and ms.total([], "x", ms.CU_HOURS)["value"] == 0
    with pytest.raises(ValueError, match="scenario"):
        ms.total([est, ms.make(ms.SCENARIO, "x", ms.CU_SECONDS, 1, **EST)], "x", ms.CU_HOURS)
    a = ms.make(ms.ESTIMATED, "c", ms.CURRENCY, 1, currency="USD", **EST)
    with pytest.raises(ValueError, match="can't add USD to EUR"):
        ms.total([a], "c", ms.CURRENCY, currency="EUR")


# ---- the cost report

CFG = costs.settings({"price_per_cu_hour": 0.2})


def _report(meta=(), fabric=(), writes=(), capacities=None):
    rows = costs.activities(list(meta), list(fabric), list(writes), CFG)
    return costs.report(rows, CFG, 30, NOW, capacities)


def test_fabric_runs_are_estimated_and_runs_on_this_pc_without_a_fabric_baseline_are_unknown():
    out = _report(meta=[{"entity": "a", "env": "dev", "compute": "local", "seconds": 3600, "run_id": "r1",
                         "started_at": "2026-10-14T10:00:00+00:00"}],
                  fabric=[{"kind": "Notebook run", "name": "nb", "engine": "python", "workspace_id": "w", "item_id": "i",
                           "job_id": "j", "started_at": "2026-10-14T13:00:00Z", "finished_at": "2026-10-14T13:30:00Z"}])
    ev = out["total"]["evidence"]
    assert ev["fabric_job_cu"]["classification"] == "estimated" and ev["fabric_job_cu"]["value"] == 0.5
    assert ev["fabric_cost"]["classification"] == "estimated" and ev["fabric_cost"]["value"] == 0.1
    assert any("not a charge" in a for a in ev["fabric_cost"]["assumptions"])
    # Table "a" never ran in Fabric, so what it avoided isn't known; the rough scenario stays apart.
    assert ev["avoided_cu"]["classification"] == "unknown" and ev["avoided_cost"]["value"] is None
    assert "no comparable Fabric run of the same workload" in ev["avoided_cu"]["reason"] and out["total"]["uncalibrated_runs"] == 1
    assert ev["scenario_cu"]["classification"] == "scenario" and ev["scenario_cu"]["value"] == 1.0
    assert ev["scenario_cost"]["classification"] == "scenario"
    nb = next(i for i in out["by_item"] if i["name"] == "nb")
    assert nb["evidence"]["fabric_job_cu"]["sources"] == ["Fabric job API"]
    # The plain numbers older callers read are unchanged; the scenario's are named as a scenario.
    assert out["total"]["cu_hours"] == 0.5 and out["total"]["scenario_cu_hours"] == 1.0 and out["total"]["scenario_saved"] == 0.2
    assert "saved" not in out["total"] and "saved_cu_hours" not in out["total"]
    json.dumps(out)  # goes over the engine's JSON channel as is


def test_rows_carry_run_workspace_and_workload_ids():
    rows = costs.activities([], [{"kind": "Notebook run", "name": "nb", "engine": "python", "workspace_id": "w",
                                  "item_id": "i", "job_id": "j", "started_at": "2026-10-14T13:00:00Z",
                                  "finished_at": "2026-10-14T13:30:00Z"}], [], CFG)
    m = rows[0]["fabric_job_cu"]
    assert (m["run_id"], m["workspace_id"], m["workload_id"]) == ("j", "w", "fabric:w:i")


def test_missing_and_invalid_run_times_are_unknown_not_zero():
    out = _report(meta=[{"entity": "a", "env": "prod", "compute": "fabric", "seconds": None, "started_at": "2026-10-14T10:00:00Z"},
                        {"entity": "b", "env": "dev", "compute": "local", "seconds": -5, "started_at": "2026-10-14T10:00:00Z"}],
                  fabric=[{"kind": "Notebook run", "name": "bad", "started_at": "2026-10-14T13:00:00Z", "finished_at": "not a time"}],
                  writes=[{"job": "Notebook write to LH", "finished_at": "2026-10-14T13:00:00Z"}])
    t = out["total"]
    assert t["runs"] == 4 and t["unknown_runs"] == 4
    # The runs on this PC used no Fabric job CU; the two Fabric runs' use isn't known, so the total is incomplete.
    cu = t["evidence"]["fabric_job_cu"]
    assert cu["value"] == 0 and cu["unknown_parts"] == 2 and not cu["complete"]
    assert t["evidence"]["avoided_cost"]["classification"] == "unknown" and t["evidence"]["avoided_cost"]["value"] is None
    prod = next(i for i in out["by_item"] if i["name"] == "a")
    assert prod["evidence"]["fabric_job_cu"]["classification"] == "unknown" and prod["evidence"]["fabric_cost"]["value"] is None
    bad = next(i for i in out["by_item"] if i["name"] == "bad")
    assert bad["evidence"]["fabric_job_cu"]["classification"] == "unknown"


def test_an_incomplete_total_covers_the_known_runs_and_says_so():
    out = _report(fabric=[{"kind": "Notebook run", "name": "nb", "engine": "python", "started_at": "2026-10-14T13:00:00Z",
                           "finished_at": "2026-10-14T14:00:00Z"},
                          {"kind": "Notebook run", "name": "nb", "engine": "python", "started_at": "2026-10-14T15:00:00Z",
                           "finished_at": None}])
    cu = out["total"]["evidence"]["fabric_job_cu"]
    assert cu["value"] == 1 and cu["unknown_parts"] == 1 and cu["classification"] == "estimated" and not cu["complete"]


def test_capacity_share_is_estimated_or_unknown_never_invented():
    run = {"kind": "Notebook run", "name": "nb", "engine": "python", "env": "prod", "started_at": "2026-10-14T13:00:00Z",
           "finished_at": "2026-10-14T14:00:00Z"}
    known = _report(fabric=[run], capacities={"prod": {"name": "Cap", "sku": "F2", "id": "c1"}})
    prod = next(e for e in known["by_env"] if e["env"] == "prod")
    assert prod["capacity_share_evidence"]["classification"] == "estimated"
    assert prod["capacity_share_evidence"]["capacity_id"] == "c1"
    assert prod["capacity_share"] == pytest.approx(1 / (2 * 30 * 24) * 100, abs=1e-3)
    unknown_sku = _report(fabric=[run], capacities={"prod": {"name": "Cap", "sku": "Trial-ish"}})
    prod = next(e for e in unknown_sku["by_env"] if e["env"] == "prod")
    assert prod["capacity_share"] is None and prod["capacity_share_evidence"]["classification"] == "unknown"


def test_nothing_ran_is_a_measured_zero():
    t = _report()["total"]
    assert t["runs"] == 0 and t["evidence"]["fabric_job_cu"]["classification"] == "measured"
    assert t["evidence"]["fabric_job_cu"]["value"] == 0 and t["evidence"]["fabric_cost"]["value"] == 0


def test_runs_on_this_pc_used_no_fabric_job_cu_measured():
    out = _report(meta=[{"entity": "a", "env": "dev", "compute": "local", "seconds": 60, "started_at": "2026-10-14T10:00:00Z"}])
    ev = out["total"]["evidence"]
    assert ev["fabric_job_cu"]["classification"] == "measured" and ev["fabric_job_cu"]["value"] == 0
    assert ev["fabric_cost"]["classification"] == "measured" and ev["fabric_cost"]["value"] == 0


# ---- calibrated savings (FINOPS-005)

def _meta(entity, env, compute, at, seconds=600, **extra):
    return {"entity": entity, "env": env, "compute": compute, "seconds": seconds, "started_at": at,
            "run_id": f"{entity}-{env}-{at}"} | extra


def test_a_run_on_this_pc_is_estimated_from_fabric_runs_of_the_same_table_not_from_its_time_here():
    out = _report(meta=[_meta("silver.orders", "dev", "local", "2026-10-14T10:00:00+00:00", seconds=5, logic="v1"),
                        _meta("silver.orders", "dev", "fabric", "2026-10-12T10:00:00+00:00", seconds=1800, logic="v1"),
                        _meta("silver.orders", "dev", "fabric", "2026-10-13T10:00:00+00:00", seconds=3600, logic="v1"),
                        _meta("silver.orders", "dev", "fabric", "2026-10-11T10:00:00+00:00", seconds=5400, logic="v1")])
    ev = out["total"]["evidence"]
    # The median of the three Fabric runs at 1 CU (1 h), whatever the 5 s it took here.
    assert ev["avoided_cu"]["classification"] == "estimated" and ev["avoided_cu"]["value"] == 1.0
    assert ev["avoided_cost"]["classification"] == "estimated" and ev["avoided_cost"]["value"] == 0.2
    assert any("time on this PC isn't used" in a for a in ev["avoided_cu"]["assumptions"])
    assert ev["scenario_cu"]["value"] == pytest.approx(5 / 3600, abs=1e-6)  # the rough figure, kept apart
    assert out["total"]["uncalibrated_runs"] == 0 and out["total"]["unknown_runs"] == 0
    rows = costs.activities([_meta("silver.orders", "dev", "local", "2026-10-14T10:00:00+00:00"),
                             _meta("silver.orders", "dev", "fabric", "2026-10-13T10:00:00+00:00")], [], [], CFG, now=NOW)
    assert not ms.problems(next(r for r in rows if r["where"] == "local")["avoided_cu"])


def test_fabric_runs_in_the_same_environment_are_preferred_and_others_are_noted():
    meta = [_meta("t", "dev", "local", "2026-10-14T10:00:00+00:00"),
            _meta("t", "prod", "fabric", "2026-10-13T10:00:00+00:00", seconds=7200)]
    rows = costs.activities(meta, [], [], CFG, now=NOW)
    local = next(r for r in rows if r["where"] == "local")
    assert local["avoided_cu"]["value"] == 7200
    assert any("another environment (prod)" in a for a in local["avoided_cu"]["assumptions"])
    meta.append(_meta("t", "dev", "fabric", "2026-10-12T10:00:00+00:00", seconds=60))
    local = next(r for r in costs.activities(meta, [], [], CFG, now=NOW) if r["where"] == "local")
    assert local["avoided_cu"]["value"] == 60 and not any("another environment" in a for a in local["avoided_cu"]["assumptions"])


def test_rows_read_scale_the_estimate_and_a_changed_definition_is_unknown():
    meta = [_meta("t", "dev", "local", "2026-10-14T10:00:00+00:00", rows_read=2000, logic="v2"),
            _meta("t", "dev", "fabric", "2026-10-13T10:00:00+00:00", seconds=100, rows_read=1000, logic="v2")]
    local = next(r for r in costs.activities(meta, [], [], CFG, now=NOW) if r["where"] == "local")
    assert local["avoided_cu"]["value"] == 200 and "rows read" in local["avoided_cu"]["method"]
    meta[1]["logic"] = "v1"
    local = next(r for r in costs.activities(meta, [], [], CFG, now=NOW) if r["where"] == "local")
    assert local["avoided_cu"]["classification"] == "unknown" and "unrecorded) version" in local["avoided_cu"]["reason"]


def test_a_run_here_without_a_time_can_still_be_estimated_and_mixed_totals_dont_fail():
    out = _report(meta=[_meta("t", "dev", "local", "2026-10-14T10:00:00+00:00", seconds=None),
                        _meta("t", "dev", "local", "2026-10-14T11:00:00+00:00"),
                        _meta("t", "dev", "fabric", "2026-10-13T10:00:00+00:00", seconds=3600)],
                  writes=[{"job": "Notebook write to LH", "seconds": 30, "finished_at": "2026-10-14T13:00:00Z"}])
    t = out["total"]
    ev = t["evidence"]
    # Two table runs estimated from the Fabric run; the notebook write has nothing to compare with.
    assert ev["avoided_cu"]["value"] == 2.0 and ev["avoided_cu"]["unknown_parts"] == 1
    assert "1 of 3 runs on this PC has no comparable Fabric run" in ev["avoided_cu"]["reason"]
    # The scenario covers the runs with a time; the one without is unknown beside it, not an error.
    assert ev["scenario_cu"]["classification"] == "scenario" and ev["scenario_cu"]["unknown_parts"] == 1
    assert t["unknown_runs"] == 1 and t["uncalibrated_runs"] == 1
    json.dumps(out)



def test_tables_of_one_fabric_engine_run_are_each_estimated_from_their_own_time():
    # Real id shapes: every table of one engine run shares its run id (meta/engine.py), so nothing is keyed by it.
    run = "r-20261013-100000-ab12"
    meta = [_meta("a", "dev", "fabric", "2026-10-13T10:00:00+00:00", seconds=600, run_id=run, logic="v1"),
            _meta("b", "dev", "fabric", "2026-10-13T10:00:00+00:00", seconds=1200, run_id=run, logic="v1"),
            _meta("a", "dev", "local", "2026-10-14T10:00:00+00:00", run_id="r-20261014-100000-cd34", logic="v1"),
            _meta("b", "dev", "local", "2026-10-14T10:00:00+00:00", run_id="r-20261014-100000-cd34", logic="v1")]
    local = {r["name"]: r["avoided_cu"] for r in costs.activities(meta, [], [], CFG, now=NOW) if r["where"] == "local"}
    assert (local["a"]["value"], local["b"]["value"]) == (600, 1200)
    assert all("estimated" in m["method"] for m in local.values())


def test_items_are_ranked_by_fabric_cost_and_calibrated_avoidance_not_by_the_scenario():
    # "big" ran 10 h here (a large scenario) but never in Fabric; "nb" cost real (estimated) Fabric CU.
    out = _report(meta=[_meta("big", "dev", "local", "2026-10-14T10:00:00+00:00", seconds=36000)],
                  fabric=[{"kind": "Notebook run", "name": "nb", "engine": "python", "workspace_id": "w", "item_id": "i",
                           "job_id": "j", "started_at": "2026-10-14T13:00:00Z", "finished_at": "2026-10-14T13:30:00Z"}])
    assert [i["name"] for i in out["by_item"]] == ["nb", "big"]
