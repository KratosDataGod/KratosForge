"""policy_trust.py on Windows, faked: admin_only's Windows branch and _windows_check's reading of an
owner and DACL through GetNamedSecurityInfoW / GetAce / IsWellKnownSid, with real ACL and ACE bytes
in memory and a fake advapi32 and kernel32 (a ctypes copy with WinDLL, in sys.modules)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes  # noqa: F401 - loaded so the fake ctypes copy carries it
import sys
import types
from types import SimpleNamespace

import pytest

from fabric_desktop import policy_trust as pt

SYSTEM, ADMINS, USERS = 22, 26, 0


class Fn:
    """A DLL function: restype and argtypes can be set, and a call runs ``body``."""

    def __init__(self, body):
        self.body, self.restype, self.argtypes = body, None, None

    def __call__(self, *args):
        return self.body(*args)


class Box:
    """A security descriptor in memory: a SID per name, an ACL header and its ACEs."""

    def __init__(self, owner, aces, *, null_dacl=False, status=0, ace_fails_at=None):
        self.keep = []
        self.sids = {}  # address -> well-known SID type
        self.owner = self.sid(owner) if owner is not None else None
        self.status, self.ace_fails_at = status, ace_fails_at
        self.aces = []
        for kind, flags, mask, who in aces:
            raw = (ctypes.c_ubyte * 16)()
            self.keep.append(raw)
            raw[0], raw[1] = kind, flags
            ctypes.c_uint32.from_address(ctypes.addressof(raw) + 4).value = mask
            self.sids[ctypes.addressof(raw) + 8] = who
            self.aces.append(ctypes.addressof(raw))
        self.dacl = None
        if not null_dacl:
            acl = (ctypes.c_ubyte * 8)()
            self.keep.append(acl)
            ctypes.c_ushort.from_address(ctypes.addressof(acl) + 4).value = len(aces)
            self.dacl = ctypes.addressof(acl)
        self.freed, self.calls = [], []

    def sid(self, who):
        raw = (ctypes.c_ubyte * 8)()
        self.keep.append(raw)
        self.sids[ctypes.addressof(raw)] = who
        return ctypes.addressof(raw)

    def get_info(self, path, kind, flags, owner, group, dacl, sacl, descriptor):
        self.calls.append((path, kind, flags))
        assert group is None and sacl is None and flags == 0x1 | 0x4
        owner._obj.value, dacl._obj.value, descriptor._obj.value = self.owner, self.dacl, 0xD00D
        return self.status

    def get_ace(self, dacl, index, out):
        assert dacl.value == self.dacl
        if index == self.ace_fails_at:
            return 0
        out._obj.value = self.aces[index]
        return 1

    def well_known(self, sid, kind):
        return int(self.sids.get(sid) == kind)

    def install(self, monkeypatch):
        advapi = SimpleNamespace(GetNamedSecurityInfoW=Fn(self.get_info), IsWellKnownSid=Fn(self.well_known),
                                 GetAce=Fn(self.get_ace))
        kernel = SimpleNamespace(LocalFree=Fn(lambda d: self.freed.append(d.value)))
        fake = types.ModuleType("ctypes")
        fake.__dict__.update(ctypes.__dict__)
        fake.WinDLL = lambda name, use_last_error=False: {"advapi32": advapi, "kernel32": kernel}[name]
        monkeypatch.setitem(sys.modules, "ctypes", fake)
        monkeypatch.setattr(pt, "sys", SimpleNamespace(platform="win32"))
        return self


def test_an_admin_owned_file_whose_dacl_grants_writes_only_to_admins_passes(monkeypatch):
    box = Box(ADMINS, [(pt.ACCESS_ALLOWED, 0, pt.GENERIC_ALL, SYSTEM),
                       (pt.ACCESS_ALLOWED, 0, 0x1200A9, USERS),            # Users: read and execute
                       (pt.ACCESS_DENIED, 0, pt.GENERIC_ALL, USERS)]).install(monkeypatch)
    assert pt._windows_check(r"C:\ProgramData\FabricDesktop\policy.json", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS) is None
    assert box.calls == [(r"C:\ProgramData\FabricDesktop\policy.json", pt.SE_FILE_OBJECT, 0x5)]
    assert box.freed == [0xD00D]  # the descriptor is always freed


def test_a_write_granted_to_users_fails(monkeypatch):
    box = Box(SYSTEM, [(pt.ACCESS_ALLOWED, 0, pt.FILE_WRITE_DATA, USERS)]).install(monkeypatch)
    assert pt._windows_check("f", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS) == \
        "someone other than SYSTEM and Administrators can change f"
    assert box.freed == [0xD00D]


def test_a_denied_entrys_mask_and_sid_are_not_read_as_a_grant(monkeypatch):
    # Only an allowed entry's mask and SID count: a deny entry for Users with every right passes.
    Box(ADMINS, [(pt.ACCESS_DENIED, 0, pt.GENERIC_ALL, USERS)]).install(monkeypatch)
    assert pt._windows_check("f", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS) is None


@pytest.mark.parametrize("owner", [USERS, None])
def test_an_owner_other_than_system_or_admins_fails(monkeypatch, owner):
    Box(owner, []).install(monkeypatch)
    assert pt._windows_check("f", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS) == "f isn't owned by SYSTEM or Administrators"


def test_a_null_dacl_lets_anyone_change_it(monkeypatch):
    Box(ADMINS, [], null_dacl=True).install(monkeypatch)
    assert "has no permissions set" in pt._windows_check("f", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS)


def test_an_unreadable_descriptor_is_not_trusted_and_nothing_is_freed(monkeypatch):
    box = Box(ADMINS, [], status=5).install(monkeypatch)  # ERROR_ACCESS_DENIED
    assert pt._windows_check("f", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS) == "its permissions can't be read (5)"
    assert box.freed == []


def test_an_entry_getace_cant_read_is_not_trusted(monkeypatch):
    box = Box(ADMINS, [(pt.ACCESS_ALLOWED, 0, pt.GENERIC_ALL, SYSTEM),
                       (pt.ACCESS_ALLOWED, 0, 0, USERS)], ace_fails_at=1).install(monkeypatch)
    assert pt._windows_check("f", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS) == "f's permissions can't be read"
    assert box.freed == [0xD00D]


def test_an_entry_getace_returns_as_null_is_not_trusted(monkeypatch):
    box = Box(ADMINS, [(pt.ACCESS_ALLOWED, 0, pt.GENERIC_ALL, SYSTEM)]).install(monkeypatch)
    box.aces[0] = None
    assert pt._windows_check("f", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS) == "f's permissions can't be read"


def test_admin_only_checks_the_folder_then_the_file_on_windows(monkeypatch, tmp_path):
    seen = []
    verdicts = {}
    monkeypatch.setattr(pt, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(pt, "_windows_check", lambda p, k, r: seen.append((p, k, r)) or verdicts.get(p))
    file = tmp_path / "policy.json"
    assert pt.admin_only(file) is None
    assert seen == [(str(tmp_path), pt.SE_FILE_OBJECT, pt.FOLDER_RIGHTS), (str(file), pt.SE_FILE_OBJECT, pt.FILE_RIGHTS)]
    verdicts[str(tmp_path)] = "folder is open"
    seen.clear()
    assert pt.admin_only(file) == "folder is open"
    assert len(seen) == 1  # the file isn't checked once its folder fails
    verdicts.pop(str(tmp_path))
    verdicts[str(file)] = "file is open"
    assert pt.admin_only(file) == "file is open"


def test_admin_only_end_to_end_with_the_fake_dlls(monkeypatch, tmp_path):
    Box(ADMINS, [(pt.ACCESS_ALLOWED, 0, pt.GENERIC_ALL, ADMINS)]).install(monkeypatch)
    assert pt.admin_only(tmp_path / "policy.json") is None


def test_off_windows_the_windows_check_says_so(monkeypatch):
    monkeypatch.setattr(pt, "sys", SimpleNamespace(platform="linux"))
    assert pt._windows_check("f", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS) == "it is only checked on Windows"
