"""Azure Cost Management cost details exports: what a Fabric capacity was billed (the second provider).

Someone with Cost Management Reader on the capacity's subscription (usually finance or the
platform team) exports cost details as CSV, from the Azure portal's Exports or a downloaded
report, and hands the file over. Nothing here calls Azure.

Two documented layouts are read:

- **Cost details** (Actual cost): Date, MeterCategory, MeterSubCategory, MeterName, Quantity,
  UnitOfMeasure, CostInBillingCurrency (Cost in pay-as-you-go files), BillingCurrency,
  ResourceId, ResourceName, ChargeType, PricingModel. Microsoft notes casing can differ between
  account types; column names are compared without case.
- **FOCUS**: ChargePeriodStart, ChargePeriodEnd, SkuMeter (x_SkuMeterName), ConsumedQuantity,
  ConsumedUnit, BilledCost, BillingCurrency, ResourceId, ResourceName, ChargeCategory,
  PricingCategory.

Only rows of a Fabric capacity are kept: a resource id of type ``Microsoft.Fabric/capacities``.
Each row is one meter of one capacity on one day: its billed cost (money, measured, in its own
currency) and, for a compute meter (Microsoft Learn: names ending "Capacity Usage CU") billed in
hours, its CU hours. Storage meters (GB per month) and a reservation's "Fabric Capacity" meter
carry cost only. Amounts in different currencies are never added together.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from typing import Any, ClassVar

from .. import measurement as ms
from . import reader
from .providers import Context, Layout, Provider

FABRIC = re.compile(r"/providers/microsoft\.fabric/capacities/([^/]+)", re.IGNORECASE)
COMPUTE_SUFFIX = "capacity usage cu"
BILLED = ("Azure Cost Management bills a Fabric capacity per meter and day. Cost is the charge before credits "
          "and taxes, in the billing currency.")

_COMMON = {
    "meter_category": ("MeterCategory", "x_SkuMeterCategory"),
    "meter_subcategory": ("MeterSubCategory", "x_SkuMeterSubcategory"),
    "resource_id": ("ResourceId", "InstanceId"),
    "resource_name": ("ResourceName",),
    "currency": ("BillingCurrency", "BillingCurrencyCode"),
}

COST_DETAILS = Layout(
    id="cost_details", label="Cost details", timed=True, level="capacity",
    columns=_COMMON | {"start": ("Date", "UsageDateTime"), "meter": ("MeterName",), "quantity": ("Quantity",),
                       "unit": ("UnitOfMeasure",), "cost": ("CostInBillingCurrency", "Cost"),
                       "charge_type": ("ChargeType",), "pricing": ("PricingModel",)},
    required=("start", "meter", "quantity", "cost", "currency", "resource_id"),
    numbers=("quantity", "cost"), times=("start",))

FOCUS = Layout(
    id="focus", label="FOCUS cost and usage", timed=True, level="capacity",
    columns=_COMMON | {"start": ("ChargePeriodStart",), "end": ("ChargePeriodEnd",), "meter": ("SkuMeter", "x_SkuMeterName"),
                       "quantity": ("ConsumedQuantity",), "unit": ("ConsumedUnit",), "cost": ("BilledCost",),
                       "charge_type": ("ChargeCategory",), "pricing": ("PricingCategory",)},
    required=("start", "meter", "quantity", "cost", "currency", "resource_id"),
    numbers=("quantity", "cost"), times=("start", "end"),
    refuse={"EffectiveCost": "EffectiveCost is the amortized cost; export Actual cost (BilledCost) so a reservation "
                             "purchase isn't counted twice."})


def _hours(unit: str) -> float | None:
    """How many hours one unit is, from text like "1 Hour", "10 Hours" or "Hours"; None when it isn't hours."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)?\s*(?:cu\s*)?hours?\s*", (unit or "").lower())
    hours = float(m.group(1) or 1) if m else None
    return hours if hours is None or math.isfinite(hours) else None


class CostManagement(Provider):
    id = "cost_management"
    label = "Azure Cost Management export"
    layouts = (COST_DETAILS, FOCUS)
    private: ClassVar[dict[str, str]] = {}
    about = "cost details (Actual cost) or FOCUS exported as CSV, with a Fabric capacity's ResourceId"

    def row(self, cells: dict[str, str], layout: Layout, ctx: Context) -> dict[str, Any] | str:
        found = FABRIC.search(cells.get("resource_id", ""))
        if not found:
            return "Not a Fabric capacity"
        cost = reader.number(cells.get("cost", ""), ctx.decimal)
        quantity = reader.number(cells.get("quantity", ""), ctx.decimal)
        if cost is None:
            return "No cost" if not cells.get("cost") else "The cost isn't a number"
        if cost < 0 or (quantity is not None and quantity < 0):
            return "A refund or credit (negative amount)"
        currency = cells.get("currency", "").strip().upper()
        if not currency:
            return "No billing currency"
        start = reader.when(cells.get("start", ""), ctx.date_order, ctx.tz)
        if start is None:
            return "No date" if not cells.get("start") else "The date can't be read"
        end = reader.when(cells.get("end", ""), ctx.date_order, ctx.tz) if cells.get("end") else None
        if cells.get("end") and end is None:
            return "The end date can't be read"
        if end is None:
            end = start + dt.timedelta(days=1)  # a cost details row (no end column) is one day
        if end <= start:
            return "Ends before it starts"
        meter, unit = cells.get("meter", "").strip(), cells.get("unit", "").strip()
        resource_id = cells["resource_id"].strip()
        capacity = cells.get("resource_name", "").strip() or found.group(1)
        source = self.source(ctx)
        method = f"{layout.columns['cost'][0]} column of the {layout.label} export"
        money = ms.make(ms.MEASURED, "billed_cost", ms.CURRENCY, cost, currency=currency, capacity_id=resource_id.lower(),
                        source=source, method=method, collected_at=ctx.imported_at, period_start=start,
                        period_end=end, uncertainty=BILLED, reconciliation="unreconciled")
        cu = None
        hours = _hours(unit)
        if meter.lower().endswith(COMPUTE_SUFFIX) and quantity is not None and hours is not None:
            if not math.isfinite(quantity * hours):
                return "The quantity is too large to be real usage"
            cu = ms.make(ms.MEASURED, "billed_capacity_cu", ms.CU_HOURS, quantity * hours, capacity_id=resource_id.lower(),
                         source=source, method=f"{layout.columns['quantity'][0]} x {unit!r} of a compute meter ({meter})",
                         collected_at=ctx.imported_at, period_start=start, period_end=end, uncertainty=BILLED,
                         reconciliation="unreconciled")
        # The same capacity, meter, day and charge in two exports: a month-to-date export repeats
        # earlier days, and its last day may still be growing. "slot" names that cell, so the
        # export imported last replaces it; "key" also has the amounts, so an identical row is a duplicate.
        slot_of = [resource_id.lower(), meter, reader.iso(start), reader.iso(end), cells.get("charge_type"),
                   cells.get("pricing"), currency]
        slot = "cost_management:" + hashlib.sha256(json.dumps(slot_of).encode()).hexdigest()[:24]
        key = "cost_management:" + hashlib.sha256(json.dumps(slot_of[:6] + [quantity, cost, currency]).encode()).hexdigest()[:24]
        return {"key": key, "slot": slot, "provider": self.id, "layout": layout.id, "level": layout.level, "workspace": None,
                "item": None, "capacity_id": resource_id.lower(), "capacity": capacity, "meter": meter or None,
                "meter_category": cells.get("meter_category") or None, "unit": unit or None, "quantity": quantity,
                "charge_type": cells.get("charge_type") or None, "pricing": cells.get("pricing") or None,
                "billable": None, "start": reader.iso(start), "end": reader.iso(end), "cu": cu, "cost": money}
