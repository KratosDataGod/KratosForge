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
import math
import ntpath
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..redact import REMOVED, describe, secret_key, user_paths
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

MAX_RESULT_DEPTH = 200  # lists and objects a result may nest; past it, redacting and JSON would recurse too far


def redact_result(value: Any) -> Any:
    """Every string in a result, keys included, with secrets removed, and all the text under a field
    named like a secret (password, token, ...) blanked, in lists and objects too; counts and flags
    such as secrets_ignored stay. Anything that isn't JSON becomes its text, as the transports would
    write it, with secrets removed. Applied to every capability's result, so a token that slipped
    into a notebook, a log line or a receipt doesn't leave through a tool. A result nested past
    MAX_RESULT_DEPTH is refused as too_large."""
    if too_deep(value, MAX_RESULT_DEPTH):
        raise IntegrationError("too_large", f"The result is nested more than {MAX_RESULT_DEPTH} levels deep.",
                               hint="Ask for less, or for one part at a time.")
    return _redact(value, False)


def _redact(value: Any, blank: bool) -> Any:
    if isinstance(value, str):
        return REMOVED if blank else redact_secrets(value)
    if isinstance(value, dict):
        # Two keys that redact to the same text keep one value: rare (a key holding a secret) and
        # harmless, since less leaves, not more.
        return {redact_secrets(k) if isinstance(k, str) else k: _redact(v, blank or secret_key(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, blank) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return REMOVED if blank else redact_secrets(str(value))


def _short_os_message(exc: OSError) -> str:
    """An OSError's text with only the last part of each path it names: "[Errno 2] No such file or
    directory: 'C:\\Users\\ann\\AppData\\...\\x.ipynb'" names where the person's data folder is."""
    names = [ntpath.basename(os.fsdecode(f)) for f in (exc.filename, exc.filename2) if isinstance(f, (str, bytes, os.PathLike))]
    return f"{exc.strerror or type(exc).__name__}: {' -> '.join(repr(n) for n in names)}"


# An engine message can carry what a caller outside the app mustn't get or can't use: a native
# library's backtrace (deltalake prints "  0: <unknown>" frames), terminal colour codes, and the full
# path of a file in this PC's data folder. A path is cut to its last part, which still names the file.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_FRAME = re.compile(r"^(\s+\d+: |\s+at \S+:\d+|\s*Stack backtrace:|Traceback \(most recent call last\))")
_PATH = r"(?:file://)?(?:[A-Za-z]:[\\/]|\\\\|/)"
_QUOTED_PATH = re.compile(r"""(['"])(""" + _PATH + r"""[^'"\n]*)\1""")
# Unquoted, a path from the root needs a folder in it ("/home/x", not "/20" as in "rows 10 /20").
_BARE_PATH = re.compile(r"""(?<![\w.:/\\%])(?:file://)?(?:[A-Za-z]:[\\/]|\\\\|/(?=[^\s/'"]+/))"""
                        r"""[^\s'"<>|,;()\[\]{}]+""")


def _last_part(path: str) -> str:
    return next((p for p in reversed(re.split(r"[\\/]+", path)) if p), "")


def _is_folder(path: Any) -> bool:
    try:
        return isinstance(path, (str, bytes, os.PathLike)) and os.path.isdir(path)
    except (OSError, ValueError):
        return False


def plain_message(text: str) -> str:
    """``text`` without colour codes, a backtrace or the folders of a full path."""
    lines = []
    for line in _ANSI.sub("", text).splitlines():
        if _FRAME.match(line):
            break
        lines.append(line.rstrip())
    text = "\n".join(lines).strip()
    text = _QUOTED_PATH.sub(lambda m: m.group(1) + _last_part(m.group(2)) + m.group(1), text)
    return _BARE_PATH.sub(lambda m: _last_part(m.group(0)), text)


def error_from(exc: BaseException) -> IntegrationError:
    """The engine's exception as an IntegrationError. The engine's messages are already written for
    people, so they are kept, without secrets, backtraces or the folders of a file's full path. An
    IntegrationError is passed on with secrets removed from its message, hint and details too, since
    a handler may have built it from the engine's text."""
    if isinstance(exc, IntegrationError):
        return redacted(exc)
    if isinstance(exc, OSError) and isinstance(exc.filename, (str, bytes, os.PathLike)):
        message = _short_os_message(exc)
    else:
        # str() of a KeyError is the repr of its text, with line breaks as \n, which hides a secret on
        # the next line from the patterns; its own text is what the person should read anyway.
        message = exc.args[0] if isinstance(exc, KeyError) and len(exc.args) == 1 and isinstance(exc.args[0], str) else str(exc)
    # A path the cut below misses (inside other text) still mustn't say whose home folder it is.
    message = user_paths(redact_secrets(plain_message(message or type(exc).__name__) or type(exc).__name__))
    name = type(exc).__name__
    if any(c.__name__ == "DeltaError" for c in type(exc).__mro__):  # deltalake's, whose module is "_internal"
        # A Delta log that can't be read: a commit missing or cut off, a checkpoint that isn't there.
        # A Rust value printed whole (ParsedLogPath { ... }, Error: Os { ... }) is no use to a caller.
        message = re.sub(r"(\s*Error)?:?\s*\w+ \{.*", "", message, flags=re.S).strip()
        if name == "TableNotFoundError":
            return IntegrationError("not_found", message)
        return IntegrationError("failed", f"The Delta table's log can't be read: {message}",
                                hint="The table's _delta_log may be damaged. Open the table in KratosForge to see the problem.")
    if name == "PanicException":  # a native reader (deltalake, arrow) that gave up on a damaged file
        return IntegrationError("failed", "A data file can't be read: it may be cut off or damaged.",
                                hint="Open the project in KratosForge to see which file.")
    if name == "ReceiptError":  # a receipt that was changed after it was written, or isn't one
        return IntegrationError("failed", message)
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
        return IntegrationError("failed", message, details={"reasons": [redact_secrets(str(e)) for e in exc.errors]})
    if isinstance(exc, IsADirectoryError) or (isinstance(exc, PermissionError) and _is_folder(exc.filename)):
        # A folder where a file should be: Linux says "Is a directory", Windows "Permission denied".
        return IntegrationError("failed", message)
    if isinstance(exc, PermissionError):
        return IntegrationError("permission_denied", message)
    if isinstance(exc, (FileNotFoundError, LookupError)):  # LookupError covers KeyError
        # A KeyError's str() is its repr, quoted; a file error's quotes are around the file's name.
        quoted = len(message) > 1 and message[0] == message[-1] and message[0] in "'\""
        return IntegrationError("not_found", message[1:-1] if quoted else message)
    if isinstance(exc, TimeoutError):
        return IntegrationError("timeout", message, retryable=True)
    if isinstance(exc, (ValueError, TypeError)):
        return IntegrationError("invalid_input", message)
    if isinstance(exc, RuntimeError):
        # The engine raises RuntimeError for "something else is running" and for failed work alike.
        busy = any(word in message.lower() for word in ("is running", "is already running", "wait for it", "is using"))
        return IntegrationError("conflict" if busy else "failed", message, retryable=busy)
    if isinstance(exc, OSError):  # a file that is a folder, a folder that is a file, a disk that failed
        return IntegrationError("failed", message)
    return IntegrationError("internal", f"{name}: {message}")


def redacted(err: IntegrationError) -> IntegrationError:
    """``err`` with secrets removed from its message, hint and details (changed in place)."""
    err.message = redact_secrets(err.message)
    err.args = (err.message,)
    err.hint = redact_secrets(err.hint)
    if err.details:
        err.details = _redact(err.details, False) if not too_deep(err.details, MAX_RESULT_DEPTH) else {}
    return err


# ---- arguments

MAX_DEPTH = 64  # lists and objects arguments may nest, the arguments object itself being the first


def too_deep(value: Any, limit: int) -> bool:
    """Whether ``value`` nests lists and objects more than ``limit`` deep. Without recursion, so a
    value nested 100,000 deep is told apart as quickly as any other."""
    stack = [(value, 1)]
    while stack:
        v, level = stack.pop()
        if isinstance(v, dict):
            children: Any = v.values()
        elif isinstance(v, (list, tuple)):
            children = v
        else:
            continue
        if level > limit:
            return True
        stack.extend((c, level + 1) for c in children)
    return False


_TYPES: dict[str, Callable[[Any], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _end_of_text(pattern: str) -> str:
    """JSON Schema's $ (ECMA-262) is the end of the text, but Python's also matches before a final
    line break, so "abc\\n" would pass ^[a-z]+$. Every unescaped $ outside [...] becomes \\Z, so
    ^a$|^b$ is held to the end of the text too."""
    out: list[str] = []
    escaped = in_class = False
    for ch in pattern:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "$":
            out.append(r"\Z")
            continue
        out.append(ch)
    return "".join(out)


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
        if "pattern" in schema and not re.search(_end_of_text(schema["pattern"]), value):
            out.append(f"{where} isn't in the expected form")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):  # NaN passes every bound; JSON has no NaN or Infinity
            return [f"{where} must be a finite number"]
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
    if too_deep(arguments, MAX_DEPTH):
        # Checked first: nothing past here (the schema check, the handler, the audit line) need
        # walk a value nested deep enough to exhaust Python's recursion limit.
        raise IntegrationError("invalid_input", f"The arguments are nested more than {MAX_DEPTH} levels deep.",
                               hint="Send flatter arguments.",
                               details={"problems": [f"arguments are nested more than {MAX_DEPTH} levels deep"]})
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
