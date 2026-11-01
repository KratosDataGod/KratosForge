"""Tenant settings history (testing feedback item 30): snapshots of Fabric's tenant settings in the
central lakehouse, so a platform owner can see what changed and between which two reads.

The tables are the ``fdten_`` family (optimize/schema.py), in the row shape agreed with FinOps'
``fdcap_`` capacity settings family (/mnt/project-files/finops/settings-history-shape.md):

- ``snapshot``: one row per setting, written only when its value differs from the last row.
- ``change``: one row per difference, observed between two reads. Fabric's admin API doesn't say
  when a setting changed or who changed it, so neither is claimed.
- ``read``: one row per read attempt, so a quiet week and an unreadable one aren't confused.

Reading only: no setting is ever changed. ``GET /v1/admin/tenantsettings`` needs a Fabric
administrator (Tenant.Read.All); anyone else's read is recorded as ``unreadable``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from . import redact
from .optimize import schema

S = schema.S

# The shared row shape. FinOps' fdcap_ family uses the same columns, so one view reads both.
SNAPSHOT: dict[str, schema.Dtype] = {"taken_at": S, "scope_type": S, "scope_id": S, "setting": S, "value_json": S, "value_hash": S,
            "source": S, "read_by": S}
CHANGE: dict[str, schema.Dtype] = {"changed_after": S, "changed_by_time": S, "scope_type": S, "scope_id": S, "setting": S, "old_json": S,
          "new_json": S, "source": S, "read_by": S}
READ: dict[str, schema.Dtype] = {"read_at": S, "scope_type": S, "scope_id": S, "source": S, "outcome": S, "reason": S, "read_by": S}

FAMILY = schema.register(schema.Family("fdten_", "Tenant settings history", [schema.Migration(
    1, "Create the tenant settings history tables",
    {"snapshot": SNAPSHOT, "change": CHANGE, "read": READ, "schema_version": schema.TABLES_V1["schema_version"]})]))

SOURCE = "GET /v1/admin/tenantsettings (admin)"
# Fields that describe a setting rather than its value: Microsoft renaming a title isn't a change.
_LABELS = ("settingName", "title", "tenantSettingGroup")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical(value: Any) -> str:
    """JSON with sorted keys and no spaces, so the same value always hashes the same."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def value_of(setting: dict[str, Any]) -> dict[str, Any]:
    """A tenant setting's value: everything but its labels, with security groups and properties in a
    fixed order, so Fabric listing them differently isn't a change."""
    out = {k: v for k, v in setting.items() if k not in _LABELS}
    for key in ("enabledSecurityGroups", "excludedSecurityGroups"):
        if isinstance(out.get(key), list):
            out[key] = sorted(out[key], key=lambda g: str((g or {}).get("graphId", "")))
    if isinstance(out.get("properties"), list):
        out["properties"] = sorted(out["properties"], key=lambda p: str((p or {}).get("name", "")))
    return out


def digest(value_json: str | None) -> str:
    return hashlib.sha256((value_json if value_json is not None else "null").encode()).hexdigest()


def latest(snapshots: list[dict[str, Any]], scope_id: str) -> dict[str, dict[str, Any]]:
    """The newest snapshot row per setting for ``scope_id``."""
    out: dict[str, dict[str, Any]] = {}
    for row in snapshots:
        if row.get("scope_id") != scope_id:
            continue
        prev = out.get(row["setting"])
        if prev is None or row["taken_at"] > prev["taken_at"]:
            out[row["setting"]] = row
    return out


def compare(settings: list[dict[str, Any]], before: dict[str, dict[str, Any]], tenant_id: str, read_by: str,
            taken_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The snapshot and change rows a read adds, given the newest earlier snapshot per setting. A
    setting seen for the first time gets a snapshot and a change with no old value; a setting that
    disappeared gets a snapshot with no value and a change with no new value."""
    current = {s["settingName"]: canonical(value_of(s)) for s in settings if s.get("settingName")}
    snapshots, changes = [], []
    for name in sorted(set(current) | {n for n, r in before.items() if r.get("value_json") is not None}):
        new = current.get(name)
        old_row = before.get(name)
        old = old_row.get("value_json") if old_row else None
        if old_row is not None and digest(old) == digest(new):
            continue
        h = digest(new)
        base = {"scope_type": "tenant", "scope_id": tenant_id, "setting": name, "source": SOURCE, "read_by": read_by}
        snapshots.append(base | {"record_id": f"snap:{tenant_id}:{name}:{h}:{taken_at}", "taken_at": taken_at,
                                 "value_json": new, "value_hash": h})
        changes.append(base | {"record_id": f"change:{tenant_id}:{name}:{h}:{taken_at}",
                               "changed_after": old_row["taken_at"] if old_row else None,
                               "changed_by_time": taken_at, "old_json": old, "new_json": new})
    return snapshots, changes


def read_row(tenant_id: str | None, read_by: str, read_at: str, outcome: str, reason: str | None) -> dict[str, Any]:
    return {"record_id": f"read:{tenant_id or '-'}:{read_by}:{read_at}", "read_at": read_at, "scope_type": "tenant",
            "scope_id": tenant_id, "source": SOURCE, "outcome": outcome,
            "reason": redact.secrets(reason)[:500] if reason else None, "read_by": read_by}
