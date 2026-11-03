"""Generic credentials in Windows Credential Manager: write, read and remove one by its target name.

Used for secrets the app keeps for the signed-in Windows account and never in its settings, logs or
backups, such as the company mail server's password (workloads/deliver.py). Errors name the
credential, never its secret. Off Windows there is no Credential Manager: reads find nothing and
writes are refused.
"""

from __future__ import annotations

import sys
from typing import Any

from .engines.databricks_auth import CRED_MAX_BLOB, credman_text

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2  # this Windows account on this PC; never roams to other PCs
ERROR_NOT_FOUND = 1168


class CredentialError(RuntimeError):
    pass


def _api() -> tuple[Any, Any]:
    import ctypes
    from ctypes import wintypes

    class Credential(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    pcred = ctypes.POINTER(Credential)
    advapi.CredWriteW.argtypes, advapi.CredWriteW.restype = [pcred, wintypes.DWORD], wintypes.BOOL
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pcred)]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredDeleteW.argtypes, advapi.CredDeleteW.restype = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD], wintypes.BOOL
    advapi.CredFree.argtypes, advapi.CredFree.restype = [ctypes.c_void_p], None
    return advapi, Credential


def write(target: str, user: str, secret: str) -> None:
    """Save ``secret`` (UTF-16, as Credential Manager's own editor does) under ``target``, replacing one there."""
    if sys.platform != "win32":
        raise CredentialError("Passwords are kept in Windows Credential Manager, so they can only be saved on Windows.")
    import ctypes

    blob = secret.encode("utf-16-le")
    if not secret or len(blob) > CRED_MAX_BLOB:
        raise CredentialError(f"A password for {target!r} is 1 to {CRED_MAX_BLOB // 2} characters.")
    advapi, Credential = _api()
    buf = ctypes.create_string_buffer(blob, len(blob))
    cred = Credential(Flags=0, Type=CRED_TYPE_GENERIC, TargetName=target, Comment="Saved by KratosForge",
                      CredentialBlobSize=len(blob), CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
                      Persist=CRED_PERSIST_LOCAL_MACHINE, AttributeCount=0, Attributes=None, TargetAlias=None,
                      UserName=user or None)
    if not advapi.CredWriteW(ctypes.byref(cred), 0):
        raise CredentialError(f"Windows Credential Manager didn't save {target!r} (error {ctypes.get_last_error()}).")


def read(target: str) -> str | None:
    """The secret saved under ``target``, or None when there is none."""
    if sys.platform != "win32":
        return None
    import ctypes

    advapi, Credential = _api()
    found = ctypes.POINTER(Credential)()
    if not advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return None
        raise CredentialError(f"Windows Credential Manager didn't read {target!r} (error {ctypes.get_last_error()}).")
    try:
        size = int(found.contents.CredentialBlobSize)
        if size > CRED_MAX_BLOB:
            raise CredentialError(f"The credential {target!r} is larger than Windows allows, so it wasn't read.")
        blob = ctypes.string_at(found.contents.CredentialBlob, size) if size else b""
    finally:
        advapi.CredFree(found)
    try:
        return credman_text(blob, target)
    except Exception as exc:  # noqa: BLE001 - its own message names the credential, never the secret
        raise CredentialError(str(exc)) from None


def delete(target: str) -> None:
    """Remove the credential under ``target``; nothing there is fine."""
    if sys.platform != "win32":
        return
    import ctypes

    advapi, _ = _api()
    if not advapi.CredDeleteW(target, CRED_TYPE_GENERIC, 0) and ctypes.get_last_error() != ERROR_NOT_FOUND:
        raise CredentialError(f"Windows Credential Manager didn't remove {target!r} (error {ctypes.get_last_error()}).")
