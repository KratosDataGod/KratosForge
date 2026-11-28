"""Capacity headroom (measure B) and estimated capacity value (measure C) of CU avoided (ESI-402).

**Measure B, capacity headroom.** Always Estimated. It is the CU avoided as a share of a capacity
over time, worked out per timepoint from imported Capacity Metrics timepoints (``fdopt_cu_usage``
rows), never from period totals: a monthly total can't show a peak. For each capacity and each
timepoint window:

- **used CU**: the timepoint rows for that capacity and window, added up. A row with no figure, or
  an Unknown one, makes the timepoint's use Unknown (None), never 0.
- **CU limit**: the CU of the SKU in effect during the window times its seconds. The SKU comes
  from SKU spans (``sku_spans`` builds them from ``fdcap_change`` history, or a caller passes its
  own); a window that a SKU change falls inside is limited by each SKU for its part of the window.
  A window no span covers, or a SKU with no known CU size, has an Unknown limit.
- **CU avoided in it**: the runs (``attribution.attribute`` or ``accruals.accrue`` results) on that
  capacity whose run time falls in the window, net of overhead by default. A run whose figure isn't
  known (Unknown, or net with Unknown overhead) makes the window's avoided CU Unknown.

Each timepoint gives its utilization **as observed** (the timepoints already show the optimized
use, since the avoided CU never ran on the capacity), its utilization **if not avoided** (the
avoided CU added back) and the **headroom gained** (the avoided CU as a share of the limit). Over
the timepoints where all three are known, the result gives peak and p95 (nearest rank, so the
figure is one a timepoint really had) of both utilizations, and the headroom gained at each.

- **Unknown is None.** A timepoint with no data is Unknown, never 0, and never filled with an
  average of the others.
- **Incomplete.** When a capacity's timepoints don't cover the period, or some are Unknown, its
  result is Incomplete, the uncovered windows are listed under ``missing_windows``, and the peak
  and p95 cover only the known timepoints and say so. With no known timepoint, they are Unknown.
- **Capacities are never mixed.** Each capacity has its own timepoints, limits and figures. No
  figure adds or compares two capacities.
- **Headroom isn't savings.** A capacity using less shows headroom; the bill doesn't change until
  its SKU, hours or billing change (realized savings, ``realized.py``, ESI-403). Every result says
  ``is_savings`` False with the reason.

**Measure C, estimated capacity value.** A Scenario figure: the same runs' CU avoided priced with
``pricing.value_runs`` under a named pricing scenario, at the version in effect when each run
happened and the SKU in effect then. It is never labelled savings, and its text says it isn't
money saved. With no scenario named, it is Unknown with the reason.

Measured, estimated, scenario and unknown figures stay apart: utilization and headroom are
Estimated, capacity value is a Scenario, and nothing adds one kind to another.

Pure: nothing is read or written, and the inputs are not changed. Bad input is refused with a
ValueError in a full sentence.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any

from .. import costs
from .. import measurement as ms
from ..timeutil import iso
from . import pricing

MEASURE_B, MEASURE_C = "capacity_headroom", "capacity_value"
NET, GROSS = "net", "gross"
COMPLETE, INCOMPLETE, UNKNOWN = "Complete", "Incomplete", "Unknown"
P95 = 0.95
TOLERANCE = 1e-6

METHOD = ("Per timepoint: used CU seconds / (the CU of the SKU in effect x the timepoint's seconds) x 100, "
          "as observed and with the CU avoided by runs in that timepoint added back. Peak is the highest "
          "timepoint and p95 the nearest-rank 95th percentile, over timepoints where both are known.")
ASSUMPTIONS = [
    "The imported timepoint rows hold all of the capacity's use in each window they cover.",
    ("The timepoints show use with the optimizations and runs on PCs already in effect, so the CU they avoided "
    "is added back to show what the capacity would have used without them."),
    ("A run's CU avoided is placed in the timepoint its run time falls in. Fabric smooths use over minutes or "
    "hours, so the avoided CU of a long run may be spread wider than this, and the peak with it added back "
    "can be higher here than Fabric would have shown."),
    ("No Fabric smoothing, bursting or throttling is reproduced, so these aren't the Capacity Metrics app's "
    "utilization figures and aren't throttling evidence."),
    "A capacity's CU limit is the CU of its SKU in effect for each part of the timepoint.",
]
NOT_SAVINGS = ("Headroom and capacity value aren't savings. A capacity using less has more room, but its bill "
               "changes only when its SKU, hours or billing change; realized savings come only from that "
               "evidence (savings/realized.py, ESI-403).")
VALUE_TEXT = ("Estimated capacity value under the {scenario} pricing scenario: {value}. This is the CU avoided "
              "priced at the scenario's rate. It isn't money saved: on a reserved or running capacity the bill "
              "doesn't change unless the capacity's SKU, hours or billing change.")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _cap(value: Any) -> str:
    return _text(value).lower()


def _time(value: Any, what: str) -> dt.datetime:
    t = ms.parse_time(value)
    if t is None:
        raise ValueError(f"{what} isn't a time: {value!r}.")
    return t


def _finite(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{what} must be a finite number, not {value!r}.")
    if value < 0:
        raise ValueError(f"{what} can't be negative ({value!r}).")
    return float(value)


def _window(a: dt.datetime, b: dt.datetime) -> dict[str, str | None]:
    return {"start": iso(a), "end": iso(b)}


# ---------- SKU spans


def _sku(value_json: Any) -> str | None:
    if value_json is None:
        return None
    try:
        v = json.loads(value_json) if isinstance(value_json, str) else value_json
    except ValueError:
        v = value_json
    return str(v).strip().upper() if v not in (None, "") else None


def sku_spans(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """SKU spans from ``fdcap_change`` rows for setting ``sku``: when each capacity had which SKU.

    A SKU holds from when it was first seen (``changed_by_time``) until the next SKU's old value was
    last seen (that row's ``changed_after``). Between those two reads the SKU isn't known, so no span
    covers it. The last SKU is taken as unchanged after the last change seen, which its span says.
    Before the first time a capacity's SKU was seen there is no span. Rows for other settings are
    left out. Returns spans ``{capacity_id, sku, from, to, source, assumptions}``, ``to`` None for
    the last one, sorted by capacity and time."""
    if not isinstance(changes, list):
        raise ValueError("The SKU history must be a list of fdcap_change rows.")
    by_cap: dict[str, list[dict[str, Any]]] = {}
    for row in changes:
        if not isinstance(row, dict):
            raise ValueError("Each SKU history row must be an fdcap_change row (a dict).")
        if row.get("setting") != "sku":
            continue
        cap = _cap(row.get("scope_id"))
        if not cap:
            raise ValueError(f"SKU history row {row.get('record_id')} doesn't name its capacity.")
        _time(row.get("changed_by_time"), f"SKU history row {row.get('record_id')}'s changed_by_time")
        by_cap.setdefault(cap, []).append(row)
    out: list[dict[str, Any]] = []
    for cap in sorted(by_cap):
        rows = sorted(by_cap[cap], key=lambda r: _time(r["changed_by_time"], "changed_by_time"))
        for i, row in enumerate(rows):
            new = _sku(row.get("new_json"))
            if new is None:
                continue  # the SKU wasn't returned: nothing is known from this read
            if i + 1 < len(rows):
                nxt = rows[i + 1]
                if _sku(nxt.get("old_json")) != new:
                    raise ValueError(f"SKU history for capacity {cap} doesn't chain: row {nxt.get('record_id')} "
                                     f"says the SKU was {_sku(nxt.get('old_json'))} before it, but the row "
                                     f"before says {new}.")
                end: dt.datetime | None = _time(nxt.get("changed_after"),
                                                f"SKU history row {nxt.get('record_id')}'s changed_after")
                assumptions: list[str] = []
            else:
                end = None
                assumptions = ["No later SKU change was seen, so the SKU is taken as unchanged after the last read."]
            start = _time(row["changed_by_time"], "changed_by_time")
            if end is not None and end < start:
                raise ValueError(f"SKU history for capacity {cap} goes back in time at row {rows[i + 1].get('record_id')}.")
            out.append({"capacity_id": cap, "sku": new, "from": iso(start), "to": iso(end) if end else None,
                        "source": f"fdcap_change {row.get('record_id')}", "assumptions": assumptions})
    return out


def _spans(skus: list[dict[str, Any]]) -> dict[str, list[tuple[dt.datetime, dt.datetime | None, str, dict[str, Any]]]]:
    if not isinstance(skus, list):
        raise ValueError("The SKU spans must be a list (sku_spans builds them from capacity history).")
    out: dict[str, list[tuple[dt.datetime, dt.datetime | None, str, dict[str, Any]]]] = {}
    for s in skus:
        if not isinstance(s, dict):
            raise ValueError("Each SKU span must be a dict with capacity_id, sku, from and to.")
        cap, sku = _cap(s.get("capacity_id")), _text(s.get("sku")).upper()
        if not cap or not sku:
            raise ValueError("A SKU span needs its capacity_id and its sku.")
        start = _time(s.get("from"), f"The start of the {sku} span of capacity {cap}")
        end = None if s.get("to") in (None, "") else _time(s["to"], f"The end of the {sku} span of capacity {cap}")
        if end is not None and end <= start:
            raise ValueError(f"The {sku} span of capacity {cap} ends before it starts.")
        out.setdefault(cap, []).append((start, end, sku, s))
    far = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    for cap, spans in out.items():
        spans.sort(key=lambda x: x[0])
        for a, b in zip(spans, spans[1:], strict=False):
            if (a[1] or far) > b[0] and a[2] != b[2]:
                raise ValueError(f"Capacity {cap} has SKU spans that overlap with different SKUs ({a[2]} and "
                                 f"{b[2]} from {iso(b[0])}); a capacity has one SKU at a time.")
    return out


def _limit(spans: list[tuple[dt.datetime, dt.datetime | None, str, dict[str, Any]]], a: dt.datetime,
           b: dt.datetime) -> tuple[float | None, list[str], str]:
    """The CU seconds a capacity could use in [a, b), the SKUs in effect, and why it's unknown."""
    far = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    limit, covered, used = 0.0, 0.0, []
    for start, end, sku, _ in spans:
        lo, hi = max(a, start), min(b, end or far)
        if hi <= lo:
            continue
        cus = costs.sku_cu(sku)
        if sku not in used:
            used.append(sku)
        if cus is None:
            return None, used, f"The CU size of SKU {sku} isn't known."
        seconds = (hi - lo).total_seconds()
        limit += cus * seconds
        covered += seconds
    if covered + TOLERANCE < (b - a).total_seconds():
        return None, used, "The capacity's SKU isn't known for all of this timepoint."
    return limit, used, ""


# ---------- timepoints


def _timepoints(usage: list[dict[str, Any]], start: dt.datetime, end: dt.datetime) -> tuple[
        dict[str, dict[tuple[dt.datetime, dt.datetime], dict[str, Any]]], int]:
    """Each capacity's timepoint windows inside the period, with their rows added up."""
    if not isinstance(usage, list):
        raise ValueError("The usage must be a list of fdopt_cu_usage timepoint rows.")
    out: dict[str, dict[tuple[dt.datetime, dt.datetime], dict[str, Any]]] = {}
    seen: dict[tuple[Any, ...], Any] = {}
    outside = 0
    for row in usage:
        if not isinstance(row, dict):
            raise ValueError("Each usage row must be an fdopt_cu_usage row (a dict).")
        cap = _cap(row.get("capacity_id"))
        if not cap:
            raise ValueError("A usage row doesn't name its capacity, so it can't be placed. Capacities are never "
                             "guessed or mixed.")
        a = _time(row.get("period_start"), f"A usage row's period_start on capacity {cap}")
        b = _time(row.get("period_end"), f"A usage row's period_end on capacity {cap}")
        if b <= a:
            raise ValueError(f"A usage row on capacity {cap} at {iso(a)} has no length; headroom needs timepoint "
                             "windows, not single operations.")
        cls, cu = row.get("classification"), row.get("cu_seconds")
        if cls == ms.SCENARIO:
            raise ValueError(f"A usage row on capacity {cap} at {iso(a)} is a Scenario figure; use is measured "
                             "or estimated.")
        if cls not in (ms.MEASURED, ms.ESTIMATED, ms.UNKNOWN):
            raise ValueError(f"A usage row on capacity {cap} at {iso(a)} is classified {cls!r}; it must be "
                             "Measured, Estimated or Unknown.")
        value = None if cls == ms.UNKNOWN or cu is None else _finite(cu, f"The CU seconds on capacity {cap} at {iso(a)}")
        key = (cap, _text(row.get("workspace_id")), _text(row.get("item_id")), _text(row.get("operation")), a, b)
        if key in seen:
            if seen[key] != (value, cls):
                raise ValueError(f"Capacity {cap} has the same timepoint row at {iso(a)} twice with different "
                                 "figures; keep one.")
            continue
        seen[key] = (value, cls)
        if a < start or b > end:
            outside += 1
            continue
        tp = out.setdefault(cap, {}).setdefault((a, b), {"used": 0.0, "unknown": 0, "rows": 0,
                                                         "classes": set(), "sources": set()})
        tp["rows"] += 1
        if value is None:
            tp["unknown"] += 1
        else:
            tp["used"] += value
            tp["classes"].add(cls)
        if row.get("source"):
            tp["sources"].add(str(row["source"]))
    for cap, windows in out.items():
        keys = sorted(windows)
        for x, y in zip(keys, keys[1:], strict=False):
            if y[0] < x[1]:
                raise ValueError(f"Capacity {cap} has timepoints that overlap ({iso(x[0])} to {iso(x[1])} and "
                                 f"{iso(y[0])} to {iso(y[1])}). Import timepoint windows, not operations.")
    return out, outside


def _missing(windows: list[tuple[dt.datetime, dt.datetime]], start: dt.datetime,
             end: dt.datetime) -> list[dict[str, str | None]]:
    gaps: list[dict[str, str | None]] = []
    at = start
    for a, b in sorted(windows):
        if a > at:
            gaps.append(_window(at, a))
        at = max(at, b)
    if at < end:
        gaps.append(_window(at, end))
    return gaps


# ---------- runs


def _runs(runs: list[dict[str, Any]], basis: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Each run once, with its capacity and CU avoided; runs with no single capacity apart."""
    if not isinstance(runs, list):
        raise ValueError("The runs must be a list of attribution.attribute or accruals.accrue results.")
    seen: dict[str, dict[str, Any]] = {}
    for r in runs:
        if not isinstance(r, dict) or r.get("execution_id") in (None, ""):
            raise ValueError("Each run must be an attribution or accrual result with its execution_id.")
        eid = str(r["execution_id"])
        first = seen.setdefault(eid, r)
        if first is not r and first != r:
            raise ValueError(f"Run {eid} has two different results; give one result per run.")
    placed: list[dict[str, Any]] = []
    unplaced: list[dict[str, Any]] = []
    for eid in sorted(seen):
        r = seen[eid]
        at = _time(r.get("at"), f"Run {eid}'s run time")
        if basis == NET:
            value, cls = r.get("net_cu_hours"), r.get("net_classification")
            if value is not None and r.get("net_includes_overhead") is not True:
                value, cls = None, ms.UNKNOWN
                why = "Its overhead isn't known, so its net CU avoided isn't known."
            else:
                why = "Its net CU avoided isn't known."
        else:
            value, cls = r.get("gross_cu_hours"), r.get("gross_classification")
            why = "Its gross CU avoided isn't known."
        if cls == ms.SCENARIO:
            raise ValueError(f"Run {eid}'s CU avoided is a Scenario figure; headroom takes measured or estimated "
                             "CU avoided only.")
        if cls not in (ms.MEASURED, ms.ESTIMATED, ms.UNKNOWN):
            raise ValueError(f"Run {eid}'s CU avoided is classified {cls!r}; it must be Measured, Estimated or "
                             "Unknown.")
        if (value is None) != (cls == ms.UNKNOWN):
            raise ValueError(f"Run {eid}'s CU avoided is {cls} with figure {value!r}; a known figure has a "
                             "number and an unknown one has none.")
        hours = None if value is None else _finite(value, f"Run {eid}'s CU avoided")
        caps = {_cap(r.get("capacity_id"))} if _cap(r.get("capacity_id")) else set()
        for e in r.get("events") or []:
            if isinstance(e, dict) and _cap(e.get("capacity_id")):
                caps.add(_cap(e["capacity_id"]))
        item = {"execution_id": eid, "at": at, "cu_hours": hours, "classification": cls,
                "reason": "" if hours is not None else why}
        if len(caps) != 1:
            unplaced.append({"execution_id": eid, "reason": (
                "Neither it nor its ledger events name a capacity." if not caps else
                f"It names {len(caps)} capacities ({', '.join(sorted(caps))}), so it can't be placed.")})
            continue
        placed.append(item | {"capacity_id": next(iter(caps))})
    return placed, unplaced


# ---------- figures


def _figure(metric: str, value: float | None, reason: str, cap: str, start: dt.datetime, end: dt.datetime,
            *, known: int, of: int) -> dict[str, Any]:
    """A utilization or headroom figure in percent: Estimated with its method and assumptions, or
    Unknown with the reason. Built beside ``measurement.make`` because a capacity can run over 100%
    of its size in a timepoint, which a measurement's percent can't hold."""
    out: dict[str, Any] = {"metric": metric, "unit": ms.PERCENT, "capacity_id": cap,
                           "period": {"start": iso(start), "end": iso(end)},
                           "timepoints": known, "of_timepoints": of}
    if value is None:
        return out | {"classification": ms.UNKNOWN, "label": ms.LABELS[ms.UNKNOWN], "value": None,
                      "method": "", "assumptions": [], "complete": False, "reason": reason}
    complete = known == of
    return out | {"classification": ms.ESTIMATED, "label": ms.LABELS[ms.ESTIMATED], "value": round(value, 6),
                  "method": METHOD, "assumptions": list(ASSUMPTIONS), "complete": complete,
                  "reason": "" if complete else (f"Covers {known} of {of} timepoints; the others aren't known, so "
                                                 "the figure could be higher.")}


def _down(value: float) -> float:
    """Rounded to 6 places without going up, so rounding never adds headroom."""
    return math.floor(value * 1e6 + 1e-9) / 1e6


def _nearest_rank(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _summary(tps: list[dict[str, Any]], cap: str, start: dt.datetime, end: dt.datetime,
             missing: int) -> dict[str, Any]:
    known = [t for t in tps if t["headroom_gained_pct"] is not None]
    of = len(tps) + missing
    why = ("No timepoint of this capacity has its use, SKU limit and CU avoided all known, so its utilization "
           "isn't known." if tps else "No timepoints were imported for this capacity in the period.")
    out: dict[str, Any] = {}
    for name, pick in (("peak", max), ("p95", lambda v: _nearest_rank(v, P95))):
        if not known:
            obs = ifn = gained = None
        else:
            obs = pick([t["utilization_pct"] for t in known])
            ifn = pick([t["utilization_if_not_avoided_pct"] for t in known])
            # Each timepoint's use with the CU added back is at least its use as observed, so both
            # figures' gap is at most the largest gain of one timepoint; rounding mustn't widen it.
            gained = min(max(0.0, ifn - obs), max(t["headroom_gained_pct"] for t in known))
        out[name] = {
            "as_observed": _figure(f"{name}_utilization", obs, why, cap, start, end, known=len(known), of=of),
            "if_not_avoided": _figure(f"{name}_utilization_if_not_avoided", ifn, why, cap, start, end,
                                      known=len(known), of=of),
            "headroom_gained": _figure(f"{name}_headroom_gained", gained, why, cap, start, end,
                                       known=len(known), of=of)}
    return out


def _value(runs: list[dict[str, Any]], spans: list[Any], versions: list[dict[str, Any]],
           scenario: str | None, basis: str, region: str) -> dict[str, Any]:
    """Measure C: the capacity's runs priced under the scenario, each at the SKU in effect when it ran."""
    base: dict[str, Any] = {"measure": MEASURE_C, "scenario": scenario, "basis": basis, "is_savings": False}
    if not scenario:
        m = ms.unknown(MEASURE_C, ms.CURRENCY, "No pricing scenario was named, so the CU avoided isn't priced.")
        return base | {"total": m, "runs": [], "pricing_versions": [],
                       "text": "No pricing scenario was named, so there is no capacity value. " + NOT_SAVINGS}
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        sku = ""
        for start, end, s, _ in spans:
            if start <= r["at"] and (end is None or r["at"] < end):
                sku = s
        cu = r["cu_hours"]
        groups.setdefault(sku, []).append({"execution_id": r["execution_id"], "at": iso(r["at"]),
                                           "net_cu_hours": cu, "gross_cu_hours": cu})
    priced: list[dict[str, Any]] = []
    versions_used: set[str] = set()
    for sku in sorted(groups):
        v = pricing.value_runs(groups[sku], versions, scenario, sku=sku, region=region)
        priced.extend(x | {"sku": sku or None} for x in v["runs"])
        versions_used.update(v["pricing_versions"])
    priced.sort(key=lambda x: x["execution_id"])
    known = [x["value"] for x in priced if x["value"]["value"] is not None]
    currencies = {m["currency"] for m in known}
    if len(currencies) > 1:
        raise ValueError(f"The runs were priced in more than one currency under {scenario!r}; use a scenario "
                         "with one currency.")
    total = ms.total([x["value"] for x in priced], MEASURE_C, ms.CURRENCY, currency=next(iter(currencies), None))
    if not priced:
        total |= {"classification": ms.SCENARIO, "label": ms.LABELS[ms.SCENARIO]}
    shown = ("not known" if total["value"] is None else f"{total['value']:g} {total['currency'] or ''}".strip())
    text = VALUE_TEXT.format(scenario=scenario, value=shown)
    if total["value"] is not None and not total.get("complete", True):
        text += f" {total['reason']}."
    return base | {"total": total, "runs": priced, "pricing_versions": sorted(versions_used), "text": text}


# ---------- the measures


def headroom(usage: list[dict[str, Any]], runs: list[dict[str, Any]], skus: list[dict[str, Any]], *,
             period: dict[str, Any], versions: list[dict[str, Any]] | None = None, scenario: str | None = None,
             region: str = "", basis: str = NET, capacity_ids: list[str] | None = None) -> dict[str, Any]:
    """Capacity headroom (measure B) and estimated capacity value (measure C), per capacity.

    ``usage``: ``fdopt_cu_usage`` timepoint rows (capacity_id, period_start, period_end, cu_seconds,
    classification, source; workspace_id, item_id and operation when a timepoint is split by item).
    ``runs``: ``attribution.attribute`` results with a ``capacity_id``, or ``accruals.accrue`` results
    whose events name it. ``skus``: SKU spans (``sku_spans``). ``period``: ``{"start", "end"}``, end
    exclusive. ``versions`` and ``scenario``: the pricing versions and the scenario to price under
    (measure C); without a scenario, measure C is Unknown. ``basis``: ``net`` (default) or ``gross``
    CU avoided. ``capacity_ids``: capacities to report even with no timepoints or runs.

    Returns ``capacities`` (sorted by id), each with its ``timepoints``, ``missing_windows``,
    ``status`` (Complete, Incomplete or Unknown), ``peak`` and ``p95`` (as observed, if not avoided,
    headroom gained), ``cu_avoided``, ``capacity_value``, ``runs_not_in_a_timepoint`` and
    ``is_savings`` False; plus ``unplaced`` runs, ``outside_period`` counts and ``written`` False."""
    if basis not in (NET, GROSS):
        raise ValueError("The basis is net or gross CU avoided.")
    if not isinstance(period, dict):
        raise ValueError("The period is {'start', 'end'}.")
    start, end = _time(period.get("start"), "The period's start"), _time(period.get("end"), "The period's end")
    if end <= start:
        raise ValueError("The period ends before it starts.")
    versions = list(versions or [])
    timepoints, rows_outside = _timepoints(usage, start, end)
    spans = _spans(skus)
    placed, unplaced = _runs(runs, basis)
    in_period = [r for r in placed if start <= r["at"] < end]
    runs_outside = len(placed) - len(in_period)

    caps = set(timepoints) | {r["capacity_id"] for r in in_period} | {_cap(c) for c in capacity_ids or [] if _cap(c)}
    results = []
    for cap in sorted(caps):
        windows = timepoints.get(cap, {})
        cap_spans = spans.get(cap, [])
        cap_runs = [r for r in in_period if r["capacity_id"] == cap]
        keys = sorted(windows)
        by_window: dict[tuple[dt.datetime, dt.datetime], list[dict[str, Any]]] = {k: [] for k in keys}
        loose: list[dict[str, Any]] = []
        for r in cap_runs:
            home = next((k for k in keys if k[0] <= r["at"] < k[1]), None)
            if home is None:
                loose.append({"execution_id": r["execution_id"], "at": iso(r["at"]), "cu_hours": r["cu_hours"],
                              "reason": "Its run time falls in a window with no timepoint."})
            else:
                by_window[home].append(r)
        tps = []
        for a, b in keys:
            w = windows[(a, b)]
            in_tp = by_window[(a, b)]
            used = None if w["unknown"] else w["used"]
            limit, used_skus, limit_why = _limit(cap_spans, a, b)
            unknown_runs = [r for r in in_tp if r["cu_hours"] is None]
            avoided = None if unknown_runs else sum(ms.convert(r["cu_hours"], ms.CU_HOURS, ms.CU_SECONDS)
                                                    for r in in_tp)
            reasons = []
            if used is None:
                reasons.append(f"{w['unknown']} of its {w['rows']} rows have no CU figure, so its use isn't known.")
            if limit is None:
                reasons.append(limit_why)
            if avoided is None:
                reasons.append(f"{len(unknown_runs)} of its runs have CU avoided that isn't known.")
            util = ifn = gained_pct = None
            if used is not None and limit:
                util = used / limit * 100
                if avoided is not None:
                    ifn = (used + avoided) / limit * 100
                    gained_pct = avoided / limit * 100
            known = gained_pct is not None
            tps.append({
                "start": iso(a), "end": iso(b), "seconds": (b - a).total_seconds(),
                "used_cu_seconds": None if used is None else round(used, 6),
                "used_classification": ms.UNKNOWN if used is None else (
                    ms.ESTIMATED if ms.ESTIMATED in w["classes"] else ms.MEASURED),
                "sources": sorted(w["sources"]), "skus": used_skus,
                "limit_cu_seconds": None if limit is None else round(limit, 6),
                "avoided_cu_seconds": None if avoided is None else round(avoided, 6),
                "runs": [r["execution_id"] for r in in_tp],
                "utilization_pct": None if util is None else round(util, 6),
                "utilization_if_not_avoided_pct": None if ifn is None else round(ifn, 6),
                "headroom_gained_pct": None if gained_pct is None else _down(gained_pct),
                "headroom_gained_cu_seconds": None if gained_pct is None else round(avoided or 0.0, 6),
                "classification": ms.ESTIMATED if known else ms.UNKNOWN,
                "label": ms.LABELS[ms.ESTIMATED if known else ms.UNKNOWN],
                "reason": " ".join(reasons)})
        missing = _missing(keys, start, end)
        unknown_tps = sum(1 for t in tps if t["classification"] == ms.UNKNOWN)
        summary = _summary(tps, cap, start, end, len(missing))
        if summary["peak"]["as_observed"]["value"] is None:
            status = UNKNOWN
        elif missing or unknown_tps:
            status = INCOMPLETE
        else:
            status = COMPLETE
        status_reason = []
        if missing:
            status_reason.append(f"{len(missing)} window{'s' if len(missing) != 1 else ''} of the period "
                                 f"{'have' if len(missing) != 1 else 'has'} no timepoints.")
        if unknown_tps:
            status_reason.append(f"{unknown_tps} of its {len(tps)} timepoints aren't fully known.")
        parts = [{"classification": r["classification"], "value": r["cu_hours"], "unit": ms.CU_HOURS}
                 for r in cap_runs]
        cu_avoided = ms.total(parts, "fabric_cu_avoided", ms.CU_HOURS)
        results.append({
            "capacity_id": cap, "measure": MEASURE_B, "basis": basis,
            "period": {"start": iso(start), "end": iso(end)},
            "status": status, "complete": status == COMPLETE, "reason": " ".join(status_reason),
            "timepoints": tps, "missing_windows": missing,
            "peak": summary["peak"], "p95": summary["p95"],
            "method": METHOD, "assumptions": list(ASSUMPTIONS)
            + sorted({a for _, _, _, s in cap_spans for a in s.get("assumptions") or []}),
            "sku_spans": [{"sku": s, "from": iso(a), "to": iso(b) if b else None,
                           "source": src.get("source") or ""} for a, b, s, src in cap_spans],
            "cu_avoided": cu_avoided,
            "runs_not_in_a_timepoint": loose,
            "capacity_value": _value(cap_runs, cap_spans, versions, scenario, basis, region),
            "is_savings": False, "not_savings_reason": NOT_SAVINGS})
    return {"measures": [MEASURE_B, MEASURE_C], "period": {"start": iso(start), "end": iso(end)}, "basis": basis,
            "capacities": results, "unplaced": unplaced,
            "outside_period": {"usage_rows": rows_outside, "runs": runs_outside},
            "is_savings": False, "not_savings_reason": NOT_SAVINGS, "written": False}
