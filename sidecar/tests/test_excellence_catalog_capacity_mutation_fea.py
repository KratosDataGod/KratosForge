"""Mutation batch 261: excellence/catalog_capacity.py (docs/quality/mutation-testing.md). The survivors were
the thresholds at their exact values (10% of CU failed, 24 refreshes a day, a 10% utilization, 7 days of
Compute-page periods, a window of one day), which rows and periods count (a row with no start, a CU that is
negative, a flag or text, a period that ends where it starts, two periods that only touch), an F0 SKU, the
not-evaluated answers when no CU was used or there are no operations, the evidence's rounding, import
names and order and the top 20 refresh days, and the wording: CU-hours, the window, an unknown SKU or
state, and the benefit's numbers."""

from __future__ import annotations

import datetime as dt

from fabric_desktop.excellence import catalog_capacity as cc
from fabric_desktop.excellence import controls as ct

AT = "2026-11-18T02:00:00+00:00"
CAP = "cccccccc-2222-3333-4444-555555555555"
WS = "Sales"
START = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)


def op(item: str, cu: object, start: str | None, *, status: str | None = "Success", operation: str | None = "Run",
       end: str | None = None, workspace: str = WS) -> dict:
    return {"workspace": workspace, "item": item, "operation": operation, "status": status, "start": start, "end": end,
            "cu": {"value": cu}}


def ops_import(rows: list[dict], id_: str = "u-1", **extra: object) -> dict:
    return {"id": id_, "provider": "metrics_app", "layout": "operations", "file_name": "ops.csv", "rows": rows, **extra}


def items_import(cu: float, days: float, *, offset_days: float = 0, id_: str = "u-2") -> dict:
    start = START + dt.timedelta(days=offset_days)
    return {"id": id_, "provider": "metrics_app", "layout": "items", "file_name": "items.csv",
            "period": {"start": start.isoformat(), "end": (start + dt.timedelta(days=days)).isoformat()},
            "rows": [{"workspace": WS, "item": "nb", "cu": {"value": cu}}]}


def usage(*imports: dict) -> dict:
    recs = cc.usage(CAP, "Prod", WS, list(imports), collected_at=AT)
    assert len(recs) == 1
    return recs[0]


def ctx(u: dict | None, sku: str = "F2", state: str = "Active") -> ct.Context:
    return ct.Context({"type": "capacity", "id": CAP},
                      {"capacity.state": [{"id": "ev-state", "payload": {"sku": sku, "state": state}}],
                       cc.KIND: [] if u is None else [u]})


def refreshes(item: str, n: int, day: str = "2026-10-31") -> list[dict]:
    return [op(item, 1.0, f"{day}T{i % 24:02d}:{(i // 24) * 30:02d}:00+00:00", operation="Dataset Refresh")
            for i in range(n)]


# ---- what counts


def test_a_cu_must_be_a_number_at_least_zero_and_not_a_flag():
    assert cc._cu({"cu": {"value": 0}}) == 0.0
    assert cc._cu({"cu": {"value": 0.5}}) == 0.5
    for bad in (-1, -0.1, True, False, "12", None):
        assert cc._cu({"cu": {"value": bad}}) is None, bad


def test_a_row_without_a_start_or_a_cu_is_left_out():
    u = usage(ops_import([op("a", 5.0, "2026-10-31T01:00:00+00:00"), op("b", 7.0, None),
                          op("c", "x", "2026-10-31T02:00:00+00:00")]))
    ops = u["payload"]["operations"]
    assert ops["rows"] == 1 and ops["cu_seconds"] == 5.0


def test_cu_is_kept_to_three_places():
    u = usage(ops_import([op("a", 1.23456, "2026-10-31T01:00:00+00:00")]),
              items_import(2.34567, 7))
    assert u["payload"]["operations"]["cu_seconds"] == 1.235
    assert u["payload"]["operations"]["cu_by_status"] == {"success": 1.235}
    assert u["payload"]["items"][0]["cu_seconds"] == 2.346


def test_imports_are_named_and_listed_by_id():
    u = usage(items_import(1.0, 7, id_="u-9"),
              ops_import([op("a", 1.0, "2026-10-31T01:00:00+00:00"), op("b", 1.0, None)], id_="u-3"))
    assert [(i["id"], i["file_name"], i["layout"], i["rows"]) for i in u["payload"]["imports"]] == [
        ("u-3", "ops.csv", "operations", 2), ("u-9", "items.csv", "items", 1)]
    assert u["payload"]["items"][0]["import_id"] == "u-9"


def test_a_period_that_ends_where_it_starts_or_before_or_has_no_cu_is_left_out():
    assert cc._items(items_import(5.0, 0)) is None
    assert cc._items(items_import(5.0, -1)) is None
    assert cc._items({**items_import(5.0, 7), "rows": [{"cu": {"value": -2}}]}) is None
    assert cc._items({**items_import(5.0, 7), "period": {"start": START.isoformat()}}) is None
    assert cc._items(items_import(5.0, 7))["seconds"] == 7 * 86400


def test_refresh_days_keep_the_top_twenty_ties_by_name_with_their_workspace():
    rows = [r for i in reversed(range(21)) for r in refreshes(f"m{i:02d}", 1)]
    got = usage(ops_import(rows))["payload"]["operations"]["refreshes_per_day"]
    assert len(got) == 20
    assert [g["item"] for g in got] == [f"m{i:02d}" for i in range(20)]
    assert got[0] == {"workspace": WS, "item": "m00", "day": "2026-10-31", "count": 1}


# ---- FEA-CAP-002 failed operations


def test_no_cu_used_is_not_evaluated_and_a_little_is_a_share():
    zero = usage(ops_import([op("a", 0, "2026-10-31T01:00:00+00:00", status="Failure")]))
    got = cc._failed_work(ctx(zero))
    assert got["outcome"] == "not_evaluated" and got["text"].startswith("The imported operations used no CU")
    half = usage(ops_import([op("a", 0.5, "2026-10-31T01:00:00+00:00", status="Failure")]))
    assert cc._failed_work(ctx(half))["outcome"] == "fail"


def test_exactly_ten_percent_failed_fails_with_its_hours_and_window():
    u = usage(ops_import([op("a", 32400.0, "2026-10-30T01:00:00+00:00"),
                          op("b", 3600.0, "2026-10-31T02:00:00+00:00", status="Failure",
                             end="2026-11-01T03:00:00+00:00")]))
    got = cc._failed_work(ctx(u))
    assert got["outcome"] == "fail"
    assert got["text"] == ("Failed operations used 1.0 CU-hours, 10% of the 10.0 CU-hours the imported operations "
                           "used (2026-10-30 to 2026-11-01).")
    assert got["benefit"].startswith("Up to 1.0 CU-hours of capacity freed")


def test_the_failure_share_is_kept_to_four_places():
    u = usage(ops_import([op("a", 2.22222, "2026-10-31T01:00:00+00:00"),
                          op("b", 1.11111, "2026-10-31T02:00:00+00:00", status="Failed")]))
    got = cc._failed_work(ctx(u))
    assert got["detail"]["failed_cu_seconds"] == 1.111 and got["detail"]["cu_seconds"] == 3.333
    assert got["detail"]["share"] == 0.3333


def test_hours_are_cu_seconds_over_3600():
    assert cc._hours(36_000_000) == "10,000.0 CU-hours"


# ---- FEA-CAP-003 refreshes


def test_no_operations_leave_refreshes_not_evaluated():
    got = cc._refreshes(ctx(usage(items_import(1.0, 7))))
    assert got["outcome"] == "not_evaluated" and got["needs"] == cc.NEEDS_OPS


def test_a_day_of_exactly_24_refreshes_is_not_counted_and_the_benefit_counts_the_extra():
    rows = refreshes("sales", 30) + refreshes("stock", 24) + refreshes("sales", 1, day="2026-11-01")
    got = cc._refreshes(ctx(usage(ops_import(rows))))
    assert got["outcome"] == "fail"
    assert got["text"] == "An item refreshed more than 24 times in one day, 1 time: sales (30 on 2026-10-31)."
    assert got["benefit"].startswith("Refreshing sales hourly instead would leave out about 6 refreshes that day.")


def test_a_fail_names_five_items_and_counts_every_day():
    rows = [r for i in range(6) for r in refreshes(f"m{i}", 25)]
    got = cc._refreshes(ctx(usage(ops_import(rows))))
    assert got["text"].startswith("An item refreshed more than 24 times in one day, 6 times: m0 (25 on 2026-10-31), ")
    assert "m4 (25 on 2026-10-31)." in got["text"] and "m5" not in got["text"]


def test_a_window_of_exactly_one_day_is_enough_to_pass():
    rows = [op("a", 1.0, "2026-10-30T00:00:00+00:00", operation="Dataset Refresh"),
            op("a", 1.0, "2026-10-31T00:00:00+00:00", operation="Dataset Refresh")]
    got = cc._refreshes(ctx(usage(ops_import(rows))))
    assert got["outcome"] == "pass"
    assert got["text"] == "No item refreshed more than 24 times on any day of the imported window (2026-10-30 to 2026-10-31)."


# ---- FEA-CAP-004 utilization


def test_an_f0_or_an_unnamed_sku_does_not_apply():
    got = cc._utilization(ctx(usage(items_import(1.0, 14)), sku="F0"))
    assert got["outcome"] == "not_applicable" and got["text"] == "Not a paid F SKU (F0)."
    assert cc._utilization(ctx(None, sku=""))["text"] == "Not a paid F SKU (size not given)."


def test_a_capacity_in_no_known_state_does_not_apply():
    got = cc._utilization(ctx(None, state=""))
    assert got == {"outcome": "not_applicable", "evidence_ids": [],
                   "text": "The capacity is in an unknown state, not running (FEA-CAP-001)."}


def test_exactly_seven_days_is_enough_and_less_says_how_many_days():
    assert cc._utilization(ctx(usage(items_import(1.0, 7))))["outcome"] == "fail"
    got = cc._utilization(ctx(usage(items_import(1.0, 6.75))))
    assert got["outcome"] == "not_evaluated"
    assert got["text"] == "The imported Compute-page periods cover 6.8 days, less than 7."


def test_two_periods_that_only_touch_both_count():
    u = usage(items_import(1.0, 4, id_="u-2"), items_import(1.0, 4, offset_days=4, id_="u-3"))
    got = cc._utilization(ctx(u))
    assert got["outcome"] == "fail" and len(got["detail"]["periods"]) == 2
    assert got["text"].startswith("Over 8 days imported,")
    # the longer period is kept first, so the shorter one before it is checked against its start
    u = usage(items_import(1.0, 3, id_="u-2"), items_import(1.0, 5, offset_days=3, id_="u-3"))
    got = cc._utilization(ctx(u))
    assert got["outcome"] == "fail" and len(got["detail"]["periods"]) == 2


def test_exactly_ten_percent_used_passes():
    seconds = 7 * 86400
    got = cc._utilization(ctx(usage(items_import(2 * seconds * 0.1, 7))))
    assert got["outcome"] == "pass" and got["text"] == "Items used 10% of the F2's capacity over 7 days imported."


def test_low_utilization_wording_rounding_and_benefit():
    got = cc._utilization(ctx(usage(items_import(12345.6789, 7.5))))
    seconds = 7.5 * 86400
    available = 2 * seconds
    assert got["outcome"] == "fail"
    assert got["text"].startswith("Over 8 days imported, items used 3.4 CU-hours, which is 1.0% of the 360.0 CU-hours "
                                  "an F2 provides.")
    assert got["benefit"].startswith(f"About {(available - 12345.6789) / 3600:,.1f} CU-hours of capacity unused")
    assert got["detail"]["cu_seconds"] == 12345.679
    assert got["detail"]["available_cu_seconds"] == round(available, 3)
    assert got["detail"]["share"] == round(12345.6789 / available, 4)
