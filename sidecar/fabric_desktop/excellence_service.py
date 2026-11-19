"""Fabric Excellence Assessment of one workspace (FEA-105, engine call only, no screen yet).

``excellence_assess`` reads only, with the signed-in person's own access, and nothing new: the
optimization engine's reads (``optimization_assess``: the workspace, its items, notebook and pipeline
definitions, job history), the workspace's role assignments, the capacities the person can see, and
each lakehouse's properties (``GET .../lakehouses/{id}``, the read the app makes before writing a
table, for the architecture controls; at most ``MAX_LAKEHOUSE_READS`` per assessment), and the
workspace's Git connection and Git status (the reads the Git tab makes, for the DevOps controls; the
status only with a permission already granted, never with a new prompt).
Notebook and pipeline definitions are inspected while the engine has them (excellence.inspect_items):
the evidence keeps the rules that matched and where, never the code.
A read that fails is reported in ``reads`` and leaves the controls that need it *not evaluated*;
it never stops the assessment and never counts as a pass. No Fabric job starts and nothing is
written to Fabric, OneLake or this PC.

``demo=True`` assesses a labelled demonstration fixture with no Fabric call. See
docs/optimize/excellence-assessment.md.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from .auth import GIT_STATUS_SCOPES
from .excellence import adapters, assess, catalog, catalog_architecture, catalog_devops, inspect_items, profiles
from .opportunities_service import _guid
from .redact import describe

METHODS = {"excellence_assess"}
MAX_LAKEHOUSE_READS = 50  # lakehouse properties read per assessment; the rest are not evaluated, and reads says so
FIXTURE_ROLES = [
    {"role": "Admin", "principal": {"id": "demo-admin-1", "type": "Group", "displayName": "Sales platform admins"}},
    {"role": "Admin", "principal": {"id": "demo-app-1", "type": "ServicePrincipal", "displayName": "orders-sync app"}},
    {"role": "Contributor", "principal": {"id": "demo-guest-1", "type": "User", "displayName": "Partner analyst",
                                          "userDetails": {"userPrincipalName": "analyst_partner.example#EXT#@demo.example"}}},
    {"role": "Member", "principal": {"id": "demo-eng-1", "type": "Group", "displayName": "Sales data engineers"}},
    {"role": "Viewer", "principal": {"id": "demo-view-1", "type": "Group", "displayName": "Sales readers"}},
]
# The demonstration fixture's lakehouses (LH_Bronze, LH_Silver), as GET .../lakehouses/{id} would give them.
FIXTURE_LAKEHOUSES: dict[str, dict[str, Any] | None] = {
    "f1f1f1f1-0000-4000-8000-000000000301": {"displayName": "LH_Bronze", "properties": {"defaultSchema": "dbo"}},
    "f1f1f1f1-0000-4000-8000-000000000302": {"displayName": "LH_Silver", "properties": {}},
}
# The demonstration fixture's Git connection and status, made up: as Get Connection and Get Status
# would give them for a workspace on a GitHub branch with one uncommitted and one incoming change.
FIXTURE_GIT_CONNECTION: dict[str, Any] = {
    "gitConnectionState": "ConnectedAndInitialized",
    "gitProviderDetails": {"gitProviderType": "GitHub", "ownerName": "demo-owner", "repositoryName": "demo-repo",
                           "branchName": "main", "directoryName": "/sales"},
    "gitSyncDetails": {"head": "0" * 40, "lastSyncTime": "2026-11-17T22:00:00Z"},
}
FIXTURE_GIT_STATUS: dict[str, Any] = {
    "workspaceHead": "0" * 40, "remoteCommitHash": "1" * 40,
    "changes": [
        {"itemMetadata": {"itemIdentifier": {"objectId": "f1f1f1f1-0000-4000-8000-000000000101"},
                          "itemType": "Notebook", "displayName": "nb_load_orders"},
         "workspaceChange": "Modified", "remoteChange": None, "conflictType": "None"},
        {"itemMetadata": {"itemIdentifier": {"logicalId": "f1f1f1f1-0000-4000-8000-000000000999"},
                          "itemType": "DataPipeline", "displayName": "pl_daily_orders"},
         "workspaceChange": None, "remoteChange": "Modified", "conflictType": "None"},
    ],
}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _items(opt: dict[str, Any]) -> list[dict[str, Any]] | None:
    ws = (opt.get("workspaces") or [{}])[0]
    if ws.get("problem"):
        return None
    return [{"id": w["item_id"], "type": w["type"], "displayName": w.get("name")} for w in opt.get("workloads") or []]


def _lakehouses(fabric: Any, wid: str, items: list[dict[str, Any]] | None,
                reads: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """Each lakehouse's properties, read one by one. A lakehouse that couldn't be read (or is past
    MAX_LAKEHOUSE_READS) has no answer, so its controls are not evaluated; ``reads`` says how many."""
    lakehouses = [i for i in items or [] if i.get("type") == "Lakehouse"]
    if not lakehouses:
        return {}
    got: dict[str, dict[str, Any] | None] = {}
    problems: list[str] = []
    for lh in lakehouses[:MAX_LAKEHOUSE_READS]:
        try:
            got[str(lh["id"])] = fabric.get_lakehouse(wid, lh["id"])
        except Exception as exc:  # noqa: BLE001 - that lakehouse's controls are then not evaluated
            problems.append(f"{lh.get('displayName') or lh['id']} ({describe(exc)})")
    left = len(lakehouses) - MAX_LAKEHOUSE_READS
    notes = []
    if problems:
        notes.append(f"{len(problems)} of {len(lakehouses)} couldn't be read: " + "; ".join(problems[:3])
                     + (f" and {len(problems) - 3} more" if len(problems) > 3 else "") + ".")
    if left > 0:
        notes.append(f"{left} past the first {MAX_LAKEHOUSE_READS} weren't read.")
    reads.append({"read": "lakehouse properties", "ok": not notes, "problem": " ".join(notes) or None})
    return got


def _git(fabric: Any, status_client: Callable[[], Any], wid: str,
         reads: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The workspace's Git connection and, when it is synced, its status. Reads only: Get Connection
    with the sign-in's own client; Get Status only when the workspace is synced, with the client
    ``status_client()`` gives for a permission already granted, or not at all when it gives None.
    A read that fails is listed in ``reads``."""
    try:
        conn = fabric.git_connection(wid)
        reads.append({"read": "Git connection", "ok": True, "problem": None})
    except Exception as exc:  # noqa: BLE001 - the DevOps controls are then not evaluated
        reads.append({"read": "Git connection", "ok": False,
                      "problem": f"Couldn't be read ({describe(exc)}). Reading it needs a workspace role that can "
                                 "see the workspace's Git settings."})
        return None, None
    if not isinstance(conn, dict) or conn.get("gitConnectionState") != "ConnectedAndInitialized":
        return conn if isinstance(conn, dict) else None, None
    try:
        client = status_client()
    except Exception:  # noqa: BLE001 - signed out or no sign-in set up: no client, so no status read
        client = None
    if client is None:
        reads.append({"read": "Git status", "ok": False,
                      "problem": "Not read: it needs the Workspace.GitUpdate.All permission, which the app asks for "
                                 "the first time you open a workspace's Git item status. The assessment never asks "
                                 "for it, so the uncommitted and incoming change controls aren't evaluated."})
        return conn, None
    try:
        st = client.git_status(wid)
        reads.append({"read": "Git status", "ok": True, "problem": None})
        return conn, st if isinstance(st, dict) else None
    except Exception as exc:  # noqa: BLE001 - the status controls are then not evaluated
        reads.append({"read": "Git status", "ok": False, "problem": f"Couldn't be read ({describe(exc)})."})
        return conn, None


class ExcellenceMixin:
    def _git_status_client(self) -> Any:
        """A client for Get Status when Workspace.GitUpdate.All was granted already, else None.
        Never a sign-in prompt: the assessment doesn't ask for a permission."""
        return self._silent_client(GIT_STATUS_SCOPES)  # type: ignore[attr-defined]

    def excellence_assess(self, workspace_id: str = "", days: int = 30, profile: str = profiles.DEFAULT,
                          demo: bool = False) -> dict[str, Any]:
        """Assess one workspace (and its capacity, when you can see it) against the excellence controls.

        Reads only. ``profile`` is a built-in profile id. With ``demo`` a labelled fixture is assessed."""
        prof = profiles.get(str(profile or profiles.DEFAULT))
        at = _now()
        reads: list[dict[str, Any]] = []
        info_read = True
        if demo:
            opt = self._optimization_assess(demo=True, inspect=inspect_items.inspect)  # type: ignore[attr-defined]
            ws_raw = dict(opt["workspaces"][0])
            ws = {"id": ws_raw["id"], "displayName": ws_raw["name"], "capacityId": ws_raw["capacity_id"],
                  "description": "Sales reporting: orders, customers and products (demonstration fixture)."}
            assignments: list[dict[str, Any]] | None = FIXTURE_ROLES
            capacities: list[dict[str, Any]] | None = [{"id": ws["capacityId"], "displayName": "Demo F64",
                                                        "sku": "F64", "state": "Active", "region": "West Europe"}]
            at = str(opt.get("assessed_at") or at)
            lakehouses = dict(FIXTURE_LAKEHOUSES)
            git_conn: dict[str, Any] | None = FIXTURE_GIT_CONNECTION
            git_status: dict[str, Any] | None = FIXTURE_GIT_STATUS
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
                opt = self._optimization_assess(workspace_id=wid, days=days,  # type: ignore[attr-defined]
                                                inspect=inspect_items.inspect)
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
            lakehouses = _lakehouses(fabric, wid, _items(opt), reads)
            git_conn, git_status = _git(fabric, self._git_status_client, wid, reads)
        cap_id = str(ws.get("capacityId") or "").lower()
        records = (adapters.workspace(ws, _items(opt), collected_at=at, info_read=info_read)
                   + adapters.roles(ws, assignments, collected_at=at)
                   + adapters.capacity(cap_id, capacities, collected_at=at)
                   + adapters.optimization(opt, collected_at=at)
                   + adapters.inspection(opt, collected_at=at)
                   + catalog_architecture.evidence(ws, _items(opt), opt, lakehouses, collected_at=at)
                   + catalog_devops.evidence(ws, git_conn, git_status, collected_at=at))
        scope = {"workspaces": [{"id": str(ws["id"]), "name": ws.get("displayName")}],
                 "capacities": [cap_id] if cap_id else []}
        result = assess.assess(records, catalog.registry(), scope=scope, assessed_at=at, profile=prof, fixture=demo)
        result["reads"] = reads
        if cap_id and not any(r["kind"] == "capacity.state" for r in result["evidence"]):
            result["reads"].append({"read": "capacity", "ok": False,
                                    "problem": "The workspace's capacity isn't visible to you, so its controls "
                                               "aren't evaluated. That isn't a sign anything is wrong with it."})
        return result
