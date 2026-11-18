"""Fabric Excellence Assessment of one workspace (FEA-105, engine call only, no screen yet).

``excellence_assess`` reads only, with the signed-in person's own access, and nothing new: the
optimization engine's reads (``optimization_assess``: the workspace, its items, notebook and pipeline
definitions, job history), the workspace's role assignments, and the capacities the person can see.
It also uses the Capacity Metrics exports already
imported on this PC (the local usage store, FEA-202); that is a local read, not a Fabric call.
A read that fails is reported in ``reads`` and leaves the controls that need it *not evaluated*;
it never stops the assessment and never counts as a pass. No Fabric job starts and nothing is
written to Fabric, OneLake or this PC.

``demo=True`` assesses a labelled demonstration fixture with no Fabric call. See
docs/optimize/excellence-assessment.md.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .excellence import adapters, assess, catalog, catalog_capacity, profiles
from .opportunities_service import _guid
from .redact import describe

METHODS = {"excellence_assess"}
FIXTURE_ROLES = [
    {"role": "Admin", "principal": {"id": "demo-admin-1", "type": "Group", "displayName": "Sales platform admins"}},
    {"role": "Admin", "principal": {"id": "demo-app-1", "type": "ServicePrincipal", "displayName": "orders-sync app"}},
    {"role": "Contributor", "principal": {"id": "demo-guest-1", "type": "User", "displayName": "Partner analyst",
                                          "userDetails": {"userPrincipalName": "analyst_partner.example#EXT#@demo.example"}}},
    {"role": "Member", "principal": {"id": "demo-eng-1", "type": "Group", "displayName": "Sales data engineers"}},
    {"role": "Viewer", "principal": {"id": "demo-view-1", "type": "Group", "displayName": "Sales readers"}},
]


def _fixture_usage(workspace: str) -> list[dict[str, Any]]:
    """Two made-up Capacity Metrics imports for the demonstration: 14 days of item totals on the demo
    F64 (about 4% used) and 30 hours of operations with one semantic model refreshed 30 times in a day."""
    def row(n: int, item: str, cu: float, start: str, end: str | None = None, op: str | None = None,
            status: str | None = None) -> dict[str, Any]:
        return {"key": f"fixture:cap:{n}", "provider": "metrics_app", "workspace": workspace, "item": item,
                "operation": op, "status": status, "start": start, "end": end,
                "cu": {"value": cu, "period": {"start": start, "end": end or start}}}
    period = ("2026-10-18T00:00:00+00:00", "2026-11-01T00:00:00+00:00")
    items = [row(i, name, cu, *period) for i, (name, cu) in enumerate(
        [("nb_orders_refresh_finance", 1_620_000.0), ("pl_finance_sales_copy", 930_000.0), ("Sales model", 560_000.0)])]
    ops = [row(100 + h, "Sales model", 1_200.0, f"2026-10-31T{h % 24:02d}:{(h // 24) * 30:02d}:00+00:00", None,
               "Dataset On-Demand Refresh", "Success") for h in range(30)]
    ops += [row(200, "nb_orders_refresh_finance", 40_000.0, "2026-10-31T06:00:00+00:00", "2026-10-31T06:40:00+00:00",
                "Notebook Run", "Success"),
            row(201, "nb_orders_refresh_finance", 40_000.0, "2026-11-01T06:00:00+00:00", "2026-11-01T06:40:00+00:00",
                "Notebook Run", "Success"),
            row(202, "pl_finance_sales_copy", 2_000.0, "2026-11-01T05:00:00+00:00", "2026-11-01T05:02:00+00:00",
                "Pipeline Run", "Failure")]
    label = " (demonstration fixture).csv"
    return [{"id": "u-fixturecap00001", "provider": "metrics_app", "layout": "items", "file_name": "compute-items" + label,
             "period": {"start": period[0], "end": period[1]}, "rows": items},
            {"id": "u-fixturecap00002", "provider": "metrics_app", "layout": "operations",
             "file_name": "timepoint-operations" + label, "rows": ops}]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _items(opt: dict[str, Any]) -> list[dict[str, Any]] | None:
    ws = (opt.get("workspaces") or [{}])[0]
    if ws.get("problem"):
        return None
    return [{"id": w["item_id"], "type": w["type"], "displayName": w.get("name")} for w in opt.get("workloads") or []]


class ExcellenceMixin:
    def excellence_assess(self, workspace_id: str = "", days: int = 30, profile: str = profiles.DEFAULT,
                          demo: bool = False) -> dict[str, Any]:
        """Assess one workspace (and its capacity, when you can see it) against the excellence controls.

        Reads only. ``profile`` is a built-in profile id. With ``demo`` a labelled fixture is assessed."""
        prof = profiles.get(str(profile or profiles.DEFAULT))
        at = _now()
        reads: list[dict[str, Any]] = []
        info_read = True
        if demo:
            opt = self.optimization_assess(demo=True)  # type: ignore[attr-defined]
            ws_raw = dict(opt["workspaces"][0])
            ws = {"id": ws_raw["id"], "displayName": ws_raw["name"], "capacityId": ws_raw["capacity_id"],
                  "description": "Sales reporting: orders, customers and products (demonstration fixture)."}
            assignments: list[dict[str, Any]] | None = FIXTURE_ROLES
            capacities: list[dict[str, Any]] | None = [{"id": ws["capacityId"], "displayName": "Demo F64",
                                                        "sku": "F64", "state": "Active", "region": "West Europe"}]
            at = str(opt.get("assessed_at") or at)
            imports: list[dict[str, Any]] | None = _fixture_usage(str(ws["displayName"]))
        else:
            wid = _guid(workspace_id, "workspace")
            fabric = self.fabric  # type: ignore[attr-defined]
            info_read = True
            try:
                got = fabric.get_workspace(wid)
                ws = {**got, "id": got.get("id") or wid}
                reads.append({"read": "workspace settings", "ok": True, "problem": None})
            except Exception as exc:  # noqa: BLE001 - its controls are then not evaluated
                ws, info_read = {"id": wid}, False
                reads.append({"read": "workspace settings", "ok": False,
                              "problem": f"Couldn't be read ({describe(exc)}); its capacity isn't known either."})
            try:
                opt = self.optimization_assess(workspace_id=wid, days=days)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - its controls are then not evaluated
                opt = {"workspaces": [{"id": wid, "problem": f"Couldn't be read ({describe(exc)})."}],
                       "workloads": [], "opportunities": [], "scope": {}}
            reads.append({"read": "items, definitions and job history", "ok": not opt["workspaces"][0].get("problem"),
                          "problem": opt["workspaces"][0].get("problem"), "budget": opt["scope"].get("budget")})
            try:
                assignments = fabric.list_role_assignments(wid)
                reads.append({"read": "role assignments", "ok": True, "problem": None})
            except Exception as exc:  # noqa: BLE001 - the role controls are then not evaluated
                assignments = None
                reads.append({"read": "role assignments", "ok": False,
                              "problem": f"Couldn't be read ({describe(exc)}). Listing roles needs a workspace role "
                                         "that can see them."})
            try:
                capacities = fabric.list_capacities()
                reads.append({"read": "capacities", "ok": True, "problem": None})
            except Exception as exc:  # noqa: BLE001 - the capacity controls are then not evaluated
                capacities = None
                reads.append({"read": "capacities", "ok": False, "problem": f"Couldn't be read ({describe(exc)})."})
            try:
                imports = self._usage.all()  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - the capacity usage controls are then not evaluated
                imports = None
                reads.append({"read": "imported Capacity Metrics exports", "ok": False,
                              "problem": f"The imports on this PC couldn't be read ({describe(exc)})."})
        cap_id = str(ws.get("capacityId") or "").lower()
        records = (adapters.workspace(ws, _items(opt), collected_at=at, info_read=info_read)
                   + adapters.roles(ws, assignments, collected_at=at)
                   + adapters.capacity(cap_id, capacities, collected_at=at)
                   + adapters.optimization(opt, collected_at=at))
        cap_name = next((c.get("displayName") for c in capacities or [] if str(c.get("id") or "").lower() == cap_id), None)
        usage = catalog_capacity.usage(cap_id, cap_name, ws.get("displayName"), imports, collected_at=at)
        records += usage
        if cap_id and imports is not None:
            reads.append({"read": "imported Capacity Metrics exports", "ok": True, "problem": None if usage else (
                "No Capacity Metrics export imported on this PC has this workspace's use, so the capacity usage "
                "controls aren't evaluated. Import one in Optimize > Cost and savings.")})
        scope = {"workspaces": [{"id": str(ws["id"]), "name": ws.get("displayName")}],
                 "capacities": [cap_id] if cap_id else []}
        result = assess.assess(records, catalog.registry(), scope=scope, assessed_at=at, profile=prof, fixture=demo)
        result["reads"] = reads
        if cap_id and not any(r["kind"] == "capacity.state" for r in result["evidence"]):
            result["reads"].append({"read": "capacity", "ok": False,
                                    "problem": "The workspace's capacity isn't visible to you, so its controls "
                                               "aren't evaluated. That isn't a sign anything is wrong with it."})
        return result
