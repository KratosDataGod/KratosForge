"""What the app's Settings > Integrations page calls (engine RPC methods, see server.py).

These run in the app's own engine, which only the app can reach (a private pipe from the shell).
They are how the person switches integrations on and off, connects and disconnects tools, gives
and takes away permissions and answers approval requests. None of them is a capability, so no
external tool can call them through MCP or the local API.
"""

from __future__ import annotations

from typing import Any

from . import policy
from .config import TOOLS, launcher_path, snippets, write_launcher
from .permissions import OFFERED, permission_rows
from .registry import PERMISSIONS, default_registry
from .store import IntegrationStore

METHODS = {"integrations_status", "integrations_set_enabled", "integrations_add_client", "integrations_update_client",
           "integrations_revoke_client", "integrations_approvals", "integrations_decide"}


def _check_permissions(values: list[str], field: str) -> list[str]:
    bad = [v for v in values if v not in PERMISSIONS]
    if bad:
        raise ValueError(f"{field}: unknown permission {', '.join(bad)}")
    rules = policy.current()
    not_offered = [v for v in values if v not in OFFERED]
    if not_offered:
        raise ValueError(f"{', '.join(not_offered)} can't be given to external tools in this release.")
    blocked = [v for v in values if v not in rules["max_permissions"]]
    if blocked:
        raise PermissionError(f"Your organization doesn't allow external tools {', '.join(blocked)}.")
    return values


class IntegrationsMixin:
    root: Any  # from Service

    @property
    def integration_store(self) -> IntegrationStore:
        if getattr(self, "_integration_store", None) is None:
            self._integration_store = IntegrationStore.at(self.root)
        return self._integration_store

    def _client_view(self, client: dict[str, Any]) -> dict[str, Any]:
        command = str(launcher_path(self.root))
        return client | {"permissions": permission_rows(client), "config": snippets(client, command),
                         "kind_label": TOOLS.get(client["kind"], client["kind"])}

    def integrations_status(self) -> dict[str, Any]:
        store, rules = self.integration_store, policy.current()
        settings = store.settings()
        enabled = settings["enabled"] and rules["enabled"] is not False
        if enabled:
            write_launcher(self.root)
        return {"enabled": enabled, "switch": settings["enabled"], "policy": rules,
                "set_by_organization": rules["enabled"] is False, "launcher": str(launcher_path(self.root)),
                "tools": TOOLS, "permissions": permission_rows(),
                "clients": [self._client_view(c) for c in store.clients()],
                "pending": store.approvals("pending"),
                "capabilities": default_registry().describe()}

    def integrations_set_enabled(self, enabled: bool) -> dict[str, Any]:
        if enabled and policy.current()["enabled"] is False:
            raise PermissionError("Your organization has turned off external AI tools on this PC.")
        self.integration_store.set_setting("enabled", bool(enabled))
        return self.integrations_status()

    def integrations_add_client(self, name: str, kind: str = "other-mcp", grants: list[str] | None = None,
                                auto: list[str] | None = None, workspaces: list[str] | None = None,
                                transport: str = "stdio") -> dict[str, Any]:
        """Connect a tool. READ_METADATA is always included: without it a tool can't see anything."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Give the tool a name, for example Claude Code on this PC.")
        if kind not in TOOLS:
            raise ValueError(f"kind is one of {', '.join(TOOLS)}")
        if transport not in ("stdio", "http"):
            raise ValueError("transport is stdio or http")
        grants = _check_permissions(sorted(set((grants or []) + ["READ_METADATA"])), "grants")
        auto = _check_permissions(sorted(set(auto or []) & set(grants)), "auto")
        if "EXECUTE_LOCAL_CODE" in auto:
            raise ValueError("Running code always asks first.")
        client, token = self.integration_store.add_client(name, kind, transport, grants, auto, workspaces or [])
        write_launcher(self.root)
        return {"client": self._client_view(client), "token": token}

    def integrations_update_client(self, client_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        changes = dict(changes)
        client = self.integration_store.client(client_id)
        if client is None or client["revoked_at"]:
            raise LookupError("That tool isn't connected.")
        if "grants" in changes:
            changes["grants"] = _check_permissions(sorted(set(changes["grants"]) | {"READ_METADATA"}), "grants")
        grants = set(changes.get("grants", client["grants"]))
        if "auto" in changes or "grants" in changes:
            auto = set(changes.get("auto", client["auto"])) & grants
            if "EXECUTE_LOCAL_CODE" in auto:
                raise ValueError("Running code always asks first.")
            changes["auto"] = _check_permissions(sorted(auto), "auto")
        return self._client_view(self.integration_store.update_client(client_id, **changes))

    def integrations_revoke_client(self, client_id: str) -> dict[str, Any]:
        return self._client_view(self.integration_store.revoke_client(client_id))

    def integrations_approvals(self, state: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
        return self.integration_store.approvals(state, limit)

    def integrations_decide(self, approval_id: str, approve: bool, note: str = "") -> dict[str, Any]:
        return self.integration_store.decide(approval_id, bool(approve), note)
