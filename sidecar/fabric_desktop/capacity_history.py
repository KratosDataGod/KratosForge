"""Capacity settings history (testing feedback item 30): what each Fabric capacity's size, state,
region and name were, read by read, in the central lakehouse.

The tables are the ``fdcap_`` family (optimize/schema.py), in the columns Enterprise readiness'
``fdten_`` tenant settings family uses (tenant_history.py), so its history view reads both:

- ``snapshot``: one row per capacity setting, written only when its value differs from the last row.
- ``change``: one row per difference, observed between two reads. The capacities API says neither
  when a setting changed nor who changed it, so neither is claimed.
- ``read``: one row per capacity per read, and one for a read that failed, so a quiet week and an
  unreadable one aren't confused.

Reading only: ``GET /v1/capacities`` (Capacity.Read.All), no Fabric admin role, and no setting is
ever changed. A capacity the reader can't see isn't compared at all: people see different
capacities, so one missing from a read says nothing about whether it changed or was deleted.
"""

from __future__ import annotations

from typing import Any

from . import redact
from . import tenant_history as th
from .optimize import schema

FAMILY = schema.register(schema.Family("fdcap_", "Capacity settings history", [schema.Migration(
    1, "Create the capacity settings history tables",
    {"snapshot": th.SNAPSHOT, "change": th.CHANGE, "read": th.READ,
     "schema_version": schema.TABLES_V1["schema_version"]})]))

SOURCE = "GET /v1/capacities"
SETTINGS = ("displayName", "sku", "region", "state")


def compare(capacities: list[dict[str, Any]], snapshots: list[dict[str, Any]], read_by: str,
            taken_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The snapshot and change rows a read adds, given the earlier snapshot rows. Only capacities in
    this read are compared. A setting seen for the first time gets a change with no old value; one a
    listed capacity no longer returns gets a change with no new value."""
    snaps, changes = [], []
    for cap in sorted((c for c in capacities if c.get("id")), key=lambda c: str(c["id"])):
        cap_id = str(cap["id"]).lower()
        before = th.latest(snapshots, cap_id)
        for name in SETTINGS:
            new = th.canonical(cap[name]) if cap.get(name) is not None else None
            old_row = before.get(name)
            old = old_row.get("value_json") if old_row else None
            if (old_row is None and new is None) or (old_row is not None and th.digest(old) == th.digest(new)):
                continue
            h = th.digest(new)
            base = {"scope_type": "capacity", "scope_id": cap_id, "setting": name, "source": SOURCE,
                    "read_by": read_by}
            snaps.append(base | {"record_id": f"snap:{cap_id}:{name}:{h}:{taken_at}", "taken_at": taken_at,
                                 "value_json": new, "value_hash": h})
            changes.append(base | {"record_id": f"change:{cap_id}:{name}:{h}:{taken_at}",
                                   "changed_after": old_row["taken_at"] if old_row else None,
                                   "changed_by_time": taken_at, "old_json": old, "new_json": new})
    return snaps, changes


def read_row(capacity_id: str | None, read_by: str, read_at: str, outcome: str, reason: str | None) -> dict[str, Any]:
    return {"record_id": f"read:{capacity_id or '-'}:{read_by}:{read_at}", "read_at": read_at,
            "scope_type": "capacity", "scope_id": capacity_id, "source": SOURCE, "outcome": outcome,
            "reason": redact.secrets(reason)[:500] if reason else None, "read_by": read_by}
