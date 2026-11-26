"""policy_trust's Windows calls, driven through a stand-in advapi32 and kernel32 so every branch of the
owner and DACL read runs on any OS: a descriptor that can't be read, a missing DACL, entries that can't be
fetched, allowed entries for administrators and for others, deny and inherit-only entries, an unknown entry
type, the folder checked before the file, Group Policy's key, and the descriptor freed every time."""

from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

from fabric_desktop import policy_trust as pt

SYSTEM, ADMINS, USER = 22, 26, 0  # WinLocalSystemSid, WinBuiltinAdministratorsSid, someone else


class Security:
    """The parts of advapi32 and kernel32 policy_trust calls. ``objects`` maps a path to its owner (a SID
    kind, or None for no owner), its DACL (a list of (type, flags, mask, SID kind) entries, or None for
    no DACL) and, optionally, a GetNamedSecurityInfoW status. ``bad_ace`` makes GetAce fail ("fail") or
    hand back nothing ("null") at that index."""

    def __init__(self, objects, *, bad_ace=None):
        self.objects, self.bad_ace = objects, bad_ace
        self.keep: list = []  # the buffers handed out, alive until the test ends
        self.sids: dict[int, int] = {}
        self.aces: dict[int, list[int]] = {}
        self.asked: list[tuple[str, int, int]] = []
        self.freed: list[int] = []
        self.loaded: list[tuple[str, bool]] = []

    def _buffer(self, size: int) -> ctypes.Array:
        buf = ctypes.create_string_buffer(size)
        self.keep.append(buf)
        return buf

    def _sid(self, kind: int) -> int:
        buf = self._buffer(16)
        self.sids[ctypes.addressof(buf)] = kind
        return ctypes.addressof(buf)

    def GetNamedSecurityInfoW(self, path, kind, info, owner, group, dacl, sacl, descriptor):
        self.asked.append((path, kind, info))
        assert group is None and sacl is None
        spec = self.objects[path]
        owner_kind, entries = spec[0], spec[1]
        status = spec[2] if len(spec) > 2 else 0
        if status:
            return status
        if owner_kind is not None:
            owner._obj.value = self._sid(owner_kind)
        if entries is not None:
            acl = self._buffer(8)
            acl[4:6] = len(entries).to_bytes(2, "little")  # AceCount
            addresses = []
            for ace_type, flags, mask, sid_kind in entries:
                ace = self._buffer(24)
                ace[0], ace[1] = bytes([ace_type]), bytes([flags])
                ace[2:4] = (24).to_bytes(2, "little")
                ace[4:8] = mask.to_bytes(4, "little")
                self.sids[ctypes.addressof(ace) + 8] = sid_kind
                addresses.append(ctypes.addressof(ace))
            self.aces[ctypes.addressof(acl)] = addresses
            dacl._obj.value = ctypes.addressof(acl)
        descriptor._obj.value = ctypes.addressof(self._buffer(8))
        return 0

    def IsWellKnownSid(self, sid, kind):
        return int(self.sids.get(sid) == kind)

    def GetAce(self, dacl, index, ace):
        if self.bad_ace == ("fail", index):
            return 0
        if self.bad_ace != ("null", index):
            ace._obj.value = self.aces[dacl.value][index]
        return 1

    def LocalFree(self, pointer):
        self.freed.append(pointer)
        return None

    def dll(self, name, use_last_error=False):
        """ctypes.WinDLL: plain functions, so restype and argtypes can be set on them."""
        self.loaded.append((name, use_last_error))
        names = {"advapi32": ("GetNamedSecurityInfoW", "IsWellKnownSid", "GetAce"), "kernel32": ("LocalFree",)}[name]
        lib = type(name, (), {})()
        for fn in names:
            method = getattr(self, fn)

            def call(*args, _m=method):
                return _m(*args)
            setattr(lib, fn, call)
        return lib


@pytest.fixture
def windows(monkeypatch):
    """Runs policy_trust as on Windows with the given stand-in; returns a function that installs one."""
    monkeypatch.setattr(pt.sys, "platform", "win32")

    def install(objects, **kw) -> Security:
        sec = Security(objects, **kw)
        monkeypatch.setattr(ctypes, "WinDLL", sec.dll, raising=False)
        return sec
    return install


ALLOWED, DENIED, AUDIT = pt.ACCESS_ALLOWED, pt.ACCESS_DENIED, 2
READ = 0x1_20089  # FILE_GENERIC_READ: no right that changes anything
ADMIN_FULL = [(ALLOWED, 0, pt.GENERIC_ALL, SYSTEM), (ALLOWED, 0, pt.GENERIC_ALL, ADMINS), (ALLOWED, 0, READ, USER)]


def _file(tmp_path: Path) -> tuple[Path, str, str]:
    file = tmp_path / "FabricDesktop" / "policy.json"
    return file, str(file.parent), str(file)


def test_an_admin_owned_folder_and_file_others_only_read_are_trusted(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    sec = windows({folder: (ADMINS, ADMIN_FULL), path: (SYSTEM, ADMIN_FULL)})
    assert pt.admin_only(file) is None
    # The folder first, then the file; owner and DACL asked for, as file objects.
    assert sec.asked == [(folder, pt.SE_FILE_OBJECT, 0x5), (path, pt.SE_FILE_OBJECT, 0x5)]
    assert len(sec.freed) == 2 and all(sec.freed)
    assert ("advapi32", True) in sec.loaded and ("kernel32", True) in sec.loaded


def test_a_folder_someone_else_owns_is_refused_before_the_file_is_read(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    sec = windows({folder: (USER, ADMIN_FULL), path: (SYSTEM, ADMIN_FULL)})
    assert pt.admin_only(file) == f"{folder} isn't owned by SYSTEM or Administrators"
    assert [a[0] for a in sec.asked] == [folder]


def test_a_file_with_no_owner_isnt_trusted(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    windows({folder: (ADMINS, ADMIN_FULL), path: (None, ADMIN_FULL)})
    assert pt.admin_only(file) == f"{path} isn't owned by SYSTEM or Administrators"


def test_a_file_anyone_may_write_to_isnt_trusted(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    windows({folder: (ADMINS, ADMIN_FULL), path: (ADMINS, [*ADMIN_FULL, (ALLOWED, 0, pt.FILE_APPEND_DATA, USER)])})
    assert pt.admin_only(file) == f"someone other than SYSTEM and Administrators can change {path}"


def test_someone_else_who_may_delete_files_in_the_folder_makes_it_untrusted(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    windows({folder: (SYSTEM, [(ALLOWED, 0, pt.FILE_DELETE_CHILD, USER)]), path: (SYSTEM, ADMIN_FULL)})
    assert pt.admin_only(file) == f"someone other than SYSTEM and Administrators can change {folder}"


def test_creating_files_in_the_folder_is_allowed_for_everyone(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    windows({folder: (SYSTEM, [(ALLOWED, 0, pt.FILE_WRITE_DATA, USER)]), path: (SYSTEM, ADMIN_FULL)})
    assert pt.admin_only(file) is None


def test_deny_inherit_only_and_audit_entries_for_others_grant_nothing(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    others = [(DENIED, 0, pt.GENERIC_ALL, USER), (ALLOWED, pt.INHERIT_ONLY, pt.GENERIC_ALL, USER),
              (AUDIT, 0, pt.GENERIC_ALL, USER)]
    windows({folder: (ADMINS, ADMIN_FULL), path: (ADMINS, ADMIN_FULL + others)})
    assert pt.admin_only(file) is None


def test_an_entry_type_windows_doesnt_define_isnt_trusted(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    windows({folder: (ADMINS, ADMIN_FULL), path: (ADMINS, [(pt.LAST_ACE_TYPE + 1, 0, 0, ADMINS)])})
    assert pt.admin_only(file) == f"{path} has a permission entry the app can't check"


def test_an_object_entry_for_administrators_still_isnt_checked(windows, tmp_path):
    """An allowed-object entry (type 5) isn't read, so its SID and mask aren't trusted even for admins."""
    file, folder, path = _file(tmp_path)
    windows({folder: (ADMINS, ADMIN_FULL), path: (ADMINS, [(5, 0, pt.GENERIC_ALL, ADMINS)])})
    assert pt.admin_only(file) == f"{path} has a permission entry the app can't check"


def test_no_dacl_means_anyone_can_change_it(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    sec = windows({folder: (ADMINS, ADMIN_FULL), path: (ADMINS, None)})
    assert pt.admin_only(file) == f"{path} has no permissions set, so anyone can change it"
    assert len(sec.freed) == 2


def test_an_empty_dacl_grants_nobody_anything(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    windows({folder: (ADMINS, []), path: (ADMINS, [])})
    assert pt.admin_only(file) is None


def test_a_descriptor_that_cant_be_read_isnt_trusted_and_nothing_is_freed(windows, tmp_path):
    file, folder, path = _file(tmp_path)
    sec = windows({folder: (ADMINS, ADMIN_FULL), path: (ADMINS, ADMIN_FULL, 5)})
    assert pt.admin_only(file) == "its permissions can't be read (5)"
    assert len(sec.freed) == 1  # the folder's; the file's was never handed out


@pytest.mark.parametrize("how", ["fail", "null"])
def test_an_entry_that_cant_be_fetched_isnt_trusted_and_the_descriptor_is_freed(windows, tmp_path, how):
    file, folder, path = _file(tmp_path)
    sec = windows({folder: (ADMINS, ADMIN_FULL), path: (ADMINS, ADMIN_FULL)}, bad_ace=(how, 1))
    assert pt.admin_only(file) == f"{folder}'s permissions can't be read"
    assert len(sec.freed) == 1 and sec.freed[0]


def test_a_failing_windows_call_isnt_trusted(monkeypatch, tmp_path):
    monkeypatch.setattr(pt.sys, "platform", "win32")

    def no_dll(name, use_last_error=False):
        raise OSError("advapi32 isn't here")
    monkeypatch.setattr(ctypes, "WinDLL", no_dll, raising=False)
    assert pt.admin_only(tmp_path / "policy.json") == "its permissions can't be read (OSError)"
    assert pt.key_admin_only(r"Software\Policies\KratosBI\KratosForge") == "its permissions can't be read (OSError)"


KEY = r"Software\Policies\KratosBI\KratosForge"
PARENT = r"Software\Policies\KratosBI"


def test_group_policys_key_read_through_the_64_bit_registry_view(windows):
    full = [(ALLOWED, 0, pt.GENERIC_ALL, SYSTEM), (ALLOWED, 0, pt.GENERIC_ALL, ADMINS)]
    sec = windows({f"MACHINE\\{PARENT}": (ADMINS, full), f"MACHINE\\{KEY}": (SYSTEM, full),
                   f"MACHINE\\{KEY}\\allowed_tenants": (SYSTEM, full)})
    assert pt.key_admin_only(KEY, ["allowed_tenants"]) is None
    assert sec.asked == [(f"MACHINE\\{PARENT}", pt.SE_REGISTRY_WOW64_64KEY, 0x5),
                         (f"MACHINE\\{KEY}", pt.SE_REGISTRY_WOW64_64KEY, 0x5),
                         (f"MACHINE\\{KEY}\\allowed_tenants", pt.SE_REGISTRY_WOW64_64KEY, 0x5)]


def test_a_list_subkey_others_may_add_values_to_isnt_trusted(windows):
    full = [(ALLOWED, 0, pt.GENERIC_ALL, ADMINS)]
    sub = f"MACHINE\\{KEY}\\allowed_tenants"
    windows({f"MACHINE\\{PARENT}": (ADMINS, full), f"MACHINE\\{KEY}": (ADMINS, full),
             sub: (ADMINS, [*full, (ALLOWED, 0, pt.KEY_SET_VALUE, USER)])})
    assert pt.key_admin_only(KEY, ["allowed_tenants"]) == f"someone other than SYSTEM and Administrators can change {sub}"


def test_setting_values_on_the_parent_isnt_a_parent_right(windows):
    """Only adding the key, or taking the parent over, counts on the parent."""
    full = [(ALLOWED, 0, pt.GENERIC_ALL, ADMINS)]
    windows({f"MACHINE\\{PARENT}": (ADMINS, [*full, (ALLOWED, 0, pt.KEY_SET_VALUE, USER)]),
             f"MACHINE\\{KEY}": (ADMINS, full)})
    assert pt.key_admin_only(KEY) is None
    windows({f"MACHINE\\{PARENT}": (ADMINS, [*full, (ALLOWED, 0, pt.KEY_CREATE_SUB_KEY, USER)]),
             f"MACHINE\\{KEY}": (ADMINS, full)})
    assert pt.key_admin_only(KEY) == f"someone other than SYSTEM and Administrators can change MACHINE\\{PARENT}"


def test_off_windows_the_windows_check_refuses_outright(monkeypatch):
    monkeypatch.setattr(pt.sys, "platform", "linux")
    assert pt._windows_check("C:\\x", pt.SE_FILE_OBJECT, pt.FILE_RIGHTS) == "it is only checked on Windows"

