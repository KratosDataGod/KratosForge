"""FINOPS-003: measured CU from an imported Capacity Metrics app export, and its reconciliation."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from fabric_desktop import audit, usage
from fabric_desktop import measurement as ms
from fabric_desktop.service import Service
from fabric_desktop.usage import reader
from fabric_desktop.usage import reconcile as rc

NOW = dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)
HEAD = ("Workspace,Item kind,Item name,Operation,Start,End,Status,User,Duration (s),Total CU (s),Timepoint CU (s),"
        "Billing type,Operation ID")
OPS = "﻿" + HEAD + """
Sales Dev,Notebook,nb_sales,Notebook run,10/16/2026 2:00:05 PM,10/16/2026 2:04:00 PM,Succeeded,ana@contoso.com,235,"1,880.5",0.65,Billable,op-1
Sales Dev,Notebook,nb_sales,Notebook run,10/16/2026 2:00:10 PM,10/16/2026 2:03:00 PM,Succeeded,ana@contoso.com,170,120.25,0.1,Billable,op-2
Sales Dev,Notebook,nb_other,Notebook run,10/16/2026 3:00:05 PM,10/16/2026 3:01:00 PM,Succeeded,bo@contoso.com,55,440,0.1,Non-billable,op-3
Sales Dev,Lakehouse,LH_Bronze,OneLake read,10/16/2026 3:00:05 PM,,Succeeded,bo@contoso.com,1,,0,Billable,op-4
"""
ITEMS = """Workspace,Item kind,Item name,CU (s),Duration (s),Users,Billing type
Sales Dev,Notebook,nb_sales,"12,000.5",900,3,Billable
Sales Dev,Lakehouse,LH_Bronze,300.25,10,2,Both
"""


def read(text, options=None, **kw):
    return usage.read(text, "export.csv", options, now=NOW, **kw)


# ---- reading numbers and times without guessing

def test_decimal_mark_is_worked_out_from_the_column_or_asked():
    assert reader.decimal_mark(["1,880.5", "12"]) == "."
    assert reader.decimal_mark(["1.880,5"]) == ","
    assert reader.decimal_mark(["1,5", "2"]) == ","
    assert reader.decimal_mark(["1,234,567"]) == "."
    assert reader.decimal_mark(["12", "7"]) is None
    with pytest.raises(reader.NeedsChoice) as exc:
        reader.decimal_mark(["1,234", "2,500"])
    assert exc.value.option == "decimal" and set(exc.value.choices) == {".", ","}
    with pytest.raises(reader.FileProblem):
        reader.decimal_mark(["1,5", "1.5"])
    assert reader.number("1.880,5", ",") == 1880.5 and reader.number("1,880.5", ".") == 1880.5
    assert reader.number("", ".") is None and reader.number("n/a", ".") is None


def test_dates_are_read_in_the_order_the_file_settles_or_asked():
    assert reader.date_order(["10/16/2026 2:00 PM"]) == "mdy"
    assert reader.date_order(["16/10/2026 14:00"]) == "dmy"
    assert reader.date_order(["2026-10-16T14:00:00Z"]) is None
    with pytest.raises(reader.NeedsChoice) as exc:
        reader.date_order(["03/04/2026 10:00"])
    assert exc.value.option == "date_order"
    utc = reader.zone("UTC")
    assert reader.when("10/16/2026 2:00:05 PM", "mdy", utc) == dt.datetime(2026, 10, 16, 14, 0, 5, tzinfo=dt.UTC)
    assert reader.when("16/10/2026 12:30 AM", "dmy", utc) == dt.datetime(2026, 10, 16, 0, 30, tzinfo=dt.UTC)
    assert reader.when("10/16/2026 14:00", "mdy", reader.zone("+02:00")) == dt.datetime(2026, 10, 16, 12, tzinfo=dt.UTC)
    assert reader.when("2026-10-16T14:00:00+01:00", None, utc) == dt.datetime(2026, 10, 16, 13, tzinfo=dt.UTC)
    assert reader.when("13/13/2026", "mdy", utc) is None
    with pytest.raises(reader.FileProblem):
        reader.zone("Europe/Paris")


def test_files_that_cant_be_read_say_what_to_do():
    assert "empty" in read("")["problem"]
    assert "CSV" in read("PK\x03\x04garbage")["problem"]
    assert "Supported" in read("a,b\n1,2\n")["problem"]


# ---- the Capacity Metrics app operations export

def test_operations_export_becomes_measured_rows_without_peoples_names():
    p = read(OPS)
    assert p["ready"] and p["provider"]["id"] == "metrics_app" and p["layout"]["id"] == "operations"
    assert p["rows_read"] == 4 and p["to_import"] == 3
    assert p["skipped"] == [{"reason": "No CU value", "count": 1, "lines": [5]}]
    assert p["columns"]["used"]["cu"] == "Total CU (s)" and "Timepoint CU (s)" in p["columns"]["ignored"]
    assert p["columns"]["not_stored"] == ["User"] and "contoso.com" not in json.dumps(p["rows"])
    first = p["rows"][0]
    m = first["cu"]
    assert m["classification"] == ms.MEASURED and m["unit"] == ms.CU_SECONDS and m["value"] == 1880.5
    assert m["metric"] == "fabric_operation_cu" and m["reconciliation"] == "unreconciled"
    assert "sampled" in m["uncertainty"] and "export.csv" in m["source"] and not ms.problems(m)
    assert first["start"] == "2026-10-16T14:00:05+00:00" and first["billable"] is True
    assert p["rows"][2]["billable"] is False
    assert p["total"]["classification"] == ms.MEASURED and p["total"]["complete"]
    assert p["total"]["value"] == pytest.approx((1880.5 + 120.25 + 440) / 3600, abs=1e-6)
    assert p["options"] == {"tz": "UTC", "decimal": ".", "date_order": "mdy", "period_start": None, "period_end": None}
    assert any("time zone" in n for n in p["notes"])


def test_timepoint_cu_alone_is_refused_as_not_the_operations_use():
    text = "Workspace,Item kind,Item name,Operation,Start,End,Timepoint CU (s)\nW,Notebook,n,Run,2026-10-16T14:00:00Z,,0.5\n"
    p = read(text)
    assert not p["ready"] and "Total CU (s)" in p["problem"] and "30-second" in p["problem"]


def test_bad_rows_are_skipped_with_their_reason():
    text = HEAD + """
W,Notebook,a,Run,10/16/2026 2:00 PM,10/16/2026 1:00 PM,Succeeded,,1,5,0,Billable,x1
W,Notebook,b,Run,10/16/2026 2:00 PM,,Succeeded,,1,-5,0,Billable,x2
W,Notebook,c,Run,,,Succeeded,,1,5,0,Billable,x3
W,Notebook,d,Run,soon,,Succeeded,,1,5,0,Billable,x4
W,Notebook,e,Run,10/16/2026 2:00 PM,,Succeeded,,1,lots,0,Billable,x5
W,Notebook,f,Run,10/16/2026 2:00 PM,,Succeeded,,1,7,0,Billable,x6
"""
    p = read(text)
    reasons = {s["reason"] for s in p["skipped"]}
    assert reasons == {"Ends before it starts", "Negative CU value", "No start time", "The start time can't be read",
                       "The CU value isn't a number"}
    assert p["to_import"] == 1 and p["rows"][0]["item"] == "f"


def test_duplicates_are_not_counted_twice():
    p = read(OPS + "Sales Dev,Notebook,nb_other,Notebook run,10/16/2026 3:00:05 PM,10/16/2026 3:01:00 PM,Succeeded,,55,440,0.1,Billable,op-3\n")
    assert p["duplicates_in_file"] == 1 and p["to_import"] == 3
    again = read(OPS.replace("Succeeded,bo", "Succeeded, bo"),  # another export holding the same operations
                 known_keys={r["key"] for r in p["rows"]})
    assert again["already_imported"] == 3 and not again["ready"] and "already imported" in again["problem"]
    same = read(OPS, imports=[{"id": "u-1", "sha256": read(OPS)["sha256"], "file_name": "old.csv", "imported_at": "2026-10-16T00:00:00"}])
    assert not same["ready"] and "already imported" in same["problem"] and same["duplicate_of"] == "u-1"


def test_comma_decimal_files_with_semicolons_and_ambiguous_dates_ask_first():
    text = ("Workspace;Item kind;Item name;Operation;Start;End;Total CU (s)\n"
            "W;Notebook;n;Run;03/04/2026 10:00;03/04/2026 10:05;1.880,5\n")
    p = read(text)
    assert not p["ready"] and p["needs"]["option"] == "date_order"
    p = read(text, {"date_order": "dmy"})
    assert p["ready"] and p["rows"][0]["cu"]["value"] == 1880.5 and p["rows"][0]["start"] == "2026-04-03T10:00:00+00:00"


# ---- the Compute page's items matrix: totals over a period the person gives

def test_item_totals_need_their_period_and_never_overlap():
    p = read(ITEMS)
    assert not p["ready"] and p["needs"]["option"] == "period" and p["layout"]["id"] == "items"
    p = read(ITEMS, {"period_start": "2026-10-01", "period_end": "2026-10-14"})
    assert p["ready"] and p["to_import"] == 2 and p["columns"]["not_stored"] == ["Users"]
    m = p["rows"][0]["cu"]
    assert m["metric"] == "fabric_item_cu" and m["value"] == 12000.5
    assert m["period"] == {"start": "2026-10-01T00:00:00+00:00", "end": "2026-10-15T00:00:00+00:00"}
    assert p["rows"][1]["billable"] is None  # "Both"
    earlier = [{"id": "u-2", "provider": "metrics_app", "layout": "items", "workspaces": ["Sales Dev"], "file_name": "old.csv",
                "imported_at": "2026-10-15T00:00:00", "period": {"start": "2026-10-10T00:00:00+00:00", "end": "2026-10-20T00:00:00+00:00"},
                "sha256": "x"}]
    clash = read(ITEMS, {"period_start": "2026-10-01", "period_end": "2026-10-14"}, imports=earlier)
    assert not clash["ready"] and "count twice" in clash["problem"]
    assert "ends before" in read(ITEMS, {"period_start": "2026-10-14", "period_end": "2026-10-01"})["problem"]


# ---- reconciliation with this app's runs

def run(job, name, start, end, kind="Notebook run", ws="Sales Dev", **extra):
    return {"kind": kind, "name": name, "workspace_name": ws, "workspace_id": "w1", "job_id": job,
            "started_at": start, "finished_at": end} | extra


def recon(rows_text, runs, estimates=None, ws_names=None, options=None):
    p = read(rows_text, options)
    imp = {"id": "u-1", "imported_at": NOW.isoformat(), "rows": p["rows"]}
    return rc.reconcile([imp], runs, estimates or {}, NOW - dt.timedelta(days=30), NOW, ws_names, now=NOW)


def test_rows_match_only_the_one_run_they_can_belong_to():
    est = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, 1200, method="run time x CU", assumptions=["1 CU"], run_id="j1")
    r = recon(OPS, [run("j1", "nb_sales", "2026-10-16T14:00:00+00:00", "2026-10-16T14:05:00+00:00"),
                    run("j9", "nb_late", "2026-10-16T18:00:00+00:00", "2026-10-16T18:05:00+00:00")], {"j1": est})
    assert r["matched"]["runs"] == 1 and r["matched"]["rows"] == 2  # two operations of one job
    assert r["matched"]["measured"]["value"] == pytest.approx(2000.75 / 3600, abs=1e-6)
    assert r["matched"]["measured"]["classification"] == ms.MEASURED
    assert r["matched"]["estimated"]["classification"] == ms.ESTIMATED
    assert r["matched"]["measured_to_estimate"] == pytest.approx(2000.75 / 1200, abs=1e-3)
    assert r["unmatched"] == {"rows": 1, "measured": r["unmatched"]["measured"], "by_reason": {rc.NO_RUN: 1}}
    assert r["unmatched"]["measured"]["value"] == pytest.approx(440 / 3600, abs=1e-6)
    j1 = next(b for b in r["by_run"] if b["job_id"] == "j1")
    assert j1["state"] == "matched" and j1["measured"]["reconciliation"] == "matched" and j1["measured"]["run_id"] == "j1"
    assert next(b for b in r["by_run"] if b["job_id"] == "j9")["state"] == "not_covered"
    assert r["imported"]["value"] == pytest.approx((2000.75 + 440) / 3600, abs=1e-6)


def test_a_row_that_fits_two_runs_isnt_given_to_either():
    r = recon(OPS, [run("j1", "nb_sales", "2026-10-16T14:00:00+00:00", "2026-10-16T14:05:00+00:00"),
                    run("j2", "nb_sales", "2026-10-16T14:00:01+00:00", "2026-10-16T14:06:00+00:00")])
    assert r["matched"]["runs"] == 0 and r["unmatched"]["by_reason"][rc.SEVERAL] == 2
    assert r["matched"]["measured"] is None


def test_a_run_of_the_same_item_that_ended_just_before_isnt_counted_as_this_apps():
    # A schedule of nb_sales ran 13:50-13:56; this app started its run at 14:00. Only the row that
    # started inside the app's run is measured; the schedule's 9,000 CU-s stays other use.
    text = HEAD + """
Sales Dev,Notebook,nb_sales,Notebook run,10/16/2026 1:50:00 PM,10/16/2026 1:56:00 PM,Succeeded,sched@contoso.com,360,"9,000.0",1,Billable,op-s
Sales Dev,Notebook,nb_sales,Notebook run,10/16/2026 2:00:05 PM,10/16/2026 2:04:00 PM,Succeeded,ana@contoso.com,235,"1,200.0",0.65,Billable,op-1
"""
    r = recon(text, [run("j1", "nb_sales", "2026-10-16T14:00:00+00:00", "2026-10-16T14:05:00+00:00")])
    cu_s = lambda m: ms.convert(m["value"], m["unit"], ms.CU_SECONDS)  # noqa: E731
    assert r["matched"]["runs"] == 1 and cu_s(r["matched"]["measured"]) == pytest.approx(1200, abs=0.01)
    assert "start time" in r["by_run"][0]["measured"]["method"]
    assert cu_s(r["unmatched"]["measured"]) == pytest.approx(9000, abs=0.01) and r["unmatched"]["by_reason"][rc.NO_RUN] == 1


def test_runs_inside_an_import_without_a_row_are_told_apart_from_runs_no_import_covers():
    r = recon(OPS, [run("j3", "nb_missing", "2026-10-16T14:30:00+00:00", "2026-10-16T14:31:00+00:00"),
                    run("j4", "nb_sales", "2026-10-10T14:00:00+00:00", "2026-10-10T14:05:00+00:00", ws="Other WS")])
    states = {b["job_id"]: b["state"] for b in r["by_run"]}
    assert states == {"j3": "no_row", "j4": "not_covered"} and r["runs"]["no_row"] == 1


def test_an_imported_row_whose_start_cant_be_read_covers_nothing_and_breaks_nothing():
    # Before: min() compared the unreadable start (None) with the others and the whole reconcile failed.
    p = read(OPS)
    rows = [dict(r) for r in p["rows"]]
    rows.append(rows[0] | {"start": "not a time", "end": "not a time"})
    imp = {"id": "u-1", "imported_at": NOW.isoformat(), "rows": rows}
    r = rc.reconcile([imp], [run("j3", "nb_missing", "2026-10-16T14:30:00+00:00", "2026-10-16T14:31:00+00:00")],
                     {}, NOW - dt.timedelta(days=30), NOW, now=NOW)
    assert {b["job_id"]: b["state"] for b in r["by_run"]} == {"j3": "no_row"}  # still covered by the readable rows


def test_item_names_come_from_what_each_kind_of_run_ran():
    assert rc.item_name({"kind": "Metadata run", "name": "all 3 entities"}) == "nb_fabric_desktop_runner"
    assert rc.item_name({"kind": "Table maintenance", "name": "LH_Silver dbo.sales", "table": "dbo.sales"}) == "LH_Silver"
    assert rc.item_name({"kind": "Table maintenance", "name": "Vacuum LH_Silver sales", "table": "sales"}) == "LH_Silver"
    assert rc.item_name({"kind": "Something new", "name": "x"}) is None
    r = recon(OPS, [run("j5", "?", "2026-10-16T14:00:00+00:00", "2026-10-16T14:05:00+00:00", kind="Something new")])
    assert r["by_run"][0]["state"] == "no_item_name"


def test_workspace_names_fill_in_for_runs_that_didnt_record_one():
    r = recon(OPS, [run("j1", "nb_sales", "2026-10-16T14:00:00+00:00", "2026-10-16T14:05:00+00:00", ws="")],
              ws_names={"w1": "Sales Dev"})
    assert r["matched"]["runs"] == 1


def test_item_totals_are_shown_but_never_tied_to_runs():
    r = recon(ITEMS, [run("j1", "nb_sales", "2026-10-02T14:00:00+00:00", "2026-10-02T14:05:00+00:00")],
              options={"period_start": "2026-10-01", "period_end": "2026-10-14"})
    assert r["matched"]["runs"] == 0 and r["unmatched"]["by_reason"] == {rc.UNTIMED: 2}
    partly = recon(ITEMS, [], options={"period_start": "2026-09-01", "period_end": "2026-09-30"})
    assert partly["rows"] == 0 and partly["rows_outside_period"] == 2 and partly["imported"] is None


# ---- the service: preview writes nothing, import keeps a local copy, the cost view shows it

REAL = dt.datetime.now(dt.UTC).replace(microsecond=0)  # the service reads runs relative to the real clock


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s._log_fabric_run({"kind": "Notebook run", "name": "nb_sales", "workspace_id": "w1", "workspace_name": "Sales Dev",
                       "engine": "python", "item_id": "i1", "job_id": "j1", "status": "Completed",
                       "started_at": (REAL - dt.timedelta(hours=2)).isoformat(),
                       "finished_at": (REAL - dt.timedelta(hours=2) + dt.timedelta(minutes=5)).isoformat()})
    return s


def recent_ops():
    t = REAL - dt.timedelta(hours=2)
    stamp = lambda d: (t + d).strftime("%m/%d/%Y %I:%M:%S %p")
    return HEAD + f"\nSales Dev,Notebook,nb_sales,Notebook run,{stamp(dt.timedelta(seconds=5))},{stamp(dt.timedelta(minutes=4))},Succeeded,ana@contoso.com,235,1880.5,0.6,Billable,op-1\n"


# recent_ops() writes month first. On days 1 to 12 the dates read both ways, and the preview rightly
# asks which (main went red on 1 November), so the order is given as a person would choose it.
MDY = {"date_order": "mdy"}


def test_service_preview_saves_nothing_and_import_keeps_a_local_copy(svc):
    p = svc.usage_preview("timepoint.csv", recent_ops(), MDY)
    assert p["ready"] and "rows" not in p and not (svc.root / "usage_imports").exists()
    saved = svc.usage_import("timepoint.csv", recent_ops(), MDY)
    assert saved["rows"] == 1 and saved["file_name"] == "timepoint.csv" and saved["id"].startswith("u-")
    assert svc.usage_imports()[0]["id"] == saved["id"]
    with pytest.raises(ValueError, match="already imported"):
        svc.usage_import("again.csv", recent_ops(), MDY)
    rec = svc.usage_reconciliation(7)
    assert rec["matched"]["runs"] == 1 and rec["matched"]["estimated"]["classification"] == ms.ESTIMATED
    report = svc.cost_report(7)
    assert report["usage"]["matched"]["measured"]["value"] == pytest.approx(1880.5 / 3600, abs=1e-6)
    assert report["total"]["fabric_runs"] == 1  # existing figures are unchanged
    assert svc.usage_import_delete(saved["id"]) == {"deleted": True}
    assert svc.usage_imports() == [] and svc.cost_report(7)["usage"]["imported"] is None
    with pytest.raises(ValueError):
        svc.usage_import_delete("../secrets")


def test_import_refuses_what_preview_refuses(svc):
    with pytest.raises(ValueError, match="Supported"):
        svc.usage_import("x.csv", "a,b\n1,2\n")


def test_preview_is_a_read_and_import_is_audited_without_the_files_content():
    assert not audit.audited("usage_preview")
    assert audit.audited("usage_import") and audit.audited("usage_import_delete")
    assert audit.target({"file_name": "t.csv", "content": OPS})["content"] == "(given)"



# ---- Azure Cost Management exports: what a capacity was billed

CAP = "/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Fabric/capacities/contosof64"
COST = f"""Date,MeterCategory,MeterSubCategory,MeterName,Quantity,UnitOfMeasure,CostInBillingCurrency,BillingCurrency,ResourceId,ResourceName,ChargeType,PricingModel
10/15/2026,Microsoft Fabric,Fabric Capacity,Data Warehouse Capacity Usage CU,120.5,1 Hour,21.69,EUR,{CAP},ContosoF64,Usage,OnDemand
10/15/2026,Microsoft Fabric,Fabric Capacity,Spark Memory Optimized Capacity Usage CU,40,10 Hours,72.00,EUR,{CAP},ContosoF64,Usage,OnDemand
10/15/2026,Microsoft Fabric,OneLake,OneLake Storage,3.2,1 GB/Month,0.08,EUR,{CAP},ContosoF64,Usage,OnDemand
10/16/2026,Microsoft Fabric,Fabric Capacity,Data Warehouse Capacity Usage CU,100,1 Hour,18.00,EUR,{CAP},ContosoF64,Usage,OnDemand
10/16/2026,Virtual Machines,D2s v5,D2s v5,24,1 Hour,2.30,EUR,/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1,vm1,Usage,OnDemand
10/16/2026,Microsoft Fabric,Fabric Capacity,Data Warehouse Capacity Usage CU,1,1 Hour,-0.18,EUR,{CAP},ContosoF64,Refund,OnDemand
"""


def test_cost_details_keep_only_fabric_capacities_and_never_mix_currencies():
    p = read(COST)
    assert p["ready"] and p["provider"]["id"] == "cost_management" and p["layout"]["id"] == "cost_details"
    assert p["to_import"] == 4 and p["capacities"] == ["ContosoF64"]
    assert {s["reason"]: s["count"] for s in p["skipped"]} == {"Not a Fabric capacity": 1,
                                                                 "A refund or credit (negative amount)": 1}
    first = p["rows"][0]
    assert first["level"] == "capacity" and first["capacity_id"] == CAP.lower()
    assert first["cost"]["classification"] == ms.MEASURED and first["cost"]["currency"] == "EUR"
    assert first["cost"]["value"] == 21.69 and first["cu"]["unit"] == ms.CU_HOURS and first["cu"]["value"] == 120.5
    assert first["cost"]["period"] == {"start": "2026-10-15T00:00:00+00:00", "end": "2026-10-16T00:00:00+00:00"}
    assert p["rows"][1]["cu"]["value"] == 400  # 40 x "10 Hours"
    assert p["rows"][2]["cu"] is None and p["rows"][2]["cost"]["value"] == 0.08  # storage: cost only
    assert p["total"]["value"] == pytest.approx(620.5) and p["total"]["classification"] == ms.MEASURED
    assert [c["currency"] for c in p["cost"]] == ["EUR"] and p["cost"][0]["value"] == pytest.approx(111.77)
    usd = read(COST.replace("21.69,EUR", "21.69,USD"))
    assert sorted(c["currency"] for c in usd["cost"]) == ["EUR", "USD"]


def test_focus_exports_are_read_and_amortized_cost_alone_is_refused():
    focus = ("ChargePeriodStart,ChargePeriodEnd,SkuMeter,ConsumedQuantity,ConsumedUnit,BilledCost,BillingCurrency,"
             "ResourceId,ResourceName,ChargeCategory,PricingCategory\n"
             f"2026-10-15T00:00:00Z,2026-10-16T00:00:00Z,Power BI Capacity Usage CU,10,Hours,1.80,USD,{CAP},ContosoF64,Usage,Standard\n")
    p = read(focus)
    assert p["ready"] and p["layout"]["id"] == "focus" and p["rows"][0]["cu"]["value"] == 10
    amortized = read(focus.replace("BilledCost", "EffectiveCost"))
    assert not amortized["ready"] and "Actual cost" in amortized["problem"]


def test_compute_meters_not_billed_in_hours_carry_cost_only():
    p = read(COST.replace("120.5,1 Hour", "120.5,1 CU-Second"))
    assert p["rows"][0]["cu"] is None and p["rows"][0]["cost"]["value"] == 21.69


def test_billed_totals_are_shown_per_capacity_and_never_matched_to_runs():
    p = read(COST)
    imp = {"id": "u-9", "imported_at": NOW.isoformat(), "rows": p["rows"]}
    r = rc.reconcile([imp], [run("j1", "nb_sales", "2026-10-15T10:00:00+00:00", "2026-10-15T10:05:00+00:00")], {},
                     NOW - dt.timedelta(days=30), NOW, now=NOW, capacity_envs={"contosof64": ["dev", "test"]})
    assert r["rows"] == 0 and r["imported"] is None and r["matched"]["runs"] == 0
    assert r["runs"]["not_covered"] == 1  # a bill doesn't cover single runs
    [cap] = r["billed"]
    assert cap["capacity"] == "ContosoF64" and cap["environments"] == ["dev", "test"]
    assert cap["days"] == 2 and cap["first_day"] == "2026-10-15" and cap["last_day"] == "2026-10-16"
    assert cap["cu"]["value"] == pytest.approx(620.5) and cap["cost"][0]["value"] == pytest.approx(111.77)
    assert cap["meters"]["Data Warehouse Capacity Usage CU"] == 2


def test_month_to_date_exports_that_repeat_days_are_counted_once(tmp_path):
    # Export A ran mid-day on the 16th (partial day); export B, a day later, repeats the 15th and has the full 16th.
    head = COST.splitlines()[0]
    line = lambda day, qty, cost: (f"10/{day}/2026,Microsoft Fabric,Fabric Capacity,Data Warehouse Capacity Usage CU,"  # noqa: E731
                                   f"{qty},1 Hour,{cost},EUR,{CAP},ContosoF64,Usage,OnDemand")
    a = "\n".join([head, line(15, 96, "17.28"), line(16, 40, "7.20")]) + "\n"
    b = "\n".join([head, line(15, 96, "17.28"), line(16, 96, "17.28"), line(17, 96, "17.28")]) + "\n"
    svc = Service(root=tmp_path)
    svc.usage_import("a.csv", a)
    preview = svc.usage_preview("b.csv", b)
    assert preview["already_imported"] == 1 and preview["replaces_earlier"] == 1 and preview["to_import"] == 2
    svc.usage_import("b.csv", b)
    imports = svc._usage.all()
    r = rc.reconcile(imports, [], {}, NOW - dt.timedelta(days=30), NOW + dt.timedelta(days=2), now=NOW)
    [cap] = r["billed"]
    assert cap["cu"]["value"] == pytest.approx(288) and cap["cost"][0]["value"] == pytest.approx(51.84)
    assert cap["days"] == 3


def test_an_older_export_imported_later_doesnt_replace_a_finished_day(tmp_path):
    # Export B (full 16th, through the 17th) is imported first; the older export A (partial 16th) after it.
    head = COST.splitlines()[0]
    line = lambda day, qty, cost: (f"10/{day}/2026,Microsoft Fabric,Fabric Capacity,Data Warehouse Capacity Usage CU,"
                                   f"{qty},1 Hour,{cost},EUR,{CAP},ContosoF64,Usage,OnDemand")
    a = "\n".join([head, line(15, 96, "17.28"), line(16, 40, "7.20")]) + "\n"
    b = "\n".join([head, line(15, 96, "17.28"), line(16, 96, "17.28"), line(17, 96, "17.28")]) + "\n"
    svc = Service(root=tmp_path)
    svc.usage_import("b.csv", b)
    svc.usage_import("a.csv", a)
    r = rc.reconcile(svc._usage.all(), [], {}, NOW - dt.timedelta(days=30), NOW + dt.timedelta(days=2), now=NOW)
    [cap] = r["billed"]
    assert cap["cu"]["value"] == pytest.approx(288) and cap["cost"][0]["value"] == pytest.approx(51.84)
    assert cap["days"] == 3
