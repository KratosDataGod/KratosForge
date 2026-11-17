"""usage/cost_management.py paths that mutation testing (batch 52, docs/quality/mutation-testing.md)
showed no test pinned: the refusals for no currency, a zero-length period and negative amounts (and
that zero is not negative), the text columns kept as they are, the capacity name taken from the
resource id, and the duplicate-detection keys, which earlier imports stored and must keep matching.
"""

import datetime as dt

import pytest

from fabric_desktop.usage import cost_management as cm
from fabric_desktop.usage.providers import Context, Options

RID = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Fabric/capacities/fcap01"
CTX = Context(file_name="costs.csv", imported_at=dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc),
              options=Options(), tz=dt.timezone.utc, decimal=".", date_order=None)


def _row(layout=cm.COST_DETAILS, **cells):
    base = {"resource_id": RID, "cost": "12.5", "quantity": "3", "currency": "usd", "start": "2026-11-01",
            "meter": "F2 Capacity Usage CU", "unit": "1 Hour"}
    return cm.CostManagement().row(base | cells, layout, CTX)


def test_a_row_without_a_billing_currency_is_skipped():
    assert _row(currency="  ") == "No billing currency"


def test_a_period_that_ends_when_it_starts_is_skipped():
    got = _row(layout=cm.FOCUS, start="2026-11-01T00:00:00Z", end="2026-11-01T00:00:00Z")
    assert got == "Ends before it starts"
    ok = _row(layout=cm.FOCUS, start="2026-11-01T00:00:00Z", end="2026-11-01T01:00:00Z")
    assert isinstance(ok, dict) and ok["end"].startswith("2026-11-01T01:00")


@pytest.mark.parametrize("cost, quantity", [("-0.01", "3"), ("12.5", "-1")])
def test_a_negative_cost_or_quantity_is_a_refund(cost, quantity):
    assert _row(cost=cost, quantity=quantity) == "A refund or credit (negative amount)"


@pytest.mark.parametrize("cost, quantity", [("0", "0"), ("0.5", "0.5")])
def test_zero_and_small_amounts_are_not_refunds(cost, quantity):
    got = _row(cost=cost, quantity=quantity)
    assert isinstance(got, dict)
    assert got["quantity"] == float(quantity) and got["cost"]["value"] == float(cost)


def test_text_columns_are_kept_and_empty_ones_are_none():
    got = _row(meter_category="Microsoft Fabric", charge_type="Usage", pricing="OnDemand")
    assert (got["meter_category"], got["unit"], got["charge_type"], got["pricing"]) == (
        "Microsoft Fabric", "1 Hour", "Usage", "OnDemand")
    empty = _row(unit="")
    assert (empty["meter_category"], empty["unit"], empty["charge_type"], empty["pricing"]) == (None, None, None, None)


def test_the_capacity_name_comes_from_the_resource_id_when_there_is_no_name():
    assert _row()["capacity"] == "fcap01"
    assert _row(resource_name="Sales capacity")["capacity"] == "Sales capacity"


def test_the_duplicate_keys_stay_the_same_across_releases():
    # Earlier imports stored these; a changed key would let the same file import twice.
    got = _row(charge_type="Usage", pricing="OnDemand")
    assert got["slot"] == "cost_management:6f23354e12714ace1d25d314"
    assert got["key"] == "cost_management:2cf75178ee4b5bce2de7d4b0"
