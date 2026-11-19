"""Overhead per execution (ESI-202): separate measurements, Unknown where not measured, never 0."""

from __future__ import annotations

import math
import random

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import overhead as oh

WHEN = "2026-11-10T00:05:00Z"


def execution(**kw) -> dict:
    row = {"execution_id": "e1", "artifact_key": "nb:sales", "workspace_id": "w1", "capacity_id": "c1",
           "started_at": "2026-11-10T00:00:00Z", "finished_at": WHEN, "input_bytes": None, "output_bytes": None,
           "reads": None, "writes": None, "transfer_seconds": None}
    return row | kw


def full(**kw) -> dict:
    return execution(input_bytes=2048, output_bytes=1024, reads=3, writes=1, transfer_seconds=1.5, **kw)


def sync_records(seconds=2.0, rows=10, commits=1, sync_id="s1", drop=()) -> list[dict]:
    ids = {"run_id": sync_id, "workload_id": "optimize.sync.local_executions", "source": "Optimize sync on this PC",
           "collected_at": "2026-11-10T01:00:00Z", "period_start": "2026-11-10T00:59:58Z",
           "period_end": "2026-11-10T01:00:00Z"}
    out = [ms.make(ms.MEASURED, "optimize_sync_seconds", ms.SECONDS, seconds, **ids),
           ms.make(ms.MEASURED, "optimize_sync_rows", ms.COUNT, rows, **ids),
           ms.make(ms.MEASURED, "optimize_sync_commits", ms.COUNT, commits, **ids)]
    return [r for r in out if r["metric"] not in drop]


def cu(value: float, cls: str = ms.MEASURED, run_id: str = "e1") -> dict:
    if cls == ms.MEASURED:
        return ms.make(ms.MEASURED, "onelake_operations_cu", ms.CU_HOURS, value, run_id=run_id,
                       source="Capacity Metrics export", collected_at="2026-11-11T00:00:00Z")
    return ms.make(cls, "onelake_operations_cu", ms.CU_HOURS, value, run_id=run_id, method="Capacity Metrics share",
                   assumptions=["OneLake operations in the run's window were this run's"])


# ---------- transfer figures, one by one

@pytest.mark.parametrize("field", list(oh.TRANSFER))
def test_each_transfer_figure_is_measured_when_recorded_and_unknown_otherwise(field):
    value = 1.25 if field == "transfer_seconds" else 7
    m = oh.transfer(execution(**{field: value}))
    assert m[field]["classification"] == ms.MEASURED and m[field]["value"] == value
    assert m[field]["source"] == oh.SOURCE and m[field]["collected_at"].startswith("2026-11-10T00:05")
    assert m[field]["run_id"] == "e1" and m[field]["workload_id"] == "nb:sales"
    assert m[field]["metric"] == oh.TRANSFER[field][0] and m[field]["unit"] == oh.TRANSFER[field][1]
    for other in set(oh.TRANSFER) - {field}:
        assert m[other]["classification"] == ms.UNKNOWN and m[other]["value"] is None
        assert other in m[other]["reason"]


@pytest.mark.parametrize("missing", [None, "", "  "])
def test_a_missing_figure_is_unknown_never_zero(missing):
    m = oh.transfer(execution(input_bytes=missing, transfer_seconds=missing))
    assert all(x["value"] is None and x["classification"] == ms.UNKNOWN for x in m.values())


def test_a_recorded_zero_is_a_measured_zero():
    m = oh.transfer(execution(reads=0, writes=0, transfer_seconds=0.0))
    assert m["reads"]["classification"] == ms.MEASURED and m["reads"]["value"] == 0
    assert m["transfer_seconds"]["value"] == 0.0 and m["input_bytes"]["value"] is None


def test_bytes_and_operations_are_separate_measurements():
    m = oh.transfer(full())
    assert {x["metric"] for x in m.values()} == {"execution_transfer_seconds", "execution_bytes_read",
                                                  "execution_bytes_written", "onelake_read_operations",
                                                  "onelake_write_operations"}
    assert len({x["id"] for x in m.values()}) == 5
    assert m["input_bytes"]["value"] == 2048 and m["output_bytes"]["value"] == 1024
    assert m["reads"]["value"] == 3 and m["writes"]["value"] == 1


@pytest.mark.parametrize("field,value,words", [
    ("input_bytes", -1, "input_bytes can't be negative"),
    ("transfer_seconds", -0.5, "transfer_seconds can't be negative"),
    ("transfer_seconds", math.nan, "transfer_seconds must be a finite number"),
    ("output_bytes", math.inf, "output_bytes must be a finite number"),
    ("writes", -math.inf, "writes must be a finite number"),
    ("reads", 2.5, "reads is a count"),
    ("reads", "3", "reads must be a number"),
    ("writes", True, "writes must be a number"),
])
def test_bad_values_are_refused_naming_the_field(field, value, words):
    with pytest.raises(ValueError, match=words):
        oh.transfer(execution(**{field: value}))


def test_collection_time_falls_back_and_can_be_given():
    m = oh.transfer(execution(finished_at=None, created_at="2026-11-10T02:00:00Z", reads=1))
    assert m["reads"]["collected_at"].startswith("2026-11-10T02:00")
    m = oh.transfer(execution(reads=1), collected_at="2026-11-12T00:00:00Z", source="Imported run log")
    assert m["reads"]["collected_at"].startswith("2026-11-12") and m["reads"]["source"] == "Imported run log"
    with pytest.raises(ValueError, match="no finished_at or created_at"):
        oh.transfer(execution(finished_at=None, reads=1))
    oh.transfer(execution(finished_at=None))  # nothing recorded, nothing to date


def test_an_execution_needs_its_id():
    with pytest.raises(ValueError, match="execution_id"):
        oh.transfer(execution(execution_id=""))


# ---------- one sync

def test_a_sync_reads_the_three_figures_optimize_records():
    s = oh.sync(sync_records())
    assert s["sync_id"] == "s1" and s["workload_id"] == "optimize.sync.local_executions"
    assert (s["seconds"]["value"], s["rows"]["value"], s["commits"]["value"]) == (2.0, 10, 1)
    assert {s[k]["classification"] for k in oh.SYNC} == {ms.MEASURED}
    assert s["seconds"]["period"]["start"].startswith("2026-11-10T00:59:58")


def test_a_sync_figure_it_didnt_record_is_unknown():
    s = oh.sync(sync_records(drop=("optimize_sync_commits",)))
    assert s["commits"]["classification"] == ms.UNKNOWN and s["commits"]["value"] is None
    assert "optimize_sync_commits" in s["commits"]["reason"]


def test_a_sync_reads_rows_as_the_lakehouse_returns_them():
    rows = [dict(r, workload_key=r["workload_id"], workload_id=None) for r in sync_records()]
    assert oh.sync(rows)["workload_id"] == "optimize.sync.local_executions"


def test_bad_sync_records_are_refused():
    with pytest.raises(ValueError, match="needs the measurements"):
        oh.sync([])
    with pytest.raises(ValueError, match="same run_id"):
        oh.sync(sync_records(sync_id="a")[:1] + sync_records(sync_id="b")[1:])
    with pytest.raises(ValueError, match="two different optimize_sync_rows"):
        oh.sync(sync_records() + sync_records(rows=11)[1:2])
    bad = sync_records()
    bad[0] = dict(bad[0], value=-1.0)
    with pytest.raises(ValueError, match="optimize_sync_seconds can't be negative"):
        oh.sync(bad)
    bad[0] = dict(bad[0], value=math.nan)
    with pytest.raises(ValueError, match="optimize_sync_seconds must be a finite number"):
        oh.sync(bad)
    bad = sync_records()
    bad[1] = dict(bad[1], unit=ms.SECONDS)
    with pytest.raises(ValueError, match="optimize_sync_rows is in count"):
        oh.sync(bad)
    bad = sync_records()
    bad[0] = dict(bad[0], classification=ms.SCENARIO)
    with pytest.raises(ValueError, match="measured or estimated"):
        oh.sync(bad)


# ---------- sharing one sync between the runs it carried

def test_a_sync_of_one_run_is_all_that_runs_as_measured():
    (share,) = oh.share_sync(oh.sync(sync_records()), {"e1": 10})
    assert share["method"] == oh.WHOLE and share["seconds"]["classification"] == ms.MEASURED
    assert (share["seconds"]["value"], share["rows"]["value"], share["commits"]["value"]) == (2.0, 10, 1)


def test_seconds_are_shared_by_rows_and_add_up_exactly():
    shares = oh.share_sync(oh.sync(sync_records(seconds=1.0, rows=3)), {"a": 1, "b": 1, "c": 1})
    assert [s["execution_id"] for s in shares] == ["a", "b", "c"]
    assert sum(round(s["seconds"]["value"] * 1_000_000) for s in shares) == 1_000_000
    assert sorted(s["seconds"]["value"] for s in shares) == [0.333333, 0.333333, 0.333334]
    for s in shares:
        assert s["method"] == oh.BY_ROWS and s["seconds"]["classification"] == ms.ESTIMATED
        assert "shared by rows" in s["seconds"]["method"] and s["seconds"]["assumptions"]
        assert s["rows"]["classification"] == ms.MEASURED and s["rows"]["value"] == 1
        assert s["commits"]["value"] is None and "isn't split" in s["commits"]["reason"]


def test_shares_follow_the_rows_each_run_sent():
    shares = {s["execution_id"]: s for s in oh.share_sync(oh.sync(sync_records(seconds=4.0, rows=4)),
                                                          {"a": 3, "b": 1, "c": 0})}
    assert (shares["a"]["seconds"]["value"], shares["b"]["seconds"]["value"], shares["c"]["seconds"]["value"]) == (
        3.0, 1.0, 0.0)


def test_shares_always_add_up_to_the_sync():
    rng = random.Random(202)
    for _ in range(300):
        weights = {f"e{i}": rng.randint(0, 50) for i in range(rng.randint(2, 9))}
        if not sum(weights.values()):
            weights["e0"] = 1
        seconds = round(rng.uniform(0, 500), 6)
        shares = oh.share_sync(oh.sync(sync_records(seconds=seconds, rows=sum(weights.values()))), weights)
        assert sum(round(s["seconds"]["value"] * 1_000_000) for s in shares) == round(seconds * 1_000_000)
        assert round(sum(s["seconds"]["value"] for s in shares), 6) == seconds
        assert sum(s["rows"]["value"] for s in shares) == sum(weights.values())
        assert all(s["seconds"]["value"] >= 0 for s in shares)


def test_sharing_is_refused_rather_than_guessed():
    s = oh.sync(sync_records(rows=10))
    with pytest.raises(ValueError, match="add up to 9 but the sync wrote 10"):
        oh.share_sync(s, {"a": 4, "b": 5})
    with pytest.raises(ValueError, match="add up to 3 but the sync wrote 10"):
        oh.share_sync(s, {"a": 3})  # one run can't take a sync that also carried other rows
    with pytest.raises(ValueError, match="rows weren't recorded"):
        oh.share_sync(oh.sync(sync_records(drop=("optimize_sync_rows",))), {"a": 1, "b": 1})
    with pytest.raises(ValueError, match="wrote no rows"):
        oh.share_sync(oh.sync(sync_records(rows=0)), {"a": 0, "b": 0})
    with pytest.raises(ValueError, match="rows of b can't be negative"):
        oh.share_sync(s, {"a": 11, "b": -1})
    with pytest.raises(ValueError, match="rows of b must be given"):
        oh.share_sync(s, {"a": 10, "b": None})
    with pytest.raises(ValueError, match="at least one run"):
        oh.share_sync(s, {})


def test_unknown_sync_seconds_stay_unknown_in_every_share():
    shares = oh.share_sync(oh.sync(sync_records(drop=("optimize_sync_seconds",))), {"a": 4, "b": 6})
    assert all(s["seconds"]["value"] is None and s["seconds"]["classification"] == ms.UNKNOWN for s in shares)
    (one,) = oh.share_sync(oh.sync(sync_records(drop=("optimize_sync_rows",))), {"a": 4})
    assert one["rows"]["value"] is None and one["seconds"]["value"] == 2.0


# ---------- one run's overhead

def test_a_run_with_nothing_recorded_is_unknown_throughout():
    out = oh.per_execution(execution())
    assert out["complete"] is False and set(out["unknown"]) == set(out["measurements"])
    assert all(m["value"] is None for m in out["measurements"].values())
    assert out["measurements"]["sync_seconds"]["reason"] == "No sync of this run has been recorded"
    assert out["overhead"]["classification"] == ms.UNKNOWN and out["overhead"]["value"] is None
    assert "doesn't measure" in out["overhead"]["reason"] and out["overhead_known"] is False


def test_a_fully_recorded_run_has_every_figure_but_still_no_cu():
    share = oh.share_sync(oh.sync(sync_records()), {"e1": 10})
    out = oh.per_execution(full(), share)
    assert out["complete"] and out["unknown"] == []
    assert out["measurements"]["sync_seconds"]["value"] == 2.0 and out["measurements"]["sync_commits"]["value"] == 1
    assert out["sync_ids"] == ["s1"] and out["sync_methods"] == [oh.WHOLE]
    # Every figure is known, but none of them is Fabric CU, and none is turned into it.
    assert out["overhead"]["value"] is None and not out["overhead_known"]


def test_a_run_synced_twice_adds_both_syncs():
    a = oh.share_sync(oh.sync(sync_records(sync_id="s1", seconds=2.0, rows=10)), {"e1": 10})
    b = oh.share_sync(oh.sync(sync_records(sync_id="s2", seconds=1.0, rows=4)), {"e1": 2, "e2": 2})
    out = oh.per_execution(full(), a + [s for s in b if s["execution_id"] == "e1"])
    m = out["measurements"]
    assert m["sync_seconds"]["value"] == 2.5 and m["sync_seconds"]["classification"] == ms.ESTIMATED
    assert m["sync_rows"]["value"] == 12
    assert m["sync_commits"]["complete"] is False and "sync_commits" in out["unknown"]
    assert out["sync_methods"] == [oh.BY_ROWS, oh.WHOLE]


def test_shares_must_be_for_this_run_and_not_repeated():
    share = oh.share_sync(oh.sync(sync_records()), {"e9": 10})
    with pytest.raises(ValueError, match="isn't for run e1"):
        oh.per_execution(full(), share)
    share = oh.share_sync(oh.sync(sync_records()), {"e1": 10})
    with pytest.raises(ValueError, match="same sync is given twice"):
        oh.per_execution(full(), share + share)


def test_a_supplied_cu_overhead_is_used_as_given():
    out = oh.per_execution(full(), cu_overhead=cu(0.25))
    assert out["overhead"]["value"] == 0.25 and out["overhead"]["classification"] == ms.MEASURED
    assert out["overhead_known"]
    est = oh.per_execution(full(), cu_overhead=cu(0.1, ms.ESTIMATED))["overhead"]
    assert est["classification"] == ms.ESTIMATED and est["assumptions"]


def test_a_bad_cu_overhead_is_refused():
    with pytest.raises(ValueError, match="CU-seconds or CU-hours"):
        oh.per_execution(full(), cu_overhead=ms.make(ms.MEASURED, "x", ms.SECONDS, 1.0, source="s",
                                                     collected_at=WHEN))
    with pytest.raises(ValueError, match="scenario"):
        oh.per_execution(full(), cu_overhead=ms.make(ms.SCENARIO, "x", ms.CU_HOURS, 1.0, method="m",
                                                     assumptions=["a"]))
    with pytest.raises(ValueError, match="for run e2, not e1"):
        oh.per_execution(full(), cu_overhead=cu(0.1, run_id="e2"))
    with pytest.raises(ValueError, match="Not a valid measurement"):
        oh.per_execution(full(), cu_overhead=dict(cu(0.1), value=-1.0))


def test_nothing_built_in_turns_operations_or_bytes_into_cu():
    big = execution(input_bytes=10**12, output_bytes=10**12, reads=10**6, writes=10**6, transfer_seconds=3600.0)
    assert oh.per_execution(big)["overhead"]["value"] is None


# ---------- with the attribution waterfall

def _attribute(overhead: dict) -> dict:
    run = {"execution_id": "e1", "workload_key": "nb:sales", "baseline_version": "b1", "at": "2026-11-10T00:00:00Z",
           "ran": at.PC}
    baseline = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, 10, source="Capacity Metrics export",
                       collected_at="2026-11-01T00:00:00Z")
    avoided = ms.make(ms.ESTIMATED, "fabric_job_cu_avoided", ms.CU_HOURS, 10, method="calibration",
                      assumptions=["median of 5 Fabric runs"])
    return at.attribute(run, baseline, local_avoided=avoided, overhead=overhead)


def test_unknown_overhead_keeps_net_incomplete():
    share = oh.share_sync(oh.sync(sync_records()), {"e1": 10})
    result = _attribute(oh.per_execution(full(), share)["overhead"])
    assert result["gross_cu_hours"] == 10 and result["net_cu_hours"] == 10
    assert result["overhead_cu_hours"] is None
    assert result["complete"] is False and result["net_includes_overhead"] is False


def test_a_measured_cu_overhead_reduces_net():
    result = _attribute(oh.per_execution(full(), cu_overhead=cu(0.25))["overhead"])
    assert result["gross_cu_hours"] == 10 and result["overhead_cu_hours"] == 0.25
    assert result["net_cu_hours"] == 9.75 and result["net_includes_overhead"] and result["complete"]
    assert result["net_classification"] == ms.ESTIMATED  # as weak as the calibrated avoided figure


def test_a_cu_overhead_in_cu_seconds_converts_in_the_waterfall():
    m = ms.make(ms.MEASURED, "onelake_operations_cu", ms.CU_SECONDS, 1800, run_id="e1",
                source="Capacity Metrics export", collected_at="2026-11-11T00:00:00Z")
    assert _attribute(oh.per_execution(full(), cu_overhead=m)["overhead"])["net_cu_hours"] == 9.5
