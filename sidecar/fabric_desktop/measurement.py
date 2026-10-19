"""Capacity and cost measurements, each labeled for where its number came from (FINOPS-001).

Every CU or cost figure KratosForge shows is one of these dicts, so a view can
say how much to trust it and how it was worked out:

- **measured**: read from a source that observed it (Fabric's job API, an imported
  Capacity Metrics export, this PC). ``source`` and ``collected_at`` are required.
- **estimated**: worked out from documented assumptions, such as run time x configured CUs.
  ``method`` and ``assumptions`` are required.
- **scenario**: what something would have used if it had run differently, such as a run on
  this PC priced as if it ran in Fabric. Never added to measured or estimated figures.
- **unknown**: not enough reliable information. Its value is ``None``, never 0.

Resource use and money are kept apart: CU-seconds and CU-hours convert into each other,
capacity utilization is a percent of a capacity over a period, and currency is a price applied
to CU use. A price is never a charge: on a reserved or already-running capacity, running more
or less doesn't change the invoice.

Shape::

    {"id", "classification", "metric", "unit", "value", "currency",
     "workload_id", "run_id", "workspace_id", "capacity_id",
     "source", "method", "collected_at", "period": {"start", "end"},
     "assumptions": [...], "uncertainty", "reconciliation", "reason"}

The framework knows nothing about any particular Fabric telemetry provider; providers (run
logs, imported exports) build measurements with :func:`make`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from typing import Any

MEASURED, ESTIMATED, SCENARIO, UNKNOWN = "measured", "estimated", "scenario", "unknown"
CLASSIFICATIONS = (MEASURED, ESTIMATED, SCENARIO, UNKNOWN)
LABELS = {MEASURED: "Measured", ESTIMATED: "Estimated", SCENARIO: "Scenario", UNKNOWN: "Unknown"}

# Units, by family. Only units in the same family convert into each other.
CU_SECONDS, CU_HOURS, PERCENT, CURRENCY, SECONDS = "cu_seconds", "cu_hours", "percent", "currency", "seconds"
UNITS = {CU_SECONDS: "capacity", CU_HOURS: "capacity", PERCENT: "utilization", CURRENCY: "money", SECONDS: "time"}
_PER_SECOND = {CU_SECONDS: 1.0, CU_HOURS: 3600.0}

RECONCILIATION = ("not_applicable", "unreconciled", "matched", "unmatched")

# A measurement is weaker than another when it is further down this list; a total is as weak as
# its weakest part. Scenario figures never mix with the others.
_STRENGTH = {MEASURED: 0, ESTIMATED: 1, UNKNOWN: 2}


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert between units of the same family: CU-seconds and CU-hours, or a unit to itself.

    Anything else raises: CU use isn't money without a price, and isn't utilization without a
    capacity size and a period."""
    if from_unit not in UNITS or to_unit not in UNITS:
        raise ValueError(f"unknown unit {from_unit if from_unit not in UNITS else to_unit!r}")
    if from_unit == to_unit:
        return float(value)
    if from_unit in _PER_SECOND and to_unit in _PER_SECOND:
        return float(value) * _PER_SECOND[from_unit] / _PER_SECOND[to_unit]
    raise ValueError(f"{from_unit} can't be converted to {to_unit}: they measure different things")


def _iso(at: Any) -> str | None:
    t = parse_time(at)
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds") if t else None


def parse_time(at: Any) -> dt.datetime | None:
    if isinstance(at, dt.datetime):
        return at if at.tzinfo else at.replace(tzinfo=dt.timezone.utc)
    if not at:
        return None
    try:
        t = dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def problems(m: dict[str, Any]) -> list[str]:
    """What is wrong with a measurement, in plain words; empty when it is valid."""
    out: list[str] = []
    c = m.get("classification")
    if c not in CLASSIFICATIONS:
        return [f"classification must be one of {', '.join(CLASSIFICATIONS)}, not {c!r}"]
    unit = m.get("unit")
    if unit not in UNITS:
        out.append(f"unit must be one of {', '.join(UNITS)}, not {unit!r}")
    if not m.get("metric"):
        out.append("metric is missing")
    value = m.get("value")
    if c == UNKNOWN:
        if value is not None:
            out.append("an unknown measurement has no value; use None, not a number")
        if not m.get("reason"):
            out.append("an unknown measurement says why it is unknown")
    elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        out.append(f"value must be a finite number, not {value!r}")
    elif value < 0:
        out.append("value can't be negative")
    elif unit == PERCENT and value > 100:
        out.append("a utilization over 100% isn't valid")
    if unit == CURRENCY and c != UNKNOWN and not m.get("currency"):
        out.append("a money value needs its currency")
    if unit != CURRENCY and m.get("currency"):
        out.append("only money values have a currency")
    if c == MEASURED:
        if not m.get("source"):
            out.append("a measured value names the source that observed it")
        if not m.get("collected_at"):
            out.append("a measured value says when it was collected")
        if m.get("assumptions"):
            out.append("a value that rests on assumptions is estimated, not measured")
    if c in (ESTIMATED, SCENARIO):
        if not m.get("method"):
            out.append(f"an {LABELS[c].lower()} value says how it was worked out")
        if not m.get("assumptions"):
            out.append(f"an {LABELS[c].lower()} value lists its assumptions")
    if m.get("reconciliation", "not_applicable") not in RECONCILIATION:
        out.append(f"reconciliation must be one of {', '.join(RECONCILIATION)}")
    period = m.get("period") or {}
    start, end = parse_time(period.get("start")), parse_time(period.get("end"))
    if (period.get("start") and not start) or (period.get("end") and not end):
        out.append("period start and end must be times")
    elif start and end and end < start:
        out.append("period ends before it starts")
    if m.get("collected_at") and not parse_time(m["collected_at"]):
        out.append("collected_at must be a time")
    return out


def validate(m: dict[str, Any]) -> dict[str, Any]:
    """The measurement, or ValueError listing everything wrong with it."""
    found = problems(m)
    if found:
        raise ValueError("Not a valid measurement: " + "; ".join(found))
    return m


def _id(m: dict[str, Any]) -> str:
    key = {k: m.get(k) for k in ("classification", "metric", "unit", "workload_id", "run_id", "workspace_id",
                                 "capacity_id", "source", "period")}
    return "m-" + hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:16]


def make(classification: str, metric: str, unit: str, value: float | None, *, currency: str | None = None,
         workload_id: str | None = None, run_id: str | None = None, workspace_id: str | None = None,
         capacity_id: str | None = None, source: str = "", method: str = "", collected_at: Any = None,
         period_start: Any = None, period_end: Any = None, assumptions: list[str] | None = None,
         uncertainty: str | None = None, reconciliation: str = "not_applicable", reason: str = "") -> dict[str, Any]:
    """A validated measurement. Its id is stable for the same metric of the same run from the same source."""
    m = {"classification": classification, "label": LABELS.get(classification, classification), "metric": metric,
         "unit": unit, "value": None if value is None else (round(float(value), 6) if isinstance(value, (int, float))
                                                              and not isinstance(value, bool) else value),
         "currency": currency, "workload_id": workload_id, "run_id": run_id, "workspace_id": workspace_id,
         "capacity_id": capacity_id, "source": source, "method": method, "collected_at": _iso(collected_at) or collected_at,
         "period": {"start": _iso(period_start), "end": _iso(period_end)}, "assumptions": list(assumptions or []),
         "uncertainty": uncertainty, "reconciliation": reconciliation, "reason": reason}
    m["id"] = _id(m)
    return validate(m)


def unknown(metric: str, unit: str, reason: str, **ids: Any) -> dict[str, Any]:
    return make(UNKNOWN, metric, unit, None, reason=reason, **ids)


def freshness(m: dict[str, Any], max_age_s: float | None, now: dt.datetime | None = None) -> dict[str, Any]:
    """How old the observation is and whether it is stale. Estimates are as fresh as their inputs,
    so only measured values (or anything with a collection time) can go stale."""
    collected = parse_time(m.get("collected_at"))
    if not collected:
        return {"age_seconds": None, "stale": False, "text": "Not collected from a source"}
    age = ((now or dt.datetime.now(dt.timezone.utc)) - collected).total_seconds()
    stale = max_age_s is not None and age > max_age_s
    return {"age_seconds": round(max(age, 0)), "stale": stale,
            "text": f"Collected {collected.isoformat(timespec='minutes')}" + (" (stale)" if stale else "")}


def to_unit(m: dict[str, Any], unit: str) -> dict[str, Any]:
    """The same measurement in another unit of the same family."""
    if m["value"] is None:
        return dict(m, unit=unit)
    return dict(m, unit=unit, value=round(convert(m["value"], m["unit"], unit), 6))


def total(parts: list[dict[str, Any]], metric: str, unit: str, *, currency: str | None = None) -> dict[str, Any]:
    """Add measurements of one metric. The result is as weak as its weakest part.

    Unknown parts aren't counted as 0: the total covers only the known parts and says how many
    were unknown (``unknown_parts``) and is labeled incomplete. Scenario figures can only be
    added to other scenario figures; unknown parts can sit beside either."""
    scen = {p["classification"] == SCENARIO for p in parts if p["classification"] != UNKNOWN}
    if len(scen) > 1:
        raise ValueError("scenario figures can't be added to measured or estimated ones")
    known = [p for p in parts if p["value"] is not None]
    unknown_parts = len(parts) - len(known)
    for p in known:
        if p.get("currency") != currency and unit == CURRENCY:
            raise ValueError(f"can't add {p.get('currency')} to {currency}")
    value = sum(convert(p["value"], p["unit"], unit) for p in known)
    if scen == {True}:
        cls = SCENARIO
    elif not parts:
        cls = MEASURED  # nothing ran: zero is observed, not assumed
    elif not known:
        cls = UNKNOWN
    else:
        cls = max((p["classification"] for p in known), key=_STRENGTH.__getitem__)
    out = {"classification": cls, "label": LABELS[cls], "metric": metric, "unit": unit,
           "value": None if cls == UNKNOWN else round(value, 6), "currency": currency if unit == CURRENCY else None,
           "parts": len(parts), "unknown_parts": unknown_parts, "complete": unknown_parts == 0,
           "by_classification": {c: sum(1 for p in parts if p["classification"] == c) for c in CLASSIFICATIONS
                                 if any(p["classification"] == c for p in parts)},
           "assumptions": sorted({a for p in known for a in p.get("assumptions") or []}),
           "methods": sorted({p["method"] for p in known if p.get("method")}),
           "sources": sorted({p["source"] for p in known if p.get("source")})}
    if not out["complete"]:
        out["reason"] = (f"{unknown_parts} of {len(parts)} part{'s' if len(parts) != 1 else ''} not known; "
                         "the total covers the rest")
    return out
