"""Usage numbers are real amounts (KUDP / FinOps hardening): a cell with more digits than a float holds
reads as infinity in Python, and an infinite or NaN amount would make every total it joins
meaningless. Such a cell is skipped with its reason, and a usage record refuses it."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from fabric_desktop import usage
from fabric_desktop.engines.usage import UsageRecord, totals
from fabric_desktop.usage import cost_management as cm
from fabric_desktop.usage import reader

NOW = dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)
HUGE = "9" * 400
HEAD = ("Workspace,Item kind,Item name,Operation,Start,End,Status,User,Duration (s),Total CU (s),Timepoint CU (s),"
        "Billing type,Operation ID")
CAP = "/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Fabric/capacities/contosof64"
COST_HEAD = ("Date,MeterCategory,MeterSubCategory,MeterName,Quantity,UnitOfMeasure,CostInBillingCurrency,"
             "BillingCurrency,ResourceId,ResourceName,ChargeType,PricingModel")


def read(text):
    return usage.read(text, "export.csv", None, now=NOW)


@pytest.mark.parametrize("raw, mark", [(HUGE, None), (HUGE, "."), (HUGE + ",5", ","), ("-" + HUGE, ".")])
def test_a_number_too_large_to_be_real_isnt_read_as_infinity(raw, mark):
    assert reader.number(raw, mark) is None


def test_ordinary_numbers_still_read():
    assert reader.number("1,880.5", ".") == 1880.5 and reader.number("1.880,5", ",") == 1880.5
    assert reader.number("0", None) == 0.0


def test_a_cu_cell_too_large_to_be_real_is_skipped_as_not_a_number():
    text = HEAD + f"""
W,Notebook,a,Run,10/16/2026 2:00 PM,,Succeeded,,1,{HUGE},0,Billable,x1
W,Notebook,b,Run,10/16/2026 2:00 PM,,Succeeded,,1,7,0,Billable,x2
"""
    p = read(text)
    assert {s["reason"] for s in p["skipped"]} == {"The CU value isn't a number"}
    assert p["to_import"] == 1 and p["rows"][0]["item"] == "b"
    assert math.isfinite(p["total"]["value"])


def _cost(quantity="10", unit="1 Hour", cost="1.80"):
    return COST_HEAD + (f"\n10/16/2026,Microsoft Fabric,Fabric Capacity,Data Warehouse Capacity Usage CU,{quantity},"
                        f"{unit},{cost},EUR,{CAP},ContosoF64,Usage,OnDemand\n")


@pytest.mark.parametrize("kw, reason", [
    ({"cost": HUGE}, "The cost isn't a number"),
    ({"quantity": "1" + "0" * 200, "unit": "1" + "0" * 200 + " Hours"}, "The quantity is too large to be real usage"),
], ids=["cost", "quantity-times-unit"])
def test_a_cost_row_with_an_amount_too_large_to_be_real_is_skipped(kw, reason):
    p = read(_cost(**kw))
    assert [s["reason"] for s in p["skipped"]] == [reason] and p["to_import"] == 0


def test_a_unit_too_large_to_be_hours_isnt_hours():
    assert cm._hours(HUGE + " Hours") is None
    assert cm._hours("10 Hours") == 10 and cm._hours("Hours") == 1


@pytest.mark.parametrize("amount", [math.inf, -math.inf, math.nan, True, "5", None])
def test_a_usage_record_refuses_an_amount_that_isnt_a_finite_number(amount):
    with pytest.raises(ValueError, match="finite number"):
        UsageRecord("local-duckdb", "seconds", amount, "measured", "engine session")


def test_totals_stay_finite():
    a = UsageRecord("local-duckdb", "seconds", 1.5, "measured", "engine session")
    b = UsageRecord("local-duckdb", "seconds", 2, "measured", "engine session")
    assert totals([a, b])[0]["amount"] == 3.5
