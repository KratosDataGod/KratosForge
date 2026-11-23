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


# ---- OneDrive (Chris, 2026-11-20): files the app stores or processes stay on this PC, out of OneDrive, so
# OneDrive isn't uploading the same files the app is publishing to Fabric.

_ONEDRIVE_ENV = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")


def _registry_onedrive() -> list[Path]:
    """Each signed-in OneDrive account's folder (HKCU\\Software\\Microsoft\\OneDrive\\Accounts\\*\\UserFolder);
    a business account can have one per organization. None off Windows."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    found: list[Path] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts") as accounts:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(accounts, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(accounts, name) as key:
                        value, _kind = winreg.QueryValueEx(key, "UserFolder")
                except OSError:
                    continue
                if isinstance(value, str) and value.strip() and Path(value.strip()).is_absolute():
                    found.append(Path(value.strip()))  # a relative one would resolve against the start folder
    except OSError:  # OneDrive was never set up for this user
        return []
    return found


def onedrive_roots() -> list[Path]:
    """The OneDrive folders on this PC: OneDrive's own environment variables, and each account's folder."""
    roots: list[Path] = []
    for name in _ONEDRIVE_ENV:
        value = (os.environ.get(name) or "").strip()
        if value and Path(value).is_absolute():
            roots.append(Path(value))
    roots += _registry_onedrive()
    unique: list[Path] = []
    for root in roots:
        if not any(_same(root, u) for u in unique):
            unique.append(root)
    return unique


def _key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def onedrive_root(path: str | Path) -> Path | None:
    """The OneDrive folder ``path`` is in, or None. Besides the folders onedrive_roots names, a folder of the
    profile named ``OneDrive`` or ``OneDrive - <Org>`` counts (OneDrive's own names for them), so a moved
    Documents is caught even when OneDrive isn't running."""
    target = _key(Path(path))
    for root in onedrive_roots():
        base = _key(root)
        if target == base or target.startswith(base.rstrip(os.sep) + os.sep):
            return root
    home = _key(Path.home())
    if target.startswith(home.rstrip(os.sep) + os.sep):
        first = Path(os.path.abspath(path)).relative_to(os.path.abspath(Path.home())).parts[:1]
        if first and (first[0].lower() == "onedrive" or first[0].lower().startswith("onedrive - ")):
            return Path.home() / first[0]
    return None


def in_onedrive(path: str | Path) -> bool:
    return onedrive_root(path) is not None


def local_folder() -> Path:
    """Where the app puts folders of the person's own that it makes (a new metadata project, exports when
    Downloads is in OneDrive): ``KratosForge`` in the profile, which OneDrive's known folder move doesn't move."""
    return Path.home() / "KratosForge"


def export_folder() -> Path:
    """Where exports are written: Downloads, unless Windows has moved it into OneDrive; then KratosForge\\Exports
    in the profile, so an export isn't uploaded to OneDrive."""
    found = downloads()
    if not in_onedrive(found):
        return found
    folder = local_folder() / "Exports"
    folder.mkdir(parents=True, exist_ok=True)  # Downloads always exists; this one may not yet
    return folder


def onedrive_refusal(path: str | Path, what: str) -> str | None:
    """Why ``what`` can't be kept at ``path`` (it is in OneDrive), or None."""
    root = onedrive_root(path)
    if root is None:
        return None
    return (f"{path} is in OneDrive ({root}), so {what} can't be kept there. OneDrive would upload the same files "
            f"the app reads and writes, and its sync can lock them. Pick a folder on this PC outside OneDrive, "
            f"such as {local_folder()}.")
