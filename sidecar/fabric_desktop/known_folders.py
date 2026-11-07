"""Where Windows keeps the person's Downloads and Documents folders, which isn't always under their profile.

OneDrive's known folder move puts Documents in ``C:\\Users\\<you>\\OneDrive - <Org>\\Documents``, and an
organization's folder redirection can put Downloads or Documents on another drive or a share. Writing an
export to ``%USERPROFILE%\\Downloads`` then puts it in a folder Explorer no longer shows. Windows says
where each folder is through ``SHGetKnownFolderPath``.

The profile folder is looked up the same way. When ``Path.home()`` isn't that folder (a sandbox, a test,
or someone who pointed USERPROFILE elsewhere), the folder under ``Path.home()`` is used instead, so
nothing written there leaks into the real profile. Off Windows it is always the folder under the home.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import uuid
from pathlib import Path

_log = logging.getLogger(__name__)

# KNOWNFOLDERID values (KnownFolders.h).
PROFILE = "{5E6C858F-0E22-4760-9AFE-EA3317B67173}"
DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"
DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
_KF_FLAG_DONT_VERIFY = 0x00004000  # don't create or check the folder: only say where it is


def _lookup(folder_id: str) -> Path | None:
    """Windows' answer for a known folder, or None off Windows or when Windows has none."""
    if sys.platform != "win32":
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(folder_id)
    guid = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    out = ctypes.c_wchar_p()
    try:
        shell32 = ctypes.windll.shell32  # Windows only: mypy (on Linux) skips this after the platform return
        ole32 = ctypes.windll.ole32
        hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), _KF_FLAG_DONT_VERIFY, None, ctypes.byref(out))
        try:
            return Path(out.value) if hr == 0 and out.value else None
        finally:
            ole32.CoTaskMemFree(out)
    except (OSError, AttributeError) as exc:
        _log.info("known folder %s: %s", folder_id, exc)
        return None


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _folder(folder_id: str, name: str) -> Path:
    home = Path.home()
    profile = _lookup(PROFILE)
    if profile is not None and _same(home, profile):
        found = _lookup(folder_id)
        if found is not None:
            return found
    return home / name


def downloads() -> Path:
    """The Downloads folder Explorer shows, which can be redirected away from the profile."""
    return _folder(DOWNLOADS, "Downloads")


def documents() -> Path:
    """The Documents folder Explorer shows: with OneDrive's known folder move, the one in OneDrive."""
    return _folder(DOCUMENTS, "Documents")
