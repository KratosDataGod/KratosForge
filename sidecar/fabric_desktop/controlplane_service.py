"""The app's calls for the KratosForge Environment (CP-009, docs/product/central-control-plane-plan.md).

    cp_status         what this PC knows, without going online: providers, environments, the active one
    cp_discover       workspaces, or the environments and adoptable central lakehouses in one (reads only)
    cp_plan           what creating an environment would do (reads only), with a preview token
    cp_create         run a previewed plan, by its token, after the person confirms
    cp_adopt_preview  the manifest adopting this PC's central lakehouse would write, with a preview token
    cp_adopt          write that manifest, by its token, after the person confirms
    cp_connect        connect this PC to an environment found by discovery
    cp_switch         make another connected environment the active one
    cp_disconnect     forget an environment on this PC (nothing in it changes)
    cp_check          reachability, compatibility and the one status the app shows
    cp_cost           what the environment itself costs, apart from savings (CP-011; reads only)
    cp_inventory      the service registry and the active environment's tables, family by family (CP-015; reads only)
    cp_upgrade_plan   what upgrading the active environment's shared tables would do (reads only), with a token
    cp_upgrade        run that plan, by its token, after the person confirms (CP-010)

Approval path (Security QC on #3121): the engine never runs a plan or manifest the caller sends. A
preview keeps the request and what was shown on the engine, under a single-use token that binds the
whole request (a hash of its canonical JSON with the plan's steps). Create and adopt look the plan up
again by token, re-plan, and refuse when anything differs. Both need ``confirmed`` (a guard parameter,
so the audit line is written before they run, or they don't run), and the workspace's name typed
when it is one this PC knows as Prod.

IT can turn this off with ``allow_central_environment``. Nothing here runs against a real tenant in tests.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import asdict
from typing import Any

from . import app_settings
from .controlplane import cost as cpcost
from .controlplane import health
from .controlplane import inventory as cpinv
from .controlplane import manifest as mf
from .controlplane import providers as pv
from .controlplane import store as cps
from .controlplane.fabric import FabricDiscovery
from .controlplane.provisioning import FabricProvisioning
from .optimize import schema, setup
from .optimize.shared import Shared
from .redact import describe

METHODS = {"cp_status", "cp_discover", "cp_plan", "cp_create", "cp_adopt_preview", "cp_adopt",
           "cp_connect", "cp_switch", "cp_disconnect", "cp_check", "cp_cost", "cp_inventory",
           "cp_upgrade_plan", "cp_upgrade"}
PREVIEW_SECONDS = 15 * 60
MAX_PREVIEWS = 20
OFF = ("Your organization has turned off KratosForge Environments (allow_central_environment in its "
       "policy). Ask IT if you need them.")
CHANGED = ("Something changed since this was shown, so nothing was done. Look at it again and confirm.")
EXPIRED = "This preview has expired or was already used. Look at it again and confirm."
REQUEST_KEYS = ("name", "workspace_id", "workspace_name", "capacity_id", "lakehouse_id", "lakehouse_name",
                "owner_object_id")
MAX_FIELD = 256


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _plan_view(plan: pv.ProvisionPlan) -> dict[str, Any]:
    return {"provider": plan.provider, "steps": [asdict(s) for s in plan.steps], "blocked": plan.blocked}


class ControlPlaneMixin:
    root: Any
    auth: Any
    fabric: Any

    # ---- plumbing

    @property
    def cp_cache(self) -> cps.Cache:
        return cps.Cache(self.root / "controlplane")

    def _cp_allowed(self) -> None:
        if not app_settings.value("allow_central_environment"):
            raise PermissionError(OFF)

    def _cp_store(self, workspace_id: str, lakehouse_id: str) -> cps.ManifestStore:
        return cps.onelake_store(workspace_id, lakehouse_id, self.auth.storage_token)

    def _cp_provisioning(self) -> FabricProvisioning:
        return FabricProvisioning(self.fabric, self._optimize_lake, self._cp_store,  # type: ignore[attr-defined]
                                  self.optimize_installation)  # type: ignore[attr-defined]

    def _cp_previews(self) -> dict[str, dict[str, Any]]:
        if getattr(self, "_cp_preview_store", None) is None:
            self._cp_preview_store: dict[str, dict[str, Any]] = {}
            self._cp_preview_lock = threading.Lock()
        return self._cp_preview_store

    def _cp_keep(self, kind: str, **entry: Any) -> dict[str, Any]:
        previews = self._cp_previews()
        token = secrets.token_urlsafe(24)
        at = time.monotonic()
        with self._cp_preview_lock:
            for t in [t for t, e in previews.items() if at - e["at"] > PREVIEW_SECONDS]:
                del previews[t]
            while len(previews) >= MAX_PREVIEWS:  # the oldest goes: one person previews a few at most
                del previews[min(previews, key=lambda t: previews[t]["at"])]
            previews[token] = {"kind": kind, "at": at, **entry}
        return {"token": token, "expires_in_seconds": PREVIEW_SECONDS}

    def _cp_peek(self, kind: str, token: str) -> dict[str, Any]:
        previews = self._cp_previews()
        with self._cp_preview_lock:
            entry = previews.get(str(token or ""))
        if entry is None or entry["kind"] != kind or time.monotonic() - entry["at"] > PREVIEW_SECONDS:
            raise PermissionError(EXPIRED)
        return entry

    def _cp_take(self, kind: str, token: str) -> dict[str, Any]:
        """The preview a token names, once: a token is used up by the attempt, whatever its outcome."""
        previews = self._cp_previews()
        with self._cp_preview_lock:
            entry = previews.pop(str(token or ""), None)
        if entry is None or entry["kind"] != kind or time.monotonic() - entry["at"] > PREVIEW_SECONDS:
            raise PermissionError(EXPIRED)
        return entry

    def _cp_workspace_name(self, workspace_id: str) -> str:
        ws = next((w for w in self.fabric.list_workspaces() if w.get("id") == workspace_id), None)
        return (ws or {}).get("displayName", "") or workspace_id

    def _cp_typed_name(self, workspace_id: str, confirm_name: str) -> None:
        """A workspace this PC knows as Prod is changed only with its name typed (as Prod writes are)."""
        if not workspace_id or not self._prod_marked(workspace_id):  # type: ignore[attr-defined]
            return
        name = self._cp_workspace_name(workspace_id)
        if confirm_name != name:  # exactly, as every other Prod confirm (WR-249, WR-250)
            raise PermissionError(f"{name} is a Prod workspace, so this runs only after you type its name exactly to confirm. "
                                  "Nothing was changed.")

    def _cp_inspections(self, workspace_id: str, lakehouse_id: str, schemas: bool | None = None) -> list[dict[str, Any]]:
        lake = self._optimize_lake(workspace_id, lakehouse_id, schemas)  # type: ignore[attr-defined]
        return [dict(setup.inspect(lake, fam), family=fam.prefix) for fam in schema.FAMILIES.values()]

    def _cp_platform_links(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """The Prod Platform (its database is the environment's operational store, CP-ADR-005) and the
        shared Keys workspace this PC uses, by ids only; None for each one that isn't in Fabric."""
        prod = self._platform_config().get("prod") or {}  # type: ignore[attr-defined]
        platform = ({k: prod[k] for k in ("workspace_id", "functions_id", "sql_id") if prod.get(k)}
                    if prod.get("workspace_id") and prod.get("functions_id") else None)
        keys_cfg = self._keys_config()  # type: ignore[attr-defined]
        keys = ({"workspace_id": keys_cfg["workspace_id"]}
                if keys_cfg.get("shared") and keys_cfg.get("workspace_id") and keys_cfg.get("functions_id") else None)
        return platform, keys

    @staticmethod
    def _cp_adopt_seen(conn: dict[str, Any], inspections: list[dict[str, Any]], platform: dict[str, Any] | None,
                       keys: dict[str, Any] | None) -> str:
        """What an adopt preview was made from: confirm refuses when any of it changed meanwhile."""
        return _digest({"connection": {k: conn.get(k) for k in ("workspace_id", "lakehouse_id", "schemas")},
                        "inspections": [(i["family"], i["state"], i.get("schema_version")) for i in inspections],
                        "platform": platform, "keys": keys})

    # ---- reads

    def cp_status(self) -> dict[str, Any]:
        """What this PC knows, without going online."""
        cache = self.cp_cache
        return {"allowed": bool(app_settings.value("allow_central_environment")), "providers": pv.describe_all(),
                "active": cache.active(), "environments": cache.all()}

    def cp_discover(self, workspace_id: str = "") -> dict[str, Any]:
        """Without a workspace: the workspaces this person can open. With one: its environments and
        central lakehouses that could become one. Reads only."""
        self._cp_allowed()
        disc = FabricDiscovery(self.fabric, self._cp_store,
                               lambda ws, lh: self._optimize_lake(ws, lh).table_names())  # type: ignore[attr-defined]
        if not workspace_id:
            return {"namespaces": disc.list_namespaces()}
        found = disc.find_environments(workspace_id)
        return {"workspace_id": workspace_id, "truncated": disc.truncated,
                "found": [{"workspace_id": f.ref.workspace_id, "lakehouse_id": f.ref.lakehouse_id,
                           "display_name": f.ref.display_name, "state": f.state, "reason": f.reason,
                           "environment": mf.summary(f.manifest) if f.manifest else None} for f in found]}

    def cp_plan(self, request: dict[str, Any]) -> dict[str, Any]:
        """What creating an environment would do. Nothing is created; the token names this preview for cp_create."""
        self._cp_allowed()
        if not isinstance(request, dict):
            raise ValueError("The request must be an object with a name, and a workspace and lakehouse by id or name.")
        unknown = sorted(str(k) for k in request if k not in REQUEST_KEYS)
        if unknown:
            raise ValueError(f"Unknown request fields: {', '.join(unknown)[:200]}")
        for key, value in request.items():
            if value is not None and (not isinstance(value, str) or len(value) > MAX_FIELD):
                raise ValueError(f"{key} must be text of at most {MAX_FIELD} characters.")
        plan = self._cp_provisioning().plan(request)
        view = _plan_view(plan)
        ws_id = plan.request.get("workspace_id") or ""
        prod = bool(ws_id) and self._prod_marked(ws_id)  # type: ignore[attr-defined]
        out = view | {"request": plan.request, "typed_name": self._cp_workspace_name(ws_id) if prod else ""}
        if plan.blocked:
            return out | {"token": ""}
        return out | self._cp_keep("create", request=plan.request, digest=_digest({"request": plan.request, **view}))

    def cp_adopt_preview(self, name: str) -> dict[str, Any]:
        """The manifest adopting this PC's central lakehouse would write: ids only. Nothing is written.
        When this PC uses a Platform in Fabric, its Prod database is linked as the operational store
        (CP-ADR-005), and a shared Keys workspace is recorded beside it."""
        self._cp_allowed()
        conn = self.optimize_registry.active()  # type: ignore[attr-defined]
        if not conn:
            raise ValueError("Connect this PC to a central lakehouse first (Settings > Optimize lakehouses), "
                             "or create an environment.")
        ws, lh = conn["workspace_id"], conn["lakehouse_id"]
        if self._cp_store(ws, lh).read() is not None:
            raise ValueError("This lakehouse already holds a KratosForge Environment. Connect to it instead.")
        inspections = self._cp_inspections(ws, lh, conn.get("schemas", True))
        platform, keys = self._cp_platform_links()
        manifest = health.adopt_preview(name=(name or "").strip(), lake_connection=conn, inspections=inspections,
                                        platform=platform, keys=keys)
        seen = self._cp_adopt_seen(conn, inspections, platform, keys)
        prod = self._prod_marked(ws)  # type: ignore[attr-defined]
        return {"manifest": manifest, "environment": mf.summary(manifest),
                "typed_name": self._cp_workspace_name(ws) if prod else ""} | \
            self._cp_keep("adopt", manifest=manifest, seen=seen)

    def cp_check(self) -> dict[str, Any]:
        """Reach the active environment and say how it is: its manifest, each store's compatibility,
        and the one status the app shows with what to do next."""
        active = self.cp_cache.active()
        if not active:
            return health.status(connected=False, reachable=None) | {"environment": None}
        self._cp_allowed()
        ws, lh = active["workspace_id"], active["lakehouse_id"]
        try:
            got = self._cp_store(ws, lh).read()
        except mf.ManifestError as exc:
            return health.failed(f"Its manifest is damaged: {exc}") | {"environment": active}
        except Exception as exc:  # noqa: BLE001 - shown as offline or refused, with the reason
            if setup.denied(exc):
                return health.status(connected=True, reachable=True, denied=True) | {"environment": active}
            return health.status(connected=True, reachable=False) | {"environment": active, "error": describe(exc)}
        if got is None or got.manifest.get("environment_id") != active["environment_id"]:
            return health.failed("The environment's manifest isn't in its lakehouse any more.") | {"environment": active}
        try:
            inspections = self._cp_inspections(ws, lh)
        except Exception as exc:  # noqa: BLE001 - tables that can't be read are reported, not fatal
            denied = setup.denied(exc)
            return health.status(connected=True, reachable=denied, denied=denied) | {
                "environment": mf.summary(got.manifest), "error": describe(exc)}
        compat = health.compatibility(got.manifest, inspections)
        sync = self.optimize_outbox.sync_state(lh)  # type: ignore[attr-defined]
        return health.status(connected=True, reachable=True, compat=compat, sync=sync) | {
            "environment": mf.summary(got.manifest), "compatibility": compat}

    def cp_cost(self, days: int = 30) -> dict[str, Any]:
        """What the active environment costs over the last ``days``: the measured sync overhead of every PC
        that syncs to it and this PC's share,
        and Fabric capacity and money as Unknown until they are measured. Net savings only when both
        sides are known. Reads only."""
        self._cp_allowed()
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 366:
            raise ValueError(f"days must be between 1 and 366, not {repr(days)[:20]}.")
        active = self.cp_cache.active()
        if not active:
            raise ValueError("Connect this PC to a KratosForge Environment first.")
        lake = self._optimize_lake(active["workspace_id"], active["lakehouse_id"])  # type: ignore[attr-defined]
        state = setup.inspect(lake)
        if state["state"] in (setup.NEWER, setup.CONFLICT):  # rows of a schema this app doesn't know aren't read
            raise PermissionError(state["message"])
        rows = Shared(lake, self.optimize_installation, access="read").rows("measurements")  # type: ignore[attr-defined]
        return cpcost.summary(rows.to_dicts(), days=days,
                              installation_id=self.optimize_installation.id) | {"environment": active}  # type: ignore[attr-defined]

    def cp_inventory(self) -> dict[str, Any]:
        """The service registry (every registered table family and its tables) and the active environment's
        lakehouse: each family's state, tables no family claims, and how many other tables there are.
        Reads only. A lakehouse that can't be read gives its status and reason, like cp_check."""
        self._cp_allowed()
        active = self.cp_cache.active()
        if not active:
            raise ValueError("Connect this PC to a KratosForge Environment first.")
        out = {"registry": cpinv.registry(), "environment": active}
        try:
            lake = self._optimize_lake(active["workspace_id"], active["lakehouse_id"])  # type: ignore[attr-defined]
            names = lake.table_names()
            inspections = [dict(setup.inspect(lake, fam), family=fam.prefix) for fam in schema.FAMILIES.values()]
        except Exception as exc:  # noqa: BLE001 - shown as offline or refused, with the reason
            denied = setup.denied(exc)
            return out | health.status(connected=True, reachable=denied, denied=denied) | {
                "inventory": None, "error": describe(exc)}
        return out | health.status(connected=True, reachable=True) | {"inventory": cpinv.inventory(names, inspections)}

    def cp_upgrade_plan(self) -> dict[str, Any]:
        """What upgrading the active environment's shared tables would do. Nothing changes; the token
        names this preview for cp_upgrade."""
        self._cp_allowed()
        active = self.cp_cache.active()
        if not active:
            raise ValueError("Connect this PC to a KratosForge Environment first.")
        got = self._cp_store(active["workspace_id"], active["lakehouse_id"]).read()
        if got is None or got.manifest.get("environment_id") != active["environment_id"]:
            raise ValueError("The environment's manifest isn't in its lakehouse any more, so it can't be upgraded. "
                             "Connect to the environment again, or ask whoever manages it.")
        plan = self._cp_provisioning().plan_upgrade(got.manifest)
        view = _plan_view(plan)
        ws_id = plan.request.get("workspace_id") or ""
        prod = bool(ws_id) and self._prod_marked(ws_id)  # type: ignore[attr-defined]
        out = view | {"request": plan.request, "environment": mf.summary(got.manifest),
                      "typed_name": self._cp_workspace_name(ws_id) if prod else ""}
        if plan.blocked:
            return out | {"token": ""}
        return out | self._cp_keep("upgrade", request=plan.request, digest=_digest({"request": plan.request, **view}))

    # ---- changes

    def cp_create(self, token: str, confirmed: bool = False, confirm_name: str = "") -> dict[str, Any]:
        """Run the plan cp_plan showed, by its token. The plan is made again here and must match."""
        self._cp_allowed()
        if not confirmed:
            raise pv.NotConfirmed("Nothing was created, because creating an environment needs your confirmation.")
        # The typed name is checked before the token is used, so a mistyped name can simply be typed again.
        self._cp_typed_name(self._cp_peek("create", token)["request"].get("workspace_id") or "", confirm_name)
        entry = self._cp_take("create", token)
        prov = self._cp_provisioning()
        fresh = prov.plan(entry["request"])
        view = _plan_view(fresh)
        if fresh.blocked:
            raise PermissionError(" ".join(fresh.blocked))
        if _digest({"request": fresh.request, **view}) != entry["digest"]:
            raise PermissionError(CHANGED)
        out = prov.apply(fresh, confirmed=True)
        if out["ok"] and out["manifest"]:
            out["environment"] = self.cp_cache.remember(out["manifest"])
        return out

    def cp_adopt(self, token: str, confirmed: bool = False, confirm_name: str = "") -> dict[str, Any]:
        """Write the manifest cp_adopt_preview showed, by its token. No table is created or changed."""
        self._cp_allowed()
        if not confirmed:
            raise pv.NotConfirmed("Nothing was written, because adopting needs your confirmation.")
        self._cp_typed_name(self._cp_peek("adopt", token)["manifest"]["analytical_store"]["workspace_id"], confirm_name)
        entry = self._cp_take("adopt", token)
        manifest = entry["manifest"]
        store = manifest["analytical_store"]
        ws, lh = store["workspace_id"], store["lakehouse_id"]
        conn = self.optimize_registry.active()  # type: ignore[attr-defined]
        if not conn or (conn["workspace_id"], conn["lakehouse_id"]) != (ws, lh):
            raise PermissionError(CHANGED)
        inspections = self._cp_inspections(ws, lh, conn.get("schemas", True))
        if self._cp_adopt_seen(conn, inspections, *self._cp_platform_links()) != entry["seen"]:
            raise PermissionError(CHANGED)
        try:
            made = self._cp_store(ws, lh).create(manifest)
        except cps.ManifestExists as exc:
            raise ValueError("This lakehouse became a KratosForge Environment meanwhile. Connect to it instead.") from exc
        return {"ok": True, "manifest": made.manifest, "environment": self.cp_cache.remember(made.manifest)}

    def cp_upgrade(self, token: str, confirmed: bool = False, confirm_name: str = "") -> dict[str, Any]:
        """Run the upgrade plan cp_upgrade_plan showed, by its token. The plan is made again here and must match."""
        self._cp_allowed()
        if not confirmed:
            raise pv.NotConfirmed("Nothing was upgraded, because upgrading needs your confirmation.")
        self._cp_typed_name(self._cp_peek("upgrade", token)["request"].get("workspace_id") or "", confirm_name)
        entry = self._cp_take("upgrade", token)
        prov = self._cp_provisioning()
        req = entry["request"]
        active = self.cp_cache.active()  # the environment it was previewed for, still the one this PC uses
        if not active or active.get("environment_id") != req.get("environment_id"):
            raise PermissionError(CHANGED)
        got = self._cp_store(req["workspace_id"], req["lakehouse_id"]).read()
        if got is None or got.manifest.get("environment_id") != req.get("environment_id"):
            raise PermissionError(CHANGED)
        fresh = prov.plan_upgrade(got.manifest)
        view = _plan_view(fresh)
        if fresh.blocked:
            raise PermissionError(" ".join(fresh.blocked))
        if _digest({"request": fresh.request, **view}) != entry["digest"]:
            raise PermissionError(CHANGED)
        out = prov.apply_upgrade(fresh, confirmed=True)
        if out["ok"] and out["manifest"]:
            out["environment"] = self.cp_cache.remember(out["manifest"])
        return out

    def cp_connect(self, workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
        """Connect this PC to the environment in this lakehouse and make it the active one. Reads the
        manifest; changes nothing in Fabric."""
        self._cp_allowed()
        got = self._cp_store(workspace_id, lakehouse_id).read()
        if got is None:
            raise ValueError("This lakehouse doesn't hold a KratosForge Environment. Create one, or adopt it.")
        store = got.manifest.get("analytical_store") or {}
        # A manifest copied into another lakehouse would point this PC elsewhere: it must name where it is.
        if (store.get("workspace_id"), store.get("lakehouse_id")) != (workspace_id, lakehouse_id):
            raise ValueError("This manifest names another lakehouse, so it wasn't trusted. Connect to the "
                             "environment where its manifest says it is.")
        return {"environment": self.cp_cache.remember(got.manifest), "writable": mf.writable(got.manifest)}

    def cp_switch(self, environment_id: str) -> dict[str, Any]:
        self._cp_allowed()
        try:
            return {"active": self.cp_cache.activate(environment_id)}
        except KeyError as exc:
            raise ValueError("This PC isn't connected to that environment.") from exc

    def cp_disconnect(self, environment_id: str) -> dict[str, Any]:
        """Forget the environment on this PC. Nothing in it is changed or deleted. Allowed with the policy
        off too, so a PC can always let go of one."""
        cache = self.cp_cache
        return {"forgotten": cache.forget(environment_id), "active": cache.active()}
