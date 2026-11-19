"""Secret values a notebook read are shown as ``[REDACTED]`` in its output, as Databricks does.

``dbutils.secrets.get`` and ``getBytes`` remember each value they return, for as long as the kernel
runs. What a cell prints, the outputs it shows (text, HTML, tables as text, errors and their
tracebacks) and the text kept in an overflow file then have every remembered value replaced
before anything leaves the kernel. A value shorter than ``MIN_CHARS`` isn't redacted: replacing a
one or two character value would garble every output, and it is no secret at that length.

The engine's and each kernel process's stderr (sidecar.log, and so the support bundle) go through
``RedactedStream``, so a value a cell's background thread prints, or a log line holding one, is
replaced there too.

Like Databricks, this stops a value being shown by mistake. It is not a wall: code that changes
the value (reverses it, splits it, encodes it) prints what it made.
"""

from __future__ import annotations

import io
import re
import threading
from typing import IO, Any

REDACTED = "[REDACTED]"
MIN_CHARS = 4

_lock = threading.Lock()
_values: set[str] = set()
_pattern: re.Pattern[str] | None = None


def remember(value: Any) -> None:
    """Redact ``value`` (text, or bytes read as UTF-8) from this kernel's output from now on."""
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="ignore")
    if not isinstance(value, str) or len(value) < MIN_CHARS:
        return
    global _pattern
    with _lock:
        if value in _values:
            return
        _values.add(value)
        # Longest first, so a value that holds another is replaced whole.
        _pattern = re.compile("|".join(re.escape(v) for v in sorted(_values, key=len, reverse=True)))


def active() -> bool:
    return _pattern is not None


def longest() -> int:
    """The length of the longest value remembered (0 with none): how much of a text written in parts
    could be the start of one."""
    with _lock:
        return max(map(len, _values), default=0)


def redact(text: str) -> str:
    pattern = _pattern
    if pattern is None or not text:
        return text
    return pattern.sub(REDACTED, text)


def redact_output(value: Any) -> Any:
    """``value`` with every remembered secret replaced in each string inside it (dicts and lists
    are copied, never changed in place). Bytes are left alone: images and other binary data."""
    if _pattern is None:
        return value
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_output(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_output(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_output(v) for v in value)
    return value


def forget_all() -> None:
    """For tests, and a kernel restart starts clean anyway."""
    global _pattern
    with _lock:
        _values.clear()
        _pattern = None


class RedactedStream(io.TextIOBase):
    """A process's stderr, which the shell keeps in sidecar.log: every line passes through
    ``redact.secrets`` and ``redact`` first. Log records already do; this covers what doesn't go through
    them: tracebacks, ``warnings``, third-party loggers' last-resort handler, anything a library prints and
    what a notebook's background thread prints. Writes are held until a newline, so a credential split
    across writes (``print("Bearer", token)``) is redacted whole; ``flush`` writes out a part line, and a
    line past ``HOLD`` characters goes out as it is. Native code writing to the file descriptor directly
    isn't seen here."""

    HOLD = 64 * 1024

    def __init__(self, stream: IO[str]):
        self.stream = stream
        self._part = ""
        self._lock = threading.Lock()

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return getattr(self.stream, "encoding", None) or "utf-8"

    def fileno(self) -> int:
        return self.stream.fileno()

    def isatty(self) -> bool:
        return False

    @staticmethod
    def _clean(text: str) -> str:
        from .redact import secrets
        return redact(secrets(text))

    def write(self, text: str) -> int:
        with self._lock:
            held = self._part + text
            cut = held.rfind("\n") + 1
            if len(held) - cut > self.HOLD:
                cut = len(held)
            self._part = held[cut:]
            if cut:
                self.stream.write(self._clean(held[:cut]))
        return len(text)

    def flush(self) -> None:
        with self._lock:
            part, self._part = self._part, ""
            if part:
                self.stream.write(self._clean(part))
        self.stream.flush()
