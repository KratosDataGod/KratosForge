"""What the app's Settings > Integrations page calls (engine RPC methods, see server.py).

These run in the app's own engine, which only the app can reach (a private pipe from the shell).
They are how the person switches integrations on and off, connects and disconnects tools, gives
and takes away permissions and answers approval requests. None of them is a capability, so no
external tool can call them through MCP or the local API.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

from . import policy
from .config import TOOLS, launcher_path, snippets, write_launcher
from .permissions import OFFERED, permission_rows
from .registry import PERMISSIONS, default_registry
from .store import IntegrationStore

METHODS = {"integrations_status", "integrations_set_enabled", "integrations_add_client", "integrations_update_client",
           "integrations_revoke_client", "integrations_approvals", "integrations_decide", "integrations_new_token",
           "integrations_set_http"}

HTTP_START_SECONDS = 15


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
        http = self._http_state()
        url = http["url"] if client["transport"] == "http" else ""
        return client | {"permissions": permission_rows(client), "config": snippets(client, command, url),
                         "kind_label": TOOLS.get(client["kind"], client["kind"])}

    # ---- the local HTTP API, a child process of this engine (http_server.serve)

    def _http_state(self) -> dict[str, Any]:
        settings, rules = self.integration_store.settings(), policy.current()
        port = int(settings["http_port"])
        proc = getattr(self, "_http_proc", None)
        running = proc is not None and proc.poll() is None
        state_file = self.root / "integrations" / "http.json"
        listening = running and state_file.exists()
        return {"switch": settings["http_enabled"], "allowed": bool(rules["http"]) and rules["enabled"] is not False,
                "running": running, "listening": listening, "port": port, "url": f"http://127.0.0.1:{port}",
                "exit_code": None if proc is None or running else proc.returncode,
                "log": str(self.root / "integrations" / "http.log")}

    def _http_start(self) -> None:
        if getattr(self, "_http_proc", None) is not None and self._http_proc.poll() is None:
            return
        folder = self.root / "integrations"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "http.json").unlink(missing_ok=True)
        port = int(self.integration_store.settings()["http_port"])
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with open(folder / "http.log", "ab") as log:
            self._http_proc = subprocess.Popen(
                [sys.executable, "-m", "fabric_desktop.cli", "integrations", "serve", "--port", str(port),
                 "--parent-pid", str(os.getpid())],
                cwd=str(self.root), creationflags=flags, env=os.environ.copy(),
                stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        deadline = time.monotonic() + HTTP_START_SECONDS
        while time.monotonic() < deadline:  # wait until it is listening, or has failed
            if self._http_proc.poll() is not None:
                raise RuntimeError(f"The local API didn't start (see {folder / 'http.log'}). "
                                   f"Port {port} may be in use by another program.")
            if (folder / "http.json").exists():
                return
            time.sleep(0.1)

    def _http_stop(self) -> None:
        proc = getattr(self, "_http_proc", None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._http_proc = None
        (self.root / "integrations" / "http.json").unlink(missing_ok=True)

    def integrations_sync_http(self) -> None:
        """Start or stop the local API to match the switches and policy. Called when the engine
        starts and after any change."""
        status = self._http_state()
        settings = self.integration_store.settings()
        want = settings["enabled"] and status["switch"] and status["allowed"]
        if want and not status["running"]:
            self._http_start()
        elif not want and status["running"]:
            self._http_stop()

    def integrations_set_http(self, enabled: bool, port: int | None = None) -> dict[str, Any]:
        """The person's switch for the local API (off by default)."""
        if enabled and not self._http_state()["allowed"]:
            raise PermissionError("Your organization doesn't allow the local API on this PC.")
        if port is not None:
            if not isinstance(port, int) or not 1024 <= port <= 65535:
                raise ValueError("port is a number from 1024 to 65535")
            if port != self.integration_store.settings()["http_port"]:
                self._http_stop()
                self.integration_store.set_setting("http_port", port)
        self.integration_store.set_setting("http_enabled", bool(enabled))
        self.integrations_sync_http()
        return self.integrations_status()

    def integrations_new_token(self, client_id: str) -> dict[str, Any]:
        """A new token for a tool that uses the local API; the old one stops working. Shown once."""
        token = self.integration_store.new_token(client_id)
        return {"client": self._client_view(self.integration_store.client(client_id)), "token": token}  # type: ignore[arg-type]

    def integrations_status(self) -> dict[str, Any]:
        store, rules = self.integration_store, policy.current()
        settings = store.settings()
        enabled = settings["enabled"] and rules["enabled"] is not False
        if enabled:
            write_launcher(self.root)
        return {"enabled": enabled, "switch": settings["enabled"], "policy": rules,
                "set_by_organization": rules["enabled"] is False, "launcher": str(launcher_path(self.root)),
                "http": self._http_state(),
                "tools": TOOLS, "permissions": permission_rows(),
                "clients": [self._client_view(c) for c in store.clients()],
                "pending": store.approvals("pending"),
                "capabilities": default_registry().describe()}

    def integrations_set_enabled(self, enabled: bool) -> dict[str, Any]:
        if enabled and policy.current()["enabled"] is False:
            raise PermissionError("Your organization has turned off external AI tools on this PC.")
        self.integration_store.set_setting("enabled", bool(enabled))
        self.integrations_sync_http()
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

    def integrations_revoke_client(self, client_id: str) -> dict[str, Any]:  # its token stops working at once
        return self._client_view(self.integration_store.revoke_client(client_id))

    def integrations_approvals(self, state: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
        return self.integration_store.approvals(state, limit)

    def integrations_decide(self, approval_id: str, approve: bool, note: str = "") -> dict[str, Any]:
        return self.integration_store.decide(approval_id, bool(approve), note)
