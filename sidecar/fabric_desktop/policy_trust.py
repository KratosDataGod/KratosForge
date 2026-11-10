"""Whether the policy file can be trusted with settings that widen what KratosForge trusts
(app_settings.LOOSENING), by the same test as the shell's policy_trust.rs.

Everyone may create folders under %ProgramData%, so on a PC whose IT pushes no policy, one person could
create %ProgramData%\\FabricDesktop\\policy.json themselves and leave it for the next person to sign in:
its creator owns it, and the next person may only read it. So a file is trusted only when it and its
folder are owned by SYSTEM or Administrators (as an Intune, Group Policy or elevated copy leaves them)
and nobody else is granted a right that could change or replace the file. Off Windows, only root may
own and write the file and its folder. Anything that can't be checked is not trusted.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterable
from pathlib import Path

FILE_WRITE_DATA, FILE_APPEND_DATA, FILE_DELETE_CHILD = 0x2, 0x4, 0x40
DELETE, WRITE_DAC, WRITE_OWNER = 0x1_0000, 0x4_0000, 0x8_0000
GENERIC_ALL, GENERIC_WRITE = 0x1000_0000, 0x4000_0000
# Rights that let someone change or replace the file itself.
FILE_RIGHTS = FILE_WRITE_DATA | FILE_APPEND_DATA | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_WRITE | GENERIC_ALL
# Rights on the folder that let someone delete or replace a file in it, or the folder. Creating new
# files isn't one: %ProgramData% lets everyone do that in folders under it.
FOLDER_RIGHTS = FILE_DELETE_CHILD | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_ALL
# The same for Group Policy's key: set or add values, add subkeys, delete it or take over its permissions;
# on its parent, add the key or take the parent over.
KEY_SET_VALUE, KEY_CREATE_SUB_KEY = 0x2, 0x4
KEY_RIGHTS = KEY_SET_VALUE | KEY_CREATE_SUB_KEY | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_WRITE | GENERIC_ALL
PARENT_KEY_RIGHTS = KEY_CREATE_SUB_KEY | WRITE_DAC | WRITE_OWNER | GENERIC_WRITE | GENERIC_ALL
SE_FILE_OBJECT, SE_REGISTRY_WOW64_64KEY = 1, 12  # the 64-bit view, which Group Policy writes
ACCESS_ALLOWED, ACCESS_DENIED, INHERIT_ONLY = 0, 1, 0x08
GRANTS_NOTHING = {2, 3, 6, 7, 8, 10, 12, 13, 14, 15, 16, 17}  # deny, audit and alarm entries, the mandatory label


def admin_only(file: Path) -> str | None:
    """None when only SYSTEM and Administrators (root off Windows) could have written ``file``;
    otherwise why not."""
    try:
        if sys.platform == "win32":
            return (_windows_check(str(file.parent), SE_FILE_OBJECT, FOLDER_RIGHTS)
                    or _windows_check(str(file), SE_FILE_OBJECT, FILE_RIGHTS))
        return _posix_check(file.parent) or _posix_check(file)
    except Exception as exc:  # noqa: BLE001 - not checked means not trusted
        return f"its permissions can't be read ({type(exc).__name__})"


def key_admin_only(key: str, subkeys: Iterable[str] = ()) -> str | None:
    """None when only SYSTEM and Administrators could have written HKLM\\``key`` (owner and permissions,
    not whether this process could open it for writing, so an elevated process is judged like any
    other), its parent, and its list ``subkeys``; otherwise why not. Off Windows there is no key."""
    if sys.platform != "win32":
        return "it is only checked on Windows"
    try:
        parent = key.rsplit("\\", 1)[0]
        return (_windows_check(f"MACHINE\\{parent}", SE_REGISTRY_WOW64_64KEY, PARENT_KEY_RIGHTS)
                or _windows_check(f"MACHINE\\{key}", SE_REGISTRY_WOW64_64KEY, KEY_RIGHTS)
                or next((why for sub in subkeys
                         if (why := _windows_check(f"MACHINE\\{key}\\{sub}", SE_REGISTRY_WOW64_64KEY, KEY_RIGHTS))), None))
    except Exception as exc:  # noqa: BLE001 - not checked means not trusted
        return f"its permissions can't be read ({type(exc).__name__})"


def _posix_check(path: Path) -> str | None:
    info = os.stat(path)
    if info.st_uid != 0:
        return f"{path} isn't owned by root"
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f"someone other than root can change {path}"
    return None


def judge(path: str, owner_is_admin: bool, dacl: Iterable[tuple[int, int, int, bool]] | None, rights: int) -> str | None:
    """The shell's rule on one path's owner and DACL entries ((type, flags, mask, sid is SYSTEM or
    Administrators)): None when it passes. A deny entry, an entry for what is created inside a folder
    only, and audit, alarm and label entries grant nothing; an entry of another kind isn't trusted."""
    if not owner_is_admin:
        return f"{path} isn't owned by SYSTEM or Administrators"
    if dacl is None:
        return f"{path} has no permissions set, so anyone can change it"
    for kind, flags, mask, admin in dacl:
        if kind == ACCESS_DENIED or kind > 17 or flags & INHERIT_ONLY or kind in GRANTS_NOTHING:
            continue
        if kind != ACCESS_ALLOWED:
            return f"{path} has a permission entry the app can't check"
        if mask & rights and not admin:
            return f"someone other than SYSTEM and Administrators can change {path}"
    return None


def _windows_check(path: str, kind: int, rights: int) -> str | None:
    if sys.platform != "win32":  # admin_only() calls this only on Windows; mypy (on Linux) skips the rest
        return "it is only checked on Windows"
    import ctypes
    from ctypes import wintypes

    class ACL(ctypes.Structure):
        _fields_ = [("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte), ("AclSize", ctypes.c_ushort),
                    ("AceCount", ctypes.c_ushort), ("Sbz2", ctypes.c_ushort)]

    class ACE(ctypes.Structure):  # ACE_HEADER, then an ACCESS_ALLOWED_ACE's Mask; its SID follows at offset 8
        _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", ctypes.c_ushort),
                    ("Mask", ctypes.c_uint32)]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = advapi.GetNamedSecurityInfoW
    get_info.restype = wintypes.DWORD
    get_info.argtypes = (wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                         ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                         ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p))
    well_known = advapi.IsWellKnownSid
    well_known.restype, well_known.argtypes = wintypes.BOOL, (ctypes.c_void_p, ctypes.c_int)
    get_ace = advapi.GetAce
    get_ace.restype = wintypes.BOOL
    get_ace.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))
    kernel.LocalFree.restype, kernel.LocalFree.argtypes = ctypes.c_void_p, (ctypes.c_void_p,)

    def admin(sid: int | None) -> bool:  # WinLocalSystemSid 22, WinBuiltinAdministratorsSid 26
        return bool(sid) and (bool(well_known(sid, 22)) or bool(well_known(sid, 26)))

    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    status = get_info(path, kind, 0x1 | 0x4, ctypes.byref(owner), None, ctypes.byref(dacl), None,
                      ctypes.byref(descriptor))  # OWNER | DACL_SECURITY_INFORMATION
    if status != 0:
        return f"its permissions can't be read ({status})"
    try:
        entries: list[tuple[int, int, int, bool]] | None = None
        if dacl.value:
            entries = []
            for index in range(ACL.from_address(dacl.value).AceCount):
                ace = ctypes.c_void_p()
                if not get_ace(dacl, index, ctypes.byref(ace)) or not ace.value:
                    return f"{path}'s permissions can't be read"
                head = ACE.from_address(ace.value)
                allowed = head.AceType == ACCESS_ALLOWED
                entries.append((head.AceType, head.AceFlags, head.Mask if allowed else 0,
                                admin(ace.value + 8) if allowed else False))
        return judge(path, admin(owner.value), entries, rights)
    finally:
        kernel.LocalFree(descriptor)
