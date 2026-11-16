"""Mutation-testing follow-up for opportunities/impact.py (docs/quality/mutation-testing.md, batch 40).

What an opportunity is worth: measured run time and imported CU per workload, the monthly figure
scaled from the measured period (never below one day), Unknown whenever a part isn't measured,
and a CU value that is a value, not a saving."""

import datetime as dt

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.opportunities import impact

NOW = dt.datetime(2026, 11, 15, 12, tzinfo=dt.timezone.utc)


def _row(key, ws, item, value, start, end=None):
    period = {"start": start} if end is None else {"start": start, "end": end}
    return {"key": key, "workspace": ws, "item": item, "cu": {"value": value, "period": period}}


def _w(item_id="i1", name="Load", ws_name="Sales", *, read=True, total=3600.0, days=10, runs=5, scheduled=4):
    return {"item_id": item_id, "workspace_id": "w1", "workspace_name": ws_name, "name": name,
            "runs": {"read": read, "seconds_total": total, "days": days, "runs": runs, "scheduled": scheduled}}


# ---- usage_by_item ----------------------------------------------------------------------------------

def test_usage_by_item_sums_and_spans_rows_and_names_files():
    imports = [{"file_name": "a.csv", "rows": [
        _row("r1", " Sales ", "LOAD", 10, "2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"),
        _row("r2", "sales", "load", 5.5, "2026-10-30T00:00:00Z", "2026-10-31T00:00:00Z")]},
               {"id": "imp-2", "rows": [_row("r3", "Sales", "Load", 1, "2026-11-05T00:00:00Z")]},
               {"rows": [_row("r4", "Sales", "Load", 2, "2026-11-03T00:00:00Z")]}]
    out = impact.usage_by_item(imports)
    assert list(out) == [("sales", "load")]
    got = out[("sales", "load")]
    assert got["cu_seconds"] == 18.5 and got["rows"] == 4
    assert got["start"] == dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc)
    # A row with no end ends at its start.
    assert got["end"] == dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc)
    assert got["files"] == {"a.csv", "imp-2", "an imported file"}


def test_usage_by_item_counts_a_row_once_and_skips_rows_without_a_value_or_time():
    row = _row("r1", "S", "L", 4, "2026-11-01T00:00:00Z")
    imports = [{"file_name": "a", "rows": [row, dict(row)]},
               {"file_name": "b", "rows": [row,
                                           _row("r2", "S", "L", None, "2026-11-01T00:00:00Z"),
                                           _row("r3", "S", "L", 7, None),
                                           {"key": "r4", "workspace": "S", "item": "L"},
                                           _row("r5", "S", "L", 0, "2026-11-02T00:00:00Z")]},
               {"file_name": "c", "rows": None}]
    got = impact.usage_by_item(imports)[("s", "l")]
    # r1 once, r5 (a measured 0 still counts as a row); r2 has no value, r3 no time, r4 no cu.
    assert got["cu_seconds"] == 4.0 and got["rows"] == 2 and got["files"] == {"a", "b"}


def test_a_skipped_row_does_not_use_up_its_key():
    # A row without a value isn't "seen": a later row with the same key and a value is counted.
    imports = [{"file_name": "a", "rows": [_row("r1", "S", "L", None, "2026-11-01T00:00:00Z"),
                                           _row("r1", "S", "L", 3, "2026-11-01T00:00:00Z")]}]
    assert impact.usage_by_item(imports)[("s", "l")]["cu_seconds"] == 3.0


# ---- current ------------------------------------------------------------------------------------------

def test_current_measures_run_time_and_cu():
    usage = {("sales", "load"): {"cu_seconds": 7200.0, "start": NOW - dt.timedelta(days=3), "end": NOW,
                                 "rows": 2, "files": {"b.csv", "a.csv"}}}
    c = impact.current(_w(), usage, NOW)
    assert (c["runs"], c["scheduled_runs"], c["days"]) == (5, 4, 10)
    s = c["run_seconds"]
    assert s["classification"] == ms.MEASURED and s["value"] == 3600.0 and s["source"] == impact.JOB_HISTORY
    assert s["method"] == "end minus start of each finished run in the last 10 days"
    assert s["period"]["start"] == (NOW - dt.timedelta(days=10)).isoformat() and s["workload_id"] == "i1"
    cu = c["cu"]
    assert cu["classification"] == ms.MEASURED and cu["value"] == 7200.0
    assert cu["source"] == "imported usage: a.csv, b.csv"
    assert cu["method"] == "sum of 2 imported row(s) matched by workspace and item name"


def test_current_unknowns():
    c = impact.current(_w(read=False), {}, NOW)
    assert c["run_seconds"]["value"] is None and c["run_seconds"]["reason"] == "The item's job history couldn't be read."
    assert c["cu"]["value"] is None and c["cu"]["reason"].startswith("No imported usage file has this item.")
    c = impact.current(_w(total=None), {}, NOW)
    assert c["run_seconds"]["reason"] == "Its runs have no end time yet."


def test_a_zero_run_total_is_measured_not_unknown():
    assert impact.current(_w(total=0.0), {}, NOW)["run_seconds"]["value"] == 0.0


# ---- _monthly / _days -----------------------------------------------------------------------------

def _m(value, start=None, end=None):
    if value is None:
        return ms.unknown("x", ms.SECONDS, "why")
    return ms.make(ms.MEASURED, "x", ms.SECONDS, value, source="s", collected_at=NOW, period_start=start,
                   period_end=end)


def test_monthly_scales_each_part_to_thirty_days():
    m = impact._monthly([_m(100), _m(60)], "y", ms.SECONDS, [10.0, 3.0], "run time", {"workload_id": "i1"})
    assert m["classification"] == ms.ESTIMATED and m["value"] == pytest.approx(100 / 10 * 30 + 60 / 3 * 30)
    assert m["method"] == "measured run time scaled to 30 days" and m["workload_id"] == "i1"
    assert m["assumptions"][0] == "the next 30 days run like the measured period"


def test_monthly_treats_a_period_under_a_day_as_a_day():
    assert impact._monthly([_m(10)], "y", ms.SECONDS, [0.25], "t", {})["value"] == 300.0
    assert impact._monthly([_m(10)], "y", ms.SECONDS, [1.0], "t", {})["value"] == 300.0
    assert impact._monthly([_m(10)], "y", ms.SECONDS, [2.0], "t", {})["value"] == 150.0


def test_monthly_is_unknown_when_any_part_is():
    m = impact._monthly([_m(100), _m(None)], "y", ms.SECONDS, [10.0, 10.0], "t", {})
    assert m["value"] is None and m["reason"] == "why"
    bare = dict(_m(None), reason="")
    assert impact._monthly([bare], "y", ms.SECONDS, [1.0], "t", {})["reason"] == "Not measured."


def test_days_of_a_measurement():
    start = NOW - dt.timedelta(days=4)
    assert impact._days(_m(1, start, NOW), 9.0) == 4.0
    # Under a day counts as one; exactly one day is one.
    assert impact._days(_m(1, NOW - dt.timedelta(hours=6), NOW), 9.0) == 1.0
    assert impact._days(_m(1, NOW - dt.timedelta(days=1), NOW), 9.0) == 1.0
    assert impact._days(_m(1, NOW - dt.timedelta(hours=36), NOW), 9.0) == 1.5
    # Without both ends, the fallback.
    assert impact._days(_m(1, start, None), 9.0) == 9.0
    assert impact._days(_m(1, None, NOW), 9.0) == 9.0


# ---- avoidable / per_workload --------------------------------------------------------------------

USAGE = {("sales", "load"): {"cu_seconds": 3600.0, "start": NOW - dt.timedelta(days=2), "end": NOW,
                             "rows": 1, "files": {"a.csv"}}}
PRICE = {"price_per_cu_hour": 0.5, "currency": "EUR", "source": "list price"}


def test_avoidable_whole_workload():
    out = impact.avoidable([_w()], USAGE, NOW, price=PRICE)
    assert out["basis"] == "whole workload" and out["realizable"] is False
    assert out["realizable_reason"] == impact.NOT_REALIZABLE
    assert out["run_seconds_per_month"]["value"] == 3600.0 / 10 * 30
    # CU is scaled by its own period (2 days), not the run history's (10).
    assert out["cu_per_month"]["value"] == 3600.0 / 2 * 30
    v = out["cu_value_per_month"]
    assert v["classification"] == ms.ESTIMATED and v["value"] == pytest.approx(15 * 0.5) and v["currency"] == "EUR"
    assert v["assumptions"][-1] == "price: list price; a price is not a charge that goes away"
    assert out["run_seconds_per_month"]["workload_id"] == "i1" and out["cu_per_month"]["workspace_id"] == "w1"
    assert out["performance"].startswith("Not measured.")


def test_avoidable_for_two_workloads_has_no_single_workload_id():
    out = impact.avoidable([_w(), _w("i2", "Other")], USAGE, NOW)
    assert out["run_seconds_per_month"]["workload_id"] is None
    assert out["run_seconds_per_month"]["workspace_id"] == "w1"
    assert out["run_seconds_per_month"]["value"] == pytest.approx(2 * 3600.0 / 10 * 30)
    # The second workload has no imported usage, so the CU total is Unknown, not partial.
    assert out["cu_per_month"]["value"] is None and len(out["current"]) == 2


def test_avoidable_part_of_a_workload_is_unknown():
    out = impact.avoidable([_w()], USAGE, NOW, partial_reason="Depends on how much changes.", price=PRICE)
    assert out["basis"] == "part of the workload"
    for k in ("run_seconds_per_month", "cu_per_month"):
        assert out[k]["value"] is None and out[k]["reason"] == "Depends on how much changes."
    assert out["cu_value_per_month"]["value"] is None


def test_value_needs_a_known_cu_a_price_and_a_currency():
    cu = ms.make(ms.ESTIMATED, "c", ms.CU_SECONDS, 7200, method="m", assumptions=["a"])
    assert impact.value(cu, None, {})["reason"].startswith("No price per CU hour is set")
    assert impact.value(cu, {"price_per_cu_hour": 0, "currency": "USD"}, {})["value"] is None
    assert impact.value(cu, {"price_per_cu_hour": 1, "currency": ""}, {})["value"] is None
    got = impact.value(cu, {"price_per_cu_hour": "2", "currency": "USD"}, {"workload_id": "i9"})
    assert got["value"] == 4.0 and got["workload_id"] == "i9"
    assert got["assumptions"] == ["a", "price: as configured; a price is not a charge that goes away"]
    assert got["method"] == "CU hours times the configured price per CU hour"
    unknown = ms.unknown("c", ms.CU_SECONDS, "no")
    assert impact.value(unknown, PRICE, {})["reason"] == "The CU figure isn't known."


def test_per_workload():
    out = impact.per_workload(_w(days=5), USAGE, NOW)
    assert out["run_seconds_per_month"]["value"] == 3600.0 / 5 * 30
    assert out["cu_per_month"]["value"] == 3600.0 / 2 * 30
    assert out["run_seconds_per_month"]["workload_id"] == "i1"
    # No usage: CU falls back to Unknown, run time stays estimated.
    out = impact.per_workload(_w(days=5), {}, NOW)
    assert out["cu_per_month"]["value"] is None and out["run_seconds_per_month"]["value"] == 21600.0
