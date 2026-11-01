"""The Warehouse page's Monitoring tab (WH-19, Preview): workspace monitoring for one warehouse.

``wh_monitoring_status`` finds the workspace's monitoring KQL database (the one whose
``WarehouseExecutions`` table holds this workspace's queries) and the monitoring views already built.
``wh_monitoring_view`` runs one view of warehouse_monitoring.py there, read-only, as the person who
signed in. ``wh_monitoring_build`` creates or updates one KQL queryset per warehouse with those views,
after the page's confirm; it is the only call here that writes, and it writes only the queryset items
it names (other tabs in them stay). The database is always read from Fabric by its id, never taken as
an address from the caller. See docs/warehouse.md, "Monitoring".
"""

from __future__ import annotations

from typing import Any

from . import app_settings, warehouse_monitoring as wm
from .fabric_api import FabricError
from .http_session import session

METHODS = {"wh_monitoring_status", "wh_monitoring_view", "wh_monitoring_build"}

PROBES = 10  # KQL databases tried, at most, when looking for a workspace's monitoring database
QUERYSET_NOTE = "Monitoring views for {name}, built by KratosForge from workspace monitoring (WarehouseExecutions)."
TAKEN = ("A KQL queryset named {queryset} is already in the workspace and KratosForge didn't make it, so it "
         "was left as it is. Rename it, or delete it in Fabric, and build again.")


def _ours(queryset: dict[str, Any]) -> bool:
    """Whether a queryset is one the app built: its description is the app's (QUERYSET_NOTE)."""
    note = str(queryset.get("description") or "")
    return note.startswith("Monitoring views for ") and note.endswith(QUERYSET_NOTE.split("{name}")[1])


class WarehouseMonitoringMixin:
    def _wm_database(self, workspace_id: str, database_id: str) -> dict[str, Any]:
        """A KQL database as {workspace_id, item_id, name, cluster}, read from Fabric."""
        db = self.fabric.get_kql_database(workspace_id, database_id)  # type: ignore[attr-defined]
        return {"workspace_id": workspace_id, "item_id": database_id, "name": str(db.get("displayName") or ""),
                "cluster": wm.cluster((db.get("properties") or {}).get("queryServiceUri"))}

    def _wm_query(self, db: dict[str, Any], kql: str) -> dict[str, Any]:
        from .auth import KUSTO_SCOPES
        refreshed = getattr(self, "_refreshed", None)  # Service's: a new token after a 401, when it can
        return wm.query(session().post, db["cluster"], db["name"], kql, self.auth.kusto_token(),  # type: ignore[attr-defined]
                        refresh=refreshed(KUSTO_SCOPES) if callable(refreshed) else None)

    def wh_monitoring_status(self, workspace_id: str, database_workspace_id: str = "",
                             database_id: str = "") -> dict[str, Any]:
        """Whether the workspace is monitored, from which KQL database, and its monitoring views.

        Without ``database_id`` the workspace's own KQL databases are tried (names ending in
        ``_Database`` first, the way Fabric names a monitoring database); with it, that database
        (monitoring sent to another workspace's Eventhouse). Each try is one small KQL query."""
        ws = wm.guid(workspace_id, "workspace")
        if database_id:
            candidates = [(wm.guid(database_workspace_id or ws, "workspace"), wm.guid(database_id, "KQL database"))]
        else:
            dbs = self.fabric.list_kql_databases(ws)  # type: ignore[attr-defined]
            dbs.sort(key=lambda d: (not str(d.get("displayName") or "").endswith("_Database"), str(d.get("displayName") or "")))
            candidates = [(ws, str(d.get("id") or "")) for d in dbs[:PROBES] if d.get("id")]
        found, problems = None, []
        for dws, did in candidates:
            try:
                db = self._wm_database(dws, did)
                out = self._wm_query(db, wm.probe_kql(ws))
            except (ValueError, RuntimeError, FabricError) as exc:
                if not wm.missing_table(str(exc)):
                    problems.append(str(exc))
                continue
            row = out["rows"][0] if out["rows"] else [None, 0]
            found = {**db, "last_seen": row[0], "items": row[1]}
            break
        views = {}
        if found:
            views = {q.get("displayName"): q.get("id") for q in self.fabric.list_kql_querysets(ws)  # type: ignore[attr-defined]
                     if str(q.get("displayName") or "").endswith("_monitoring") and _ours(q)}
        return {"workspace_id": ws, "monitored": bool(found), "database": found, "querysets": views,
                "problems": problems[:3], "build_allowed": bool(app_settings.value("wh_monitoring_views")),
                "auto": ws in [str(w).lower() for w in app_settings.value("wh_monitoring_auto") or []]}

    def wh_monitoring_view(self, database_workspace_id: str, database_id: str, warehouse_id: str,
                           view: str = "summary", window: str = "24h") -> dict[str, Any]:
        """One monitoring view (or ``summary``) of one warehouse, from the monitoring database, read-only."""
        kql = wm.view_kql(view, warehouse_id, window)
        db = self._wm_database(wm.guid(database_workspace_id, "workspace"), wm.guid(database_id, "KQL database"))
        try:
            out = self._wm_query(db, kql)
        except RuntimeError as exc:
            if wm.missing_table(str(exc)):
                return {"view": view, "window": window, "columns": [], "rows": [], "truncated": False,
                        "note": "No warehouse query has been recorded in this monitoring database yet."}
            raise
        return {"view": view, "window": window, "kql": kql, **out}

    def wh_monitoring_build(self, workspace_id: str, database_workspace_id: str, database_id: str,
                            warehouses: list[dict[str, Any]], confirm: bool = False, auto: bool = False) -> dict[str, Any]:
        """Create or update the monitoring queryset of each warehouse (``[{id, name}]``) in the workspace.

        ``confirm`` says the person saw what is created. ``auto`` also remembers the workspace, so its
        new warehouses get their views without asking again. Each warehouse is done on its own; one
        that fails is reported and the rest still go ahead."""
        if not app_settings.value("wh_monitoring_views"):
            raise PermissionError("Your organization doesn't allow building monitoring views from KratosForge "
                                  "(wh_monitoring_views is off).")
        if confirm is not True:
            raise PermissionError("Building monitoring views creates items in Fabric, so it needs your confirm.")
        ws = wm.guid(workspace_id, "workspace")
        if not isinstance(warehouses, list) or not warehouses or len(warehouses) > 100:
            raise ValueError("Pick between 1 and 100 warehouses.")
        picked = [(wm.guid(w.get("id"), "warehouse"), str(w.get("name") or "").strip()) for w in warehouses
                  if isinstance(w, dict)]
        if len(picked) != len(warehouses) or any(not name for _, name in picked):
            raise ValueError("Each warehouse needs its id and name.")
        db = self._wm_database(wm.guid(database_workspace_id, "workspace"), wm.guid(database_id, "KQL database"))
        existing = {q.get("displayName"): q for q in self.fabric.list_kql_querysets(ws)}  # type: ignore[attr-defined]
        done = []
        for item, name in picked:
            qname = wm.queryset_name(name)
            try:
                found = existing.get(qname)
                if found and not _ours(found):
                    raise ValueError(TAKEN.format(queryset=qname))
                if found:
                    current = self.fabric.get_item_definition(ws, found["id"])  # type: ignore[attr-defined]
                    self.fabric.update_item_definition(ws, found["id"],  # type: ignore[attr-defined]
                                                       wm.queryset_definition(item, db["cluster"], db["name"], current))
                    done.append({"warehouse_id": item, "queryset": qname, "item_id": found["id"], "state": "updated"})
                else:
                    made = self.fabric.create_kql_queryset(  # type: ignore[attr-defined]
                        ws, qname, wm.queryset_definition(item, db["cluster"], db["name"]), QUERYSET_NOTE.format(name=name))
                    done.append({"warehouse_id": item, "queryset": qname, "item_id": str((made or {}).get("id") or ""),
                                 "state": "created"})
            except (ValueError, RecursionError, FabricError) as exc:
                done.append({"warehouse_id": item, "queryset": qname, "state": "failed", "error": str(exc)})
        if auto:
            remembered = [str(w).lower() for w in app_settings.value("wh_monitoring_auto") or []]
            if ws not in remembered:
                try:
                    app_settings.save({"wh_monitoring_auto": [*remembered, ws]})
                except ValueError:
                    pass  # the organization fixed the list: the views were still built
        return {"workspace_id": ws, "database": db["name"], "results": done,
                "failed": sum(1 for d in done if d["state"] == "failed")}
