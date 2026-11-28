"""Secret values a notebook read are shown as ``[REDACTED]`` in its output, as Databricks does.

``dbutils.secrets.get`` and ``getBytes`` remember each value they return, for as long as the kernel
runs. What a cell prints, the outputs it shows (text, HTML, tables as text, errors and their
tracebacks) and the text kept in an overflow file then have every remembered value replaced
before anything leaves the kernel. A value shorter than ``MIN_CHARS`` isn't redacted: replacing a
one or two character value would garble every output, and it is no secret at that length.

Values are remembered in the process that read them, so the kernel process is where they are
redacted. Its stderr, which reaches sidecar.log, goes through ``RedactedStream``, so a value a cell's
background thread prints, or a log line holding one, is replaced before the engine keeps it. A
kernel in a process of its own (kernel_host.py) also passes each value it remembers to the engine,
over the pipe between them only (``forward_to``, ``wire`` and ``remember_wire``), so the engine's
stderr and its receipt exports replace it too. The value is never written to a file or a log.

A value is also redacted as its repr, ``ascii()`` and its UTF-8 bytes' repr print it (escaped), as
JSON writes it (``\\u`` escapes or not) and percent-encoded as in a URL (either hex case). In the
log and in receipt exports, values that overlap are replaced together. Like Databricks, this stops
a value being shown by mistake. It is not a wall: code that otherwise changes the value (reverses
it, splits it, encodes it another way) prints what it made.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
import threading
from typing import IO, Any, Callable
from urllib.parse import quote

REDACTED = "[REDACTED]"
MIN_CHARS = 4

_lock = threading.Lock()
_values: set[str] = set()
_pattern: re.Pattern[str] | None = None
# Each start (not the whole) of a form remembered, kept as a hash with its length, not as text: a
# long value has as many starts as characters, and storing each would grow with its length squared.
_starts: set[int] = set()
_longest = 0
_powers: list[int] = [1]
_MOD = (1 << 61) - 1
_BASE = 1_000_003
_JSON_U = re.compile(r"\\u[0-9a-f]{4}")
_PERCENT = re.compile(r"%[0-9A-F]{2}")
# Set in a kernel's own process (kernel_host.main): passes each newly remembered value to the engine.
_forward: Callable[[Any], None] | None = None


def _text_forms(value: str) -> set[str]:
    """How the text ``value`` can appear in printed text: as itself, as the inside of its repr, its
    ``ascii()`` and its UTF-8 bytes' repr, as a JSON string's inside (``\\u`` escapes, either case,
    and without them) and percent-encoded as in a URL (either hex case)."""
    forms = {value, repr(value)[1:-1], ascii(value)[1:-1],
             repr(value.encode("utf-8", errors="surrogatepass"))[2:-1]}
    for ensure_ascii in (True, False):
        escaped = json.dumps(value, ensure_ascii=ensure_ascii)[1:-1]
        forms |= {escaped, _JSON_U.sub(lambda m: m.group(0)[:2] + m.group(0)[2:].upper(), escaped)}
    for safe in ("", "/"):
        quoted = quote(value, safe=safe, errors="surrogatepass")
        forms |= {quoted, _PERCENT.sub(lambda m: m.group(0).lower(), quoted)}
    return forms


def _forms(value: Any) -> set[str]:
    """How ``value`` can appear in printed text: as itself, escaped as its repr, ``ascii()`` and its
    UTF-8 bytes' repr print it (``print(b)`` and ``repr(s)`` escape what isn't printable ASCII), and
    encoded as JSON and URLs write it."""
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
        text = data.decode("utf-8", errors="ignore")
        forms = {repr(data)[2:-1]} | _text_forms(text)
    elif isinstance(value, str):
        forms = _text_forms(value)
    else:
        return set()
    return {f for f in forms if len(f) >= MIN_CHARS}


def remember(value: Any) -> None:
    """Redact ``value`` (text or bytes) from this kernel's output from now on, in each form printing
    it can take."""
    forms = _forms(value)
    if not forms:
        return
    global _pattern, _longest
    with _lock:
        forms -= _values
        if not forms:
            return
        _values.update(forms)
        for form in forms:
            h = 0
            for n, ch in enumerate(form[:-1], 1):
                h = (h * _BASE + ord(ch)) % _MOD
                _starts.add(_key(n, h))
        _longest = max(_longest, *map(len, forms))
        while len(_powers) < _longest:
            _powers.append(_powers[-1] * _BASE % _MOD)
        # Longest first, so a value that holds another is replaced whole.
        _pattern = re.compile("|".join(re.escape(v) for v in sorted(_values, key=len, reverse=True)))
    forward = _forward
    if forward is not None:
        # Before remember returns, so the engine has the value before any output that follows it.
        try:
            forward(value)
        except Exception:  # noqa: BLE001, S110 - an engine that is gone has nothing left to mask
            pass


def forward_to(send: Callable[[Any], None] | None) -> None:
    """From now on, pass each value ``remember`` learns (not one it knew) to ``send``: a kernel's own
    process passes it to the engine this way. None stops it."""
    global _forward
    _forward = send


def wire(value: Any) -> dict[str, str] | None:
    """``value`` (text or bytes) as a JSON object for the pipe to the engine; None for anything else."""
    if isinstance(value, str):
        return {"text": value}
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": base64.b64encode(bytes(value)).decode("ascii")}
    return None


WIRE_VALUES = 1000  # values the engine takes from kernels' processes; more are dropped
WIRE_CHARS = 64 * 1024  # the longest value taken that way (characters, or bytes)
WIRE_LINE = 8 * WIRE_CHARS  # the longest protocol line read as one, before it is decoded
_wired = 0


def remember_wire(message: Any) -> None:
    """Remember the value a kernel's process sent (``wire``), with the same rules as ``remember``.
    Anything malformed, a value longer than ``WIRE_CHARS`` and every value after ``WIRE_VALUES`` are
    dropped without a word: nothing about them is logged or raised."""
    global _wired
    if not isinstance(message, dict) or _wired >= WIRE_VALUES:
        return
    text, data = message.get("text"), message.get("bytes")
    value: str | bytes
    if isinstance(text, str):
        value = text
    elif isinstance(data, str) and len(data) <= 4 * (WIRE_CHARS // 3 + 1):
        try:
            value = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            return
    else:
        return
    if len(value) > WIRE_CHARS or not _forms(value):
        return
    with _lock:
        _wired += 1
    remember(value)


def active() -> bool:
    return _pattern is not None


def partial_tail(text: str) -> int:
    """The length of the longest end of ``text`` that is the start, but not the whole, of a value
    remembered: how much of it must wait for the next write before it can be redacted."""
    if not _starts:
        return 0
    starts, powers, values = _starts, _powers, _values
    found, h = [], 0
    for n in range(1, min(len(text), _longest - 1) + 1):
        h = (ord(text[-n]) * powers[n - 1] + h) % _MOD
        if _key(n, h) in starts:
            found.append(n)
    if not found:
        return 0
    with _lock:
        forms = list(values)
    for n in reversed(found):  # a hash can match by chance, so the text itself is checked
        tail = text[-n:]
        if any(len(f) > n and f.startswith(tail) for f in forms):
            return n
    return 0


def _key(n: int, h: int) -> int:
    """One number for a start's length and hash."""
    return n * _MOD + h


def _spans(text: str, pattern: re.Pattern[str]) -> list[tuple[int, int]]:
    """Where remembered values are in ``text``, as (start, end) pairs in order: values that overlap
    or one inside another make one span, so each is covered whole."""
    spans = []
    at = 0
    while (m := pattern.search(text, at)) is not None:
        start, end = m.span()
        i = start + 1
        while i < end:  # a value that starts inside this one and runs past it
            n = pattern.match(text, i)
            if n is not None and n.end() > end:
                end = n.end()
            i += 1
        spans.append((start, end))
        at = end
    return spans


def redact_all(text: str) -> str:
    """``text`` with every remembered value replaced, as ``redact`` does, and values that overlap
    replaced together, so no part of either shows."""
    pattern = _pattern
    if pattern is None or not text:
        return text
    out, at = [], 0
    for start, end in _spans(text, pattern):
        out += [text[at:start], REDACTED]
        at = end
    out.append(text[at:])
    return "".join(out)


def safe_cut(text: str, cut: int) -> int:
    """Where ``text`` can be split for writing out its start now (at ``cut`` or before), so that no
    remembered value is split: not inside one that is there whole, nor after the start of one that
    the next write could finish."""
    pattern = _pattern
    if pattern is None:
        return cut
    cut = min(cut, len(text) - partial_tail(text))
    for start, end in _spans(text[:cut + longest()], pattern):
        if start >= cut:
            break
        if end > cut:
            return start
    return cut


def longest() -> int:
    """The length of the longest value remembered (0 with none): how much of a text written in parts
    could be the start of one."""
    return _longest


def redact(text: str) -> str:
    pattern = _pattern
    if pattern is None or not text:
        return text
    return pattern.sub(REDACTED, text)


def redact_values(text: str, values: Any) -> str:
    """``text`` with each of ``values`` (in each form printing it can take) replaced, as ``redact``
    does for the values remembered: for values kept elsewhere, such as the ones getSecret read."""
    forms = sorted({f for v in values for f in _forms(v)}, key=len, reverse=True)
    for form in forms:
        text = text.replace(form, REDACTED)
    return text


def mask_cut(text: str, values: Any) -> str:
    """``text`` cut short with "..." (a preview) whose cut fell inside one of ``values``: the start
    of that value before the cut is replaced too, when MIN_CHARS or more of it would show."""
    at = text.rfind("...")
    if at < 0:
        return text
    head, tail = text[:at], text[at:]
    for form in sorted({f for v in values for f in _forms(v)}, key=len, reverse=True):
        for k in range(len(form) - 1, MIN_CHARS - 1, -1):
            if head.endswith(form[:k]):
                return head[:-k] + REDACTED + tail
    return text


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
    """For tests, and a kernel restart starts clean anyway. The engine keeps the values a kernel's
    process passed it for as long as it runs."""
    global _pattern, _longest, _wired
    with _lock:
        _wired = 0
        _values.clear()
        _starts.clear()
        _longest = 0
        _pattern = None


class RedactedStream(io.TextIOBase):
    """A process's stderr, which the shell keeps in sidecar.log: every line passes through
    ``redact.secrets`` and ``redact`` first. Log records already do; this covers what doesn't go through
    them: tracebacks, ``warnings``, third-party loggers' last-resort handler, anything a library prints and
    what a notebook's background thread prints. Writes are held until a newline, so a credential split
    across writes (``print("Bearer", token)``) is redacted whole; ``flush`` writes out a part line, and a
    line past ``HOLD`` characters goes out in parts. A remembered value is never split between parts: one
    there whole goes out replaced, and the end that could start or continue one waits for the next write.
    Native code writing to the file descriptor directly isn't seen here."""

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
        return secrets(redact_all(text))

    def write(self, text: str) -> int:
        with self._lock:
            held = self._part + text
            cut = held.rfind("\n") + 1
            if len(held) - cut > self.HOLD:
                cut = len(held)
            # No remembered value is split: one there whole goes out replaced, and what could be the
            # start of one waits for the rest of it, even past HOLD or a newline (a value can hold one).
            if cut and active():
                cut = safe_cut(held, cut)
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

    def close(self) -> None:
        """Writes out what is held; the stream underneath stays open (it is the process's stderr)."""
        if not self.closed:
            self.flush()
        super().close()
