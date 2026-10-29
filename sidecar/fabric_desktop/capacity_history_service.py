"""The capacity settings history's app call (capacity_history.py): read the capacities the signed-in
person can see into the central lakehouse. ``tenant_settings_history`` shows the changes."""

from __future__ import annotations

from typing import Any

from . import capacity_history as ch
from . import tenant_history as th
from .auth import CAPACITY_SCOPES, TENANT_READ_SCOPES
from .fabric_api import FabricClient, FabricError
from .optimize import setup

METHODS = {"capacity_settings_read"}


class CapacityHistoryMixin:
    def capacity_settings_read(self, approve: bool = False, admin: bool = False) -> dict[str, Any]:
        """Read the capacities the signed-in person can see once and add what changed to the central
        lakehouse. The first time, it asks before adding the tables (PermissionError ending "Confirm
        to continue."). Without Capacity.Read.All the read is recorded as ``unreadable``, not raised.
        With ``admin``, a Fabric administrator also records each capacity's tenant setting overrides;
        anyone else's attempt is recorded as ``unreadable`` and the capacities are still read."""
        lake, me = self.central_family(ch.FAMILY, approve=approve)  # type: ignore[attr-defined]
        read_by = self.auth.signed_in_object_id() or "unknown"  # type: ignore[attr-defined]
        at = th.now()
        try:
            capacities = FabricClient(lambda: self.auth._token(CAPACITY_SCOPES)).list_capacities()  # type: ignore[attr-defined]
        except FabricError as exc:
            outcome = _outcome(exc)
            reason = "Listing capacities needs Capacity.Read.All, which wasn't granted." if outcome == "unreadable" else str(exc)
            setup.write(lake, me, "read", [ch.read_row(None, read_by, at, outcome, reason)], family=ch.FAMILY)
            return {"outcome": outcome, "reason": ch.redact.secrets(reason), "read_at": at, "changes": []}
        before = self._rows(lake, ch.FAMILY.table("snapshot"))  # type: ignore[attr-defined]
        snapshots, changes = ch.compare(capacities, before, read_by, at)
        reads, admin_out = [], None
        if admin:
            try:
                overrides = FabricClient(lambda: self.auth._token(TENANT_READ_SCOPES)).list_capacity_tenant_setting_overrides()  # type: ignore[attr-defined]
            except FabricError as exc:
                admin_out = _outcome(exc)
                reason = ("Only a Fabric administrator can read capacity overrides (Tenant.Read.All)."
                          if admin_out == "unreadable" else str(exc))
                reads.append(ch.read_row(None, read_by, at, admin_out, reason, ch.ADMIN_SOURCE))
            else:
                admin_out = "read"
                more_snaps, more_changes = ch.compare_overrides(overrides, before, read_by, at)
                snapshots, changes = snapshots + more_snaps, changes + more_changes
                reads.append(ch.read_row(None, read_by, at, "read", None, ch.ADMIN_SOURCE))
        if snapshots:
            setup.write(lake, me, "snapshot", snapshots, family=ch.FAMILY)
            setup.write(lake, me, "change", changes, family=ch.FAMILY)
        ids = sorted({str(c["id"]).lower() for c in capacities if c.get("id")})
        setup.write(lake, me, "read", [ch.read_row(i, read_by, at, "read", None) for i in ids or [None]] + reads,
                    family=ch.FAMILY)
        return {"outcome": "read", "read_at": at, "capacities": len(ids), "first_read": not before,
                "overrides": admin_out,
                "changes": [{k: c[k] for k in ("scope_type", "scope_id", "setting", "changed_after",
                                               "changed_by_time", "old_json", "new_json")} for c in changes]}


def _outcome(exc: FabricError) -> str:
    """``unreadable`` when the account isn't allowed (401/403), otherwise ``failed``."""
    return "unreadable" if exc.status in (401, 403) else "failed"
