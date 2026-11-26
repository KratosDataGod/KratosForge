"""Secret values a notebook read are shown as ``[REDACTED]`` in its output, as Databricks does.

``dbutils.secrets.get`` and ``getBytes`` remember each value they return, for as long as the kernel
runs. What a cell prints, the outputs it shows (text, HTML, tables as text, errors and their
tracebacks) and the text kept in an overflow file then have every remembered value replaced
before anything leaves the kernel. A value shorter than ``MIN_CHARS`` isn't redacted: replacing a
one or two character value would garble every output, and it is no secret at that length.

Values are remembered in the process that read them, so the kernel process is where they are
redacted. Its stderr, which reaches sidecar.log, goes through ``RedactedStream``, so a value a cell's
background thread prints, or a log line holding one, is replaced before the engine keeps it. The
engine's own stderr goes through it too, but the engine knows only the values a kernel running
inside it read.

A value is also redacted as its repr, ``ascii()`` and its UTF-8 bytes' repr print it (escaped), as
JSON writes it (``\\u`` escapes or not) and percent-encoded as in a URL (either hex case). In the
log and in receipt exports, values that overlap are replaced together. Like Databricks, this stops
a value being shown by mistake. It is not a wall: code that otherwise changes the value (reverses
it, splits it, encodes it another way) prints what it made.
"""

from __future__ import annotations

import io
import json
import re
import threading
from typing import IO, Any
from urllib.parse import quote

REDACTED = "[REDACTED]"
MIN_CHARS = 4

_lock = threading.Lock()
_values: set[str] = set()
_pattern: re.Pattern[str] | None = None
_starts: frozenset[str] = frozenset()  # every start, but not the whole, of a form remembered
_JSON_U = re.compile(r"\\u[0-9a-f]{4}")
_PERCENT = re.compile(r"%[0-9A-F]{2}")


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
    global _pattern, _starts
    with _lock:
        if forms <= _values:
            return
        _values.update(forms)
        _starts = _starts | {f[:n] for f in forms for n in range(1, len(f))}
        # Longest first, so a value that holds another is replaced whole.
        _pattern = re.compile("|".join(re.escape(v) for v in sorted(_values, key=len, reverse=True)))


def active() -> bool:
    return _pattern is not None


def partial_tail(text: str) -> int:
    """The length of the longest end of ``text`` that is the start, but not the whole, of a value
    remembered: how much of it must wait for the next write before it can be redacted."""
    starts = _starts
    if not starts:
        return 0
    for n in range(min(len(text), longest() - 1), 0, -1):
        if text[-n:] in starts:
            return n
    return 0


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
    with _lock:
        return max(map(len, _values), default=0)


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
    """For tests, and a kernel restart starts clean anyway."""
    global _pattern, _starts
    with _lock:
        _values.clear()
        _starts = frozenset()
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
