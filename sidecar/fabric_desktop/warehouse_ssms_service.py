"""Open in SSMS on the Warehouse page: the warehouse's SQL connection, as Fabric gives it, for the shell
to hand to SQL Server Management Studio (or to VS Code's MSSQL extension, or the clipboard).

``wh_connection`` reads the item from Fabric (warehouse.resolve, the same check every query uses: the
server must be a Fabric SQL endpoint) and returns its server, database and the signed-in account's
name. It sends no token anywhere and changes nothing; SSMS signs in on its own with Microsoft Entra.
See docs/warehouse.md, "Open in SSMS".
"""

from __future__ import annotations

from typing import Any

from . import warehouse

METHODS = {"wh_connection"}


class WarehouseSsmsMixin:
    def wh_connection(self, workspace_id: str, item_id: str, kind: str = "warehouse") -> dict[str, Any]:
        """``{server, database, user}`` of a warehouse (``kind`` "warehouse") or of a lakehouse's SQL
        analytics endpoint (``kind`` "endpoint", ``item_id`` the lakehouse). ``user`` is the signed-in
        account's sign-in name, or "" when it isn't known."""
        found = warehouse.resolve(self.fabric, {"workspace_id": workspace_id, "item_id": item_id,  # type: ignore[attr-defined]
                                                "kind": kind})
        account = self._saved_account()  # type: ignore[attr-defined]
        user = str(account.get("username") or "")
        return {"server": found.server, "database": found.database, "user": user if "@" in user else ""}
