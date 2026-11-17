"""Incident history in the central Fabric Desktop lakehouse: every incident change, kept for good.

The live list stays where the app and the agents already share it (each environment's
``incidents/incidents.json``, changed only with conditional writes), so "open once" stays exact
and agents need nothing new. What the live list drops (resolved incidents past the newest 200,
timeline entries past 100) is kept here: the ``fdinc_`` table family in the central lakehouse.

- ``incident_event``: one row per timeline entry.
- ``incident_snapshot``: one row per state of an incident (state, priority, owner, times), so
  reports can say what was open when, and how long acknowledging and resolving took.

``report`` measures time to acknowledge, time to resolve and SLAs met from each incident's latest state.

Both are insert-only and keyed by a record_id worked out from the content, so any PC (or later an
agent) can copy the same list again and nothing is added twice. SLA states aren't stored: they
are worked out from the priority and the times, against whichever targets a report uses.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import polars as pl

from ..optimize import schema
from ..optimize.shared import record_id, timestamp
from ..redact import secrets
from .. import jsonfile
from . import sla
from .incidents import ACTIVE, PRIORITIES, default_priority

S = pl.String
TABLES_V1: dict[str, dict[str, schema.Dtype]] = {
    "incident_event": {"incident_id": S, "number": S, "env": S, "workspace_id": S, "kind": S, "priority": S,
                       "at": S, "by": S, "what": S, "text": S},
    "incident_snapshot": {"incident_id": S, "number": S, "env": S, "workspace_id": S, "kind": S, "source": S,
                          "state": S, "priority": S, "owner": S, "team": S, "criticality": S, "workload": S,
                          "title": S, "opened_at": S, "acknowledged_at": S, "resolved_at": S, "resolution": S,
                          "as_of": S},
    "schema_version": schema.TABLES_V1["schema_version"],
}
FAMILY = schema.register(schema.Family("fdinc_", "Incident management",
                                       [schema.Migration(1, "Create the incident tables", TABLES_V1)]))
KEEP_SENT = 50_000  # record ids this PC remembers having copied, so a quiet copy sends nothing


def _ts(value: Any) -> str | None:
    try:
        return timestamp(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _as_of(inc: dict[str, Any]) -> str | None:
    """When the incident last changed: its newest timeline entry, else when it opened or resolved."""
    times = [e.get("at") for e in inc.get("events") or []] + [inc.get("opened_at"), inc.get("resolved_at")]
    return max((t for t in (_ts(x) for x in times) if t), default=None)


def event_rows(incidents: list[dict[str, Any]], env: str, workspace_id: str) -> list[dict[str, Any]]:
    rows = []
    for inc in incidents:
        for e in inc.get("events") or []:
            at = _ts(e.get("at"))
            rows.append({"record_id": record_id("event", inc["id"], at, e.get("what"), e.get("by"), e.get("text")),
                         "incident_id": inc["id"], "number": inc.get("number"), "env": env,
                         "workspace_id": workspace_id, "kind": inc.get("kind"), "priority": inc.get("priority"),
                         "at": at, "by": e.get("by"), "what": e.get("what"), "text": secrets(e.get("text") or "")})
    return rows


def snapshot_rows(incidents: list[dict[str, Any]], env: str, workspace_id: str) -> list[dict[str, Any]]:
    rows = []
    for inc in incidents:
        row = {"incident_id": inc["id"], "number": inc.get("number"), "env": env, "workspace_id": workspace_id,
               "kind": inc.get("kind"), "source": inc.get("source") or "detected", "state": inc.get("state"),
               "priority": inc.get("priority"), "owner": inc.get("owner") or "", "team": inc.get("team") or "",
               "criticality": inc.get("criticality") or "", "workload": inc.get("workload") or "",
               "title": secrets(inc.get("title") or ""), "opened_at": _ts(inc.get("opened_at")),
               "acknowledged_at": _ts(inc.get("acknowledged_at")), "resolved_at": _ts(inc.get("resolved_at")),
               "resolution": inc.get("resolution") or "", "as_of": _as_of(inc)}
        # The same state copied again is the same row; a changed field is a new one.
        row["record_id"] = record_id("snapshot", *(row[k] for k in sorted(row)))
        rows.append(row)
    return rows


class Sent:
    """The record ids this PC already copied, per lakehouse, so each copy sends only what's new.
    Losing the file only costs a larger copy: the table's MERGE skips rows it already has."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict[str, list[str]]:
        try:
            data = jsonfile.read_json(self.path, {})
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def new(self, lakehouse_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sent = set(self._load().get(lakehouse_id, []))
        return [r for r in rows if r["record_id"] not in sent]

    def add(self, lakehouse_id: str, ids: list[str]) -> None:
        data = self._load()
        known = data.get(lakehouse_id, [])
        seen = set(known)
        known += [i for i in ids if i not in seen]
        data[lakehouse_id] = known[-KEEP_SENT:]
        jsonfile.write_text(self.path, json.dumps(data))


# ---- reports: time to acknowledge, time to resolve, SLAs met

REPORT_DAYS = (7, 30, 90, 365)


def _minutes(start: str | None, end: str | None) -> float | None:
    a, b = _when(start), _when(end)
    if a is None or b is None or b < a:
        return None
    return (b - a).total_seconds() / 60


def _when(text: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None
    return t.replace(tzinfo=dt.UTC) if t is not None and t.tzinfo is None else t


def _spread(values: list[float]) -> dict[str, Any]:
    """Count, median and 90th percentile in whole minutes (nearest rank), or None with nothing to measure."""
    if not values:
        return {"count": 0, "median_minutes": None, "p90_minutes": None}
    s = sorted(values)

    def rank(q: float) -> int:
        return round(s[max(0, math.ceil(q * len(s)) - 1)])

    return {"count": len(s), "median_minutes": rank(0.5), "p90_minutes": rank(0.9)}


def report(incidents: list[dict[str, Any]], targets: dict[str, dict[str, int | None]], now: dt.datetime,
           days: int) -> dict[str, Any]:
    """How incidents opened in the last ``days`` were handled, per priority and in total.

    ``incidents`` are each incident's latest state (incidents_history). Times are measured from
    when it opened: to acknowledge (resolving counts as acknowledging) and to resolve. SLA counts
    use the targets in effect now: met, missed (done late), and breached (still open and late);
    an incident still inside its window, or a priority with no target, counts in neither.
    Nothing is estimated: an incident without the times a measure needs is left out of it."""
    since = now - dt.timedelta(days=days)
    picked = [i for i in incidents if (o := _when(i.get("opened_at"))) is not None and since <= o <= now]
    groups: dict[str, list[dict[str, Any]]] = {p: [] for p in PRIORITIES}
    for inc in picked:
        groups.setdefault(inc.get("priority") or default_priority(inc), []).append(inc)

    def measure(items: list[dict[str, Any]]) -> dict[str, Any]:
        ack: list[float] = []
        res: list[float] = []
        slas: dict[str, dict[str, int]] = {"acknowledge": {"met": 0, "missed": 0, "breached": 0},
                "resolve": {"met": 0, "missed": 0, "breached": 0}}
        for inc in items:
            done = inc.get("resolved_at") if inc.get("state") not in ACTIVE else None
            if (m := _minutes(inc.get("opened_at"), inc.get("acknowledged_at") or done)) is not None:
                ack.append(m)
            if done and (m := _minutes(inc.get("opened_at"), done)) is not None:
                res.append(m)
            st = sla.status(inc, targets, now)
            for clock in ("acknowledge", "resolve"):
                if st[clock]["state"] in slas[clock]:
                    slas[clock][st[clock]["state"]] += 1
        judged = {k: c["met"] + c["missed"] + c["breached"] for k, c in slas.items()}
        sla_out: dict[str, dict[str, int | None]] = {
            k: {**c, "met_percent": round(100 * c["met"] / judged[k]) if judged[k] else None} for k, c in slas.items()}
        return {"opened": len(items), "resolved": sum(i.get("state") not in ACTIVE for i in items),
                "open": sum(i.get("state") in ACTIVE for i in items),
                "time_to_acknowledge": _spread(ack), "time_to_resolve": _spread(res), "sla": sla_out}

    return {"days": days, "since": since.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "total": measure(picked),
            "priorities": [{"priority": p} | measure(items) for p, items in groups.items() if items]}
