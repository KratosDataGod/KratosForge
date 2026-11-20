"""Adopting an existing install, checking compatibility, and the one status the app shows (CP-006 to CP-008).

Pure functions over what the app already reads: optimize/setup.inspect results, the operational
store's schema version, the outbox's sync state, and whether the last call reached the environment.
Nothing here talks to Fabric.

Adopt (CP-006): an installation already connected to a central lakehouse (and maybe a Platform)
becomes a KratosForge Environment by writing one manifest built from what it is connected to, ids
only. ``adopt_preview`` builds it for the person to see; the caller writes it with ManifestStore.create
after they confirm. No table is created, changed or copied, and deleting the manifest file undoes it.

Compatibility (CP-007): every store says ready, upgrade (older, this app can upgrade it after a
confirm, including a family whose tables aren't there yet), incomplete (an earlier setup stopped part
way), read_only (a newer app wrote it, or its tables aren't KratosForge's), or missing (nothing to check). The environment's state is the worst of them.

Status (CP-008): Connected, Working offline, Synchronizing, Upgrade required, Limited permissions or
Connection failed, each with the next thing to do.
"""

from __future__ import annotations

from typing import Any

from ..optimize import setup
from . import manifest as mf

# ---- CP-006 adopt


def adopt_preview(*, name: str, lake_connection: dict[str, Any], inspections: list[dict[str, Any]],
                  platform: dict[str, Any] | None = None, keys: dict[str, Any] | None = None,
                  operational_version: int | None = None, owner_object_id: str = "",
                  services: list[str] | None = None) -> dict[str, Any]:
    """The manifest adopting would write. ``lake_connection`` is this PC's central lakehouse connection
    (optimize/registry: workspace_id, workspace_name, lakehouse_id, lakehouse_name, schemas);
    ``inspections`` are setup.inspect results for its families; ``platform`` the Prod Platform's ids
    (workspace_id, functions_id, sql_id), when this PC uses one; ``keys`` the shared Keys workspace's."""
    blocked = [i for i in inspections if i.get("state") in (setup.NEWER, setup.CONFLICT)]
    if blocked:
        raise mf.ManifestError(blocked[0].get("message") or "This lakehouse can't be adopted by this app.")
    if not any(i.get("family") == setup.CORE.prefix and i.get("schema_version") for i in inspections):
        raise mf.ManifestError("This lakehouse doesn't hold KratosForge's central tables yet. Create an "
                               "environment in it instead.")
    ws, lh = lake_connection.get("workspace_id", ""), lake_connection.get("lakehouse_id", "")
    operational = None
    if platform and platform.get("functions_id"):
        operational = {"workspace_id": platform.get("workspace_id", ""), "functions_id": platform["functions_id"],
                       "display_name": platform.get("display_name") or "SQL_Platform"}
        if platform.get("sql_id"):
            operational["database_id"] = platform["sql_id"]
    deployments: dict[str, Any] = {}
    if platform and platform.get("workspace_id"):
        deployments["prod"] = {"platform_workspace_id": platform["workspace_id"]}
    if keys and keys.get("workspace_id"):
        deployments["keys"] = {"workspace_id": keys["workspace_id"]}
    return mf.new_manifest(
        name=name, provider="fabric",
        namespace={"workspace_id": ws, "display_name": lake_connection.get("workspace_name", "")},
        analytical_store={"workspace_id": ws, "lakehouse_id": lh, "display_name": lake_connection.get("lakehouse_name", ""),
                          "schemas": bool(lake_connection.get("schemas", True))},
        operational_store=operational, deployments=deployments,
        families={i["family"]: int(i["schema_version"]) for i in inspections if i.get("schema_version")},
        operational_version=operational_version if operational else None,
        owner_object_id=owner_object_id, services=services)


# ---- CP-007 compatibility

READY, UPGRADE, INCOMPLETE, READ_ONLY, MISSING = "ready", "upgrade", "incomplete", "read_only", "missing"
_WORST = (READ_ONLY, MISSING, INCOMPLETE, UPGRADE, READY)
# A family with no tables yet (empty, or only other tables) is one this app can add after a confirm.
_FROM_INSPECT = {setup.READY: READY, setup.UPGRADE: UPGRADE, setup.INCOMPLETE: INCOMPLETE, setup.NEWER: READ_ONLY,
                 setup.CONFLICT: READ_ONLY, setup.EMPTY: UPGRADE, setup.UNRELATED: UPGRADE}


def compatibility(manifest: dict[str, Any], inspections: list[dict[str, Any]],
                  operational_version: int | None = None, app_operational_version: int | None = None) -> dict[str, Any]:
    """Each store's state and the environment's. ``inspections``: setup.inspect per family this app has.
    ``operational_version``: what the operational store reports (None when unreachable or absent)."""
    stores = [{"store": i["family"], "state": _FROM_INSPECT.get(i["state"], READ_ONLY), "message": i.get("message", ""),
               "schema_version": i.get("schema_version"), "app_schema_version": i.get("app_schema_version")}
              for i in inspections]
    if not mf.writable(manifest):
        stores.append({"store": "manifest", "state": READ_ONLY, "schema_version": manifest.get("manifest_version"),
                       "app_schema_version": mf.MANIFEST_VERSION,
                       "message": "A newer KratosForge wrote this environment. Update KratosForge to change it."})
    if manifest.get("operational_store") and operational_version is not None and app_operational_version:
        state = READY if operational_version == app_operational_version else (
            READ_ONLY if operational_version > app_operational_version else UPGRADE)
        stores.append({"store": "operational", "state": state, "schema_version": operational_version,
                       "app_schema_version": app_operational_version,
                       "message": {READY: "Ready.", UPGRADE: "The operational database uses an older schema. Finish or "
                                   "repair upgrades it and keeps every record.",
                                   READ_ONLY: "Another installation upgraded the operational database. Update "
                                   "KratosForge to change it."}[state]})
    states = [s["state"] for s in stores] or [MISSING]
    worst = next(w for w in _WORST if w in states)
    return {"state": worst, "stores": stores, "can_upgrade": worst in (UPGRADE, INCOMPLETE),
            "writable": worst != READ_ONLY}


# ---- CP-008 status

CONNECTED, OFFLINE, SYNCHRONIZING = "Connected", "Working offline", "Synchronizing"
UPGRADE_REQUIRED, LIMITED, FAILED = "Upgrade required", "Limited permissions", "Connection failed"
NEXT = {
    CONNECTED: "Shared results are saved to the environment as they're made.",
    OFFLINE: "Keep working. Results wait on this PC and are sent when the environment can be reached.",
    SYNCHRONIZING: "Results are being sent now.",
    UPGRADE_REQUIRED: "Choose Upgrade. Every record is kept.",
    LIMITED: "Ask the environment's owner for Contributor on its workspace to save shared results.",
    FAILED: "Check the sign-in and network, then choose Validate connection.",
}


def status(*, connected: bool, reachable: bool | None, compat: dict[str, Any] | None = None,
           sync: dict[str, Any] | None = None, denied: bool = False) -> dict[str, Any]:
    """The one status the app shows for its environment. ``reachable`` None means not tried yet
    (the app starts from its cached manifest and shows it as connected until a call says otherwise)."""
    if not connected:
        return {"status": "", "next": "Create an environment, or connect to one.", "detail": ""}
    sync_state = (sync or {}).get("state", "synced")
    if denied or sync_state == "failed":
        label = LIMITED
    elif compat and compat.get("state") == READ_ONLY:
        label = UPGRADE_REQUIRED  # here: this app is older; the next step says update KratosForge
    elif compat and compat.get("state") in (UPGRADE, INCOMPLETE) or sync_state == "conflict":
        label = UPGRADE_REQUIRED
    elif reachable is False or sync_state == "offline":
        label = OFFLINE
    elif sync_state in ("synchronizing", "pending"):
        label = SYNCHRONIZING
    else:
        label = CONNECTED
    next_step = NEXT[label]
    if label == UPGRADE_REQUIRED and compat and compat.get("state") == READ_ONLY:
        next_step = "Update KratosForge: another installation upgraded this environment. You can still read it."
    waiting = sum(int(k.get("pending", 0)) for k in ((sync or {}).get("kinds") or {}).values())
    return {"status": label, "next": next_step,
            "detail": f"{waiting} result{'s' if waiting != 1 else ''} waiting on this PC" if waiting else "",
            "waiting": waiting, "last_sync": (sync or {}).get("last_sync")}


def failed(reason: str) -> dict[str, Any]:
    """The status when the environment's manifest couldn't be read at all (not offline: refused or damaged)."""
    return {"status": FAILED, "next": NEXT[FAILED], "detail": reason}
