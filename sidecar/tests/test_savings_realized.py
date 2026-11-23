"""ESI-403: realized savings only from billing or capacity-change evidence, linked by a person.

An F64 with lower utilization shows value and headroom, not realized savings. Nothing is realized
without a bill and a confirmed action; a missing figure is None, never 0."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import pricing as pr
from fabric_desktop.savings import realized as rz

ARM = "/subscriptions/s1/resourcegroups/rg/providers/microsoft.fabric/capacities/prodf64"
CAP = "11111111-2222-3333-4444-555555555555"
AT = "2026-11-20T09:00:00+00:00"
BEFORE = {"start": "2026-11-01T00:00:00Z", "end": "2026-11-08T00:00:00Z"}
AFTER = {"start": "2026-11-09T00:00:00Z", "end": "2026-11-16T00:00:00Z"}
DAY = dt.timedelta(days=1)


def day(n):
    return dt.datetime(2026, 11, n, tzinfo=dt.timezone.utc)


def bill(n, cu, rate=0.18, *, currency="USD", meter="Capacity Usage CU", capacity=ARM, cost=None, key=None):
    """One cost details row as usage/cost_management.py gives it: one compute meter, one day."""
    start, end = day(n), day(n) + DAY
    src = "Azure Cost Management export (costs.csv)"
    money = ms.make(ms.MEASURED, "billed_cost", ms.CURRENCY, cu * rate if cost is None else cost, currency=currency,
                    capacity_id=capacity, source=src, method="CostInBillingCurrency column", collected_at=AT,
                    period_start=start, period_end=end)
    cu_m = (ms.make(ms.MEASURED, "billed_capacity_cu", ms.CU_HOURS, cu, capacity_id=capacity, source=src,
                    method="Quantity x '1 Hour'", collected_at=AT, period_start=start, period_end=end)
            if meter.endswith("CU") else None)
    k = key or f"cm:{capacity[-7:]}:{meter}:{n}"
    return {"key": k, "slot": k, "provider": "cost_management", "capacity_id": capacity, "meter": meter,
            "start": start.isoformat(), "end": end.isoformat(), "cu": cu_m, "cost": money}


def days(first, last, cu, **kw):
    return [bill(n, cu, **kw) for n in range(first, last + 1)]


F64_DAY, F32_DAY = 64 * 24, 32 * 24
BILL_F64_TO_F32 = days(1, 7, F64_DAY) + days(9, 15, F32_DAY)

CHANGE = {"record_id": f"change:{CAP}:sku:h:2026-11-08T18:00:00Z", "scope_type": "capacity", "scope_id": CAP,
          "setting": "sku", "old_json": json.dumps("F64"), "new_json": json.dumps("F32"),
          "changed_after": "2026-11-08T06:00:00Z", "changed_by_time": "2026-11-08T18:00:00Z",
          "source": "GET /v1/capacities", "read_by": "u1"}


def action(**kw):
    a = {"action_id": "act-1", "status": "confirmed", "confirmed_by": "oid-person", "confirmed_at": AT,
         "optimization_ids": ["opt-a", "opt-b"], "billing_capacity_id": ARM, "capacity_id": CAP,
         "change_record_id": CHANGE["record_id"]}
    return a | kw


def billing_action(**kw):
    return action(change_record_id=None, **kw)


# ---- realized from a SKU change


def test_a_sku_change_with_its_bill_and_a_confirmed_action_is_realized_and_measured():
    out = rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE])
    m = out["realized_savings"]
    assert out["realized"] is True and out["reason"] is None and out["evidence"] == rz.SKU_CHANGE
    assert m["classification"] == ms.MEASURED and m["currency"] == "USD"
    assert m["value"] == pytest.approx(7 * F32_DAY * 0.18)  # half of F64 for 7 days
    assert out["not_claimed"]["value"] == 0
    assert out["sku"]["old"] == "F64" and out["sku"]["new"] == "F32"
    assert out["optimization_ids"] == ["opt-a", "opt-b"] and out["confirmed_by"] == "oid-person"
    assert ms.problems(m) == []


def test_a_pause_on_top_of_a_sku_change_is_not_claimed():
    rows = days(1, 7, F64_DAY) + days(9, 13, F32_DAY) + days(14, 15, F32_DAY / 2)
    out = rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])
    after_cu = 5 * F32_DAY + 2 * F32_DAY / 2
    assert out["realized_savings"]["value"] == pytest.approx(32 * (after_cu / 32) * 0.18)
    drop = 7 * F64_DAY * 0.18 - after_cu * 0.18
    assert out["not_claimed"]["value"] == pytest.approx(drop - out["realized_savings"]["value"])
    assert out["realized_savings"]["value"] + out["not_claimed"]["value"] == pytest.approx(drop)


def test_a_price_rise_after_the_change_caps_the_claim_at_the_bills_actual_drop():
    rows = days(1, 7, F64_DAY) + days(9, 15, F32_DAY, rate=0.20)
    out = rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])
    drop = 7 * F64_DAY * 0.18 - 7 * F32_DAY * 0.20
    assert out["realized_savings"]["value"] == pytest.approx(drop)


def test_a_price_cut_is_not_claimed_as_part_of_the_sku_change():
    rows = days(1, 7, F64_DAY) + days(9, 15, F32_DAY, rate=0.15)
    out = rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])
    assert out["realized_savings"]["value"] == pytest.approx(32 * 7 * 24 * 0.15)  # lower rate
    assert out["not_claimed"]["value"] > 0


@pytest.mark.parametrize("old, new", [("F32", "F64"), ("F64", "F64")])
def test_a_sku_that_did_not_get_smaller_saved_nothing(old, new):
    change = CHANGE | {"old_json": json.dumps(old), "new_json": json.dumps(new)}
    out = rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[change])
    assert out["realized"] is False and out["realized_savings"]["value"] is None
    assert "not smaller" in out["reason"]


def test_a_change_seen_inside_a_period_is_not_realized():
    change = CHANGE | {"changed_after": "2026-11-05T00:00:00Z"}
    out = rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[change])
    assert out["realized"] is False and "one SKU" in out["reason"]


def test_a_bill_that_does_not_fit_the_recorded_sku_is_not_realized():
    rows = days(1, 7, F64_DAY) + days(9, 15, F64_DAY / 2 + 10)  # more than F32 can bill
    out = rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])
    assert out["realized"] is False and "doesn't fit" in out["reason"]


def test_a_change_missing_from_the_history_or_without_an_earlier_sku_is_not_realized():
    out = rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[])
    assert out["realized"] is False and "isn't in the capacity history" in out["reason"]
    first = CHANGE | {"old_json": None, "changed_after": None}
    out = rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[first])
    assert out["realized"] is False and "no earlier SKU" in out["reason"]


def test_a_non_f_sku_is_not_realized():
    change = CHANGE | {"old_json": json.dumps("P1"), "new_json": json.dumps("F32")}
    out = rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[change])
    assert out["realized"] is False and "only F SKUs" in out["reason"]


# ---- realized from billing alone


def test_fewer_billed_hours_with_a_confirmed_action_are_realized():
    rows = days(1, 7, F64_DAY) + days(9, 15, F64_DAY * 0.5)  # paused half of every day
    out = rz.realize(billing_action(), rows, BEFORE, AFTER)
    assert out["realized"] is True and out["evidence"] == rz.BILLING
    assert out["realized_savings"]["value"] == pytest.approx(7 * F64_DAY * 0.5 * 0.18)


def test_storage_rows_dont_cover_a_day_without_a_compute_row():
    storage = [bill(n, 0, meter="OneLake Storage", cost=1.0) for n in range(9, 16)]
    out = rz.realize(billing_action(), days(1, 7, F64_DAY) + storage, BEFORE, AFTER)
    assert out["realized"] is False and out["realized_savings"]["value"] is None
    assert "no compute row" in out["reason"] and "0 CU" in out["reason"]


def test_one_missing_compute_day_hidden_by_storage_rows_is_not_realized():
    # Quality probe: F64 both periods, the 11-12 compute row dropped, a storage row every day.
    storage = [bill(n, 0, meter="OneLake Storage", cost=1.0) for n in range(1, 16) if n != 8]
    rows = days(1, 7, F64_DAY) + [r for r in days(9, 15, F64_DAY) if r["start"] != day(12).isoformat()] + storage
    out = rz.realize(billing_action(), rows, BEFORE, AFTER)
    assert out["realized"] is False and out["realized_savings"]["value"] is None
    assert day(12).date().isoformat() in out["reason"]


def test_a_fully_paused_after_period_with_zero_cu_compute_rows_is_realized():
    out = rz.realize(billing_action(), days(1, 7, F64_DAY) + days(9, 15, 0), BEFORE, AFTER)
    assert out["realized_savings"]["value"] == pytest.approx(7 * F64_DAY * 0.18)
    assert out["billed_after"]["value"] == 0


def test_a_price_change_alone_is_not_realized():
    rows = days(1, 7, F64_DAY) + days(9, 15, F64_DAY, rate=0.15)
    out = rz.realize(billing_action(), rows, BEFORE, AFTER)
    assert out["realized"] is False and out["realized_savings"]["value"] is None
    assert "price change" in out["reason"]


def test_a_reserved_capacity_billed_at_zero_realizes_nothing():
    rows = days(1, 7, F64_DAY, rate=0.0) + days(9, 15, F32_DAY, rate=0.0)
    out = rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])
    assert out["realized"] is False and "did not go down" in out["reason"]


# ---- acceptance: an F64 with lower utilization


def test_an_f64_with_lower_utilization_shows_value_and_headroom_not_realized_savings():
    rows = days(1, 7, F64_DAY) + days(9, 15, F64_DAY)  # same SKU, same hours: the same bill
    out = rz.realize(billing_action(), rows, BEFORE, AFTER)
    assert out["realized"] is False
    assert out["realized_savings"]["classification"] == ms.UNKNOWN and out["realized_savings"]["value"] is None
    assert "value and headroom" in out["reason"]
    # The CU the optimizations avoided is still worth something, as a Scenario, never as savings.
    price = pr.scenario(scenario="List", rate_per_cu_hour=0.18, currency="USD", effective_from="2026-01-01T00:00:00Z",
                        pricing_model=pr.PAY_AS_YOU_GO, confidence="confirmed", source="price sheet")
    value = pr.value(400.0, "2026-11-10T00:00:00Z", [price], "List")
    assert value["classification"] == ms.SCENARIO and value["metric"] != rz.METRIC
    bill_drop = ms.make(ms.MEASURED, rz.METRIC, ms.CURRENCY, 1.0, currency="USD", source="x", collected_at=AT)
    with pytest.raises(ValueError):
        ms.total([value, bill_drop], rz.METRIC, ms.CURRENCY, currency="USD")


# ---- not realized: missing evidence, never 0


def test_no_action_is_not_realized():
    out = rz.realize(None, BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE])
    assert out["realized"] is False and out["realized_savings"]["value"] is None
    assert out["reason"].endswith(".") and "confirmed" in out["reason"]


def test_no_bill_is_not_realized():
    out = rz.realize(action(), [], BEFORE, AFTER, changes=[CHANGE])
    assert out["realized"] is False and out["realized_savings"]["value"] is None and "no billing" in out["reason"]


def test_another_capacitys_bill_is_not_this_ones():
    other = "/subscriptions/s1/resourcegroups/rg/providers/microsoft.fabric/capacities/other"
    out = rz.realize(action(), days(1, 7, F64_DAY, capacity=other) + days(9, 15, F32_DAY, capacity=other),
                     BEFORE, AFTER, changes=[CHANGE])
    assert out["realized"] is False and "no billing" in out["reason"]


def test_a_missing_day_in_the_after_period_is_not_read_as_a_saving():
    rows = days(1, 7, F64_DAY) + days(9, 14, F32_DAY)
    out = rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])
    assert out["realized"] is False and "1 days of the after period" in out["reason"]


def test_periods_of_different_lengths_are_not_compared():
    out = rz.realize(action(), BILL_F64_TO_F32, BEFORE, {"start": AFTER["start"], "end": "2026-11-15T00:00:00Z"},
                     changes=[CHANGE])
    assert out["realized"] is False and "different lengths" in out["reason"]


def test_a_row_across_a_period_edge_is_not_realized():
    rows = days(1, 7, F64_DAY) + days(9, 15, F32_DAY)
    after = {"start": "2026-11-09T12:00:00Z", "end": "2026-11-16T12:00:00Z"}
    before = {"start": "2026-11-01T00:00:00Z", "end": "2026-11-08T00:00:00Z"}
    out = rz.realize(billing_action(), rows + [bill(16, F32_DAY)], before, after)
    assert out["realized"] is False and "edge of a period" in out["reason"]


def test_the_same_row_from_two_exports_counts_once():
    out = rz.realize(action(), BILL_F64_TO_F32 + [bill(9, F32_DAY)], BEFORE, AFTER, changes=[CHANGE])
    assert out["realized_savings"]["value"] == pytest.approx(7 * F32_DAY * 0.18)


# ---- refused


def _cost_row(value):
    r = bill(9, F32_DAY)
    r["cost"] = dict(r["cost"], value=value)
    return r


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_a_nan_infinite_or_negative_amount_is_refused_naming_the_row(value):
    rows = days(1, 7, F64_DAY) + [_cost_row(value)] + days(10, 15, F32_DAY)
    with pytest.raises(ValueError, match=r"Billing row cm:.*:9"):
        rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])


def test_mixed_currencies_are_refused():
    rows = days(1, 7, F64_DAY) + days(9, 15, F32_DAY, currency="EUR")
    with pytest.raises(ValueError, match="different currencies"):
        rz.realize(action(), rows, BEFORE, AFTER, changes=[CHANGE])


def test_a_price_list_figure_is_not_a_bill():
    r = bill(9, F32_DAY)
    r["cost"] = ms.make(ms.SCENARIO, "billed_cost", ms.CURRENCY, 1.0, currency="USD", method="list price",
                        assumptions=["price sheet"])
    with pytest.raises(ValueError, match="Scenario"):
        rz.realize(action(), days(1, 7, F64_DAY) + [r] + days(10, 15, F32_DAY), BEFORE, AFTER, changes=[CHANGE])


@pytest.mark.parametrize("before, after", [
    (BEFORE, {"start": "2026-11-07T00:00:00Z", "end": "2026-11-14T00:00:00Z"}),  # overlap
    (AFTER, BEFORE),  # wrong order
    ({"start": "2026-11-08T00:00:00Z", "end": "2026-11-01T00:00:00Z"}, AFTER),  # reversed
])
def test_overlapping_or_reversed_periods_are_refused(before, after):
    with pytest.raises(ValueError, match="period"):
        rz.realize(action(), BILL_F64_TO_F32, before, after, changes=[CHANGE])


def test_the_same_meter_and_day_from_two_exports_counts_once():
    twin = bill(9, F32_DAY, key="cm:other")
    twin["slot"] = bill(9, F32_DAY)["slot"]
    once = rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE])
    both = rz.realize(action(), BILL_F64_TO_F32 + [twin], BEFORE, AFTER, changes=[CHANGE])
    assert both["realized_savings"]["value"] == once["realized_savings"]["value"]


def test_two_amounts_for_one_meter_and_day_are_refused():
    twin = bill(9, F32_DAY, rate=0.2, key="cm:other")
    twin["slot"] = bill(9, F32_DAY)["slot"]
    with pytest.raises(ValueError, match="same meter and day"):
        rz.realize(action(), BILL_F64_TO_F32 + [twin], BEFORE, AFTER, changes=[CHANGE])


@pytest.mark.parametrize("kw", [{"status": "proposed"}, {"confirmed_by": ""}, {"confirmed_by": None}])
def test_an_action_not_confirmed_by_a_person_is_refused(kw):
    with pytest.raises(ValueError, match="Action act-1 isn't confirmed by a person"):
        rz.realize(action(**kw), BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE])


def test_an_action_linking_no_optimization_is_refused():
    with pytest.raises(ValueError, match="links no optimization"):
        rz.realize(action(optimization_ids=[]), BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE])


def test_a_change_for_another_capacity_is_refused():
    with pytest.raises(ValueError, match="not " + CAP):
        rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE | {"scope_id": "other"}])


# ---- allocation


def avoided(value, cls=ms.MEASURED):
    kw = ({"source": "Capacity Metrics export", "collected_at": AT} if cls == ms.MEASURED
          else {"method": "calibration", "assumptions": ["median"]})
    return ms.make(cls, "fabric_job_cu_avoided", ms.CU_HOURS, value, period_start=AFTER["start"],
                   period_end=AFTER["end"], **kw)


def test_allocation_is_pro_rata_to_measured_cu_and_adds_up_exactly():
    out = rz.realize(action(optimization_ids=["a", "b", "c"]), BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE])
    split = rz.allocate(out, {"a": avoided(1.0), "b": avoided(2.0), "c": avoided(5.0, ms.ESTIMATED)})
    assert split["split"] is True and split["method"] == rz.PRO_RATA
    shares = {r["optimization_id"]: r["share"]["value"] for r in split["allocations"]}
    assert set(shares) == {"a", "b"}
    assert round(sum(shares.values()), 6) == out["realized_savings"]["value"]
    assert shares["b"] == pytest.approx(2 * shares["a"], abs=1e-5)
    assert split["left_out"][0]["optimization_id"] == "c"


def test_without_measured_cu_the_realized_figure_stays_whole():
    out = rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE])
    split = rz.allocate(out, {"opt-a": avoided(3.0, ms.ESTIMATED)})
    assert split["split"] is False and split["allocations"] == []


def test_nothing_realized_is_never_split():
    assert rz.allocate(rz.realize(None, [], BEFORE, AFTER), {})["split"] is False


# ---- measure F


def cost(cat, value, cls=ms.MEASURED, currency="USD"):
    if cls == ms.UNKNOWN:
        m = ms.unknown("cost", ms.CURRENCY, "not recorded", period_start=AFTER["start"], period_end=AFTER["end"])
    elif cls == ms.MEASURED:
        m = ms.make(cls, "cost", ms.CURRENCY, value, currency=currency, source="sync log", collected_at=AT,
                    period_start=AFTER["start"], period_end=AFTER["end"])
    else:
        m = ms.make(cls, "cost", ms.CURRENCY, value, currency=currency, method="m", assumptions=["a"],
                    period_start=AFTER["start"], period_end=AFTER["end"])
    return {"category": cat, "cost": m}


def all_costs(**over):
    return [over.get(c) or cost(c, 0.0) for c in rz.COST_CATEGORIES]


def realized():
    return rz.realize(action(), BILL_F64_TO_F32, BEFORE, AFTER, changes=[CHANGE])


def test_net_is_realized_minus_every_cost():
    out = rz.net_benefit(realized(), all_costs(sync_overhead=cost("sync_overhead", 10.0),
                                               kratosforge=cost("kratosforge", 50.0)))
    assert out["net"]["classification"] == ms.MEASURED and out["net"]["metric"] == rz.NET
    assert out["signed"] == pytest.approx(7 * F32_DAY * 0.18 - 60)


def test_an_estimated_cost_makes_net_estimated():
    out = rz.net_benefit(realized(), all_costs(collector_calls=cost("collector_calls", 5.0, ms.ESTIMATED)))
    assert out["net"]["classification"] == ms.ESTIMATED


def test_costs_above_realized_are_a_shortfall():
    out = rz.net_benefit(realized(), all_costs(kratosforge=cost("kratosforge", 5000.0)))
    assert out["signed"] < 0 and out["net"]["metric"] == rz.SHORTFALL and out["net"]["value"] > 0


def test_a_missing_cost_category_makes_net_unknown_not_realized():
    out = rz.net_benefit(realized(), all_costs()[:-1])
    assert out["net"]["value"] is None and "KratosForge" not in out["reason"] and "kratosforge" in out["reason"]


def test_an_unknown_cost_makes_net_unknown():
    out = rz.net_benefit(realized(), all_costs(staged_merge=cost("staged_merge", None, ms.UNKNOWN)))
    assert out["net"]["value"] is None and "staged merge" in out["reason"]


def test_a_scenario_cost_is_never_subtracted_from_a_bill():
    out = rz.net_benefit(realized(), all_costs(sync_overhead=cost("sync_overhead", 1.0, ms.SCENARIO)))
    assert out["net"]["value"] is None and "Scenario" in out["reason"]


def test_net_without_realized_is_unknown():
    out = rz.net_benefit(rz.realize(action(), [], BEFORE, AFTER, changes=[CHANGE]), all_costs())
    assert out["net"]["value"] is None and out["reason"].startswith("Nothing is realized")


def test_a_cost_in_another_currency_is_refused():
    with pytest.raises(ValueError, match="EUR"):
        rz.net_benefit(realized(), all_costs(sync_overhead=cost("sync_overhead", 1.0, currency="EUR")))


def test_a_cost_outside_the_after_period_is_refused():
    c = cost("sync_overhead", 1.0)
    c["cost"] = dict(c["cost"], period={"start": BEFORE["start"], "end": BEFORE["end"]})
    with pytest.raises(ValueError, match="after period"):
        rz.net_benefit(realized(), all_costs(sync_overhead=c))
