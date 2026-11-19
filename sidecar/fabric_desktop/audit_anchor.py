"""The end of an audit trail, also recorded outside its folder: in the Windows Application event log.

``head.json`` (audit_chain.py) says where a trail ends, but whoever can write the trail's folder can
rewrite it, or delete it, along with the lines. A user who isn't an administrator can add records to
the Application event log but can't change or clear the ones there. So each time the head moves,
``record()`` also writes one event-log record from the source ``KratosForge`` holding:

    the folder's fingerprint (a SHA-256 of its path: never the path or anything from the trail),
    the head hash, and the line count (counting lines a prune deleted, so a prune doesn't shift it)

and ``audit_chain.verify()`` compares the trail with the newest such record for its folder.

Writing is best-effort and quick: off Windows, or when the event log fails, ``record()`` does nothing,
never raises, logs a warning once per process, and leaves the event log alone for a minute before it
tries again, so a failing event log can't slow every audit write. Reading is bounded (the newest
``READ_RECORDS`` records, at most ``READ_SECONDS``).

Limits: anyone on the PC can add a record from this source. One with a higher line count than the trail
makes ``verify()`` report lines deleted from the end that weren't (it only reports; nothing is refused).
Someone who forges the trail can add a matching anchor after it (only the newest one is compared, so a restored backup and a deleted
``head.json``, the documented way to clear a gap, are accepted after the next write). An administrator
can clear the event log, and the Application log overwrites its oldest records when it is full.

The writer and reader are module attributes (``writer``, ``reader``), so tests swap in fakes.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

SOURCE = "KratosForge"
EVENT_ID = 1000
PREFIX = "KratosForge audit trail anchor v1"
READ_RECORDS = 2000
READ_SECONDS = 2.0
RETRY_SECONDS = 60.0

Writer = Callable[[str], None]  # writes one record holding this text
Reader = Callable[[], "Iterable[str] | None"]  # our source's record texts, newest first; None: no event log here


@dataclass(frozen=True)
class Anchor:
    folder: str  # fingerprint
    hash: str
    lines: int


def fingerprint(folder: Path | str) -> str:
    path = os.path.normcase(os.path.abspath(str(folder)))
    return hashlib.sha256(("kratosforge-audit-folder\0" + path).encode("utf-8")).hexdigest()[:32]


def text(anchor: Anchor) -> str:
    return f"{PREFIX}; folder={anchor.folder}; lines={anchor.lines}; hash={anchor.hash}"


def parse(value: str) -> Anchor | None:
    if not isinstance(value, str) or not value.startswith(PREFIX + ";"):
        return None
    fields = dict(part.strip().partition("=")[::2] for part in value[len(PREFIX) + 1:].split(";"))
    try:
        return Anchor(fields["folder"], fields["hash"], int(fields["lines"]))
    except (KeyError, ValueError):
        return None


# ----------------------------------------------------------------------------- the Windows event log

_LOCK = threading.Lock()
_handle: int | None = None


def _last_error() -> int:
    import ctypes
    return int(ctypes.get_last_error())  # type: ignore[attr-defined]  # Windows-only


def _advapi32() -> Any:
    import ctypes
    return ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]  # Windows-only


def _windows_writer(value: str) -> None:
    """One information record in the Application log. ``KratosForge`` isn't a registered event source
    (registering one needs an administrator, and the installer doesn't), so Windows does what it does for
    any unregistered source name: the record goes to the Application log under that name, and Event
    Viewer shows our text after a note that the source's message file can't be found."""
    import ctypes
    from ctypes import wintypes
    global _handle
    advapi32 = _advapi32()
    with _LOCK:
        if _handle is None:
            advapi32.RegisterEventSourceW.restype = wintypes.HANDLE
            advapi32.RegisterEventSourceW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            handle = advapi32.RegisterEventSourceW(None, SOURCE)
            if not handle:
                raise OSError(_last_error(), "RegisterEventSourceW failed")
            _handle = int(handle)
        strings = (wintypes.LPCWSTR * 1)(value)
        advapi32.ReportEventW.argtypes = [wintypes.HANDLE, wintypes.WORD, wintypes.WORD, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.WORD, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.LPCWSTR), ctypes.c_void_p]
        advapi32.ReportEventW.restype = wintypes.BOOL
        if not advapi32.ReportEventW(_handle, 0x0004, 0, EVENT_ID, None, 1, 0, strings, None):  # information
            error = _last_error()
            advapi32.DeregisterEventSource.argtypes = [wintypes.HANDLE]
            advapi32.DeregisterEventSource(_handle)
            _handle = None
            raise OSError(error, "ReportEventW failed")


def _windows_reader() -> Iterable[str]:
    """Our source's record texts in the Application log, newest first: the newest ``READ_RECORDS``
    records of any source, read for at most ``READ_SECONDS``."""
    import ctypes
    from ctypes import wintypes
    advapi32 = _advapi32()
    advapi32.OpenEventLogW.restype = wintypes.HANDLE
    advapi32.OpenEventLogW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    advapi32.ReadEventLogW.restype = wintypes.BOOL
    advapi32.ReadEventLogW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    advapi32.CloseEventLog.argtypes = [wintypes.HANDLE]
    handle = advapi32.OpenEventLogW(None, "Application")
    if not handle:
        raise OSError(_last_error(), "OpenEventLogW failed")
    found: list[str] = []
    try:
        flags = 0x0001 | 0x0008  # EVENTLOG_SEQUENTIAL_READ | EVENTLOG_BACKWARDS_READ
        size, seen, stop = 64 * 1024, 0, time.monotonic() + READ_SECONDS
        buffer = ctypes.create_string_buffer(size)
        while seen < READ_RECORDS and time.monotonic() < stop:
            read, needed = wintypes.DWORD(0), wintypes.DWORD(0)
            if not advapi32.ReadEventLogW(handle, flags, 0, buffer, size, ctypes.byref(read), ctypes.byref(needed)):
                error = _last_error()
                if error == 122 and needed.value > size:  # ERROR_INSUFFICIENT_BUFFER: one record is bigger
                    size = needed.value
                    buffer = ctypes.create_string_buffer(size)
                    continue
                if error == 38:  # ERROR_HANDLE_EOF: the oldest record was read
                    break
                raise OSError(error, "ReadEventLogW failed")
            data, offset = buffer.raw[:read.value], 0
            while offset + 56 <= len(data) and seen < READ_RECORDS:
                seen += 1
                value = _record_text(data, offset)
                if value is not None:
                    found.append(value)
                length = int.from_bytes(data[offset:offset + 4], "little")
                if length <= 0:
                    break
                offset += length
    finally:
        advapi32.CloseEventLog(handle)
    return found


def _record_text(data: bytes, offset: int) -> str | None:
    """The first string of an EVENTLOGRECORD at ``offset``, when it is from our source."""
    length = int.from_bytes(data[offset:offset + 4], "little")
    record = data[offset:offset + length]
    if len(record) < 56:
        return None
    strings = int.from_bytes(record[26:28], "little")  # NumStrings
    at = int.from_bytes(record[36:40], "little")  # StringOffset
    if not strings or not 56 < at < len(record):
        return None
    names = record[56:at - (at - 56) % 2].decode("utf-16-le", "replace")  # SourceName, ComputerName, ...
    if names.split("\0", 1)[0] != SOURCE:
        return None
    rest = record[at:]
    return rest[:len(rest) - len(rest) % 2].decode("utf-16-le", "replace").split("\0", 1)[0]


def _no_writer(value: str) -> None:
    """Off Windows there is no event log: nothing is written."""


def _no_reader() -> None:
    return None


writer: Writer = _windows_writer if sys.platform == "win32" else _no_writer
reader: Reader = _windows_reader if sys.platform == "win32" else _no_reader

_warned = False
_off_until = 0.0


def _warn(what: str, error: BaseException) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("The audit trail's end couldn't be %s the Windows event log (%s); the trail itself is "
                    "unaffected. This is logged once.", what, type(error).__name__)


def record(folder: Path | str, head_hash: str, lines: int) -> None:
    """Write the trail's new end to the event log. Never raises, and does nothing for a minute after a
    failure."""
    global _off_until
    if not head_hash or time.monotonic() < _off_until:
        return
    with _cache_lock:
        _cache.pop(fingerprint(folder), None)
    try:
        writer(text(Anchor(fingerprint(folder), str(head_hash), int(lines))))
    except Exception as error:  # noqa: BLE001 - the event log is extra evidence; the audit write goes on
        _off_until = time.monotonic() + RETRY_SECONDS
        _warn("written to", error)


CACHE_SECONDS = 60.0
# fingerprint -> (the caller's key, when read, (anchor, note)): the audit page checks the trail on every load,
# and reading the event log takes up to READ_SECONDS, so a read is reused while the trail's head is unchanged.
_cache: dict[str, tuple[object, float, tuple[Anchor | None, str]]] = {}
_cache_lock = threading.Lock()


def newest(folder: Path | str, key: object = None) -> tuple[Anchor | None, str]:
    """The newest anchor for ``folder``, and a note when none could be read (empty off Windows). With
    ``key`` (what the trail's head file is now, such as its size and time), a read made for the same key in
    the last CACHE_SECONDS is reused; writing an anchor here for the folder drops it."""
    want = fingerprint(folder)
    if key is not None:
        with _cache_lock:
            hit = _cache.get(want)
        if hit is not None and hit[0] == key and time.monotonic() - hit[1] < CACHE_SECONDS:
            return hit[2]
    found = _read_newest(want)
    if key is not None:
        with _cache_lock:
            _cache[want] = (key, time.monotonic(), found)
    return found


def _read_newest(want: str) -> tuple[Anchor | None, str]:
    try:
        values = reader()
        if values is None:
            return None, ""
        for value in values:
            anchor = parse(value)
            if anchor is not None and anchor.folder == want:
                return anchor, ""
    except Exception as error:  # noqa: BLE001 - an unreadable event log is a note, never a failed check
        _warn("read from", error)
        return None, ("event log: the Windows Application event log couldn't be read, so the end of the trail "
                      "is checked against head.json only")
    return None, ("event log: no record of this trail in the newest Windows Application event log records, "
                  "so the end of the trail is checked against head.json only")
