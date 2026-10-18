"""OneLake > Access across every workspace: the access cache in the central lakehouse (access_cache.py).

``access_cache`` reads what the cache holds, for the workspaces the signed-in person can open.
``access_cache_refresh`` asks Fabric again and writes what changed; the first refresh sets up the
``fdacc_`` tables only after the person confirms. Neither changes anything in the workspaces it
looks at: the only writes are the cache's own rows in the central lakehouse.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from . import access as access_mod
from . import access_cache as cache
from .auth import ONELAKE_SCOPES
from .optimize import setup

METHODS = {"access_cache", "access_cache_refresh"}


def _now() -> str:
    # Microseconds, so two refreshes in the same second still order and never share a scan id.
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds")


class AccessCacheMixin:

    def _visible_workspaces(self) -> list[dict[str, Any]]:
        # Fails rather than shows an empty list: no one sees access they can't open.
        return [{"id": w["id"], "name": w.get("displayName") or ""}
                for w in self.fabric.list_workspaces() if w.get("id")]  # type: ignore[attr-defined]

    def access_cache(self) -> dict[str, Any]:
        """{ready, state, message, workspaces}: what the cache last saw, for workspaces this person can open."""
        conn = self.central_lakehouse()  # type: ignore[attr-defined]
        if not conn:
            return {"ready": False, "state": None, "workspaces": [],
                    "message": "Connect this PC to a central lakehouse first (Settings > Optimize lakehouses)."}
        lake = self._optimize_lake(conn["workspace_id"], conn["lakehouse_id"], conn.get("schemas", True))  # type: ignore[attr-defined]
        state = setup.inspect(lake, cache.FAMILY)
        if state["state"] not in (setup.READY, setup.NEWER):
            return {"ready": False, "state": state["state"], "message": state["message"], "workspaces": [],
                    "can_write": conn.get("access") == "write"}
        visible = {w["id"] for w in self._visible_workspaces()}
        fam = cache.FAMILY
        workspaces = cache.view(lake.rows(fam.table("scans")), lake.rows(fam.table(cache.WORKSPACE)),
                                lake.rows(fam.table(cache.LAKEHOUSE)), visible)
        return {"ready": True, "state": state["state"], "message": None, "workspaces": workspaces,
                "can_write": conn.get("access") == "write"}

    def access_cache_refresh(self, workspace_ids: list[str] | None = None, lakehouses: bool = True,
                             approve: bool = False) -> dict[str, Any]:
        """Ask Fabric for the workspace roles (and, with ``lakehouses``, each lakehouse's data access
        roles) of these workspaces, default every one this person can open, and write what changed.
        Raises "... Confirm to continue." when the cache's tables need setting up first."""
        lake, me = self.central_family(cache.FAMILY, approve)  # type: ignore[attr-defined]
        workspaces = self._visible_workspaces()
        if workspace_ids is not None:
            wanted = set(workspace_ids)
            workspaces = [w for w in workspaces if w["id"] in wanted]
        fam = cache.FAMILY
        scans = lake.rows(fam.table("scans"))
        run = cache.Refresh(cache.latest_snapshots(scans), _now(), me.id)
        fabric = self.fabric  # type: ignore[attr-defined]
        onelake = self._scoped(ONELAKE_SCOPES) if lakehouses else None  # type: ignore[attr-defined]
        unlisted = 0  # workspaces whose lakehouses couldn't be listed: theirs stay as last seen
        for ws in workspaces:
            try:
                run.add(cache.WORKSPACE, ws, None, cache.workspace_rows(ws["id"], fabric.list_role_assignments(ws["id"])))
            except Exception as exc:  # noqa: BLE001 - e.g. a Viewer can't list roles; kept as stale, not empty
                run.add(cache.WORKSPACE, ws, None, None, f"The workspace roles couldn't be read: {exc}")
            if onelake is None:
                continue
            try:
                items = [{"id": lh["id"], "name": lh.get("displayName") or ""} for lh in fabric.list_lakehouses(ws["id"])]
            except Exception:  # noqa: BLE001 - counted in problems below
                unlisted += 1
                continue
            for item in items:
                roles, problem = access_mod.read_roles(
                    lambda ws=ws, item=item: onelake.list_data_access_roles(ws["id"], item["id"]))
                run.add(cache.LAKEHOUSE, ws, item, None if problem else cache.lakehouse_rows(ws["id"], item["id"], roles),
                        problem)
            listed = {i["id"] for i in items}
            for item_id, name in cache.known_lakehouses(scans, ws["id"]).items():
                if item_id not in listed:
                    run.add(cache.LAKEHOUSE, ws, {"id": item_id, "name": name}, None, gone=True)
        for name, rows in run.rows.items():
            if rows:
                setup.write(lake, me, name, rows, family=fam)
        if run.scans:
            setup.write(lake, me, "scans", run.scans, family=fam)
        return {"checked": len(run.scans), "changed": run.changed,
                "problems": sum(s["state"] == "error" for s in run.scans) + unlisted, "at": run.at}
