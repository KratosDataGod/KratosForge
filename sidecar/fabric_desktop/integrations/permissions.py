"""Who may do what: the permission and approval checks every external call passes.

``PermissionAuthorizer`` is the registry's authorizer outside tests. ``Registry.invoke`` calls its
``check`` for every call from every transport, so there is one decision, made here, in this order:

1. External AI tools are on: the organization hasn't turned them off (policy ``integrations``)
   and the person switched them on in Settings > Integrations.
2. The caller is a connected tool that hasn't been disconnected.
3. The capability's permission category is offered in this release, allowed by the organization
   (``integrations_max_permissions``) and granted to this tool.
4. Its target is in the tool's scope (the workspaces the person limited it to, if any).
5. If the capability needs approval, the person approves this exact call in Fabric Desktop. The
   call waits a while for the answer; if none comes it fails with ``approval_required`` and the
   same call made again later uses the approval once it is given. An approval is single-use and
   bound to a hash of the exact arguments.

Approvals are decided only through the app's own engine (``IntegrationStore.decide``, reached
from the Settings page). No capability, MCP message or API endpoint can grant, approve or change
permissions.
"""

from __future__ import annotations

import time
from typing import Any

from . import policy
from .core import CallContext, IntegrationError, redact_secrets
from .registry import PERMISSIONS, Capability
from .store import IntegrationStore, args_hash

# Categories a tool can be given in this release. The rest exist so the model is complete, and
# show as not offered.
OFFERED = ("READ_METADATA", "READ_DATA", "WRITE_LOCAL_FILES", "EXECUTE_LOCAL_CODE")
NEVER = ("MANAGE_CONNECTIONS",)  # credentials never leave the app
APPROVAL_WAIT_SECONDS = 60.0
WHERE = "Fabric Desktop by KratosBI > Settings > Integrations"
_TARGET_KEYS = ("workspace_id", "lakehouse_id", "item_id", "name", "notebook", "table", "id", "execution_id")
_CONTENT_KEYS = ("code", "query", "cells", "source")


def summary(cap: Capability, client: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """What the person sees before deciding: who, what, on what, and any code in full."""
    out: dict[str, Any] = {"client": client["name"], "action": cap.title, "permission": cap.permission,
                           "destructive": cap.destructive, "locations": list(cap.locations),
                           "target": {k: arguments[k] for k in _TARGET_KEYS if k in arguments}}
    for key in _CONTENT_KEYS:
        if key in arguments:
            value = arguments[key]
            out["content"] = {"kind": key, "text": redact_secrets(value if isinstance(value, str) else repr(value))[:20_000]}
            break
    return out


class PermissionAuthorizer:
    def __init__(self, store: IntegrationStore, wait_seconds: float = APPROVAL_WAIT_SECONDS, poll: float = 0.25):
        self.store = store
        self.wait_seconds = wait_seconds
        self.poll = poll

    # ---- the decision

    def _enabled(self) -> tuple[bool, str]:
        rules = policy.current()
        if rules["enabled"] is False:
            return False, "Your organization has turned off external AI tools on this PC."
        if not self.store.settings()["enabled"]:
            return False, f"External AI tools are turned off. Turn them on in {WHERE}."
        return True, ""

    def _category(self, cap: Capability, client: dict[str, Any] | None) -> tuple[bool, str]:
        if cap.permission in NEVER:
            return False, "Fabric Desktop by KratosBI never gives connections or credentials to external tools."
        if cap.permission not in OFFERED:
            return False, f"{cap.permission} isn't offered to external tools in this release."
        if cap.permission not in policy.current()["max_permissions"]:
            return False, f"Your organization doesn't allow external tools {cap.permission}."
        if client is None or cap.permission not in client["grants"]:
            return False, f"This tool hasn't been given {cap.permission}. The person can add it in {WHERE}."
        if cap.permission == "EXECUTE_LOCAL_CODE" and client["workspaces"]:
            # Code runs with the person's sign-in, which reaches every workspace they can open.
            return False, "A tool limited to some workspaces can't run code: code would reach every workspace you can."
        return True, ""

    def _client(self, ctx: CallContext | None) -> dict[str, Any] | None:
        if ctx is None:
            return None
        client = self.store.client(ctx.caller.client_id)
        return None if client is None or client["revoked_at"] else client

    def availability(self, cap: Capability, ctx: CallContext | None) -> tuple[str, str]:
        on, why = self._enabled()
        if not on:
            return "unavailable", why
        if cap.public:
            return cap.status, cap.reason
        client = self._client(ctx)
        if client is None:
            return "unavailable", f"This tool isn't connected. Add it in {WHERE} and use the configuration shown there."
        ok, why = self._category(cap, client)
        if not ok:
            return "unavailable", why
        return cap.status, cap.reason

    def check(self, cap: Capability, arguments: dict[str, Any], ctx: CallContext) -> None:
        on, why = self._enabled()
        if not on:
            if cap.public:
                return  # list_capabilities still answers, showing everything unavailable and why
            raise IntegrationError("integrations_disabled", why)
        if cap.public:
            return
        client = self._client(ctx)
        if client is None:
            known = self.store.client(ctx.caller.client_id)
            message = ("This tool was disconnected in Fabric Desktop by KratosBI." if known else
                       "This tool isn't connected to Fabric Desktop by KratosBI.")
            raise IntegrationError("permission_denied", message,
                                   hint=f"Ask the person to add it in {WHERE} and update your MCP or API configuration.")
        ok, why = self._category(cap, client)
        if not ok:
            raise IntegrationError("permission_denied", why, details={"permission": cap.permission})
        scope = client["workspaces"]
        if scope and "workspace_id" in arguments and arguments["workspace_id"] not in scope:
            raise IntegrationError("permission_denied", "This tool is limited to other workspaces.",
                                   hint=f"The person can change its workspaces in {WHERE}.")
        self.store.touch(client["id"])
        if self._needs_approval(cap, client):
            self._approve(cap, client, arguments, ctx)

    @staticmethod
    def _needs_approval(cap: Capability, client: dict[str, Any]) -> bool:
        if cap.approval == "always" or cap.destructive:
            return True
        return cap.approval == "ask" and cap.permission not in client["auto"]

    def _approve(self, cap: Capability, client: dict[str, Any], arguments: dict[str, Any], ctx: CallContext) -> None:
        digest = args_hash(cap.id, arguments)
        request = self.store.request_approval(client, cap.id, cap.title, cap.permission, summary(cap, client, arguments), arguments)
        give_up = time.monotonic() + min(self.wait_seconds, max(ctx.remaining(), 0.0) or self.wait_seconds)
        ctx.report(0, None, "Waiting for approval in Fabric Desktop by KratosBI")
        while True:
            current = self.store.approval(request["id"]) or request
            state = current["state"]
            if state == "approved":
                if self.store.consume(current["id"], client["id"], digest):
                    return
                state = "used"
            if state == "denied":
                raise IntegrationError("approval_denied", f"The person said no to {cap.title.lower()}.",
                                       details={"approval_id": current["id"], "note": current.get("note", "")})
            if state in ("expired", "used"):
                raise IntegrationError("approval_required", "The approval request expired before anyone answered it.",
                                       retryable=True, hint="Call again to ask again.",
                                       details={"approval_id": current["id"]})
            if ctx.cancelled:
                raise IntegrationError("cancelled", "Cancelled by the caller while waiting for approval.")
            if time.monotonic() >= give_up:
                raise IntegrationError(
                    "approval_required", f"Waiting for the person to approve {cap.title.lower()} in Fabric Desktop by KratosBI.",
                    retryable=True, details={"approval_id": current["id"], "expires_at": current["expires_at"]},
                    hint="Tell the person what you asked for. Once they approve it, make the same call again with the same arguments.")
            time.sleep(self.poll)


def permission_rows(client: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every category with whether it is offered, allowed by policy and granted: for the Settings page."""
    from .registry import PERMISSION_TEXT
    rules = policy.current()
    return [{"id": p, "description": PERMISSION_TEXT[p], "offered": p in OFFERED and p not in NEVER,
             "allowed": p in rules["max_permissions"], "granted": bool(client) and p in client["grants"],  # type: ignore[index]
             "auto": bool(client) and p in client["auto"]} for p in PERMISSIONS]  # type: ignore[index]
