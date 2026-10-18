"""The app's calls for setting up Optimize: create, connect to, switch and forget optimization lakehouses.

Creating a lakehouse is a Fabric write and only happens from ``optimize_create``. Connecting to an
existing one never initializes it unless the call says the person confirmed. IT can turn the
whole feature off with the ``allow_optimize_lakehouse`` policy key.
"""

from __future__ import annotations

from typing import Any

from . import app_settings, redact
from .optimize import setup
from .optimize.installation import Installation, now
from .optimize.lakehouse import OneLakeLakehouse
from .optimize.registry import Registry

DEFAULT_NAME = "FabricDesktop_Optimization"
METHODS = {"optimize_status", "optimize_discover", "optimize_inspect", "optimize_create", "optimize_connect",
           "optimize_switch", "optimize_disconnect", "optimize_new_installation_id", "optimize_set_label"}


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

    def _optimize_lake(self, workspace_id: str, lakehouse_id: str, schemas: bool | None = None) -> Any:
        if schemas is None:
            schemas = self._schemas_enabled(workspace_id, lakehouse_id)  # type: ignore[attr-defined]
        return OneLakeLakehouse(workspace_id, lakehouse_id, schemas, self.auth.storage_token,
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
                entry["other_tables"] = sum(1 for n in names if not n.startswith("fdopt_"))
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
