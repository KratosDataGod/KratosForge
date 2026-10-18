"""The tenant settings history's app calls (tenant_history.py): read Fabric's tenant settings into
the central lakehouse, and show what changed."""

from __future__ import annotations

import re
from typing import Any

from . import tenant_history as th
from .auth import TENANT_READ_SCOPES
from .fabric_api import FabricClient, FabricError
from .optimize import setup

METHODS = {"tenant_settings_read", "tenant_settings_history"}

# FinOps' capacity settings family: read for the combined view when it is there, never written.
_CAPACITY_TABLES = {"change": "fdcap_change", "read": "fdcap_read"}


class TenantHistoryMixin:
    def tenant_settings_read(self, approve: bool = False) -> dict[str, Any]:
        """Read Fabric's tenant settings once and add what changed to the central lakehouse. The first
        time, it asks before adding the tables (PermissionError ending "Confirm to continue."). A
        person who isn't a Fabric administrator gets an ``unreadable`` read recorded, not an error."""
        lake, me = self.central_family(th.FAMILY, approve=approve)  # type: ignore[attr-defined]
        tenant = self.auth.signed_in_tenant()  # type: ignore[attr-defined]
        read_by = self.auth.signed_in_object_id() or "unknown"  # type: ignore[attr-defined]
        at = th.now()
        try:
            client = FabricClient(lambda: self.auth._token(TENANT_READ_SCOPES))  # type: ignore[attr-defined]
            settings = client.list_tenant_settings()
        except FabricError as exc:
            denied = exc.status in (401, 403) or bool(re.search(r"failed with 40[13]\b", str(exc)))
            outcome = "unreadable" if denied else "failed"
            reason = ("Only a Fabric administrator can read tenant settings (Tenant.Read.All)." if denied else str(exc))
            setup.write(lake, me, "read", [th.read_row(tenant, read_by, at, outcome, reason)], family=th.FAMILY)
            return {"outcome": outcome, "reason": th.redact.secrets(reason), "read_at": at, "changes": []}
        if not tenant:
            raise RuntimeError("Sign in again: the signed-in account's tenant isn't known.")
        before = th.latest(self._rows(lake, th.FAMILY.table("snapshot")), tenant)
        snapshots, changes = th.compare(settings, before, tenant, read_by, at)
        if snapshots:
            setup.write(lake, me, "snapshot", snapshots, family=th.FAMILY)
            setup.write(lake, me, "change", changes, family=th.FAMILY)
        setup.write(lake, me, "read", [th.read_row(tenant, read_by, at, "read", None)], family=th.FAMILY)
        return {"outcome": "read", "read_at": at, "settings": len(settings), "first_read": not before,
                "changes": [_public(c) for c in changes]}

    def tenant_settings_history(self, limit: int = 200) -> dict[str, Any]:
        """Changes to tenant settings, and to capacity settings when FinOps' tables are there, newest
        first, with the last successful read and the reads that failed since. Reads only."""
        conn = self.central_lakehouse()  # type: ignore[attr-defined]
        if not conn:
            return {"connected": False, "changes": [], "last_read": None, "gaps": []}
        lake = self._optimize_lake(conn["workspace_id"], conn["lakehouse_id"], conn.get("schemas", True))  # type: ignore[attr-defined]
        changes = self._rows(lake, th.FAMILY.table("change")) + self._rows(lake, _CAPACITY_TABLES["change"])
        reads = self._rows(lake, th.FAMILY.table("read")) + self._rows(lake, _CAPACITY_TABLES["read"])
        changes.sort(key=lambda r: r.get("changed_by_time") or "", reverse=True)
        reads.sort(key=lambda r: r.get("read_at") or "", reverse=True)
        last = {}
        for r in reads:  # newest successful read per kind of scope
            if r.get("outcome") == "read":
                last.setdefault(r.get("scope_type"), r.get("read_at"))
        gaps = [{k: r.get(k) for k in ("read_at", "scope_type", "scope_id", "outcome", "reason")}
                for r in reads if r.get("outcome") != "read" and r.get("read_at", "") > last.get(r.get("scope_type"), "")]
        return {"connected": True, "changes": [_public(c) for c in changes[:limit]], "last_read": last, "gaps": gaps}

    @staticmethod
    def _rows(lake: Any, name: str) -> list[dict[str, Any]]:
        df = lake.rows(name) if lake.has(name) else None
        return df.to_dicts() if df is not None else []


def _public(change: dict[str, Any]) -> dict[str, Any]:
    """A change as the UI shows it: observed between two reads, never an exact time or a person."""
    return {k: change.get(k) for k in ("scope_type", "scope_id", "setting", "changed_after", "changed_by_time",
                                         "old_json", "new_json")}
