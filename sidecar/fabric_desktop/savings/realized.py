"""Realized savings (measure E) and net financial benefit (measure F) (ESI-403).

Realized savings are money that a bill shows was not spent. They are never worked out from CU
avoided, a price list or utilization: those are capacity value (a Scenario) and headroom (an
Estimate), measures B and C. A capacity whose utilization fell still costs the same while its SKU
and running hours stay the same, so it shows value and headroom, not realized savings.

**Evidence.** A figure is realized only when all of these hold:

- **A bill.** Rows of an Azure Cost Management export (``usage/cost_management.py`` rows), for one
  capacity, over two periods of the same length: before and after. Only compute meters (the rows
  with billed CU hours) are compared. Storage meters and reservation purchases are not.
- **A person's confirmation.** A confirmed action, by a person (their user id), that names the
  capacity, the optimizations it links to and, for a SKU change, the ``fdcap_change`` row.
  An action that is not confirmed by a person is refused.
- **What changed, for a SKU change.** An ``fdcap_change`` row for setting ``sku``, seen between
  the two periods: the before period ends when the old SKU was last seen (``changed_after``) or
  earlier, and the after period starts when the new SKU was first seen (``changed_by_time``) or
  later.

**What is claimed.** Only the part of the bill's drop that the confirmed change explains, never
the whole drop:

- A **SKU change** claims the old SKU's extra CU for the hours the capacity ran after the change:
  ``(old CU - new CU) x after hours x the lower of the two billed rates``, where after hours are
  the after period's billed CU hours divided by the new SKU's CU.
- A **billing change** with no SKU change (for example a capacity paused for more hours) claims
  the drop in billed CU hours at the lower of the two billed rates.

Either way the claim is capped at the bill's actual drop. A price change is not claimed, because
the lower rate is used. Whatever else moved the bill (a pause, usage on other meters, a price
change) is reported as not claimed. The bill can't say why its hours changed, so a drop in billed
hours is taken as the confirmed action's only because a person said so. If the capacity's work
moved to another capacity, that capacity's bill went up; this module sees one capacity and can't
net that off, so the result says so.

**Not realized.** When any evidence is missing, the result is not realized: Unknown, value None
(never 0), with the reason in a full sentence. That covers no bill, a period the export doesn't
cover on every day (a missing day would look like a saving), periods of different lengths, a bill
that did not go down, a SKU that went up, and a bill that doesn't fit the recorded SKU.

**Refused.** Bad input raises ``ValueError`` naming the row: a NaN, negative or infinite amount,
a billing figure that isn't Measured (a price list is a Scenario, never a bill), mixed currencies,
reversed or overlapping periods, two different amounts for one meter and day, and an action that
isn't confirmed by a person or links no optimization.

**Allocation.** A realized figure is recorded once, at its capacity and period. ``allocate`` splits
it, only when asked, pro rata to the measured CU avoided of the linked optimizations. The shares
add up to exactly the realized figure and carry the method.

**Measure F.** ``net_benefit`` is realized savings minus every attributable cost: sync overhead,
ingestion overhead, collector calls, staged merge runs and KratosForge costs the organization
entered. Each category must be given, as 0 when it doesn't apply, or net is Unknown. Net is never
realized savings with a cost left out, and a Scenario cost is never subtracted from a bill.

Pure: no I/O, nothing written.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any

from .. import costs
from .. import measurement as ms
from .overhead import _split

METRIC = "realized_savings"
NOT_CLAIMED = "billed_drop_not_claimed"
NET, SHORTFALL = "net_financial_benefit", "net_financial_shortfall"
SKU_CHANGE, BILLING = "sku_change", "billing_change"
CONFIRMED = "confirmed"
COST_CATEGORIES = ("sync_overhead", "ingestion_overhead", "collector_calls", "staged_merge", "kratosforge")
PRO_RATA = "pro rata to measured CU avoided on the capacity in the period"
TOLERANCE = 1e-6
_MICRO = 1_000_000

ELSEWHERE = ("Work moved to another capacity shows up on that capacity's bill. Only this capacity's bill "
             "is read here, so that can't be netted off.")
REASON_DIDNT_SAY = ("The bill doesn't say why its hours changed. The drop is tied to the action only because "
                    "a person confirmed it.")


# ---- input checks ---------------------------------------------------------------------------------


def _time(value: Any, what: str) -> dt.datetime:
    t = ms.parse_time(value)
    if t is None:
        raise ValueError(f"{what} isn't a time: {value!r}.")
    return t


def _period(p: Any, what: str) -> tuple[dt.datetime, dt.datetime]:
    if isinstance(p, dict):
        start, end = p.get("start"), p.get("end")
    elif isinstance(p, (tuple, list)) and len(p) == 2:
        start, end = p
    else:
        raise ValueError(f"The {what} period needs a start and an end.")
    a, b = _time(start, f"The {what} period's start"), _time(end, f"The {what} period's end")
    if b <= a:
        raise ValueError(f"The {what} period ends before it starts.")
    return a, b


def _amount(m: Any, row: str, what: str, unit: str) -> float:
    if not isinstance(m, dict):
        raise ValueError(f"Billing row {row} has no {what} figure.")
    if m.get("classification") != ms.MEASURED:
        label = ms.LABELS.get(str(m.get("classification")), str(m.get("classification")))
        raise ValueError(f"Billing row {row} has a {label} "
                         f"{what}, not a Measured one. A price list is a Scenario; only a bill is evidence.")
    if m.get("unit") != unit:
        raise ValueError(f"Billing row {row} gives its {what} in {m.get('unit')}, not {unit}.")
    v = m.get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"Billing row {row} has a {what} that isn't a finite number: {v!r}.")
    if v < 0:
        raise ValueError(f"Billing row {row} has a negative {what} ({v}). A refund or credit isn't a saving.")
    return float(v)


def _rows(billing: list[dict[str, Any]], capacity: str) -> list[dict[str, Any]]:
    """The capacity's billing rows, checked, one per key. Rows of other capacities are left out."""
    by_key: dict[str, dict[str, Any]] = {}
    by_slot: dict[str, dict[str, Any]] = {}
    currencies: dict[str, str] = {}
    for i, r in enumerate(billing):
        if str(r.get("capacity_id") or "").lower() != capacity:
            continue
        name = str(r.get("key") or f"#{i + 1}")
        cost = _amount(r.get("cost"), name, "cost", ms.CURRENCY)
        cu = None if r.get("cu") is None else _amount(r["cu"], name, "billed CU", ms.CU_HOURS)
        currency = str(r["cost"].get("currency") or "").upper()
        if not currency:
            raise ValueError(f"Billing row {name} has no currency.")
        currencies.setdefault(currency, name)
        if len(currencies) > 1:
            a, b = sorted(currencies.items())[:2]
            raise ValueError(f"Billing rows {a[1]} ({a[0]}) and {b[1]} ({b[0]}) are in different currencies; "
                             "amounts in different currencies are never added together.")
        start = _time(r.get("start") or r["cost"].get("period", {}).get("start"), f"Billing row {name}'s start")
        end = _time(r.get("end") or r["cost"].get("period", {}).get("end"), f"Billing row {name}'s end")
        if end <= start:
            raise ValueError(f"Billing row {name} ends before it starts.")
        row = {"key": name, "slot": r.get("slot"), "meter": r.get("meter"), "start": start, "end": end,
               "cost": cost, "cu": cu, "currency": currency, "source": r["cost"].get("source") or "",
               "collected_at": r["cost"].get("collected_at")}
        seen = by_key.setdefault(name, row)
        if seen is not row and (seen["cost"], seen["cu"]) != (cost, cu):
            raise ValueError(f"Billing row {name} appears twice with different amounts.")
        if row["slot"]:
            other = by_slot.setdefault(row["slot"], row)
            if other is not row and other["key"] != name:
                raise ValueError(f"Billing rows {other['key']} and {name} are the same meter and day with different "
                                 "amounts. Keep the later export only.")
    return list(by_key.values())


def _action(action: dict[str, Any]) -> dict[str, Any]:
    name = str(action.get("action_id") or "without an id")
    if action.get("status") != CONFIRMED or not str(action.get("confirmed_by") or "").strip():
        raise ValueError(f"Action {name} isn't confirmed by a person. Realized savings need a person to confirm "
                         "what caused the change.")
    _time(action.get("confirmed_at"), f"Action {name}'s confirmation time")
    opts = [str(o) for o in action.get("optimization_ids") or [] if str(o).strip()]
    if not opts:
        raise ValueError(f"Action {name} links no optimization.")
    billing = str(action.get("billing_capacity_id") or "").strip().lower()
    if not billing:
        raise ValueError(f"Action {name} doesn't name the capacity's billing resource id.")
    return {"action_id": name, "optimization_ids": sorted(set(opts)), "billing_capacity_id": billing,
            "capacity_id": str(action.get("capacity_id") or "").lower() or None,
            "change_record_id": action.get("change_record_id") or None, "confirmed_by": action["confirmed_by"],
            "confirmed_at": action["confirmed_at"]}


def _sku(value_json: Any) -> str | None:
    if value_json is None:
        return None
    try:
        v = json.loads(value_json) if isinstance(value_json, str) else value_json
    except ValueError:
        v = value_json
    return str(v).strip().upper() if v not in (None, "") else None


# ---- periods ---------------------------------------------------------------------------------------


def _covered(rows: list[dict[str, Any]], start: dt.datetime, end: dt.datetime) -> float:
    """Seconds of [start, end) that at least one row covers."""
    spans = sorted((max(r["start"], start), min(r["end"], end)) for r in rows if r["start"] < end and r["end"] > start)
    total, cursor = 0.0, start
    for a, b in spans:
        a = max(a, cursor)
        if b > a:
            total += (b - a).total_seconds()
            cursor = b
    return total


def _sum(rows: list[dict[str, Any]], start: dt.datetime, end: dt.datetime) -> tuple[dict[str, float], str | None]:
    """Billed cost and CU hours of compute rows inside [start, end), or why it can't be read."""
    compute = [r for r in rows if r["cu"] is not None]
    for r in compute:
        if r["start"] < end and r["end"] > start and (r["start"] < start or r["end"] > end):
            return {}, (f"Billing row {r['key']} runs across the edge of a period. Start and end the periods "
                        "on the export's day boundaries.")
    inside = [r for r in compute if r["start"] >= start and r["end"] <= end]
    return {"cost": sum(r["cost"] for r in inside), "cu": sum(r["cu"] for r in inside), "rows": len(inside)}, None


def _not_realized(base: dict[str, Any], reason: str, **ids: Any) -> dict[str, Any]:
    return base | {"realized": False, "reason": reason,
                   "realized_savings": ms.unknown(METRIC, ms.CURRENCY, reason, **ids)}


# ---- measure E -------------------------------------------------------------------------------------


def realize(action: dict[str, Any] | None, billing: list[dict[str, Any]], before: Any, after: Any, *,
            changes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Realized savings on one capacity for the ``after`` period, from its bill and a confirmed action.

    ``billing``: rows as ``usage/cost_management.py`` gives them. ``before``, ``after``: periods as
    ``{"start", "end"}`` (end exclusive). ``changes``: ``fdcap_change`` rows. Returns the evidence
    used, ``realized`` (True or False), ``realized_savings`` (Measured, or Unknown with the reason),
    ``not_claimed`` (the rest of the bill's drop) and ``not_separated`` (what this can't tell apart)."""
    if action is None:
        reason = ("No person has confirmed what caused a change on this capacity, so nothing is realized. "
                  "A bill alone doesn't say why it went down.")
        return {"realized": False, "reason": reason, "evidence": None, "action_id": None,
                "realized_savings": ms.unknown(METRIC, ms.CURRENCY, reason)}
    act = _action(action)
    b0, b1 = _period(before, "before")
    a0, a1 = _period(after, "after")
    if a0 < b1:
        raise ValueError("The before and after periods overlap or are in the wrong order; "
                         "the before period must end before the after period starts.")
    cap = act["billing_capacity_id"]
    ids = {"capacity_id": cap, "period_start": a0, "period_end": a1}
    kind = SKU_CHANGE if act["change_record_id"] else BILLING
    base: dict[str, Any] = {
        "action_id": act["action_id"], "optimization_ids": act["optimization_ids"], "confirmed_by": act["confirmed_by"],
        "confirmed_at": act["confirmed_at"], "billing_capacity_id": cap, "capacity_id": act["capacity_id"],
        "evidence": kind, "before": {"start": b0.isoformat(), "end": b1.isoformat()},
        "after": {"start": a0.isoformat(), "end": a1.isoformat()}, "currency": None, "billed_before": None,
        "billed_after": None, "not_claimed": None, "sku": None,
        "not_separated": [ELSEWHERE, REASON_DIDNT_SAY]}
    rows = _rows(billing, cap)

    # What changed, for a SKU change.
    old_cu = new_cu = None
    if kind == SKU_CHANGE:
        if not act["capacity_id"]:
            raise ValueError(f"Action {act['action_id']} names a SKU change but not the capacity it changed.")
        row = next((c for c in changes or [] if c.get("record_id") == act["change_record_id"]), None)
        if row is None:
            return _not_realized(base, f"The SKU change that action {act['action_id']} names "
                                       f"({act['change_record_id']}) isn't in the capacity history.", **ids)
        if row.get("setting") != "sku":
            raise ValueError(f"Capacity history row {row.get('record_id')} records {row.get('setting')!r}, not a SKU.")
        if str(row.get("scope_id") or "").lower() != act["capacity_id"]:
            raise ValueError(f"Capacity history row {row.get('record_id')} is for capacity {row.get('scope_id')}, "
                             f"not {act['capacity_id']}, which action {act['action_id']} names.")
        old, new = _sku(row.get("old_json")), _sku(row.get("new_json"))
        base["sku"] = {"old": old, "new": new, "change_record_id": row.get("record_id"),
                       "changed_after": row.get("changed_after"), "changed_by_time": row.get("changed_by_time")}
        if old is None or not row.get("changed_after"):
            return _not_realized(base, "The capacity history has no earlier SKU for this capacity, so the change "
                                       "can't be compared with anything.", **ids)
        old_cu, new_cu = costs.sku_cu(old), costs.sku_cu(new)
        if not old_cu or not new_cu or not old.startswith("F") or not (new or "").startswith("F"):
            return _not_realized(base, f"The SKU changed from {old} to {new}, and only F SKUs are billed per CU "
                                       "hour, so the bill can't be tied to the change.", **ids)
        if new_cu >= old_cu:
            return _not_realized(base, f"The SKU went from {old} to {new}, which is not smaller, so it saved "
                                       "nothing.", **ids)
        seen_old = _time(row["changed_after"], f"Capacity history row {row.get('record_id')}'s changed_after")
        seen_new = _time(row.get("changed_by_time"), f"Capacity history row {row.get('record_id')}'s changed_by_time")
        if b1 > seen_old or a0 < seen_new:
            return _not_realized(base, f"The SKU changed some time between {seen_old.isoformat()} and "
                                       f"{seen_new.isoformat()}. The before period must end by the first time and "
                                       "the after period start at or after the second, so each is billed at one SKU.",
                                 **ids)

    # The bill.
    if not rows:
        return _not_realized(base, "There is no billing for this capacity. Realized savings need a Cost "
                                   "Management export that shows the bill.", **ids)
    if (b1 - b0) != (a1 - a0):
        return _not_realized(base, "The before and after periods have different lengths, so their bills "
                                   "can't be compared as they are.", **ids)
    for name, (s, e) in (("before", (b0, b1)), ("after", (a0, a1))):
        gap = (e - s).total_seconds() - _covered(rows, s, e)
        if gap > TOLERANCE:
            return _not_realized(base, f"The export has no row for this capacity for {gap / 86400:g} days of the "
                                       f"{name} period. A missing day would look like a saving.", **ids)
    pre, why = _sum(rows, b0, b1)
    post, why2 = _sum(rows, a0, a1)
    if why or why2:
        return _not_realized(base, why or why2 or "", **ids)
    currency = rows[0]["currency"]
    collected = max((r["collected_at"] for r in rows if r["collected_at"]), default=None)
    source = "; ".join(sorted({r["source"] for r in rows if r["source"]})) or "Azure Cost Management export"
    uncertainty = "Compute meters only. Storage meters and reservation purchases are not compared."
    for key, s, e, f in (("billed_before", b0, b1, pre), ("billed_after", a0, a1, post)):
        base[key] = ms.make(ms.MEASURED, "billed_compute_cost", ms.CURRENCY, f["cost"], currency=currency,
                            capacity_id=cap, source=source, method="Sum of the compute meter rows in the period",
                            collected_at=collected, period_start=s, period_end=e, uncertainty=uncertainty)
        base[key] |= {"billed_cu_hours": round(f["cu"], 6), "rows": f["rows"]}
    base["currency"] = currency
    drop = pre["cost"] - post["cost"]
    if drop <= TOLERANCE:
        return _not_realized(base, "The bill did not go down between the before and after periods, so nothing "
                                   "is realized. Lower utilization alone shows value and headroom, not savings.",
                             **ids)

    rates = [f["cost"] / f["cu"] for f in (pre, post) if f["cu"] > 0]
    rate = min(rates) if rates else 0.0
    if kind == SKU_CHANGE:
        assert old_cu is not None and new_cu is not None
        hours = (a1 - a0).total_seconds() / 3600
        if pre["cu"] > old_cu * hours + TOLERANCE or post["cu"] > new_cu * hours + TOLERANCE:
            return _not_realized(base, f"The bill has more CU hours than {base['sku']['old']} before or "
                                       f"{base['sku']['new']} after could bill, so it doesn't fit the recorded SKU "
                                       "change.", **ids)
        tied = (old_cu - new_cu) * (post["cu"] / new_cu) * rate
        method = (f"({base['sku']['old']} CU - {base['sku']['new']} CU) x the hours billed after the change x the "
                  "lower of the two billed rates per CU hour, capped at the bill's drop")
        base["not_separated"].append("A SKU change and a pause in the same period are told apart by billed CU "
                                     "hours only.")
    else:
        tied = max(0.0, pre["cu"] - post["cu"]) * rate
        method = ("Drop in billed CU hours x the lower of the two billed rates per CU hour, capped at the bill's "
                  "drop")
    realized = min(tied, drop)
    if realized <= TOLERANCE:
        return _not_realized(base, "The bill went down, but not through fewer billed CU hours or a smaller SKU. "
                                   "A price change or other change isn't claimed.", **ids)
    rest = drop - realized
    base["not_claimed"] = ms.make(
        ms.MEASURED, NOT_CLAIMED, ms.CURRENCY, rest, currency=currency, capacity_id=cap, source=source,
        method="The bill's drop minus the realized savings", collected_at=collected, period_start=a0, period_end=a1,
        uncertainty="Other changes to the bill, such as a price change or a pause the action didn't cause.")
    base["realized_savings"] = ms.make(
        ms.MEASURED, METRIC, ms.CURRENCY, realized, currency=currency, capacity_id=cap, source=source, method=method,
        collected_at=collected, period_start=a0, period_end=a1,
        uncertainty=f"{uncertainty} Confirmed by a person in action {act['action_id']}.")
    return base | {"realized": True, "reason": None}


# ---- allocation ------------------------------------------------------------------------------------


def allocate(result: dict[str, Any], avoided: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Split a realized figure across the action's optimizations, pro rata to each one's measured CU
    avoided on the capacity in the after period. The shares add up to exactly the realized figure.

    ``avoided`` maps optimization id to a CU measurement. Only Measured figures count; an
    optimization without one gets no share and is listed with the reason. When none is Measured,
    the figure isn't split."""
    m = result["realized_savings"]
    if not result.get("realized"):
        return {"split": False, "reason": "Nothing is realized, so there is nothing to split.", "allocations": []}
    a0, a1 = _period(result["after"], "after")
    weights: dict[str, int] = {}
    left_out: list[dict[str, str]] = []
    for oid in result["optimization_ids"]:
        cu = avoided.get(oid)
        if cu is None:
            left_out.append({"optimization_id": oid, "reason": "No CU avoided figure was given."})
            continue
        ms.validate(cu)
        if cu["unit"] not in (ms.CU_HOURS, ms.CU_SECONDS):
            raise ValueError(f"The CU avoided for optimization {oid} isn't in CU hours or CU seconds.")
        s, e = ms.parse_time(cu["period"]["start"]), ms.parse_time(cu["period"]["end"])
        if not s or not e or s < a0 or e > a1:
            raise ValueError(f"The CU avoided for optimization {oid} isn't inside the after period.")
        if cu["classification"] != ms.MEASURED:
            left_out.append({"optimization_id": oid, "reason": f"Its CU avoided is {cu['label']}, not Measured."})
            continue
        micro = round(ms.convert(cu["value"], cu["unit"], ms.CU_HOURS) * _MICRO)
        if micro > 0:
            weights[oid] = micro
        else:
            left_out.append({"optimization_id": oid, "reason": "Its measured CU avoided is 0."})
    if not weights:
        return {"split": False, "reason": "No linked optimization has measured CU avoided, so the realized figure "
                                          "stays whole at the capacity.", "allocations": [], "left_out": left_out}
    shares = _split(round(m["value"] * _MICRO), weights)
    rows = [{"optimization_id": oid, "method": PRO_RATA,
             "share": ms.make(ms.MEASURED, "realized_savings_share", ms.CURRENCY, shares[oid] / _MICRO,
                              currency=m["currency"], capacity_id=m["capacity_id"], workload_id=oid,
                              source=m["source"], method=PRO_RATA, collected_at=m["collected_at"],
                              period_start=a0, period_end=a1)}
            for oid in sorted(weights)]
    return {"split": True, "reason": None, "method": PRO_RATA, "allocations": rows, "left_out": left_out}


# ---- measure F -------------------------------------------------------------------------------------


def net_benefit(result: dict[str, Any], costs_in: list[dict[str, Any]]) -> dict[str, Any]:
    """Realized savings minus attributable costs (measure F), for the after period.

    ``costs_in``: ``{"category", "cost"}`` with a money measurement, one or more per category in
    :data:`COST_CATEGORIES`. A category that doesn't apply is given as Measured 0. Net is Unknown,
    with the reason, when nothing is realized, a category is missing or Unknown, or a cost is a
    Scenario. Otherwise it is as weak as its weakest part. ``signed`` is the net as a number that
    can be below 0; the measurement is a benefit, or a shortfall when costs are higher."""
    e = result["realized_savings"]
    a0, a1 = _period(result["after"], "after")
    ids = {"capacity_id": result["billing_capacity_id"], "period_start": a0, "period_end": a1}

    def unknown(reason: str) -> dict[str, Any]:
        return {"net": ms.unknown(NET, ms.CURRENCY, reason, **ids), "signed": None, "reason": reason,
                "costs": total_costs}

    total_costs: float | None = None
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for i, c in enumerate(costs_in):
        cat, m = c.get("category"), c.get("cost")
        name = f"Cost {i + 1} ({cat})"
        if cat not in COST_CATEGORIES:
            raise ValueError(f"{name} isn't one of {', '.join(COST_CATEGORIES)}.")
        if not isinstance(m, dict):
            raise ValueError(f"{name} has no figure.")
        v = m.get("value")
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0):
            raise ValueError(f"{name} must be a finite amount of zero or more, not {v!r}.")
        ms.validate(m)
        if m["unit"] != ms.CURRENCY:
            raise ValueError(f"{name} isn't money; turn CU into money with a bill, not a price list.")
        if m["value"] is not None and result.get("currency") and m["currency"] != result["currency"]:
            raise ValueError(f"{name} is in {m['currency']}, but the bill is in {result['currency']}.")
        s, en = ms.parse_time(m["period"]["start"]), ms.parse_time(m["period"]["end"])
        if not s or not en or s < a0 or en > a1:
            raise ValueError(f"{name} isn't inside the after period. Give each cost for that period only.")
        by_cat.setdefault(cat, []).append(m)
    if not result.get("realized"):
        return unknown(f"Nothing is realized, so there is no net benefit. {result['reason']}")
    missing = [c for c in COST_CATEGORIES if c not in by_cat]
    if missing:
        return unknown(f"No figure was given for {', '.join(x.replace('_', ' ') for x in missing)}. Give 0 when a "
                       "cost doesn't apply, so a missing cost is never read as none.")
    parts = [m for ms_ in by_cat.values() for m in ms_]
    scen = [m for m in parts if m["classification"] == ms.SCENARIO]
    if scen:
        return unknown("A cost is a Scenario figure (a price applied to CU). It can't be subtracted from a bill.")
    if any(m["classification"] == ms.UNKNOWN for m in parts):
        cats = sorted({c for c, ms_ in by_cat.items() for m in ms_ if m["classification"] == ms.UNKNOWN})
        return unknown(f"The cost of {', '.join(x.replace('_', ' ') for x in cats)} isn't known, so net can't be "
                       "worked out without overstating it.")
    total = ms.total(parts, "attributable_cost", ms.CURRENCY, currency=e["currency"])
    total_costs = total["value"]
    signed = round(e["value"] - total["value"], 6)
    cls = ms.ESTIMATED if total["classification"] == ms.ESTIMATED else ms.MEASURED
    common = {"currency": e["currency"], "method": "Realized savings minus attributable costs", **ids}
    if cls == ms.MEASURED:
        common |= {"source": e["source"], "collected_at": e["collected_at"]}
    else:
        common |= {"assumptions": total["assumptions"] or ["Some costs are estimated"]}
    net = ms.make(cls, NET if signed >= 0 else SHORTFALL, ms.CURRENCY, abs(signed), **common)
    return {"net": net, "signed": signed, "reason": None, "costs": total_costs,
            "by_category": {c: round(sum(m["value"] for m in ms_), 6) for c, ms_ in sorted(by_cat.items())}}
