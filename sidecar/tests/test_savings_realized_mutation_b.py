"""Mutation tests for savings/realized.py (batch 457): the refusals and their wording, the exact
edges of the tolerances, the list of missing days, and the parts of net benefit."""

from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import realized as rz

ARM = "/subscriptions/s1/resourcegroups/rg/providers/microsoft.fabric/capacities/prodf64"
CAP = "11111111-2222-3333-4444-555555555555"
AT = "2026-11-20T09:00:00+00:00"
BEFORE = {"start": "2026-11-01T00:00:00Z", "end": "2026-11-08T00:00:00Z"}
AFTER = {"start": "2026-11-09T00:00:00Z", "end": "2026-11-16T00:00:00Z"}
DAY = dt.timedelta(days=1)
F64_DAY, F32_DAY = 64 * 24, 32 * 24
SRC = "Azure Cost Management export (costs.csv)"


def day(n):
    return dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc) + (n - 1) * DAY


def bill(n, cu, rate=0.18, *, currency="USD", cost=None, key=None):
    start, end = day(n), day(n) + DAY
    money = ms.make(ms.MEASURED, "billed_cost", ms.CURRENCY, cu * rate if cost is None else cost, currency=currency,
                    capacity_id=ARM, source=SRC, method="CostInBillingCurrency column", collected_at=AT,
                    period_start=start, period_end=end)
    cu_m = ms.make(ms.MEASURED, "billed_capacity_cu", ms.CU_HOURS, cu, capacity_id=ARM, source=SRC,
                   method="Quantity x '1 Hour'", collected_at=AT, period_start=start, period_end=end)
    k = key or f"cm:{n}"
    return {"key": k, "slot": k, "capacity_id": ARM, "meter": "Capacity Usage CU", "start": start.isoformat(),
            "end": end.isoformat(), "cu": cu_m, "cost": money}


def days(first, last, cu, **kw):
    return [bill(n, cu, **kw) for n in range(first, last + 1)]


BILL = days(1, 7, F64_DAY) + days(9, 15, F32_DAY)
CHANGE = {"record_id": f"change:{CAP}:sku", "scope_type": "capacity", "scope_id": CAP, "setting": "sku",
          "old_json": json.dumps("F64"), "new_json": json.dumps("F32"), "changed_after": "2026-11-08T06:00:00Z",
          "changed_by_time": "2026-11-08T18:00:00Z"}


def action(**kw):
    a = {"action_id": "act-1", "status": "confirmed", "confirmed_by": "oid-person", "confirmed_at": AT,
         "optimization_ids": ["opt-a"], "billing_capacity_id": ARM, "capacity_id": CAP,
         "change_record_id": CHANGE["record_id"]}
    return a | kw


def billing_action(**kw):
    return action(change_record_id=None, **kw)


# ---- billing rows


def test_a_row_that_ends_when_it_starts_is_refused():
    r = bill(9, F32_DAY)
    r["end"] = r["start"]
    with pytest.raises(ValueError, match="Billing row cm:9 ends before it starts"):
        rz.realize(billing_action(), days(1, 7, F64_DAY) + [r], BEFORE, AFTER)


def test_one_key_with_two_amounts_is_refused_even_on_another_slot():
    twin = bill(9, F32_DAY, rate=0.2)
    twin["slot"] = "elsewhere"
    with pytest.raises(ValueError, match="Billing row cm:9 appears twice with different amounts"):
        rz.realize(billing_action(), BILL + [twin], BEFORE, AFTER)


def test_one_key_twice_with_the_same_amounts_counts_once():
    twin = bill(9, F32_DAY)
    twin["slot"] = "elsewhere"
    out = rz.realize(action(), BILL + [twin], BEFORE, AFTER, changes=[CHANGE])
    assert out["realized_savings"]["value"] == pytest.approx(7 * F32_DAY * 0.18)


def test_a_row_without_its_own_end_takes_the_cost_periods_end():
    rows = days(1, 7, F64_DAY) + days(9, 15, F32_DAY)
    for r in rows:
        del r["end"]
    out = rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])
    assert out["realized"] is True


def test_the_source_is_the_exports_own_name():
    out = rz.realize(action(), BILL, BEFORE, AFTER, changes=[CHANGE])
    assert out["realized_savings"]["source"] == SRC and out["billed_before"]["source"] == SRC


def test_a_row_without_a_key_is_named_by_its_place_counting_from_1():
    r = bill(1, F64_DAY)
    r["cost"] = dict(r["cost"], value=-1.0)
    del r["key"]
    with pytest.raises(ValueError, match=r"Billing row #1 has a negative cost"):
        rz.realize(billing_action(), [r], BEFORE, AFTER)


def test_mixed_currencies_name_each_row_with_its_currency():
    rows = [bill(1, F64_DAY, key="u"), bill(2, F64_DAY, currency="EUR", key="e")]
    msg = "Billing rows e (EUR) and u (USD) are in different currencies"
    with pytest.raises(ValueError, match=re.escape(msg)):
        rz.realize(billing_action(), rows, BEFORE, AFTER)


# ---- the SKU change


def test_a_sku_change_without_the_capacity_is_refused_with_its_own_reason():
    with pytest.raises(ValueError, match="names a SKU change but not the capacity it changed"):
        rz.realize(action(capacity_id=None), BILL, BEFORE, AFTER, changes=[CHANGE])


def test_a_history_row_for_another_setting_is_refused():
    with pytest.raises(ValueError, match="records 'state', not a SKU"):
        rz.realize(action(), BILL, BEFORE, AFTER, changes=[CHANGE | {"setting": "state"}])


def test_a_bill_of_exactly_the_old_skus_hours_plus_the_tolerance_fits():
    x = round((64 * 168 + rz.TOLERANCE) - 6 * F64_DAY, 6)  # as a measurement keeps it
    rows = [bill(1, x, cost=F64_DAY * 0.18)] + days(2, 7, F64_DAY) + days(9, 15, F32_DAY)
    assert sum([x] + [float(F64_DAY)] * 6) == 64 * 168 + rz.TOLERANCE
    out = rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])
    assert out["realized"] is True



def test_a_bill_of_exactly_the_new_skus_hours_plus_the_tolerance_fits():
    x = round((32 * 168 + rz.TOLERANCE) - 6 * F32_DAY, 6)
    rows = days(1, 7, F64_DAY) + [bill(9, x, cost=F32_DAY * 0.18)] + days(10, 15, F32_DAY)
    assert sum([x] + [float(F32_DAY)] * 6) == 32 * 168 + rz.TOLERANCE
    out = rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])
    assert out["realized"] is True

# ---- periods


def test_an_after_period_that_starts_when_the_before_period_ends_is_allowed():
    after = {"start": "2026-11-08T00:00:00Z", "end": "2026-11-15T00:00:00Z"}
    rows = days(1, 7, F64_DAY) + days(8, 14, F64_DAY / 2)
    out = rz.realize(billing_action(), rows, BEFORE, after)
    assert out["realized"] is True


def _missing_reason(n_missing):
    before = {"start": "2026-11-01T00:00:00Z", "end": "2026-11-15T00:00:00Z"}
    after = {"start": "2026-11-15T00:00:00Z", "end": "2026-11-29T00:00:00Z"}
    rows = days(1, 14, F64_DAY) + days(15 + n_missing, 28, F64_DAY)
    out = rz.realize(billing_action(), rows, before, after)
    assert out["realized"] is False
    return out["reason"]


def test_one_missing_day_is_named_without_and_more():
    reason = _missing_reason(1)
    assert "(2026-11-15)" in reason and "and more" not in reason


def test_eleven_missing_days_list_ten_and_say_and_more():
    reason = _missing_reason(11)
    listed = re.findall(r"2026-11-\d\d", reason)
    assert listed == [f"2026-11-{d}" for d in range(15, 25)]
    assert "2026-11-24, and more)" in reason


def test_twelve_missing_days_still_list_only_ten():
    assert len(re.findall(r"2026-11-\d\d", _missing_reason(12))) == 10


# ---- claimed amounts


def test_a_period_under_one_cu_hour_still_gives_its_rate():
    rows = days(1, 7, F64_DAY) + days(9, 15, 0.1, rate=0.10)
    out = rz.realize(billing_action(), rows, BEFORE, AFTER)
    pre_cu, post_cu = 7 * F64_DAY, sum([0.1] * 7)
    assert out["realized_savings"]["value"] == pytest.approx((pre_cu - post_cu) * 0.10)


def test_a_drop_of_exactly_the_tolerance_is_no_drop():
    rows = [bill(1, 1, cost=rz.TOLERANCE)] + days(2, 7, 0, cost=0.0) + days(9, 15, 0, cost=0.0)
    out = rz.realize(billing_action(), rows, BEFORE, AFTER)
    assert out["realized"] is False and "did not go down" in out["reason"]


def test_a_claim_of_exactly_the_tolerance_is_not_realized():
    rows = ([bill(1, 2, cost=1.0)] + days(2, 7, 0, cost=0.0) + [bill(9, 1, cost=rz.TOLERANCE)]
            + days(10, 15, 0, cost=0.0))
    out = rz.realize(billing_action(), rows, BEFORE, AFTER)
    assert out["realized"] is False and "price change" in out["reason"]


# ---- net benefit


def realized():
    return rz.realize(action(), BILL, BEFORE, AFTER, changes=[CHANGE])


def cost(cat, value, cls=ms.MEASURED, currency="USD"):
    if cls == ms.MEASURED:
        m = ms.make(cls, "cost", ms.CURRENCY, value, currency=currency, source="sync log", collected_at=AT,
                    period_start=AFTER["start"], period_end=AFTER["end"])
    else:
        m = ms.make(cls, "cost", ms.CURRENCY, value, currency=currency, method="m", assumptions=["from the log"],
                    period_start=AFTER["start"], period_end=AFTER["end"])
    return {"category": cat, "cost": m}


def all_costs(**over):
    return [over.get(c) or cost(c, 0.0) for c in rz.COST_CATEGORIES]


def test_a_cost_without_a_figure_is_refused():
    with pytest.raises(ValueError, match=re.escape("Cost 1 (sync_overhead) has no figure")):
        rz.net_benefit(realized(), [{"category": "sync_overhead", "cost": None}])


def test_a_bad_category_names_the_cost_counting_from_1():
    with pytest.raises(ValueError, match=re.escape("Cost 1 (bogus) isn't one of")):
        rz.net_benefit(realized(), [{"category": "bogus", "cost": None}])


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf"), True])
def test_a_negative_or_non_finite_cost_is_refused_with_its_own_reason(value):
    c = cost("sync_overhead", 0.0)
    c["cost"] = dict(c["cost"], value=value)
    with pytest.raises(ValueError, match="must be a finite amount of zero or more"):
        rz.net_benefit(realized(), all_costs(sync_overhead=c))


def test_a_cost_in_another_currency_names_the_bills_currency():
    with pytest.raises(ValueError, match="is in EUR, but the bill is in USD"):
        rz.net_benefit(realized(), all_costs(sync_overhead=cost("sync_overhead", 1.0, currency="EUR")))


def test_an_estimated_net_carries_the_costs_own_assumptions():
    out = rz.net_benefit(realized(), all_costs(collector_calls=cost("collector_calls", 5.0, ms.ESTIMATED)))
    assert out["net"]["assumptions"] == ["from the log"]


@pytest.mark.parametrize("left", [0.0, 0.5])
def test_a_net_from_zero_up_to_one_is_a_benefit(left):
    e = realized()["realized_savings"]["value"]
    out = rz.net_benefit(realized(), all_costs(kratosforge=cost("kratosforge", round(e - left, 6))))
    assert out["signed"] == pytest.approx(left) and out["net"]["metric"] == rz.NET
