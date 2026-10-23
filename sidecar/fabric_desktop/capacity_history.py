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
ever changed. A Fabric administrator can choose to add each capacity's tenant setting overrides
(``ADMIN_SOURCE``, Tenant.Read.All), stored as settings named ``override:<settingName>``.

A capacity the reader can't see isn't compared at all: people see different capacities, so one
missing from a read says nothing about whether it changed or was deleted.
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
# Optional, Fabric administrators only: the tenant settings each capacity overrides. Asked for only
# when the person chooses to (``admin=True``), so nobody else is ever prompted for Tenant.Read.All.
ADMIN_SOURCE = "GET /v1/admin/capacities/delegatedTenantSettingOverrides (admin)"
OVERRIDE = "override:"  # setting name prefix, e.g. override:ExportToExcel


def _diff(scope_id: str, name: str, new: str | None, before: dict[str, dict[str, Any]], source: str,
          read_by: str, taken_at: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The snapshot and change rows for one setting, or None when it didn't change."""
    old_row = before.get(name)
    old = old_row.get("value_json") if old_row else None
    if (old_row is None and new is None) or (old_row is not None and th.digest(old) == th.digest(new)):
        return None
    h = th.digest(new)
    base = {"scope_type": "capacity", "scope_id": scope_id, "setting": name, "source": source, "read_by": read_by}
    return (base | {"record_id": f"snap:{scope_id}:{name}:{h}:{taken_at}", "taken_at": taken_at,
                    "value_json": new, "value_hash": h},
            base | {"record_id": f"change:{scope_id}:{name}:{h}:{taken_at}",
                    "changed_after": old_row["taken_at"] if old_row else None,
                    "changed_by_time": taken_at, "old_json": old, "new_json": new})


def compare(capacities: list[dict[str, Any]], snapshots: list[dict[str, Any]], read_by: str,
            taken_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The snapshot and change rows a read adds, given the earlier snapshot rows. Only capacities in
    this read are compared, and only the settings it returned: a field missing from one response
    says nothing about its value, so it isn't recorded as removed."""
    out = []
    for cap in sorted((c for c in capacities if c.get("id")), key=lambda c: str(c["id"])):
        cap_id = str(cap["id"]).lower()
        before = th.latest(snapshots, cap_id)
        for name in SETTINGS:
            if cap.get(name) is not None:
                out.append(_diff(cap_id, name, th.canonical(cap[name]), before, SOURCE, read_by, taken_at))
    rows = [r for r in out if r]
    return [r[0] for r in rows], [r[1] for r in rows]


def compare_overrides(overrides: list[dict[str, Any]], snapshots: list[dict[str, Any]], read_by: str,
                      taken_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The rows an administrator's read of capacity overrides adds. That read covers every capacity,
    so an override seen before and missing now was removed: it gets a change with no new value."""
    current: dict[str, dict[str, str]] = {}
    for o in overrides:
        if o.get("id"):
            current.setdefault(str(o["id"]).lower(), {}).update(
                {OVERRIDE + s["settingName"]: th.canonical(th.value_of(s))
                 for s in o.get("tenantSettings") or [] if s.get("settingName")})
    known = {r["scope_id"] for r in snapshots if str(r.get("setting", "")).startswith(OVERRIDE) and r.get("scope_id")}
    out = []
    for cap_id in sorted(set(current) | known):
        before = {k: v for k, v in th.latest(snapshots, cap_id).items() if k.startswith(OVERRIDE)}
        now = current.get(cap_id, {})
        for name in sorted(set(now) | {n for n, r in before.items() if r.get("value_json") is not None}):
            out.append(_diff(cap_id, name, now.get(name), before, ADMIN_SOURCE, read_by, taken_at))
    rows = [r for r in out if r]
    return [r[0] for r in rows], [r[1] for r in rows]


def read_row(capacity_id: str | None, read_by: str, read_at: str, outcome: str, reason: str | None,
             source: str = SOURCE) -> dict[str, Any]:
    tag = "admin:" if source == ADMIN_SOURCE else ""
    return {"record_id": f"read:{tag}{capacity_id or '-'}:{read_by}:{read_at}", "read_at": read_at,
            "scope_type": "capacity", "scope_id": capacity_id, "source": source, "outcome": outcome,
            "reason": redact.secrets(reason)[:500] if reason else None, "read_by": read_by}
