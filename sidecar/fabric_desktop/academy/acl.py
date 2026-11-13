"""Read and add one allow ACE on a file or folder, in process (Windows only).

The exercise runtime's capability is granted read and execute on the Python install, path by path.
That used to be one ``icacls /grant`` process per path, about 116 of them, 117 ms each. This does the
same grant through the Win32 security API, and first checks whether the path already holds it.

Rules (Academy sandbox and Security):

- A path counts as already granted only when its DACL holds an *explicit* (not inherited) allow ACE
  for the capability SID whose mask includes read and execute (``RX``) and whose inheritance flags are
  exactly the ones this module would set. A wider ACE that is already there is left alone.
- A grant adds read and execute for that SID only, with ``SetEntriesInAclW`` (``GRANT_ACCESS``), and
  writes only the DACL back. The owner, the SACL and every other SID's entries are not touched, and
  the DACL keeps whatever protection from inheritance it had.
- Every failed call, a read included, raises ``OSError`` naming the file, so the run falls back to the
  v1 learner process. A failed read never counts as "already granted".
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# FILE_GENERIC_READ | FILE_GENERIC_EXECUTE: what icacls writes for (RX).
RX = 0x1200A9
OBJECT_INHERIT_ACE, CONTAINER_INHERIT_ACE = 0x1, 0x2
NO_PROPAGATE_INHERIT_ACE, INHERIT_ONLY_ACE, INHERITED_ACE = 0x4, 0x8, 0x10
ACCESS_ALLOWED_ACE_TYPE = 0x0
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x4
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
SE_DACL_PROTECTED = 0x1000
GRANT_ACCESS = 1
NO_INHERITANCE, SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x0, 0x3
TRUSTEE_IS_SID, TRUSTEE_IS_UNKNOWN = 0, 0
INHERIT_FLAGS = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE | NO_PROPAGATE_INHERIT_ACE | INHERIT_ONLY_ACE

_api_cache: list[Any] = []


def _api() -> Any:
    if _api_cache:
        return _api_cache[0]
    import ctypes
    from ctypes import wintypes

    class AceHeader(ctypes.Structure):
        _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", wintypes.WORD)]

    class AccessAllowedAce(ctypes.Structure):
        _fields_ = [("Header", AceHeader), ("Mask", wintypes.DWORD), ("SidStart", wintypes.DWORD)]

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                    ("AclBytesFree", wintypes.DWORD)]

    class Trustee(ctypes.Structure):
        _fields_ = [("pMultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_int),
                    ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int), ("ptstrName", ctypes.c_void_p)]

    class ExplicitAccess(ctypes.Structure):
        _fields_ = [("grfAccessPermissions", wintypes.DWORD), ("grfAccessMode", ctypes.c_int),
                    ("grfInheritance", wintypes.DWORD), ("Trustee", Trustee)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    P, D = ctypes.c_void_p, wintypes.DWORD
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, D, ctypes.POINTER(P), ctypes.POINTER(P),
                                          ctypes.POINTER(P), ctypes.POINTER(P), ctypes.POINTER(P)]
    adv.GetNamedSecurityInfoW.restype = D
    adv.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, D, P, P, P, P]
    adv.SetNamedSecurityInfoW.restype = D
    adv.SetEntriesInAclW.argtypes = [wintypes.ULONG, ctypes.POINTER(ExplicitAccess), P, ctypes.POINTER(P)]
    adv.SetEntriesInAclW.restype = D
    adv.GetSecurityDescriptorControl.argtypes = [P, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(D)]
    adv.GetAclInformation.argtypes = [P, P, D, ctypes.c_int]
    adv.GetAce.argtypes = [P, D, ctypes.POINTER(P)]
    adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(P)]
    adv.EqualSid.argtypes = [P, P]
    adv.IsValidSid.argtypes = [P]
    k.LocalFree.argtypes = [P]
    k.LocalFree.restype = P
    api = type("AclApi", (), {"ctypes": ctypes, "wt": wintypes, "adv": adv, "k": k,
                              "AccessAllowedAce": AccessAllowedAce, "AclSizeInformation": AclSizeInformation,
                              "Trustee": Trustee, "ExplicitAccess": ExplicitAccess})
    _api_cache.append(api)
    return api


def _long(path: Path) -> str:
    """``path`` in the ``\\\\?\\`` form, so a path over 260 characters (deep in site-packages, say) works
    whether or not long paths are turned on (icacls coped with those)."""
    p = os.path.abspath(str(path))
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def _fail(path: Path, what: str, code: int) -> OSError:
    return OSError(f"Windows wouldn't {what} the permissions on {Path(path).name} (error {code}).")


class _Sid:
    """A SID from its string form, freed on exit."""

    def __init__(self, api: Any, sid_string: str):
        self.api, self.ptr = api, api.ctypes.c_void_p()
        if not api.adv.ConvertStringSidToSidW(sid_string, api.ctypes.byref(self.ptr)):
            raise OSError("Windows wouldn't read the exercise runtime's capability.")

    def __enter__(self) -> Any:
        return self.ptr

    def __exit__(self, *exc: Any) -> None:
        self.api.k.LocalFree(self.ptr)


def _read_dacl(api: Any, path: Path) -> tuple[Any, Any, bool]:
    """``(security descriptor, dacl, protected)``; the caller frees the descriptor with LocalFree."""
    c = api.ctypes
    sd, dacl = c.c_void_p(), c.c_void_p()
    code = api.adv.GetNamedSecurityInfoW(_long(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None,
                                         c.byref(dacl), None, c.byref(sd))
    if code != 0:
        raise _fail(path, "read", code)
    control, revision = api.wt.WORD(), api.wt.DWORD()
    if not api.adv.GetSecurityDescriptorControl(sd, c.byref(control), c.byref(revision)):
        code = c.get_last_error()
        api.k.LocalFree(sd)
        raise _fail(path, "read", code)
    return sd, dacl, bool(control.value & SE_DACL_PROTECTED)


def expected_flags(path: Path, inherit: bool) -> int:
    """The ACE flags a grant on ``path`` writes: object and container inherit on a folder granted with
    inheritance, none otherwise (what ``icacls /grant *SID:(OI)(CI)(RX)`` or ``:(RX)`` writes)."""
    return OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE if inherit and Path(path).is_dir() else 0


def has_grant(path: Path, sid_string: str, inherit: bool) -> bool:
    """Whether ``path``'s DACL holds an explicit allow ACE for ``sid_string`` covering read and execute,
    with exactly the inheritance flags a grant would set. Raises OSError if the DACL can't be read."""
    api, c = _api(), _api().ctypes
    want_flags = expected_flags(path, inherit)
    sd, dacl, _protected = _read_dacl(api, path)
    try:
        if not dacl:   # a NULL DACL: everyone has full access, but it holds no ACE of ours
            return False
        info = api.AclSizeInformation()
        if not api.adv.GetAclInformation(dacl, c.byref(info), c.sizeof(info), 2):   # AclSizeInformation
            raise _fail(path, "read", c.get_last_error())
        with _Sid(api, sid_string) as sid:
            for i in range(info.AceCount):
                ace = c.c_void_p()
                if not api.adv.GetAce(dacl, i, c.byref(ace)):
                    raise _fail(path, "read", c.get_last_error())
                allowed = c.cast(ace, c.POINTER(api.AccessAllowedAce)).contents
                flags = allowed.Header.AceFlags
                if (allowed.Header.AceType != ACCESS_ALLOWED_ACE_TYPE or flags & INHERITED_ACE
                        or flags & INHERIT_FLAGS != want_flags or allowed.Mask & RX != RX):
                    continue
                ace_sid = c.c_void_p(ace.value + api.AccessAllowedAce.SidStart.offset)
                if api.adv.IsValidSid(ace_sid) and api.adv.EqualSid(ace_sid, sid):
                    return True
        return False
    finally:
        api.k.LocalFree(sd)


def grant(path: Path, sid_string: str, inherit: bool) -> None:
    """Add read and execute for ``sid_string`` to ``path``'s DACL, merged with what is there, with
    inheritance on a folder when ``inherit``. Only the DACL is written, keeping its protection."""
    api, c = _api(), _api().ctypes
    sd, dacl, protected = _read_dacl(api, path)
    new_acl = c.c_void_p()
    try:
        with _Sid(api, sid_string) as sid:
            entry = api.ExplicitAccess()
            entry.grfAccessPermissions = RX
            entry.grfAccessMode = GRANT_ACCESS
            entry.grfInheritance = SUB_CONTAINERS_AND_OBJECTS_INHERIT if expected_flags(path, inherit) else NO_INHERITANCE
            entry.Trustee.TrusteeForm = TRUSTEE_IS_SID
            entry.Trustee.TrusteeType = TRUSTEE_IS_UNKNOWN
            entry.Trustee.ptstrName = sid
            code = api.adv.SetEntriesInAclW(1, c.byref(entry), dacl, c.byref(new_acl))
            if code != 0:
                raise _fail(path, "change", code)
            # Keep the DACL's protection from inheritance as it is (without one of these two flags,
            # Windows decides it from its own default, which could turn inheritance back on).
            keep = PROTECTED_DACL_SECURITY_INFORMATION if protected else UNPROTECTED_DACL_SECURITY_INFORMATION
            code = api.adv.SetNamedSecurityInfoW(_long(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | keep,
                                                 None, None, new_acl, None)
            if code != 0:
                raise _fail(path, "change", code)
    finally:
        if new_acl:
            api.k.LocalFree(new_acl)
        api.k.LocalFree(sd)
