"""Which program has a file open, when Windows refuses to replace it, and saying so plainly.

A save or export swaps a finished file into place (jsonfile.replace), which waits out a brief hold by a
virus scanner, the indexer or OneDrive. When a program keeps the file open (a CSV open in Excel), Windows
still refuses after the wait, with "The process cannot access the file because it is being used by
another process" (WinError 32) or "Access is denied" (5), and neither says which program. Windows'
Restart Manager does: ``holders`` asks it for the programs that have the file open, by name, and
``explain`` turns the refusal into a message that names them and the file.

It only reads: a Restart Manager session lists programs and is ended at once; nothing is closed or
restarted. Off Windows, or when Restart Manager can't say, there are no names and the message says
"another program". A leaf module: it imports nothing from the package.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

# ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_LOCKED_WINERRORS = frozenset({5, 32, 33})
_ERROR_MORE_DATA = 234
_CCH_RM_SESSION_KEY = 32
_CCH_RM_MAX_APP_NAME = 255
_CCH_RM_MAX_SVC_NAME = 63
_MAX_NAMED = 3
_ERROR_ACCESS_DENIED = 5
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800  # load the DLL from System32 only, never the app folder


def holders(path: str | os.PathLike[str]) -> list[str]:
    """The names of the programs that have ``path`` open, as Windows shows them (Excel's is "Microsoft
    Excel"), each once; empty off Windows or when Windows can't say."""
    if sys.platform != "win32":
        return []
    try:
        return _restart_manager_list(str(path))
    except (OSError, AttributeError, ValueError) as exc:
        _log.info("Couldn't ask Windows which program has %s open: %s", path, exc)
        return []


def _restart_manager_list(path: str) -> list[str]:
    if sys.platform != "win32":  # holders checks too; this lets mypy (on Linux) skip what follows
        return []
    from ctypes import wintypes

    class RM_UNIQUE_PROCESS(ctypes.Structure):  # noqa: N801 - Windows' name
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):  # noqa: N801 - Windows' name
        _fields_ = [("Process", RM_UNIQUE_PROCESS),
                    ("strAppName", ctypes.c_wchar * (_CCH_RM_MAX_APP_NAME + 1)),
                    ("strServiceShortName", ctypes.c_wchar * (_CCH_RM_MAX_SVC_NAME + 1)),
                    ("ApplicationType", ctypes.c_int),
                    ("AppStatus", wintypes.ULONG),
                    ("TSSessionId", wintypes.DWORD),
                    ("bRestartable", wintypes.BOOL)]

    rm = ctypes.WinDLL("rstrtmgr.dll", winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(_CCH_RM_SESSION_KEY + 1)
    err = rm.RmStartSession(ctypes.byref(session), 0, key)
    if err:
        raise OSError(err, "RmStartSession failed")
    try:
        files = (wintypes.LPCWSTR * 1)(path)
        err = rm.RmRegisterResources(session, 1, files, 0, None, 0, None)
        if err:
            raise OSError(err, "RmRegisterResources failed")
        needed, count, reasons = wintypes.UINT(0), wintypes.UINT(0), wintypes.DWORD()
        infos = (RM_PROCESS_INFO * 1)()
        for _ in range(3):  # the list can grow between asking for its size and reading it
            count = wintypes.UINT(len(infos))
            err = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons))
            if err != _ERROR_MORE_DATA:
                break
            infos = (RM_PROCESS_INFO * max(needed.value, 1))()
        if err:
            raise OSError(err, "RmGetList failed")
        names: list[str] = []
        for info in infos[:count.value]:
            name = info.strAppName.strip() or f"process {info.Process.dwProcessId}"
            if name not in names:
                names.append(name)
        return names
    finally:
        rm.RmEndSession(session)


def is_locked(exc: BaseException) -> bool:
    """Whether ``exc`` is Windows refusing a file because a program has it open."""
    return isinstance(exc, PermissionError) and getattr(exc, "winerror", None) in _LOCKED_WINERRORS


def explain(exc: BaseException, path: str | os.PathLike[str], doing: str = "saved") -> str | None:
    """A plain message for a file Windows wouldn't let this app replace because a program has it open,
    naming the file and, where Windows says, the program; None for any other failure."""
    if not is_locked(exc):
        return None
    name = Path(path).name
    found = holders(path)
    if len(found) == 1:
        return f"{name} couldn't be {doing} because it is open in {found[0]}. Close it there and try again."
    if found:
        named = ", ".join(found[:_MAX_NAMED]) + (f" and {len(found) - _MAX_NAMED} more" if len(found) > _MAX_NAMED
                                                 else "")
        return f"{name} couldn't be {doing} because it is open in {named}. Close it in each of them and try again."
    if getattr(exc, "winerror", None) == _ERROR_ACCESS_DENIED:
        # "Access is denied" also comes from a read-only file or a folder this account can't change.
        return (f"{name} couldn't be {doing}. Another program may have it open, or this account can't change it. "
                "Close the file if it is open, check that it isn't read-only, and try again.")
    return (f"{name} couldn't be {doing} because another program has it open, such as Excel. Close the file "
            "there, or wait a moment, and try again.")


def locked_error(exc: OSError, path: str | os.PathLike[str], doing: str = "saved") -> PermissionError | None:
    """``explain``'s message as the error to raise: its text is only the message (no "[WinError 32]" and no
    path after it), and it keeps ``errno`` and ``winerror`` for code that checks them, and the path as
    ``path``. None for any other failure."""
    plain = explain(exc, path, doing)
    if plain is None:
        return None
    err = PermissionError(plain)
    err.errno = exc.errno
    err.winerror = getattr(exc, "winerror", None)  # type: ignore[attr-defined]  # set on Windows' errors only
    err.path = os.fspath(path)  # type: ignore[attr-defined]  # the file, for logs; not in the text
    return err
