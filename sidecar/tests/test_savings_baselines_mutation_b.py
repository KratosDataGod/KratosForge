"""Mutation-testing follow-ups for savings/baselines.py (batch 462): refusals of a figure with an
unknown classification, a run without its id and a min_runs below 1, the edges of zero CU, the
window and min_runs, ids of a fixed length, where a figure's source, period and collection time come
from, and how a change is worded and rounded."""
from __future__ import annotations

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import baselines as bl

WL, DEF, SCOPE = "ws-1/nb-sales", "def-v1", "lh-1:sales/2026"
START, END = "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z"


def cu(v, kind=ms.MEASURED, run_id="r", unit=ms.CU_HOURS, source="Capacity Metrics export",
       collected_at="2026-11-01T00:00:00Z"):
    if kind == ms.MEASURED:
        return ms.make(ms.MEASURED, "fabric_job_cu", unit, v, run_id=run_id, source=source, collected_at=collected_at)
    return ms.make(ms.ESTIMATED, "fabric_job_cu", unit, v, run_id=run_id, method="run time x configured CUs",
                   source=source, assumptions=["8 CUs configured"])


def fr(rid, v, day=10, kind=ms.MEASURED, month=10, at=None, **kw):
    r = {"run_id": rid, "workload_key": WL, "definition_version": DEF, "input_scope": SCOPE,
         "at": at or f"2026-{month:02d}-{day:02d}T09:00:00Z", "cu": cu(v, kind, rid, **kw), "ran": "fabric"}
    return r


def base(runs=None, **kw):
    runs = runs if runs is not None else [fr("a", 10, 3), fr("b", 12, 10), fr("c", 9, 20)]
    args = {"definition_version": DEF, "input_scope": SCOPE, "window_start": START, "window_end": END} | kw
    return bl.baseline(WL, runs, **args)


AFTERS = [fr("x", 6, 3, month=11), fr("y", 7, 5, month=11), fr("z", 5, 4, month=11)]


# ---- refusals


def test_a_figure_of_an_unknown_classification_is_refused():
    with pytest.raises(ValueError, match="unknown classification 'guess'"):
        bl.cu_of({"classification": "guess", "unit": ms.CU_HOURS, "value": 1.0}, "figure")


def test_a_run_without_its_id_is_refused():
    run = fr("a", 10)
    run["run_id"] = "  "
    with pytest.raises(ValueError, match="Every run needs its run_id"):
        base([run])


@pytest.mark.parametrize("bad", [0, -1, True])
def test_validate_refuses_a_min_runs_below_one(bad):
    with pytest.raises(ValueError, match="min_runs is a whole number"):
        bl.validate("opt-1", base(), AFTERS, outputs_match=True, min_runs=bad)


# ---- edges


@pytest.mark.parametrize("v", [0, 0.5])
def test_zero_and_fractional_cu_are_valid(v):
    assert bl.cu_of(cu(v), "figure") == (pytest.approx(v), ms.MEASURED)


def test_a_window_that_ends_where_it_starts_is_refused():
    with pytest.raises(ValueError, match="end must be after its start"):
        base(window_end=START)


def test_min_runs_of_one_uses_a_single_run():
    b = base([fr("a", 10)], min_runs=1)
    assert b["cu"]["value"] == pytest.approx(10.0) and b["row"]["runs"] == 1


def test_a_run_at_the_window_start_counts():
    b = base([fr("a", 10, at=START), fr("b", 12), fr("c", 9)])
    assert b["excluded"] == [] and b["row"]["runs"] == 3


# ---- ids


def test_ids_have_sixteen_characters_after_their_prefix():
    b = base()
    assert len(b["baseline_id"]) == 3 + 16 and len(b["baseline_version"]) == 3 + 16
    assert len(b["cu"]["id"]) == 2 + 16
    v = bl.validate("opt-1", b, AFTERS, outputs_match=True)
    assert len(v["row"]["after_measurement_id"]) == 2 + 16 and len(v["row"]["validation_id"]) == 3 + 16


# ---- sources, periods, times


def test_a_measured_baseline_names_its_runs_sources():
    assert base()["cu"]["source"] == "Capacity Metrics export"
    runs = [fr("a", 10, 3), fr("b", 12, 10), fr("c", 9, 20)]
    for r in runs:  # a figure read back without its source
        r["cu"]["source"] = ""
    assert base(runs)["cu"]["source"] == "Fabric runs of this workload"


def test_an_estimated_baseline_keeps_its_window_and_source():
    runs = [fr("a", 10, 3, ms.ESTIMATED), fr("b", 12, 10, ms.ESTIMATED), fr("c", 9, 20, ms.ESTIMATED)]
    got = base(runs)["cu"]
    assert got["classification"] == ms.ESTIMATED and got["source"] == "Capacity Metrics export"
    assert ms.parse_time(got["period"]["start"]) == ms.parse_time(START)
    assert ms.parse_time(got["period"]["end"]) == ms.parse_time(END)


def test_a_validation_is_dated_at_its_latest_after_run():
    v = bl.validate("opt-1", base(), AFTERS, outputs_match=True)
    assert ms.parse_time(v["row"]["at"]) == ms.parse_time("2026-11-05T09:00:00Z")


# ---- change


def test_an_unknown_side_is_named():
    known = cu(10)
    assert bl.change(None, known)["reason"] == "The before figure isn't known."
    assert bl.change(known, None)["reason"] == "The after figure isn't known."


def test_a_measured_change_names_both_sources_and_the_after_collection_time():
    before = cu(10, source="A", collected_at="2026-10-01T00:00:00Z")
    after = cu(8, source="B", collected_at="2026-11-02T00:00:00Z")
    got = bl.change(before, after)
    assert got["source"] == "A; B"
    assert ms.parse_time(got["collected_at"]) == ms.parse_time("2026-11-02T00:00:00Z")


def test_no_change_is_a_reduction_of_zero():
    got = bl.change(cu(10), cu(10))
    assert got["metric"] == bl.REDUCTION and got["direction"] == "none" and got["change_cu_hours"] == 0


def test_the_change_is_rounded_to_six_places():
    got = bl.change(cu(0), cu(1, unit=ms.CU_SECONDS))
    assert got["change_cu_hours"] == 0.000278
