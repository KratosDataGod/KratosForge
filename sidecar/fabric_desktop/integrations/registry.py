"""What Fabric Desktop by KratosBI can do for an external caller, in a form a machine can read.

Every capability is one ``Capability``: an id, plain-language text, JSON Schemas for its input and
output, the permission category it needs, whether it changes anything, whether the person must
approve it, where it runs, and a handler that calls an existing service (services.py). The MCP
tool list, the local API's OpenAPI document and the Integrations page are all generated from the
registry, so a capability added here reaches every interface with no transport code.

``invoke`` is the one way in. It finds the capability, refuses it if it isn't available, checks
the arguments, asks the authorizer (permissions, approvals: PR-AI-005), runs the handler with a
timeout and hands back the result or an IntegrationError. Every transport calls it; none calls a
service directly, so a check here can't be skipped by choosing another route.

A read-only capability can also be a resource: ``resource`` is a ``fabric-desktop://`` URI
template whose ``{placeholders}`` are its arguments (RFC 6570 level 1). ``resolve`` turns a URI
back into the capability and its arguments, so reading a resource is the same ``invoke``.

Extensions add capabilities with ``register(..., source="<extension id>")`` and take them away
with ``unregister_source``. A capability can never be registered without an explicit permission
category, and an extension can't replace a core capability.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

from urllib.parse import quote, unquote

from .core import CallContext, IntegrationError, redact_result, run_call, validate

# Permission categories, from least to most trust. Each capability needs exactly one.
PERMISSIONS = ("READ_METADATA", "READ_DATA", "WRITE_LOCAL_FILES", "EXECUTE_LOCAL_CODE", "WRITE_ONELAKE",
               "MODIFY_FABRIC_ARTIFACTS", "DEPLOY_TO_FABRIC", "MANAGE_CONNECTIONS")
READ_PERMISSIONS = ("READ_METADATA", "READ_DATA")
PERMISSION_TEXT = {
    "READ_METADATA": "Read names, structure, schemas, checks and run status. No rows of data.",
    "READ_DATA": "Read rows: table previews and query results.",
    "WRITE_LOCAL_FILES": "Create and change notebooks in your notebook folder on this PC.",
    "EXECUTE_LOCAL_CODE": "Run Python and notebooks on this PC, as you. You approve every run.",
    "WRITE_ONELAKE": "Write tables and files in OneLake.",
    "MODIFY_FABRIC_ARTIFACTS": "Create or change items in Fabric workspaces.",
    "DEPLOY_TO_FABRIC": "Publish, promote and deploy to Fabric.",
    "MANAGE_CONNECTIONS": "Manage connections and credentials.",
}
APPROVALS = ("never", "ask", "always")
LOCATIONS = ("this_pc", "fabric")
STATUSES = ("available", "preview", "unavailable")
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
SOURCE_PATTERN = re.compile(r"^[a-z][a-z0-9_.\-]{1,63}$")
URI_SCHEME = "fabric-desktop://"
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
MAX_URI = 2000

Handler = Callable[..., Any]


@dataclass
class Capability:
    id: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission: str
    handler: Handler
    read_only: bool = True
    destructive: bool = False
    approval: str = "never"
    locations: tuple[str, ...] = ("this_pc",)
    artifact_types: tuple[str, ...] = ()
    status: str = "available"
    reason: str = ""
    limitations: tuple[str, ...] = ()
    version: int = 1
    source: str = "core"
    timeout: float = 60.0
    # Whether this capability can be called with no service behind it (status, capability list).
    needs_service: bool = True
    # Answered for any caller while integrations are on, connected or not (the capability list,
    # so a tool that isn't connected yet can see why nothing else is available).
    public: bool = False
    # A fabric-desktop:// URI template that also serves this capability as an MCP resource.
    resource: str = ""

    @property
    def resource_params(self) -> list[str]:
        return _PLACEHOLDER.findall(self.resource)

    def resource_uri(self, **params: str) -> str:
        return _PLACEHOLDER.sub(lambda m: quote(str(params[m.group(1)]), safe=""), self.resource)

    def describe(self) -> dict[str, Any]:
        """The capability as a client sees it."""
        return {"id": self.id, "title": self.title, "description": self.description,
                "input_schema": self.input_schema, "output_schema": self.output_schema,
                "permission": self.permission, "read_only": self.read_only, "destructive": self.destructive,
                "approval": self.approval, "locations": list(self.locations), "artifact_types": list(self.artifact_types),
                "status": self.status, "reason": self.reason, "limitations": list(self.limitations),
                "version": self.version, "source": self.source, "timeout_seconds": self.timeout, "public": self.public,
                "resource": self.resource or None}


class Authorizer(Protocol):
    def check(self, capability: Capability, arguments: dict[str, Any], ctx: CallContext) -> None:
        """Raise an IntegrationError to refuse the call; return to allow it."""

    def availability(self, capability: Capability, ctx: CallContext | None) -> tuple[str, str]:
        """(status, reason) for listing: whether this caller could use it now."""


class ReadOnlyAuthorizer:
    """Until the permission framework (PR-AI-005) decides per client: read-only capabilities only.
    Anything that writes or runs code is listed as unavailable and refused."""

    REASON = "Needs the permission and approval framework, which isn't switched on in this build."

    def check(self, capability: Capability, arguments: dict[str, Any], ctx: CallContext) -> None:
        if not capability.read_only or capability.permission not in READ_PERMISSIONS:
            raise IntegrationError("unavailable", f"{capability.title} isn't available to external tools yet.",
                                   hint=self.REASON)

    def availability(self, capability: Capability, ctx: CallContext | None) -> tuple[str, str]:
        if not capability.read_only or capability.permission not in READ_PERMISSIONS:
            return "unavailable", self.REASON
        return capability.status, capability.reason


def _check_schema(schema: Any, what: str, cap_id: str) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError(f"{cap_id}: {what} must be a JSON Schema with type object")
    if not isinstance(schema.get("properties", {}), dict):
        raise ValueError(f"{cap_id}: {what} properties must be an object")


class Registry:
    def __init__(self, authorizer: Authorizer | None = None):
        self._caps: dict[str, Capability] = {}
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self.authorizer: Authorizer = authorizer or ReadOnlyAuthorizer()

    # ---- registration

    def register(self, cap: Capability, replace: bool = False) -> Capability:
        """Add ``cap`` after checking it is well formed. Raises ValueError otherwise, so a broken
        extension fails when it loads rather than when a client calls it."""
        if not ID_PATTERN.match(cap.id):
            raise ValueError(f"capability id {cap.id!r} must be lower case letters, digits and _ (3-64)")
        if not SOURCE_PATTERN.match(cap.source):
            raise ValueError(f"{cap.id}: source {cap.source!r} isn't a valid extension id")
        if cap.permission not in PERMISSIONS:
            raise ValueError(f"{cap.id}: permission must be one of {', '.join(PERMISSIONS)}")
        if cap.approval not in APPROVALS:
            raise ValueError(f"{cap.id}: approval must be one of {', '.join(APPROVALS)}")
        if cap.status not in STATUSES:
            raise ValueError(f"{cap.id}: status must be one of {', '.join(STATUSES)}")
        if not cap.locations or any(loc not in LOCATIONS for loc in cap.locations):
            raise ValueError(f"{cap.id}: locations must be from {', '.join(LOCATIONS)}")
        if cap.read_only and cap.permission not in READ_PERMISSIONS:
            raise ValueError(f"{cap.id}: a read-only capability needs READ_METADATA or READ_DATA")
        if not cap.read_only and cap.permission in READ_PERMISSIONS:
            raise ValueError(f"{cap.id}: a capability that changes or runs something can't use a read permission")
        if cap.destructive and cap.approval == "never":
            raise ValueError(f"{cap.id}: a destructive capability must ask for approval")
        if cap.status == "unavailable" and not cap.reason:
            raise ValueError(f"{cap.id}: say why it is unavailable")
        if not cap.title.strip() or not cap.description.strip():
            raise ValueError(f"{cap.id}: title and description are required")
        if not callable(cap.handler):
            raise ValueError(f"{cap.id}: handler must be callable")
        if not 0 < cap.timeout <= 3600:
            raise ValueError(f"{cap.id}: timeout must be between 0 and 3600 seconds")
        _check_schema(cap.input_schema, "input_schema", cap.id)
        _check_schema(cap.output_schema, "output_schema", cap.id)
        if cap.resource:
            _check_resource(cap)
        with self._lock:
            if cap.resource:
                clash = next((c for c in self._caps.values() if c.id != cap.id and _pattern(c.resource) is not None
                              and _shape(c.resource) == _shape(cap.resource)), None)
                if clash is not None:
                    raise ValueError(f"{cap.id}: resource {cap.resource} is the same as {clash.id}'s")
            existing = self._caps.get(cap.id)
            if existing is not None:
                if existing.source == "core" and cap.source != "core":
                    raise ValueError(f"{cap.id} is a core capability; an extension can't replace it")
                if existing.source != cap.source:
                    raise ValueError(f"{cap.id} is already registered by {existing.source}")
                if not replace:
                    raise ValueError(f"{cap.id} is already registered")
            self._caps[cap.id] = cap
        self._changed()
        return cap

    def unregister(self, cap_id: str) -> bool:
        with self._lock:
            removed = self._caps.pop(cap_id, None) is not None
        if removed:
            self._changed()
        return removed

    def unregister_source(self, source: str) -> list[str]:
        """Remove every capability ``source`` registered (an extension being turned off)."""
        if source == "core":
            raise ValueError("core capabilities can't be removed")
        with self._lock:
            gone = [cid for cid, cap in self._caps.items() if cap.source == source]
            for cid in gone:
                del self._caps[cid]
        if gone:
            self._changed()
        return gone

    def on_change(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever capabilities are added or removed (MCP tools/list_changed).
        Returns a function that stops listening."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _changed(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - a listener problem must not break registration
                pass

    # ---- reading

    def get(self, cap_id: str) -> Capability | None:
        with self._lock:
            return self._caps.get(cap_id)

    def all(self) -> list[Capability]:
        with self._lock:
            return sorted(self._caps.values(), key=lambda c: c.id)

    def availability(self, cap: Capability, ctx: CallContext | None = None) -> tuple[str, str]:
        if cap.status == "unavailable":
            return cap.status, cap.reason
        status, reason = self.authorizer.availability(cap, ctx)
        if status == "available" and cap.status == "preview":
            return "preview", cap.reason
        return status, reason

    def describe(self, ctx: CallContext | None = None, include_unavailable: bool = True) -> list[dict[str, Any]]:
        """Every capability with its status for this caller. ``include_unavailable=False`` leaves out
        what the caller can't use now (MCP lists only usable tools)."""
        out = []
        for cap in self.all():
            status, reason = self.availability(cap, ctx)
            if status == "unavailable" and not include_unavailable:
                continue
            out.append(cap.describe() | {"status": status, "reason": reason})
        return out

    def resources(self) -> list[Capability]:
        """Capabilities that are also resources."""
        return [c for c in self.all() if c.resource]

    def resolve(self, uri: str) -> tuple[Capability, dict[str, Any]]:
        """The capability a resource URI names and the arguments it carries. Raises not_found."""
        if len(uri) <= MAX_URI and uri.startswith(URI_SCHEME):
            path, _, query = uri.partition("?")
            for cap in self.resources():
                m = _pattern(cap.resource).fullmatch(path)  # type: ignore[union-attr]
                if m is not None:
                    args: dict[str, Any] = {k: unquote(v) for k, v in m.groupdict().items()}
                    for part in filter(None, query.split("&")):  # paging and options: ?offset=100&limit=50
                        key, _, value = part.partition("=")
                        prop = cap.input_schema.get("properties", {}).get(unquote(key))
                        if prop is None or unquote(key) in args:
                            raise IntegrationError("invalid_input", f"{unquote(key)!r} isn't an option of {uri.split('?')[0]}.")
                        args[unquote(key)] = _query_value(unquote(value), prop)
                    return cap, args
        raise IntegrationError("not_found", f"There is no resource {uri[:200]}.",
                               hint="List resources and resource templates to see what can be read.")

    # ---- calling

    def invoke(self, service: Any, cap_id: str, arguments: dict[str, Any] | None, ctx: CallContext) -> Any:
        """The one way an external caller runs a capability. Raises IntegrationError."""
        cap = self.get(cap_id)
        if cap is None:
            raise IntegrationError("not_found", f"There is no capability {cap_id!r}.",
                                   hint="Call fabric_desktop_list_capabilities to see what is available.")
        if cap.status == "unavailable":
            raise IntegrationError("unavailable", f"{cap.title} isn't available: {cap.reason}")
        args = validate(cap.input_schema, arguments)
        self.authorizer.check(cap, args, ctx)  # may wait for the person's approval
        ctx.started = time.monotonic()  # the time limit is for the work, not for the approval wait
        ctx.timeout = min(ctx.timeout, cap.timeout) if ctx.timeout else cap.timeout
        if cap.needs_service and service is None:
            raise IntegrationError("unavailable", "The Fabric Desktop by KratosBI engine isn't running here.")
        started = time.monotonic()
        result = run_call(lambda: cap.handler(service, ctx, **args), ctx)
        ctx.seconds = round(time.monotonic() - started, 3)
        return redact_result(result)


def _shape(template: str) -> str:
    return _PLACEHOLDER.sub("{}", template)


def _pattern(template: str) -> re.Pattern[str] | None:
    if not template:
        return None
    parts = _PLACEHOLDER.split(template)
    regex = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>[^/?#]+)" for i, p in enumerate(parts))
    return re.compile(regex)


def _check_resource(cap: Capability) -> None:
    if not cap.read_only or cap.approval != "never":
        raise ValueError(f"{cap.id}: only read-only capabilities that don't ask can be resources")
    if not cap.resource.startswith(URI_SCHEME) or any(c in cap.resource for c in " ?#"):
        raise ValueError(f"{cap.id}: resource must be a {URI_SCHEME} URI template with no query")
    props = cap.input_schema.get("properties", {})
    required = set(cap.input_schema.get("required", []))
    params = cap.resource_params
    if len(set(params)) != len(params):
        raise ValueError(f"{cap.id}: resource repeats a placeholder")
    missing = [p for p in params if p not in props]
    if missing:
        raise ValueError(f"{cap.id}: resource placeholder {', '.join(missing)} isn't an input")
    if required - set(params):
        raise ValueError(f"{cap.id}: required inputs {', '.join(sorted(required - set(params)))} must be in the resource URI")


def _query_value(value: str, prop: dict[str, Any]) -> Any:
    kind = prop.get("type")
    try:
        if kind == "integer":
            return int(value)
        if kind == "number":
            return float(value)
    except ValueError:
        raise IntegrationError("invalid_input", f"{value!r} isn't a number.") from None
    if kind == "boolean":
        if value not in ("true", "false"):
            raise IntegrationError("invalid_input", f"{value!r} isn't true or false.")
        return value == "true"
    return value


def schema(properties: dict[str, Any] | None = None, required: Iterable[str] = (), *, extra: bool = False) -> dict[str, Any]:
    """A JSON Schema object, the shape every capability's input and output uses."""
    out: dict[str, Any] = {"type": "object", "properties": properties or {}}
    req = list(required)
    if req:
        out["required"] = req
    if not extra:
        out["additionalProperties"] = False
    return out


PAGING = {"offset": {"type": "integer", "minimum": 0, "default": 0, "description": "Where to start; use next_offset from the last page."},
          "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100, "description": "How many to return (at most 500)."}}


def paged_output(item: dict[str, Any]) -> dict[str, Any]:
    return schema({"items": {"type": "array", "items": item}, "total": {"type": "integer"},
                   "offset": {"type": "integer"}, "next_offset": {"type": ["integer", "null"]}},
                  ["items", "total", "offset", "next_offset"], extra=True)


@dataclass
class _Default:
    registry: Registry | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_default = _Default()


def default_registry() -> Registry:
    """The registry with every core capability, built once per process."""
    with _default.lock:
        if _default.registry is None:
            from .capabilities import register_core
            reg = Registry()
            register_core(reg)
            _default.registry = reg
        return _default.registry
