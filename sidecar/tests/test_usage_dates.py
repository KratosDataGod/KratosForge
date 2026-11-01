"""Usage import times at the edge of the calendar (FinOps hardening): a time that leaves the calendar
once moved to UTC is a cell that can't be read, skipped with its reason, never an import that fails."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import usage
from fabric_desktop.usage import reader

NOW = dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)
HEAD = ("Workspace,Item kind,Item name,Operation,Start,End,Status,User,Duration (s),Total CU (s),Timepoint CU (s),"
        "Billing type,Operation ID")
EAST, WEST = reader.zone("+05:00"), reader.zone("-05:00")


@pytest.mark.parametrize("raw, tz", [
    ("9999-12-31T23:59:59-05:00", dt.UTC),
    ("0001-01-01T00:00:00+05:00", dt.UTC),
    ("9999-12-31T23:00:00", WEST),
    ("0001-01-01T01:00:00", EAST),
    ("12/31/9999 11:00 PM", WEST),
    ("01/01/0001 1:00 AM", EAST),
])
def test_a_time_that_leaves_the_calendar_in_utc_cant_be_read(raw, tz):
    assert reader.when(raw, "mdy", tz) is None


@pytest.mark.parametrize("raw", ["12/31/9999", "9999-12-31", "01/01/1969", "3000-01-01T00:00:00Z", "1969-12-31T23:59:59Z"])
def test_a_year_outside_the_usage_era_cant_be_read(raw):
    assert reader.when(raw, "mdy", dt.UTC) is None


def test_times_inside_the_era_still_read_with_their_offset():
    assert reader.when("2999-12-31T18:00:00-05:00", "mdy", dt.UTC) == dt.datetime(2999, 12, 31, 23, tzinfo=dt.UTC)
    assert reader.when("01/01/1970 5:00 AM", "mdy", EAST) == dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    assert reader.when("10/16/2026 2:00 PM", "mdy", WEST) == dt.datetime(2026, 10, 16, 19, tzinfo=dt.UTC)


def test_a_cost_row_on_the_last_day_of_the_calendar_is_skipped_not_a_failed_import():
    cap = "/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Fabric/capacities/contosof64"
    text = ("Date,MeterCategory,MeterSubCategory,MeterName,Quantity,UnitOfMeasure,CostInBillingCurrency,"
            "BillingCurrency,ResourceId,ResourceName,ChargeType,PricingModel\n"
            f"12/31/9999,Microsoft Fabric,Fabric Capacity,Data Warehouse Capacity Usage CU,10,1 Hour,1.80,EUR,{cap},"
            "ContosoF64,Usage,OnDemand\n"
            f"10/16/2026,Microsoft Fabric,Fabric Capacity,Data Warehouse Capacity Usage CU,10,1 Hour,1.80,EUR,{cap},"
            "ContosoF64,Usage,OnDemand\n")
    p = usage.read(text, "export.csv", None, now=NOW)
    assert [s["reason"] for s in p["skipped"]] == ["The date can't be read"] and p["to_import"] == 1


def test_a_period_end_on_the_last_day_of_the_calendar_says_it_isnt_a_date():
    with pytest.raises(reader.FileProblem, match="isn't a date"):
        usage._period(usage.Options(period_end="12/31/9999"), "mdy", dt.UTC)


def test_a_row_with_such_a_time_is_skipped_and_the_rest_import():
    text = HEAD + """
W,Notebook,a,Run,9999-12-31T23:59:59-05:00,,Succeeded,,1,5,0,Billable,x1
W,Notebook,b,Run,10/16/2026 2:00 PM,9999-12-31T23:59:59-05:00,Succeeded,,1,5,0,Billable,x2
W,Notebook,c,Run,10/16/2026 2:00 PM,,Succeeded,,1,7,0,Billable,x3
"""
    p = usage.read(text, "export.csv", None, now=NOW)
    assert {s["reason"] for s in p["skipped"]} == {"The start time can't be read", "The end time can't be read"}
    assert p["to_import"] == 1 and p["rows"][0]["item"] == "c"
