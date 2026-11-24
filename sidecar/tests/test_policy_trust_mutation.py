"""policy_trust.py: what mutation testing showed no test checked. The rights are Windows' own bit values, so a
read-only grant to Users never counts as one that could change the policy file, its folder or Group Policy's
key; an inheritance flag other than inherit-only still applies to the object; and advapi32 and kernel32 are
loaded with use_last_error=True."""

from __future__ import annotations

import ctypes
import ctypes.wintypes  # noqa: F401 - loaded so the fake ctypes copy carries it
import sys
import types
from types import SimpleNamespace

import pytest

from fabric_desktop import policy_trust as pt

ALLOWED = pt.ACCESS_ALLOWED
READ_EXECUTE = 0x1200A9  # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE, what Users get under %ProgramData%
KEY_READ = 0x20019       # READ_CONTROL | KEY_QUERY_VALUE | KEY_ENUMERATE_SUB_KEYS | KEY_NOTIFY
OBJECT_INHERIT, CONTAINER_INHERIT = 0x1, 0x2


def test_the_rights_are_windows_bit_values():
    # winnt.h
    assert (pt.FILE_WRITE_DATA, pt.FILE_APPEND_DATA, pt.FILE_DELETE_CHILD) == (0x2, 0x4, 0x40)
    assert (pt.DELETE, pt.WRITE_DAC, pt.WRITE_OWNER) == (0x10000, 0x40000, 0x80000)
    assert (pt.GENERIC_ALL, pt.GENERIC_WRITE) == (0x10000000, 0x40000000)
    assert (pt.KEY_SET_VALUE, pt.KEY_CREATE_SUB_KEY) == (0x2, 0x4)
    assert (pt.ACCESS_ALLOWED, pt.ACCESS_DENIED, pt.INHERIT_ONLY) == (0, 1, 0x8)


@pytest.mark.parametrize("rights", [pt.FILE_RIGHTS, pt.FOLDER_RIGHTS])
def test_users_reading_the_file_or_its_folder_is_fine(rights):
    assert pt.judge("p", True, [(ALLOWED, 0, pt.GENERIC_ALL, True), (ALLOWED, 0, READ_EXECUTE, False)], rights) is None


@pytest.mark.parametrize("rights", [pt.KEY_RIGHTS, pt.PARENT_KEY_RIGHTS])
def test_users_reading_group_policys_key_or_its_parent_is_fine(rights):
    assert pt.judge("k", True, [(ALLOWED, 0, KEY_READ, False)], rights) is None


@pytest.mark.parametrize("mask, rights", [
    (pt.KEY_SET_VALUE, pt.KEY_RIGHTS), (pt.KEY_CREATE_SUB_KEY, pt.KEY_RIGHTS), (pt.KEY_CREATE_SUB_KEY, pt.PARENT_KEY_RIGHTS),
    (pt.FILE_DELETE_CHILD, pt.FOLDER_RIGHTS), (pt.FILE_APPEND_DATA, pt.FILE_RIGHTS),
])
def test_each_write_right_given_to_users_fails(mask, rights):
    assert "someone other than" in pt.judge("k", True, [(ALLOWED, 0, mask, False)], rights)


@pytest.mark.parametrize("flags", [OBJECT_INHERIT, CONTAINER_INHERIT, OBJECT_INHERIT | CONTAINER_INHERIT])
def test_an_inherited_grant_that_also_applies_here_counts(flags):
    assert "someone other than" in pt.judge("d", True, [(ALLOWED, flags, pt.FILE_DELETE_CHILD, False)], pt.FOLDER_RIGHTS)
    assert pt.judge("d", True, [(ALLOWED, flags | pt.INHERIT_ONLY, pt.FILE_DELETE_CHILD, False)], pt.FOLDER_RIGHTS) is None


def test_the_dlls_are_loaded_with_use_last_error(monkeypatch):
    loaded = []

    class Fn:
        def __init__(self, body):
            self.body = body

        def __call__(self, *a):
            return self.body(*a)

    def get_info(path, kind, flags, owner, group, dacl, sacl, descriptor):
        return 5  # ERROR_ACCESS_DENIED: nothing more is read

    advapi = SimpleNamespace(GetNamedSecurityInfoW=Fn(get_info), IsWellKnownSid=Fn(lambda *a: 0), GetAce=Fn(lambda *a: 0))
    kernel = SimpleNamespace(LocalFree=Fn(lambda d: None))
    fake = types.ModuleType("ctypes")
    fake.__dict__.update(ctypes.__dict__)

    def win_dll(name, use_last_error=False):
        loaded.append((name, use_last_error))
        return {"advapi32": advapi, "kernel32": kernel}[name]
    fake.WinDLL = win_dll
    monkeypatch.setitem(sys.modules, "ctypes", fake)
    monkeypatch.setattr(pt, "sys", SimpleNamespace(platform="win32"))
    assert pt._windows_check("f", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS) == "its permissions can't be read (5)"
    assert loaded == [("advapi32", True), ("kernel32", True)]


def test_every_ace_type_windows_defines_past_allowed_and_denied_grants_nothing_or_is_refused():
    # 2 audit, 3 alarm, 4 compound (not trusted), 5 object allowed (not trusted), 6/7/8 object deny/audit/alarm,
    # 9 callback allowed (not trusted), 10 callback deny, 11 callback object allowed (not trusted), 12-21 system.
    assert pt.GRANTS_NOTHING == {2, 3, 6, 7, 8, 10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21}
    assert pt.LAST_ACE_TYPE == 21
