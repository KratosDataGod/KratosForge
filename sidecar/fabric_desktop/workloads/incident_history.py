"""Incident history in the central Fabric Desktop lakehouse: every incident change, kept for good.

The live list stays where the app and the agents already share it (each environment's
``incidents/incidents.json``, changed only with conditional writes), so "open once" stays exact
and agents need nothing new. What the live list drops (resolved incidents past the newest 200,
timeline entries past 100) is kept here: the ``fdinc_`` table family in the central lakehouse.

- ``incident_event``: one row per timeline entry.
- ``incident_snapshot``: one row per state of an incident (state, priority, owner, times), so
  reports can say what was open when, and how long acknowledging and resolving took.

Both are insert-only and keyed by a record_id worked out from the content, so any PC (or later an
agent) can copy the same list again and nothing is added twice. SLA states aren't stored: they
are worked out from the priority and the times, against whichever targets a report uses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from ..optimize import schema
from ..optimize.shared import record_id, timestamp
from ..redact import secrets
from .. import jsonfile

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
