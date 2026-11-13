"""What an opportunity is worth, as measurement.py figures (brief section 9).

Only two things are counted as known:

- **Run time**, measured from Fabric's own job history for the item (start and end of each run).
- **CU**, measured, only from a Capacity Metrics or Cost Management export the person imported
  (usage/), matched to the item by workspace and item name.

From those, the monthly figure an opportunity could avoid is *estimated* ("the next 30 days run
like the last N"), and only when the opportunity removes the whole workload. When it would reduce
part of a workload (an incremental load instead of a full one), how much is not known until it is
measured, so the figure is Unknown with that reason, never a guess.

CU is never turned into money saved. A CU figure at a configured price is the *value* of that
capacity, and it is realizable only when a capacity is resized, paused or retired, or when the
capacity is billed by use; the record says so.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .. import measurement as ms

MONTH_DAYS = 30
JOB_HISTORY = "Fabric job history (GET /workspaces/{id}/items/{id}/jobs/instances)"
NOT_REALIZABLE = ("CU avoided frees capacity; money is saved only when a capacity is resized, paused or retired, "
                  "or on a pay-as-you-go capacity. The capacity planner decides that.")


def _key(workspace: Any, item: Any) -> tuple[str, str]:
    return (str(workspace or "").strip().lower(), str(item or "").strip().lower())


def usage_by_item(imports: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Measured CU per (workspace name, item name) from imported usage files: the total, the
    period it covers, the files it came from. Rows seen in two imports count once."""
    seen: set[str] = set()
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for doc in imports:
        for row in doc.get("rows") or []:
            m = row.get("cu") or {}
            if row.get("key") in seen or m.get("value") is None:
                continue
            seen.add(str(row.get("key")))
            start = ms.parse_time((m.get("period") or {}).get("start"))
            end = ms.parse_time((m.get("period") or {}).get("end")) or start
            if start is None or end is None:
                continue
            got = out.setdefault(_key(row.get("workspace"), row.get("item")),
                                 {"cu_seconds": 0.0, "start": start, "end": end, "rows": 0, "files": set()})
            got["cu_seconds"] += float(m["value"])
            got["start"], got["end"] = min(got["start"], start), max(got["end"], end)
            got["rows"] += 1
            got["files"].add(str(doc.get("file_name") or doc.get("id") or "an imported file"))
    return out


def current(w: dict[str, Any], usage: dict[tuple[str, str], dict[str, Any]], now: dt.datetime) -> dict[str, Any]:
    """What the workload costs today, as measured: its run time and, if imported, its CU."""
    r = w["runs"]
    ids = {"workload_id": w["item_id"], "workspace_id": w.get("workspace_id")}
    if not r["read"]:
        secs = ms.unknown("fabric_run_seconds", ms.SECONDS, "The item's job history couldn't be read.", **ids)
    elif r["seconds_total"] is None:
        secs = ms.unknown("fabric_run_seconds", ms.SECONDS, "Its runs have no end time yet.", **ids)
    else:
        secs = ms.make(ms.MEASURED, "fabric_run_seconds", ms.SECONDS, r["seconds_total"], source=JOB_HISTORY,
                       method=f"end minus start of each finished run in the last {r['days']} days",
                       collected_at=now, period_start=now - dt.timedelta(days=r["days"]), period_end=now, **ids)
    u = usage.get(_key(w.get("workspace_name"), w["name"]))
    if u is None:
        cu = ms.unknown("fabric_item_cu", ms.CU_SECONDS, "No imported usage file has this item. Import a Capacity "
                        "Metrics export (Optimize > Cost and savings) to measure it.", **ids)
    else:
        cu = ms.make(ms.MEASURED, "fabric_item_cu", ms.CU_SECONDS, u["cu_seconds"],
                     source="imported usage: " + ", ".join(sorted(u["files"])),
                     method=f"sum of {u['rows']} imported row(s) matched by workspace and item name",
                     collected_at=now, period_start=u["start"], period_end=u["end"], **ids)
    return {"runs": r["runs"], "scheduled_runs": r["scheduled"], "days": r["days"], "run_seconds": secs, "cu": cu}


def _monthly(parts: list[dict[str, Any]], metric: str, unit: str, days_of: list[float], what: str,
             ids: dict[str, Any]) -> dict[str, Any]:
    missing = [p for p in parts if p["value"] is None]
    if missing:
        return ms.unknown(metric, unit, missing[0].get("reason") or "Not measured.", **ids)
    value = sum(p["value"] / max(d, 1.0) * MONTH_DAYS for p, d in zip(parts, days_of, strict=True))
    return ms.make(ms.ESTIMATED, metric, unit, value, method=f"measured {what} scaled to {MONTH_DAYS} days",
                   assumptions=[f"the next {MONTH_DAYS} days run like the measured period",
                                "OneLake reads and writes the workload makes are not counted separately"], **ids)


def _days(m: dict[str, Any], fallback: float) -> float:
    start, end = ms.parse_time(m["period"]["start"]), ms.parse_time(m["period"]["end"])
    return max((end - start).total_seconds() / 86400, 1.0) if start and end else fallback


def avoidable(removed: list[dict[str, Any]], usage: dict[tuple[str, str], dict[str, Any]], now: dt.datetime, *,
              partial_reason: str = "", price: dict[str, Any] | None = None) -> dict[str, Any]:
    """The impact of an opportunity that removes the ``removed`` workloads' runs (all of them), or,
    with ``partial_reason``, reduces them by an amount not yet known."""
    ids = {"workload_id": removed[0]["item_id"] if len(removed) == 1 else None,
           "workspace_id": removed[0].get("workspace_id")}
    now_of = [current(w, usage, now) for w in removed]
    if partial_reason:
        secs = ms.unknown("fabric_run_seconds_avoided", ms.SECONDS, partial_reason, **ids)
        cu = ms.unknown("fabric_cu_avoided", ms.CU_SECONDS, partial_reason, **ids)
    else:
        secs = _monthly([c["run_seconds"] for c in now_of], "fabric_run_seconds_avoided", ms.SECONDS,
                        [float(w["runs"]["days"]) for w in removed], "Fabric run time", ids)
        cu = _monthly([c["cu"] for c in now_of], "fabric_cu_avoided", ms.CU_SECONDS,
                      [_days(c["cu"], float(w["runs"]["days"])) for c, w in zip(now_of, removed, strict=True)],
                      "CU from imported usage", ids)
    worth = value(cu, price, ids)
    return {"basis": "part of the workload" if partial_reason else "whole workload",
            "current": now_of, "run_seconds_per_month": secs, "cu_per_month": cu, "cu_value_per_month": worth,
            "realizable": False, "realizable_reason": NOT_REALIZABLE,
            "performance": "Not measured. Run a before and after comparison to measure it."}


def value(cu: dict[str, Any], price: dict[str, Any] | None, ids: dict[str, Any]) -> dict[str, Any]:
    """The CU figure at the configured price per CU hour: a value, not a saving (see NOT_REALIZABLE)."""
    per_hour = (price or {}).get("price_per_cu_hour")
    currency = (price or {}).get("currency") or None
    if cu["value"] is None:
        return ms.unknown("cu_value", ms.CURRENCY, "The CU figure isn't known.", **ids)
    if not per_hour or not currency:
        return ms.unknown("cu_value", ms.CURRENCY, "No price per CU hour is set (Optimize > Cost and savings).", **ids)
    return ms.make(ms.ESTIMATED, "cu_value", ms.CURRENCY, ms.convert(cu["value"], cu["unit"], ms.CU_HOURS) * float(per_hour), currency=currency,
                   method="CU hours times the configured price per CU hour",
                   assumptions=list(cu.get("assumptions") or []) + [
                       f"price: {(price or {}).get('source') or 'as configured'}; a price is not a charge that goes away"],
                   **ids)


def per_workload(w: dict[str, Any], usage: dict[tuple[str, str], dict[str, Any]], now: dt.datetime) -> dict[str, Any]:
    """The monthly run time and CU of one workload, as removing it all would avoid. Totals add these
    per workload, so two opportunities on the same workload are never counted twice."""
    c = current(w, usage, now)
    ids = {"workload_id": w["item_id"], "workspace_id": w.get("workspace_id")}
    days = float(w["runs"]["days"])
    return {"run_seconds_per_month": _monthly([c["run_seconds"]], "fabric_run_seconds_avoided", ms.SECONDS, [days],
                                              "Fabric run time", ids),
            "cu_per_month": _monthly([c["cu"]], "fabric_cu_avoided", ms.CU_SECONDS, [_days(c["cu"], days)],
                                     "CU from imported usage", ids)}
