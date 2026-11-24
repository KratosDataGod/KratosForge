"""Mutation batch 411: academy/acl.py against a stand-in for advapi32 and kernel32, so the DACL reading and
granting logic runs on every platform. The stand-in fills the out-pointers and structures the way the Win32
calls do; ``test_academy_acl.py`` checks the real API on Windows."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fabric_desktop.academy import acl

RX = 0x1200A9   # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE, as icacls writes (RX)


class AceHeader(ctypes.Structure):
    _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", wintypes.WORD)]


class AccessAllowedAce(ctypes.Structure):
    _fields_ = [("Header", AceHeader), ("Mask", wintypes.DWORD), ("SidStart", wintypes.DWORD)]


class AclSizeInformation(ctypes.Structure):
    _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD), ("AclBytesFree", wintypes.DWORD)]


class Trustee(ctypes.Structure):
    _fields_ = [("pMultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_int),
                ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int), ("ptstrName", ctypes.c_void_p)]


class ExplicitAccess(ctypes.Structure):
    _fields_ = [("grfAccessPermissions", wintypes.DWORD), ("grfAccessMode", ctypes.c_int),
                ("grfInheritance", wintypes.DWORD), ("Trustee", Trustee)]


class _Ctypes:
    """The real ctypes, with a last-error the stand-in sets (ctypes has none off Windows)."""

    def __init__(self) -> None:
        self.last_error = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(ctypes, name)

    def get_last_error(self) -> int:
        return self.last_error


def _out(ref: Any) -> Any:
    return ref._obj


class Fake:
    """advapi32 and kernel32 for one file's DACL: a list of ACEs ``(type, flags, mask, sid)``."""

    def __init__(self, aces: list[tuple[int, int, int, str]] | None = None, *, null_dacl: bool = False,
                 protected: bool = False, fail: dict[str, int] | None = None) -> None:
        self.c = _Ctypes()
        self.fail = fail or {}
        self.null_dacl = null_dacl
        self.protected = protected
        self.sid_ids: dict[str, int] = {}
        self._keep: list[Any] = []
        self.aces = [self._ace(*a) for a in aces or []]
        self.freed: list[int] = []
        self.calls: list[tuple] = []
        self.sd = ctypes.c_uint32(0x5D)
        self.dacl = ctypes.c_uint32(0xDAC1)
        self.new_acl = ctypes.c_uint32(0x9E4)
        self.adv = SimpleNamespace(GetNamedSecurityInfoW=self.get_info, GetSecurityDescriptorControl=self.control,
                                   GetAclInformation=self.acl_info, GetAce=self.get_ace,
                                   ConvertStringSidToSidW=self.to_sid, IsValidSid=lambda p: True,
                                   EqualSid=self.equal_sid, SetEntriesInAclW=self.set_entries,
                                   SetNamedSecurityInfoW=self.set_info)
        self.k = SimpleNamespace(LocalFree=self.local_free)
        self.api = SimpleNamespace(ctypes=self.c, wt=wintypes, adv=self.adv, k=self.k,
                                   AccessAllowedAce=AccessAllowedAce, AclSizeInformation=AclSizeInformation,
                                   Trustee=Trustee, ExplicitAccess=ExplicitAccess)

    def _sid(self, text: str) -> int:
        return self.sid_ids.setdefault(text, 1000 + len(self.sid_ids))

    def _ace(self, typ: int, flags: int, mask: int, sid: str) -> AccessAllowedAce:
        ace = AccessAllowedAce()
        ace.Header.AceType, ace.Header.AceFlags, ace.Mask, ace.SidStart = typ, flags, mask, self._sid(sid)
        return ace

    def _addr(self, obj: Any) -> int:
        return ctypes.addressof(obj)

    # advapi32
    def get_info(self, path, kind, what, owner, group, dacl, sacl, sd) -> int:
        self.calls.append(("get", path, kind, what))
        if "get" in self.fail:
            return self.fail["get"]
        _out(dacl).value = None if self.null_dacl else self._addr(self.dacl)
        _out(sd).value = self._addr(self.sd)
        return 0

    def control(self, sd, control, revision) -> int:
        if "control" in self.fail:
            self.c.last_error = self.fail["control"]
            return 0
        _out(control).value = 0x1000 if self.protected else 0x4
        return 1

    def acl_info(self, dacl, info, size, cls) -> int:
        self.calls.append(("acl_info", cls, size))
        if "acl_info" in self.fail:
            self.c.last_error = self.fail["acl_info"]
            return 0
        _out(info).AceCount = len(self.aces)
        return 1

    def get_ace(self, dacl, i, ace) -> int:
        if "get_ace" in self.fail:
            self.c.last_error = self.fail["get_ace"]
            return 0
        _out(ace).value = self._addr(self.aces[i])
        return 1

    def to_sid(self, text, ptr) -> int:
        if "sid" in self.fail:
            return 0
        box = ctypes.c_uint32(self._sid(text))
        self._keep.append(box)
        _out(ptr).value = self._addr(box)
        return 1

    def equal_sid(self, a, b) -> bool:
        value = lambda p: ctypes.c_uint32.from_address(p.value if hasattr(p, "value") else p).value  # noqa: E731
        return value(a) == value(b)

    def set_entries(self, count, entry, old, new) -> int:
        e = _out(entry)
        self.calls.append(("entries", count, e.grfAccessPermissions, e.grfAccessMode, e.grfInheritance,
                           e.Trustee.TrusteeForm, e.Trustee.TrusteeType, old.value if hasattr(old, "value") else old))
        if "entries" in self.fail:
            return self.fail["entries"]
        _out(new).value = self._addr(self.new_acl)
        return 0

    def set_info(self, path, kind, what, owner, group, dacl, sacl) -> int:
        self.calls.append(("set", path, kind, what, owner, group, dacl.value if hasattr(dacl, "value") else dacl, sacl))
        return self.fail.get("set", 0)

    # kernel32
    def local_free(self, p) -> None:
        self.freed.append(p.value if hasattr(p, "value") else p)


@pytest.fixture
def install(monkeypatch):
    def put(fake: Fake) -> Fake:
        monkeypatch.setattr(acl, "_api_cache", [fake.api])
        return fake
    return put


SID = "S-1-15-2-1"
OTHER = "S-1-15-2-2"


def test_the_constants_are_the_win32_values() -> None:
    assert acl.RX == RX
    assert (acl.OBJECT_INHERIT_ACE, acl.CONTAINER_INHERIT_ACE) == (1, 2)
    assert (acl.NO_PROPAGATE_INHERIT_ACE, acl.INHERIT_ONLY_ACE, acl.INHERITED_ACE) == (4, 8, 0x10)
    assert acl.INHERIT_FLAGS == 0xF and acl.ACCESS_ALLOWED_ACE_TYPE == 0 and acl.SE_FILE_OBJECT == 1
    assert acl.DACL_SECURITY_INFORMATION == 4 and acl.SE_DACL_PROTECTED == 0x1000 and acl.GRANT_ACCESS == 1
    assert acl.PROTECTED_DACL_SECURITY_INFORMATION == 0x80000000
    assert acl.UNPROTECTED_DACL_SECURITY_INFORMATION == 0x20000000
    assert (acl.NO_INHERITANCE, acl.SUB_CONTAINERS_AND_OBJECTS_INHERIT) == (0, 3)
    assert (acl.TRUSTEE_IS_SID, acl.TRUSTEE_IS_UNKNOWN) == (0, 0)


def test_the_cached_api_is_returned_without_loading_windows(monkeypatch) -> None:
    marker = object()
    monkeypatch.setattr(acl, "_api_cache", [marker])
    assert acl._api() is marker


@pytest.mark.parametrize("raw, want", [
    ("\\\\?\\C:\\x", "\\\\?\\C:\\x"),
    ("\\\\server\\share\\x", "\\\\?\\UNC\\server\\share\\x"),
])
def test_long_paths_keep_their_prefix_or_gain_one(monkeypatch, raw, want) -> None:
    monkeypatch.setattr(acl.os.path, "abspath", lambda p: p)
    assert acl._long(raw) == want   # type: ignore[arg-type]


def test_a_plain_path_gains_the_long_prefix(monkeypatch) -> None:
    monkeypatch.setattr(acl.os.path, "abspath", lambda p: "C:\\Python\\Lib")
    assert acl._long(Path("x")) == "\\\\?\\C:\\Python\\Lib"


def test_a_failure_names_the_file_and_the_code() -> None:
    err = acl._fail(Path("/a/b/python.exe"), "read", 5)
    assert isinstance(err, OSError) and str(err) == "Windows wouldn't read the permissions on python.exe (error 5)."


def test_expected_flags_inherit_only_on_a_folder(tmp_path) -> None:
    (tmp_path / "f").write_text("x")
    assert acl.expected_flags(tmp_path, True) == 3
    assert acl.expected_flags(tmp_path, False) == 0
    assert acl.expected_flags(tmp_path / "f", True) == 0


def test_an_explicit_matching_ace_is_a_grant(install, tmp_path) -> None:
    fake = install(Fake([(0, 0, RX, OTHER), (0, 3, RX, SID)]))
    assert acl.has_grant(tmp_path, SID, True) is True
    assert fake.calls[0][2:] == (1, 4)
    assert ("acl_info", 2, ctypes.sizeof(AclSizeInformation)) in fake.calls
    assert ctypes.addressof(fake.sd) in fake.freed


def test_a_file_grant_needs_no_inheritance_flags(install, tmp_path) -> None:
    f = tmp_path / "python.exe"
    f.write_text("x")
    install(Fake([(0, 0, RX, SID)]))
    assert acl.has_grant(f, SID, True) is True
    install(Fake([(0, 3, RX, SID)]))
    assert acl.has_grant(f, SID, True) is False


@pytest.mark.parametrize("ace", [
    (1, 3, RX, SID),           # a deny ACE
    (0, 3 | 0x10, RX, SID),    # inherited
    (0, 1, RX, SID),           # object inherit only
    (0, 3 | 4, RX, SID),       # no-propagate as well
    (0, 3 | 8, RX, SID),       # inherit-only as well
    (0, 3, RX & ~0x20, SID),   # read without execute
    (0, 3, RX & ~0x1, SID),    # missing one read bit
    (0, 3, RX, OTHER),         # someone else's
])
def test_anything_short_of_an_exact_grant_is_not_one(install, tmp_path, ace) -> None:
    install(Fake([ace]))
    assert acl.has_grant(tmp_path, SID, True) is False


def test_a_wider_mask_still_counts(install, tmp_path) -> None:
    install(Fake([(0, 3, 0x1F01FF, SID)]))
    assert acl.has_grant(tmp_path, SID, True) is True


def test_a_null_dacl_holds_no_grant(install, tmp_path) -> None:
    fake = install(Fake([(0, 3, RX, SID)], null_dacl=True))
    assert acl.has_grant(tmp_path, SID, True) is False
    assert ctypes.addressof(fake.sd) in fake.freed


@pytest.mark.parametrize("step, code", [("get", 5), ("control", 6), ("acl_info", 7), ("get_ace", 8)])
def test_every_failed_read_raises_naming_the_file_and_frees_the_descriptor(install, tmp_path, step, code) -> None:
    folder = tmp_path / "Lib"
    folder.mkdir()
    fake = install(Fake([(0, 3, RX, SID)], fail={step: code}))
    with pytest.raises(OSError, match=rf"wouldn't read the permissions on Lib \(error {code}\)"):
        acl.has_grant(folder, SID, True)
    if step != "get":
        assert ctypes.addressof(fake.sd) in fake.freed


def test_a_sid_that_wont_convert_is_refused(install, tmp_path) -> None:
    install(Fake([(0, 3, RX, SID)], fail={"sid": 1}))
    with pytest.raises(OSError, match="capability"):
        acl.has_grant(tmp_path, SID, True)


def test_grant_adds_read_and_execute_with_inheritance_on_a_folder(install, tmp_path) -> None:
    fake = install(Fake([]))
    acl.grant(tmp_path, SID, True)
    entries = next(c for c in fake.calls if c[0] == "entries")
    assert entries[1:7] == (1, RX, 1, 3, 0, 0)
    assert entries[7] == ctypes.addressof(fake.dacl)
    sets = next(c for c in fake.calls if c[0] == "set")
    assert sets[2] == 1 and sets[3] == 4 | 0x20000000
    assert sets[4] is None and sets[5] is None and sets[7] is None
    assert sets[6] == ctypes.addressof(fake.new_acl)
    assert ctypes.addressof(fake.new_acl) in fake.freed and ctypes.addressof(fake.sd) in fake.freed


def test_grant_on_a_file_or_without_inherit_sets_no_inheritance(install, tmp_path) -> None:
    fake = install(Fake([]))
    acl.grant(tmp_path, SID, False)
    assert next(c for c in fake.calls if c[0] == "entries")[4] == 0


def test_grant_keeps_a_protected_dacl_protected(install, tmp_path) -> None:
    fake = install(Fake([], protected=True))
    acl.grant(tmp_path, SID, True)
    assert next(c for c in fake.calls if c[0] == "set")[3] == 4 | 0x80000000


@pytest.mark.parametrize("step", ["entries", "set"])
def test_a_failed_change_raises_and_frees_what_it_got(install, tmp_path, step) -> None:
    folder = tmp_path / "Lib"
    folder.mkdir()
    fake = install(Fake([], fail={step: 87}))
    with pytest.raises(OSError, match=r"wouldn't change the permissions on Lib \(error 87\)"):
        acl.grant(folder, SID, True)
    assert ctypes.addressof(fake.sd) in fake.freed
    assert (ctypes.addressof(fake.new_acl) in fake.freed) == (step == "set")


def test_a_failed_read_stops_a_grant_before_any_change(install, tmp_path) -> None:
    fake = install(Fake([], fail={"get": 5}))
    with pytest.raises(OSError, match="read"):
        acl.grant(tmp_path, SID, True)
    assert not any(c[0] in ("entries", "set") for c in fake.calls)
