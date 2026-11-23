"""What the KratosForge Environment itself costs, kept apart from what it saves (CP-011, gap G6).

Read-only and honest by construction (measurement.py's rules):

- **Sync overhead** is Measured: the seconds, rows and commits each Optimize sync recorded
  (optimize_service._record_overhead), added up per metric over the period. It covers every PC that
  syncs to the environment's lakehouse, with this PC's own share beside it (by installation id).
- **Fabric capacity** the environment's items use is Unknown until the FinOps capacity import provides
  measured CU for them (ESI decisions D3 and D4). It is never estimated here.
- **Money** is Unknown for the same reason. A price would only ever be a Scenario figure (savings/pricing.py).
- **Net** (savings minus cost) is shown only when both sides are known in the same unit and currency,
  and neither is a scenario mixed with a measured figure. Otherwise it is None, with the reason.

Nothing here writes, and nothing is fabricated: a figure that isn't known says so.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Iterable

from .. import measurement as ms

SYNC_PREFIX = "optimize.sync."
SYNC_METRICS = (("optimize_sync_seconds", ms.SECONDS), ("optimize_sync_rows", ms.COUNT),
                ("optimize_sync_commits", ms.COUNT))
NO_CU = ("No measured Fabric capacity use for this environment yet, because KratosForge doesn't import "
         "capacity use from Fabric yet.")
NO_MONEY = "Not known, because there is no measured Fabric capacity use to price yet."
SYNC_SCOPE = "Every PC that syncs to this environment"
NO_NET = "Net savings is shown only when both the savings and the environment's cost are known."


def _part(row: dict[str, Any]) -> dict[str, Any]:
    try:
        assumptions = json.loads(row.get("assumptions") or "[]")
    except (TypeError, ValueError):
        assumptions = []
    return {"classification": row.get("classification") or ms.UNKNOWN, "unit": row.get("unit"),
            "value": row.get("value"), "currency": row.get("currency"), "source": row.get("source") or "",
            "method": row.get("method") or "", "assumptions": assumptions if isinstance(assumptions, list) else []}


def _when(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, str) and value:
        try:
            got = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return got if got.tzinfo else got.replace(tzinfo=dt.timezone.utc)
    return None


def summary(rows: Iterable[dict[str, Any]], *, days: int = 30, now: dt.datetime | None = None,
            savings: dict[str, Any] | None = None, installation_id: str = "") -> dict[str, Any]:
    """The environment's cost over the last ``days``. ``rows``: fdopt_measurements rows.
    ``savings``: a measurement.total result for the same period, when the caller has one.
    ``installation_id``: this PC's, for its own share (``this_pc``) beside every PC's."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=days)
    sync: dict[str, list[dict[str, Any]]] = {metric: [] for metric, _ in SYNC_METRICS}
    mine: dict[str, list[dict[str, Any]]] = {metric: [] for metric, _ in SYNC_METRICS}
    pcs: set[str] = set()
    for row in rows:
        if not str(row.get("workload_key") or "").startswith(SYNC_PREFIX) or row.get("metric") not in sync:
            continue
        at = _when(row.get("collected_at"))
        if at is None or not since <= at <= now:
            continue
        sync[row["metric"]].append(_part(row))
        if row.get("installation_id"):
            pcs.add(str(row["installation_id"]))
        if installation_id and row.get("installation_id") == installation_id:
            mine[row["metric"]].append(_part(row))
    overhead = {metric: ms.total(sync[metric], metric, unit) for metric, unit in SYNC_METRICS}
    this_pc = ({metric: ms.total(mine[metric], metric, unit) for metric, unit in SYNC_METRICS}
               if installation_id else None)
    cu = ms.unknown("environment_cu", ms.CU_SECONDS, NO_CU)
    money = ms.unknown("environment_cost", ms.CURRENCY, NO_MONEY)
    return {"period": {"start": since.isoformat(), "end": now.isoformat(), "days": days},
            "sync_overhead": overhead, "sync_scope": SYNC_SCOPE, "pcs": len(pcs),
            "syncs": len(sync["optimize_sync_seconds"]), "this_pc": this_pc,
            "fabric_cu": cu, "cost": money, "savings": savings, "net": net(savings, money)}


def net(savings: dict[str, Any] | None, cost: dict[str, Any]) -> dict[str, Any]:
    """Savings minus cost, only when both are known and comparable; otherwise None with why."""
    if not savings or savings.get("value") is None or cost.get("value") is None:
        return {"value": None, "reason": NO_NET}
    if (savings.get("unit"), savings.get("currency")) != (cost.get("unit"), cost.get("currency")):
        return {"value": None, "reason": "The savings and the cost aren't in the same unit or currency."}
    scenario = {savings.get("classification") == ms.SCENARIO, cost.get("classification") == ms.SCENARIO}
    if len(scenario) > 1:
        return {"value": None, "reason": "A scenario figure can't be netted against a measured or estimated one."}
    order = (ms.MEASURED, ms.ESTIMATED, ms.SCENARIO, ms.UNKNOWN)  # weakest last
    weakest = max((savings.get("classification") or ms.UNKNOWN, cost.get("classification") or ms.UNKNOWN),
                  key=lambda c: order.index(c) if c in order else len(order))
    return {"value": round(float(savings["value"]) - float(cost["value"]), 6), "unit": cost.get("unit"),
            "currency": cost.get("currency"), "classification": weakest, "label": ms.LABELS.get(weakest, weakest), "reason": ""}
