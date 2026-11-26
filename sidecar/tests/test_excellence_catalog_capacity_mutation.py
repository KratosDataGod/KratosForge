"""Mutation batch 358: catalog_capacity.py's sums, rounding, cut-offs and exact texts, which
tests/test_excellence_capacity.py checks through whole assessments."""

from __future__ import annotations

import datetime as dt

from fabric_desktop.excellence import catalog_capacity as cc
from fabric_desktop.excellence import controls as ct

AT = "2026-11-18T02:00:00+00:00"
WS = "Sales"


def op(cu, start="2026-10-30T00:00:00+00:00", *, status="Success", operation="Notebook Run", item="nb", end=None):
    return {"workspace": WS, "item": item, "operation": operation, "status": status, "start": start, "end": end,
            "cu": {"value": cu}}


def items_doc(cu, days, *, start=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc), id_="u-2"):
    return {"id": id_, "provider": "metrics_app", "layout": "items", "file_name": "items.csv",
            "period": {"start": start.isoformat(), "end": (start + dt.timedelta(days=days)).isoformat()},
            "rows": [{"workspace": WS, "item": "nb", "cu": {"value": cu}}]}


def ops_doc(rows, id_="u-1"):
    return {"id": id_, "provider": "metrics_app", "layout": "operations", "file_name": "ops.csv", "rows": rows}


def ctx(imports, sku="F2", state="Active"):
    records = cc.usage("cap", "Cap", WS, imports, collected_at=AT)
    ev = {"capacity.state": [{"id": "st", "payload": {"sku": sku, "state": state}}]}
    if records:
        ev[cc.KIND] = records
    return ct.Context({"id": "cap"}, ev)


def test_cu_values_must_be_non_negative_numbers():
    assert cc._cu({"cu": {"value": 0.5}}) == 0.5
    assert cc._cu({"cu": {"value": 0}}) == 0.0
    assert cc._cu({"cu": {"value": -1}}) is None
    assert cc._cu({"cu": {"value": True}}) is None
    assert cc._cu({"cu": {"value": "7"}}) is None
    assert cc._cu({}) is None


def test_operations_skip_a_row_without_cu_and_round_to_three_places():
    got = cc._operations([op(None), op(0.12345, status="Failed"), op(0.0004)])
    assert got["rows"] == 2
    assert got["cu_seconds"] == 0.124
    assert got["cu_by_status"] == {"failed": 0.123, "success": 0.0}


def test_operations_keep_the_top_twenty_refresh_days_but_count_every_one_over():
    rows = []
    for d in range(21):
        day = (dt.date(2026, 10, 1) + dt.timedelta(days=d)).isoformat()
        rows += [op(1.0, f"{day}T{h:02d}:00:00+00:00", operation="Refresh") for h in range(1)]
    got = cc._operations(rows)
    assert len(got["refreshes_per_day"]) == 20


def test_items_need_a_period_that_ends_after_it_starts_and_some_cu():
    assert cc._items(items_doc(1.0, 0)) is None
    assert cc._items({**items_doc(1.0, 1), "rows": []}) is None
    got = cc._items(items_doc(0.12345, 1))
    assert got["cu_seconds"] == 0.123
    assert got["seconds"] == 86400.0


def test_usage_lists_each_import_it_used():
    rec = cc.usage("CAP", "Cap", WS, [ops_doc([op(1.0)], id_="u-9"), {**ops_doc([op(2.0)]), "id": None,
                                                                       "file_name": None, "layout": "operations"}],
                   collected_at=AT)[0]
    assert rec["payload"]["imports"] == [
        {"id": "", "file_name": "", "layout": "operations", "imported_at": None, "rows": 1},
        {"id": "u-9", "file_name": "ops.csv", "layout": "operations", "imported_at": None, "rows": 1}]
    no_rows = {"id": "u-3", "provider": "metrics_app", "layout": "items", "rows": None}
    assert cc.usage("CAP", "Cap", WS, [ops_doc([op(1.0)]), no_rows], collected_at=AT)[0]["payload"]["imports"][0][
        "rows"] == 1


def test_hours():
    assert cc._hours(3_600_000) == "1,000.0 CU-hours"


def test_failed_work_with_a_little_cu_is_worked_out():
    r = cc._failed_work(ctx([ops_doc([op(0.5, status="Failed", end="2026-10-31T05:00:00+00:00")])]))
    assert r["outcome"] == "fail"
    assert r["text"] == ("Failed operations used 0.0 CU-hours, 100% of the 0.0 CU-hours the imported operations used "
                         "(2026-10-30 to 2026-10-31).")


def test_failed_work_with_no_cu_is_not_evaluated():
    r = cc._failed_work(ctx([ops_doc([op(0.0)])]))
    assert r["outcome"] == "not_evaluated"
    assert r["text"] == "The imported operations used no CU, so there is no share to work out."


def test_failed_work_detail_rounds_share_to_four_places():
    r = cc._failed_work(ctx([ops_doc([op(1.23456, status="Failed"), op(2.0)])]))
    assert r["detail"]["failed_cu_seconds"] == 1.235
    assert r["detail"]["cu_seconds"] == 3.235
    assert r["detail"]["share"] == 0.3818


def test_refreshes_without_operations_are_not_evaluated():
    r = cc._refreshes(ctx([items_doc(1.0, 14)]))
    assert r["outcome"] == "not_evaluated"
    assert r["text"].startswith("The imported export has no operations")


def test_apart_keeps_periods_that_dont_overlap():
    a = cc._items(items_doc(1.0, 3, id_="a"))
    b = cc._items(items_doc(1.0, 3, start=dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc), id_="b"))
    assert [x["import_id"] for x in cc._apart([a, b])] == ["a", "b"]


def test_utilization_takes_f1_as_a_paid_size():
    r = cc._utilization(ctx([items_doc(1.0, 14)], sku="F1"))
    assert r["outcome"] == "fail"


def test_utilization_names_a_capacity_state():
    r = cc._utilization(ctx([items_doc(1.0, 14)], state="Paused"))
    assert r["text"] == "The capacity is Paused, not running (FEA-CAP-001)."


def test_utilization_counts_exactly_seven_days():
    assert cc._utilization(ctx([items_doc(1.0, 7)]))["outcome"] == "fail"


def test_utilization_rounds_days_and_detail():
    r = cc._utilization(ctx([items_doc(1.23456, 7.5)]))
    assert r["text"].startswith("Over 8 days imported, items used")
    assert r["detail"]["cu_seconds"] == 1.235
    assert r["detail"]["available_cu_seconds"] == 1296000.0
    big = cc._utilization(ctx([items_doc(123456.789, 7.5)]))
    assert big["detail"]["share"] == 0.0953


def refreshes(item, n, day="2026-10-31"):
    return [op(1.0, f"{day}T{i % 24:02d}:{(i // 24) * 30:02d}:00+00:00", operation="Dataset Refresh", item=item)
            for i in range(n)]


def test_refresh_days_name_their_workspace_and_item_and_tie_by_name():
    got = cc._operations(refreshes("b", 1) + refreshes("a", 1))
    assert got["refreshes_per_day"] == [{"workspace": WS, "item": "a", "day": "2026-10-31", "count": 1},
                                        {"workspace": WS, "item": "b", "day": "2026-10-31", "count": 1}]


def test_twenty_four_refreshes_in_a_day_arent_too_many():
    assert cc._operations(refreshes("a", 24))["refresh_days_over"] == 0
    assert cc._operations(refreshes("a", 25))["refresh_days_over"] == 1


def test_a_failed_share_of_exactly_ten_percent_fails():
    r = cc._failed_work(ctx([ops_doc([op(1.0, status="Failed")] + [op(1.0)] * 9)]))
    assert r["outcome"] == "fail"


def test_too_many_refreshes_names_five_days_and_the_hourly_saving():
    rows = []
    for k, item in enumerate("abcdef"):
        rows += refreshes(item, 30 + k)
    r = cc._refreshes(ctx([ops_doc(rows)]))
    assert r["text"] == ("An item refreshed more than 24 times in one day, 6 times: f (35 on 2026-10-31), "
                         "e (34 on 2026-10-31), d (33 on 2026-10-31), c (32 on 2026-10-31), b (31 on 2026-10-31).")
    assert r["benefit"].startswith("Refreshing f hourly instead would leave out about 11 refreshes that day.")


def test_operations_over_exactly_one_day_are_enough_to_pass():
    r = cc._refreshes(ctx([ops_doc([op(1.0, "2026-10-30T00:00:00+00:00"), op(1.0, "2026-10-31T00:00:00+00:00")])]))
    assert r["outcome"] == "pass"


def test_apart_keeps_periods_that_touch_in_either_order():
    start = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    early = cc._items(items_doc(1.0, 3, start=start, id_="early"))
    late = cc._items(items_doc(1.0, 3, start=start + dt.timedelta(days=3), id_="late"))
    assert [x["import_id"] for x in cc._apart([early, late])] == ["early", "late"]
    longer_late = cc._items(items_doc(1.0, 5, start=start + dt.timedelta(days=3), id_="late"))
    assert [x["import_id"] for x in cc._apart([early, longer_late])] == ["early", "late"]


def test_utilization_refuses_f0():
    assert cc._utilization(ctx([items_doc(1.0, 14)], sku="F0"))["outcome"] == "not_applicable"


def test_utilization_names_short_periods_in_tenths_of_days():
    r = cc._utilization(ctx([items_doc(1.0, 6.95005)]))
    assert r["text"] == "The imported Compute-page periods cover 7.0 days, less than 7."


def test_utilization_of_exactly_ten_percent_passes():
    # An F2 over 7 days provides 2 x 604,800 CU seconds; a tenth of that is used.
    assert cc._utilization(ctx([items_doc(120960.0, 7)]))["outcome"] == "pass"


def test_utilization_benefit_is_the_unused_capacity():
    r = cc._utilization(ctx([items_doc(3600.0, 7)]))
    assert r["benefit"].startswith("About 335.0 CU-hours of capacity unused over the period")


def test_available_cu_is_rounded_to_three_places():
    # A period that isn't a whole number of seconds: 7 days and 8.64 ms, on an F2.
    r = cc._utilization(ctx([items_doc(1.0, 7 + 8.64e-3 / 86400)]))
    assert r["detail"]["available_cu_seconds"] == 1209600.017
