"""excellence/catalog_capacity.py read directly (mutation testing, batch 376).

test_excellence_capacity.py runs the controls through an assessment. These pin what it leaves open: which
rows count, rounding, the record's list of imports, overlapping Compute-page periods, each threshold at its
exact value, and the exact wording of each control's answer.
"""

from __future__ import annotations

import pytest

from fabric_desktop.excellence import catalog_capacity as cc, controls as ct

AT = "2026-11-18T02:00:00+00:00"
DAY = 86400


def ctx(payload: dict | None, sku: str = "F2", state: str = "Active") -> ct.Context:
    usage = [] if payload is None else [{"id": "u1", "payload": payload}]
    return ct.Context({"type": "capacity", "id": "cap"},
                      {"capacity.state": [{"id": "s1", "payload": {"sku": sku, "state": state}}], cc.KIND: usage})


def ops(**kw) -> dict:
    base = {"rows": 2, "cu_seconds": 100.0, "status_rows": 2, "cu_by_status": {"success": 100.0}, "operation_rows": 2,
            "window": {"start": "2026-10-30T00:00:00+00:00", "end": "2026-11-01T00:00:00+00:00"},
            "refreshes_per_day": [], "refresh_days_over": 0}
    base.update(kw)
    return {"operations": base, "items": []}


def period(start_day: int, days: float, cu: float = 0.0, id_: str = "i") -> dict:
    start = DAY * start_day
    return {"import_id": id_, "period": {"start": f"p{start:012.0f}", "end": f"p{start + days * DAY:012.0f}"},
            "seconds": days * DAY, "cu_seconds": cu}


def row(cu=1.0, start="2026-10-31T01:00:00+00:00", **kw) -> dict:
    return {"workspace": "Sales", "item": "nb", "cu": {"value": cu}, "start": start, **kw}


# ---- reading the imports


@pytest.mark.parametrize("value,want", [(0, 0.0), (0.5, 0.5), (3, 3.0), (-1, None), (True, None), ("5", None),
                                        (None, None)])
def test_a_cu_value_counts_only_as_a_number_of_zero_or_more(value, want):
    assert cc._cu({"cu": {"value": value}}) == want


def test_an_operation_counts_only_with_both_cu_and_a_start():
    got = cc._operations([row(), row(start=None), {"start": "2026-10-31T02:00:00+00:00"}, row(cu=0.12345)])
    assert got["rows"] == 2 and got["cu_seconds"] == 1.123


def test_operations_round_cu_to_three_places():
    got = cc._operations([row(cu=0.12345, status="Failure"), row(cu=1.00049, status="Failure")])
    assert got["cu_seconds"] == 1.124 and got["cu_by_status"] == {"failure": 1.124}


def test_refreshes_are_counted_per_workspace_item_and_day():
    rows = ([row(item="b", operation="Refresh") for _ in range(2)] + [row(item="a", operation="Refresh")
            for _ in range(2)] + [row(item="a", operation="Refresh", workspace="Ops")])
    got = cc._operations(rows)["refreshes_per_day"]
    assert got == [{"workspace": "Sales", "item": "a", "day": "2026-10-31", "count": 2},
                   {"workspace": "Sales", "item": "b", "day": "2026-10-31", "count": 2},
                   {"workspace": "Ops", "item": "a", "day": "2026-10-31", "count": 1}]


def test_the_twenty_busiest_refresh_days_are_kept_and_days_over_counted_first():
    rows = [row(item=f"m{i:02d}", operation="Refresh") for i in range(21)]
    rows += [row(item="big", operation="Refresh") for _ in range(24)]
    got = cc._operations(rows)
    assert len(got["refreshes_per_day"]) == 20 and got["refreshes_per_day"][0]["item"] == "big"
    assert got["refresh_days_over"] == 0
    got = cc._operations(rows + [row(item="big", operation="Refresh")])
    assert got["refresh_days_over"] == 1


def item_doc(start, end, rows, id_="u-2"):
    return {"id": id_, "period": {"start": start, "end": end}, "rows": rows}


def test_a_compute_page_import_needs_a_period_that_ends_after_it_starts_and_some_cu():
    s, e = "2026-10-01T00:00:00+00:00", "2026-10-08T00:00:00+00:00"
    good = [{"cu": {"value": 1.23456}}, {"cu": {"value": "x"}}]
    assert cc._items(item_doc(s, e, good)) == {"import_id": "u-2", "period": {"start": s, "end": e},
                                               "seconds": 7 * DAY, "rows": 1, "cu_seconds": 1.235}
    assert cc._items(item_doc(None, e, good)) is None
    assert cc._items(item_doc(s, None, good)) is None
    assert cc._items(item_doc(s, s, good)) is None
    assert cc._items(item_doc(e, s, good)) is None
    assert cc._items(item_doc(s, e, [{"cu": {"value": None}}])) is None


def test_the_record_lists_its_imports_in_id_order():
    rows = [row(operation="Refresh", status="Success")]
    docs = [{"id": "u-9", "provider": "metrics_app", "layout": "operations", "file_name": "b.csv", "rows": rows,
             "imported_at": AT},
            {"id": "u-1", "provider": "metrics_app", "layout": "operations", "file_name": "a.csv", "rows": rows * 3}]
    rec = cc.usage("CAP", "Prod", "sales", docs, collected_at=AT)[0]
    assert rec["payload"]["imports"] == [
        {"id": "u-1", "file_name": "a.csv", "layout": "operations", "imported_at": None, "rows": 3},
        {"id": "u-9", "file_name": "b.csv", "layout": "operations", "imported_at": AT, "rows": 1}]


def test_compute_page_periods_that_overlap_count_once_and_touching_ones_both_count():
    a, inside, after, before = period(10, 10, id_="a"), period(12, 5, id_="in"), period(20, 4, id_="after"), \
        period(5, 5, id_="before")
    assert [x["import_id"] for x in cc._apart([inside, after, before, a])] == ["before", "a", "after"]


# ---- FEA-CAP-002 failed work


def test_failed_work_wording_and_detail():
    got = cc._failed_work(ctx(ops(cu_by_status={"failure": 3600.12345, "success": 3600.0})))
    assert got["text"] == ("Failed operations used 1.0 CU-hours, 50% of the 2.0 CU-hours the imported operations "
                           "used (2026-10-30 to 2026-11-01).")
    assert got["benefit"].startswith("Up to 1.0 CU-hours of capacity freed")
    assert got["detail"] == {"failed_cu_seconds": 3600.123, "cu_seconds": 7200.123, "share": 0.5,
                             "window": ops()["operations"]["window"]}
    got = cc._failed_work(ctx(ops(cu_by_status={"failure": 1.0, "success": 2.0})))
    assert got["detail"]["share"] == 0.3333


def test_failed_work_at_exactly_ten_percent_fails():
    got = cc._failed_work(ctx(ops(cu_by_status={"failure": 10.0, "success": 90.0})))
    assert got["outcome"] == "fail" and "10%" in got["text"]


def test_failed_work_under_ten_percent_passes_with_the_window():
    got = cc._failed_work(ctx(ops(cu_by_status={"failure": 0.2, "success": 0.3})))
    assert got["outcome"] == "fail"
    got = cc._failed_work(ctx(ops(cu_by_status={"failure": 0.0, "success": 0.5})))
    assert got == {"outcome": "pass", "evidence_ids": ["u1"],
                   "text": "Failed operations used 0% of the imported operations' CU (2026-10-30 to 2026-11-01)."}
    got = cc._failed_work(ctx(ops(cu_by_status={"success": 1.0}, window={"start": None, "end": None})))
    assert got["text"] == "Failed operations used 0% of the imported operations' CU ( to )."


def test_failed_work_with_no_cu_is_not_evaluated():
    got = cc._failed_work(ctx(ops(cu_by_status={"success": 0.0})))
    assert got == {"outcome": "not_evaluated", "evidence_ids": [], "needs": cc.NEEDS_OPS,
                   "text": "The imported operations used no CU, so there is no share to work out."}


def test_hours_are_a_tenth_with_thousands():
    assert cc._hours(3600 * 1000) == "1,000.0 CU-hours"


# ---- FEA-CAP-003 refreshes


def test_refreshes_without_operations_are_not_evaluated():
    assert cc._refreshes(ctx({"operations": None, "items": []})) == {
        "outcome": "not_evaluated", "evidence_ids": [], "needs": cc.NEEDS_OPS,
        "text": "The imported export has no operations, only totals per item, so refreshes can't be counted."}


def test_refreshes_over_the_limit_name_five_and_the_benefit_counts_the_excess():
    over = [{"workspace": "W", "item": f"m{i}", "day": "2026-10-31", "count": 30 - i} for i in range(6)]
    got = cc._refreshes(ctx(ops(refreshes_per_day=over, refresh_days_over=6)))
    assert got["text"] == ("An item refreshed more than 24 times in one day, 6 times: m0 (30 on 2026-10-31), "
                           "m1 (29 on 2026-10-31), m2 (28 on 2026-10-31), m3 (27 on 2026-10-31), "
                           "m4 (26 on 2026-10-31).")
    assert got["benefit"].startswith("Refreshing m0 hourly instead would leave out about 6 refreshes that day.")


def test_refreshes_pass_on_a_window_of_exactly_a_day():
    got = cc._refreshes(ctx(ops(window={"start": "2026-10-30T00:00:00+00:00", "end": "2026-10-31T00:00:00+00:00"})))
    assert got == {"outcome": "pass", "evidence_ids": ["u1"], "text": "No item refreshed more than 24 times on "
                   "any day of the imported window (2026-10-30 to 2026-10-31)."}


# ---- FEA-CAP-004 utilization


@pytest.mark.parametrize("sku,state,text", [
    ("", "Active", "Not a paid F SKU (size not given)."), ("F0", "Active", "Not a paid F SKU (F0)."),
    ("F2", "", "The capacity is in an unknown state, not running (FEA-CAP-001).")])
def test_utilization_doesnt_apply_without_a_paid_running_f_sku(sku, state, text):
    assert cc._utilization(ctx(None, sku=sku, state=state)) == {"outcome": "not_applicable", "evidence_ids": [],
                                                                 "text": text}


def test_an_f1_counts_as_a_paid_size():
    got = cc._utilization(ctx({"operations": None, "items": [period(0, 7, cu=1.0)]}, sku="F1"))
    assert got["outcome"] == "fail" and "an F1 provides" in got["text"]


def test_utilization_needs_seven_days_exactly_or_more():
    got = cc._utilization(ctx({"operations": None, "items": [period(0, 7, cu=0.1 * 2 * 7 * DAY)]}))
    assert got == {"outcome": "pass", "evidence_ids": ["s1", "u1"],
                   "text": "Items used 10% of the F2's capacity over 7 days imported."}
    short = (3.25 * DAY + 1) / DAY
    got = cc._utilization(ctx({"operations": None, "items": [period(0, short)]}))
    assert got["text"] == "The imported Compute-page periods cover 3.3 days, less than 7."


def test_utilization_wording_and_detail():
    days = (10.5 * DAY + 1) / DAY
    items = [period(0, days, cu=3600.12345)]
    got = cc._utilization(ctx({"operations": None, "items": items}))
    avail = 2 * days * DAY
    assert got["text"].startswith("Over 11 days imported, items used 1.0 CU-hours, which is 0.2% of the 504.0 CU-hours")
    assert got["benefit"].startswith("About 503.0 CU-hours of capacity unused")
    assert got["detail"] == {"cu_seconds": 3600.123, "available_cu_seconds": round(avail, 3),
                             "share": round(3600.12345 / avail, 4), "sku": "F2", "periods": [items[0]["period"]]}
    odd = [{**period(0, 7), "seconds": 7 * DAY + 0.12345}]
    assert cc._utilization(ctx({"operations": None, "items": odd}))["detail"]["available_cu_seconds"] == 1209600.247
    got = cc._utilization(ctx({"operations": None, "items": [period(0, 7, cu=2 * 7 * DAY / 30)]}))
    assert got["detail"]["share"] == 0.0333
