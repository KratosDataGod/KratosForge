"""The app's calls for setting up Optimize: create, connect to, switch and forget optimization lakehouses.

Creating a lakehouse is a Fabric write and only happens from ``optimize_create``. Connecting to an
existing one never initializes it unless the call says the person confirmed. IT can turn the
whole feature off with the ``allow_optimize_lakehouse`` policy key.
"""

from __future__ import annotations

from typing import Any, Callable

from . import app_settings, redact
from .optimize import schema, setup
from .optimize.installation import Installation, now
from .optimize.lakehouse import OneLakeLakehouse
from .optimize.registry import Registry
from .optimize.shared import Shared

DEFAULT_NAME = "FabricDesktop_Central"
METHODS = {"optimize_status", "optimize_discover", "optimize_inspect", "optimize_create", "optimize_connect",
           "optimize_switch", "optimize_disconnect", "optimize_new_installation_id", "optimize_set_label",
           "optimize_findings", "optimize_recommendations", "optimize_set_recommendation"}


class OptimizeMixin:
    root: Any
    auth: Any
    fabric: Any

    @property
    def optimize_installation(self) -> Installation:
        return Installation(self.root / "optimize")

    @property
    def optimize_registry(self) -> Registry:
        return Registry(self.root / "optimize")

    def _optimize_allowed(self) -> None:
        if not app_settings.value("allow_optimize_lakehouse"):
            raise PermissionError("Your organization has turned off shared optimization lakehouses "
                                  "(allow_optimize_lakehouse in its policy file).")

    def _optimize_lake(self, workspace_id: str, lakehouse_id: str, schemas: bool | None = None,
                       token: Callable[[], str] | None = None) -> Any:
        """``token`` is a storage token taken earlier, for callers off the calling thread (sign-in
        tokens are fetched on the thread that handles the call)."""
        if schemas is None:
            schemas = self._schemas_enabled(workspace_id, lakehouse_id)  # type: ignore[attr-defined]
        return OneLakeLakehouse(workspace_id, lakehouse_id, schemas, token or self.auth.storage_token,
                                self.root / "optimize" / "cache")

    def optimize_status(self) -> dict[str, Any]:
        """What Optimize knows on this PC, without going online."""
        reg = self.optimize_registry
        return {"allowed": bool(app_settings.value("allow_optimize_lakehouse")), "default_name": DEFAULT_NAME,
                "installation_id": self.optimize_installation.id, "label": self.optimize_installation.label,
                "active": reg.active(), "connections": reg.all()}

    def optimize_discover(self, workspace_id: str) -> list[dict[str, Any]]:
        """The workspace's lakehouses, each marked with whether it already holds Optimize's tables."""
        out = []
        for lh in self.fabric.list_lakehouses(workspace_id):
            entry = {"lakehouse_id": lh["id"], "lakehouse_name": lh.get("displayName", ""), "optimize": False}
            try:
                names = self._optimize_lake(workspace_id, lh["id"]).table_names()
                entry["optimize"] = "fdopt_schema_version" in names
                entry["other_tables"] = sum(1 for n in names if schema.family_of(n) is None)
            except Exception as exc:
                entry["error"] = "No access" if setup.denied(exc) else "Couldn't be read"
            out.append(entry)
        return sorted(out, key=lambda e: (not e["optimize"], e["lakehouse_name"].lower()))

    def optimize_inspect(self, workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
        """What the lakehouse holds and what connecting to it would do. Reads only."""
        return setup.inspect(self._optimize_lake(workspace_id, lakehouse_id))

    def optimize_create(self, workspace_id: str, name: str = DEFAULT_NAME, workspace_name: str = "") -> dict[str, Any]:
        """Create a lakehouse with Optimize's tables in it, and connect this PC to it."""
        self._optimize_allowed()
        name = (name or DEFAULT_NAME).strip()
        if self.fabric.find_lakehouse(workspace_id, name):
            raise ValueError(f"This workspace already has a lakehouse called {name}. Connect to it instead, "
                             "or pick another name.")
        lh = self.fabric.create_lakehouse(workspace_id, name)
        lake = self._optimize_lake(workspace_id, lh["id"], schemas=True)
        try:
            setup.initialize(lake, self.optimize_installation, approve=True)
        except Exception as exc:
            raise RuntimeError(f"{name} was created, but setting it up didn't finish ({redact.secrets(str(exc))}). Connect to it to "
                               "finish: nothing in it is lost.") from exc
        return self._optimize_register(lake, workspace_id, workspace_name, lh["id"], name, created=True)

    def optimize_connect(self, workspace_id: str, lakehouse_id: str, workspace_name: str = "",
                         lakehouse_name: str = "", initialize: bool = False) -> dict[str, Any]:
        """Connect this PC to an existing lakehouse. One that has no Optimize tables yet, an older
        schema or an unfinished setup is changed only with ``initialize`` (the person confirmed);
        otherwise the answer says what connecting would do."""
        self._optimize_allowed()
        lake = self._optimize_lake(workspace_id, lakehouse_id)
        state = setup.inspect(lake)
        if state["state"] == setup.CONFLICT:
            raise ValueError(state["message"])
        if state["state"] in setup.CAN_INITIALIZE:
            if not initialize:
                return {"connected": False, "needs_confirmation": True, "inspection": state}
            setup.initialize(lake, self.optimize_installation, approve=True)
        return self._optimize_register(lake, workspace_id, workspace_name, lakehouse_id, lakehouse_name)

    def _optimize_register(self, lake: Any, workspace_id: str, workspace_name: str, lakehouse_id: str,
                           lakehouse_name: str, created: bool = False) -> dict[str, Any]:
        access = setup.check_access(lake, self.optimize_installation)
        state = access.get("state") or {}
        conn = self.optimize_registry.add({
            "workspace_id": workspace_id, "workspace_name": workspace_name, "lakehouse_id": lakehouse_id,
            "lakehouse_name": lakehouse_name, "schemas": lake.schemas, "schema_version": state.get("schema_version"),
            "access": "write" if access["write"] else "read" if access["read"] else "none", "checked_at": now()})
        return {"connected": True, "created": created, "connection": conn, "access": access}

    def optimize_switch(self, lakehouse_id: str) -> dict[str, Any]:
        return {"active": self.optimize_registry.activate(lakehouse_id)}

    def optimize_disconnect(self, lakehouse_id: str) -> dict[str, Any]:
        """Forget the lakehouse on this PC. Nothing in the lakehouse is changed or deleted."""
        self.optimize_registry.forget(lakehouse_id)
        return {"disconnected": lakehouse_id, "active": self.optimize_registry.active()}

    def optimize_new_installation_id(self) -> dict[str, Any]:
        return {"installation_id": self.optimize_installation.reset()}

    def optimize_set_label(self, label: str) -> dict[str, Any]:
        self.optimize_installation.set_label(label)
        return {"label": self.optimize_installation.label}

    # The shared results (optimize/shared.py)

    def _optimize_shared(self, token: Callable[[], str] | None = None) -> Shared:
        self._optimize_allowed()
        conn = self.optimize_registry.active()
        if not conn:
            raise ValueError("Connect this PC to an optimization lakehouse first (Settings > Optimize lakehouses).")
        lake = self._optimize_lake(conn["workspace_id"], conn["lakehouse_id"], conn.get("schemas", True),
                                  **({"token": token} if token else {}))
        return Shared(lake, self.optimize_installation, access=conn.get("access") or "none")

    # ---------- the central lakehouse, for other features ----------

    def central_lakehouse(self) -> dict[str, Any] | None:
        """The central lakehouse this PC uses (workspace_id, lakehouse_id, lakehouse_name, schemas,
        access), for features that keep their own table family or files there. None when the
        organization's policy turns it off or this PC isn't connected to one."""
        if not app_settings.value("allow_optimize_lakehouse"):
            return None
        conn = self.optimize_registry.active()
        return dict(conn) if conn else None

    def central_family(self, family: schema.Family, approve: bool = False) -> tuple[Any, Installation]:
        """The central lakehouse and this installation, ready for ``family``'s tables: write with
        setup.write(lake, installation, name, rows, family=family), read with
        lake.rows(family.table(name)). When the family's tables (or Optimize's shared ones) are missing,
        unfinished or older, it sets them up only with ``approve``, which the caller passes after the
        person confirmed; without it, it raises with what setting up would do. Raises when there's no
        central lakehouse, this person can only read it, or the tables there are newer or not the
        family's."""
        self._optimize_allowed()
        conn = self.optimize_registry.active()
        if not conn:
            raise ValueError("Connect this PC to a central lakehouse first (Settings > Optimize lakehouses).")
        if conn.get("access") != "write":
            raise PermissionError("You can read the central lakehouse but not write to it. "
                                  "Ask for Contributor on its workspace.")
        lake = self._optimize_lake(conn["workspace_id"], conn["lakehouse_id"], conn.get("schemas", True))
        for fam in (setup.CORE, family):
            state = setup.inspect(lake, fam)
            if state["state"] == setup.READY:
                continue
            if state["state"] not in setup.CAN_INITIALIZE:
                raise PermissionError(state["message"])
            if not approve:
                raise PermissionError(f"{state['message']} Confirm to continue.")
            setup.initialize(lake, self.optimize_installation, approve=True, family=fam)
        return lake, self.optimize_installation

    def _optimize_workspaces(self) -> set[str]:
        """The workspaces the signed-in person can open in Fabric: results about items anywhere
        else aren't shown to them."""
        return {w["id"] for w in self.fabric.list_workspaces() if w.get("id")}

    def optimize_findings(self) -> list[dict[str, Any]]:
        """Problems found by every installation, one per problem, for items the person can open."""
        return self._optimize_shared().findings(self._optimize_workspaces())

    def optimize_recommendations(self, include_closed: bool = False) -> list[dict[str, Any]]:
        """Recommendations at their latest state, for items the person can open."""
        return self._optimize_shared().recommendations(self._optimize_workspaces(), include_closed=include_closed)

    def optimize_set_recommendation(self, recommendation_id: str, state: str, reason: str = "") -> dict[str, Any]:
        """Accept, dismiss, reopen or otherwise move a recommendation on, for everyone using the lakehouse."""
        return self._optimize_shared().set_recommendation_state(recommendation_id, state, reason,
                                                               self._optimize_workspaces())
