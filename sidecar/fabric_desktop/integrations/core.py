"""The contract every external caller gets: one error shape, checked inputs, timeouts, cancellation.

The engine's services (``Service`` and its mixins) were written for the app's own UI: they take
keyword arguments, return JSON-ready values and raise ordinary Python exceptions with a message
for a person. This module wraps them for callers outside the app (an MCP client, the local API)
without changing them:

    IntegrationError   a stable ``code``, a plain message, a ``hint`` saying what to do, and
                       whether retrying can help. ``error_from`` turns the engine's own exceptions
                       into one, with secrets removed from the message.
    validate           checks arguments against a capability's JSON Schema before anything runs.
    CallContext        who is calling, how long the call may take, and a flag a handler checks to
                       stop early when the caller cancels.
    run_call           runs a handler on a worker thread and gives up at the deadline.

Nothing here knows about MCP or HTTP; the transports in this package all go through it.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..redact import describe, secret_key
from ..redact import secrets as redact_secrets
from ..retrying import TRANSIENT_STATUS, status_of

_log = logging.getLogger(__name__)

# The codes a caller can rely on. Messages change; codes don't.
CODES = {
    "invalid_input": "The arguments don't match what the capability takes.",
    "not_found": "The thing named doesn't exist, or you can't see it.",
    "permission_denied": "This client, your account or your organization doesn't allow it.",
    "approval_required": "The person at the PC has to approve this in KratosForge.",
    "approval_denied": "The person at the PC said no.",
    "conflict": "Something else is using what this needs; try again when it has finished.",
    "timeout": "It took longer than allowed.",
    "cancelled": "The caller cancelled it.",
    "unavailable": "This capability isn't available here.",
    "integrations_disabled": "External AI tools are turned off in KratosForge.",
    "too_large": "The request or the result is bigger than allowed.",
    "rate_limited": "Too many requests; slow down.",
    "upstream": "Microsoft Fabric or OneLake returned an error.",
    "failed": "The operation ran and failed.",
    "internal": "KratosForge hit an unexpected problem.",
}


class IntegrationError(Exception):
    def __init__(self, code: str, message: str, *, hint: str = "", retryable: bool = False,
                 details: dict[str, Any] | None = None):
        if code not in CODES:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


# ---- secrets out of messages

def redact_result(value: Any) -> Any:
    """Every string in a result, with secrets removed, and the text of every field named like a
    secret (password, token, ...) blanked; counts and flags such as secrets_ignored stay. Applied to
    every capability's result, so a token that slipped into a notebook, a log line or a receipt
    doesn't leave through a tool."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {k: "[removed]" if secret_key(k) and isinstance(v, str) else redact_result(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_result(v) for v in value]
    return value


def error_from(exc: BaseException) -> IntegrationError:
    """The engine's exception as an IntegrationError. The engine's messages are already written for
    people, so they are kept, without secrets."""
    if isinstance(exc, IntegrationError):
        return exc
    message = redact_secrets(str(exc) or type(exc).__name__)
    name = type(exc).__name__
    status = status_of(exc)
    if name == "FabricError" or status is not None or name in ("HTTPError", "ConnectionError", "Timeout"):
        if status in (401, 403) or "Unauthorized" in message or "Forbidden" in message:
            return IntegrationError("permission_denied", message, hint="Your account can't do this in Fabric. Check your workspace role.")
        if status == 404 or "NotFound" in message:
            return IntegrationError("not_found", message)
        if status in TRANSIENT_STATUS or name in ("ConnectionError", "Timeout"):
            return IntegrationError("upstream", message, retryable=True, hint="Fabric is busy or can't be reached; try again shortly.")
        return IntegrationError("upstream", message)
    if hasattr(exc, "errors"):  # a metadata gate: its message lists every reason
        return IntegrationError("failed", message, details={"reasons": [redact_secrets(str(e)) for e in exc.errors]})  # type: ignore[attr-defined]
    if isinstance(exc, PermissionError):
        return IntegrationError("permission_denied", message)
    if isinstance(exc, (FileNotFoundError, LookupError)):  # LookupError covers KeyError
        return IntegrationError("not_found", message.strip("'\""))
    if isinstance(exc, TimeoutError):
        return IntegrationError("timeout", message, retryable=True)
    if isinstance(exc, (ValueError, TypeError)):
        return IntegrationError("invalid_input", message)
    if isinstance(exc, RuntimeError):
        # The engine raises RuntimeError for "something else is running" and for failed work alike.
        busy = any(word in message.lower() for word in ("is running", "is already running", "wait for it", "is using"))
        return IntegrationError("conflict" if busy else "failed", message, retryable=busy)
    return IntegrationError("internal", f"{name}: {message}")


# ---- arguments

_TYPES: dict[str, Callable[[Any], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _problems(schema: dict[str, Any], value: Any, path: str) -> list[str]:
    """The JSON Schema keywords capabilities use: type, enum, const, properties, required,
    additionalProperties, items, min/max(Length|Items|imum), pattern, anyOf."""
    where = path or "arguments"
    if "anyOf" in schema:
        if all(_problems(option, value, path) for option in schema["anyOf"]):
            return [f"{where} doesn't match any allowed form"]
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not any(_TYPES[k](value) for k in kinds):
            return [f"{where} should be {' or '.join(kinds)}"]
    out: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{where} must be one of {', '.join(map(str, schema['enum']))}")
    if "const" in schema and value != schema["const"]:
        out.append(f"{where} must be {schema['const']!r}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{where} is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{where} is longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{where} isn't in the expected form")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{where} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{where} must be at most {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{where} needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{where} has more than {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                out += _problems(schema["items"], item, f"{where}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{key} is required" if not path else f"{path}.{key} is required")
        for key, item in value.items():
            sub = f"{path}.{key}" if path else key
            if key in props:
                out += _problems(props[key], item, sub)
            elif schema.get("additionalProperties") is False:
                out.append(f"{sub} isn't an argument this takes")
            elif isinstance(schema.get("additionalProperties"), dict):
                out += _problems(schema["additionalProperties"], item, sub)
    return out


def validate(schema: dict[str, Any], arguments: Any) -> dict[str, Any]:
    """``arguments`` checked against ``schema``, with schema defaults filled in for missing
    top-level properties. Raises invalid_input listing every problem."""
    if arguments is None:
        arguments = {}
    problems = _problems(schema, arguments, "")
    if problems:
        raise IntegrationError("invalid_input", "; ".join(problems[:10]),
                               hint="List the capabilities to see each one's input schema.",
                               details={"problems": problems[:50]})
    if isinstance(arguments, dict):
        defaults = {k: p["default"] for k, p in schema.get("properties", {}).items() if "default" in p}
        return defaults | arguments
    return arguments


# ---- calls

@dataclass(frozen=True)
class Caller:
    """Who is asking. ``channel`` is how they reached the engine: "mcp-stdio", "mcp-http", "api",
    or "test"."""
    client_id: str
    channel: str
    name: str = ""


@dataclass
class CallContext:
    caller: Caller
    timeout: float = 60.0
    started: float = field(default_factory=time.monotonic)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    progress: Callable[[float, float | None, str], None] | None = None
    seconds: float = 0.0  # how long the handler ran, set by Registry.invoke
    decision: str = ""  # "allowed" or "approved", set by the authorizer; for the audit line
    approval_id: str = ""  # the approval this call used, set by the authorizer; for the audit lines
    # The workspaces the caller is limited to, set by the authorizer; None is no limit (scope.keep).
    workspaces: frozenset[str] | None = None

    @property
    def deadline(self) -> float:
        return self.started + self.timeout

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def cancel(self) -> None:
        self.cancel_event.set()

    def check(self) -> None:
        """For handlers that loop: stop here if the caller cancelled or time ran out."""
        if self.cancelled:
            raise IntegrationError("cancelled", "Cancelled by the caller.")
        if time.monotonic() >= self.deadline:  # >=: Windows clock ticks are ~15 ms, so a zero timeout may read as "now"
            raise IntegrationError("timeout", f"Gave up after {self.timeout:g} seconds.", retryable=True)

    def report(self, done: float, total: float | None = None, message: str = "") -> None:
        if self.progress is not None:
            try:
                self.progress(done, total, message)
            except Exception as exc:  # noqa: BLE001 - a progress message must never fail the work
                _log.info("Progress callback failed: %s", describe(exc))


def run_call(handler: Callable[[], Any], ctx: CallContext) -> Any:
    """Run ``handler`` on a worker thread and wait until it finishes, the deadline passes or the
    caller cancels. On timeout or cancel the context's cancel flag is set so a handler that checks
    it stops; one that can't be interrupted finishes on its own and its result is dropped."""
    box: dict[str, Any] = {}
    done = threading.Event()

    def work() -> None:
        try:
            box["result"] = handler()
        except BaseException as exc:  # noqa: BLE001 - handed to the caller below
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=work, daemon=True, name="integration-call").start()
    while not done.wait(0.05):
        if ctx.cancelled:
            raise IntegrationError("cancelled", "Cancelled by the caller.")
        if time.monotonic() >= ctx.deadline:
            ctx.cancel()
            raise IntegrationError("timeout", f"Gave up after {ctx.timeout:g} seconds.", retryable=True,
                                   hint="Ask for less (a smaller page or limit), or use an execution and poll its status.")
    if "error" in box:
        raise error_from(box["error"])
    return box.get("result")
