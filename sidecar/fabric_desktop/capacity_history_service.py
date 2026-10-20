"""The capacity settings history's app call (capacity_history.py): read the capacities the signed-in
person can see into the central lakehouse. ``tenant_settings_history`` shows the changes."""

from __future__ import annotations

import re
from typing import Any

from . import capacity_history as ch
from . import tenant_history as th
from .auth import CAPACITY_SCOPES
from .fabric_api import FabricClient, FabricError
from .optimize import setup

METHODS = {"capacity_settings_read"}


class CapacityHistoryMixin:
    def capacity_settings_read(self, approve: bool = False) -> dict[str, Any]:
        """Read the capacities the signed-in person can see once and add what changed to the central
        lakehouse. The first time, it asks before adding the tables (PermissionError ending "Confirm
        to continue."). Without Capacity.Read.All the read is recorded as ``unreadable``, not raised."""
        lake, me = self.central_family(ch.FAMILY, approve=approve)  # type: ignore[attr-defined]
        read_by = self.auth.signed_in_object_id() or "unknown"  # type: ignore[attr-defined]
        at = th.now()
        try:
            capacities = FabricClient(lambda: self.auth._token(CAPACITY_SCOPES)).list_capacities()  # type: ignore[attr-defined]
        except FabricError as exc:
            denied = exc.status in (401, 403) or bool(re.search(r"failed with 40[13]\b", str(exc)))
            outcome = "unreadable" if denied else "failed"
            reason = "Listing capacities needs Capacity.Read.All, which wasn't granted." if denied else str(exc)
            setup.write(lake, me, "read", [ch.read_row(None, read_by, at, outcome, reason)], family=ch.FAMILY)
            return {"outcome": outcome, "reason": ch.redact.secrets(reason), "read_at": at, "changes": []}
        before = self._rows(lake, ch.FAMILY.table("snapshot"))  # type: ignore[attr-defined]
        snapshots, changes = ch.compare(capacities, before, read_by, at)
        if snapshots:
            setup.write(lake, me, "snapshot", snapshots, family=ch.FAMILY)
            setup.write(lake, me, "change", changes, family=ch.FAMILY)
        ids = sorted({str(c["id"]).lower() for c in capacities if c.get("id")})
        setup.write(lake, me, "read", [ch.read_row(i, read_by, at, "read", None) for i in ids or [None]],
                    family=ch.FAMILY)
        return {"outcome": "read", "read_at": at, "capacities": len(ids), "first_read": not before,
                "changes": [{k: c[k] for k in ("scope_type", "scope_id", "setting", "changed_after",
                                               "changed_by_time", "old_json", "new_json")} for c in changes]}
