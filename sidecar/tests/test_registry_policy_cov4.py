"""registry_policy.py: the Windows reads, faked (a winreg module in sys.modules and a module-local
sys), and the off-Windows defaults."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from fabric_desktop import policy_schema, registry_policy as rp

KEY = policy_schema.REGISTRY_KEY
PARENT = KEY.rsplit("\\", 1)[0]


class Key:
    def __init__(self, path, values=(), subkeys=()):
        self.path, self.values, self.subkeys, self.closed = path, list(values), list(subkeys), False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True
        return False

    def Close(self):
        self.closed = True


def fake_winreg(monkeypatch, tree, opened=None):
    """``tree``: {path: Key or an OSError to raise}. Subkeys open as ``path\\name``."""
    opened = [] if opened is None else opened

    def open_key(base, sub, reserved, access):
        path = f"{base.path}\\{sub}" if isinstance(base, Key) else sub
        opened.append((path, access))
        found = tree.get(path, FileNotFoundError(2, "missing"))
        if isinstance(found, OSError):
            raise found
        return found

    mod = types.ModuleType("winreg")
    mod.HKEY_LOCAL_MACHINE, mod.KEY_READ, mod.KEY_WOW64_64KEY = "HKLM", 0x20019, 0x100
    mod.OpenKey = open_key
    mod.QueryInfoKey = lambda k: (len(k.subkeys), len(k.values), 0)
    mod.EnumValue = lambda k, i: k.values[i]
    mod.EnumKey = lambda k, i: k.subkeys[i]
    monkeypatch.setitem(sys.modules, "winreg", mod)
    monkeypatch.setattr(rp, "sys", SimpleNamespace(platform="win32"))
    return opened


def test_values_and_list_subkeys_are_read_from_the_64_bit_view(monkeypatch):
    root = Key(KEY, [("telemetry", 1, rp.REG_DWORD), ("theme", "dark", rp.REG_SZ)],
               ["allowed_tenants", "broken"])
    hosts = Key(KEY + "\\allowed_tenants", [("2", "b", rp.REG_EXPAND_SZ), ("1", "a", rp.REG_SZ),
                                            ("3", 7, rp.REG_DWORD)])  # not a string: passed through as None
    opened = fake_winreg(monkeypatch, {KEY: root, KEY + "\\allowed_tenants": hosts,
                                       KEY + "\\broken": PermissionError(5, "Access is denied")})
    values, lists = rp._winreg_read()
    assert values == {"telemetry": (1, rp.REG_DWORD), "theme": ("dark", rp.REG_SZ)}
    assert lists["allowed_tenants"] == ["a", "b", None]  # read_all reports and fails closed on it
    assert isinstance(lists["broken"], PermissionError)  # only that setting is left unknown
    assert all(access == 0x20019 | 0x100 for _, access in opened)
    assert root.closed and hosts.closed


def test_no_key_reads_as_nothing(monkeypatch):
    fake_winreg(monkeypatch, {})
    assert rp._winreg_read() == ({}, {})


def test_a_key_that_cant_be_opened_is_raised_for_read_all(monkeypatch):
    fake_winreg(monkeypatch, {KEY: PermissionError(5, "Access is denied")})
    with pytest.raises(PermissionError):
        rp._winreg_read()
    monkeypatch.setattr(rp, "_reader", lambda: rp._winreg_read)
    out, problems, unknown = rp.read_all()
    assert unknown == {rp.ALL} and "couldn't be read" in problems[0]


def test_off_windows_nothing_is_read_or_tried(monkeypatch):
    monkeypatch.setattr(rp, "sys", SimpleNamespace(platform="linux"))
    assert rp._winreg_read() == ({}, {})
    assert rp._reader() is None and rp._opener() is None
    assert rp.read_all() == ({}, [], frozenset())
    assert rp.read() == ({}, [])
    assert rp.user_can_change() is False
    assert rp._opens(KEY, 0x2) is False


def test_on_windows_the_reader_is_winreg(monkeypatch):
    monkeypatch.setattr(rp, "sys", SimpleNamespace(platform="win32"))
    assert rp._reader() is rp._winreg_read


def fake_shell32(monkeypatch, admin):
    import ctypes
    fake = types.ModuleType("ctypes")
    fake.__dict__.update(ctypes.__dict__)
    fake.windll = SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: admin))
    monkeypatch.setitem(sys.modules, "ctypes", fake)
    monkeypatch.setattr(rp, "sys", SimpleNamespace(platform="win32"))


def test_an_administrator_isnt_counted_and_anyone_else_is_tried(monkeypatch):
    fake_shell32(monkeypatch, 1)
    assert rp._opener() is None
    fake_shell32(monkeypatch, 0)
    assert rp._opener() is rp._opens


def test_opens_asks_for_the_right_on_the_64_bit_view_and_closes(monkeypatch):
    key = Key(KEY)
    opened = fake_winreg(monkeypatch, {KEY: key})
    assert rp._opens(KEY, 0x2) is True and key.closed
    assert opened == [(KEY, 0x2 | 0x100)]
    assert rp._opens(PARENT, 0x4) is False  # not found: an OSError


def test_with_no_key_there_is_nothing_to_change():
    asked = []
    assert rp.user_can_change(lambda path, right: asked.append((path, right)) or False) is False
    assert asked == [(KEY, 0x20019)]


@pytest.mark.parametrize("granted, expected", [
    (set(), False),
    ({(KEY, 0x10000)}, True),       # DELETE on the key
    ({(PARENT, 0x80000)}, True),    # WRITE_OWNER on its parent
    ({(PARENT, 0x2)}, False),       # setting a value on the parent isn't a parent right
])
def test_a_right_on_the_key_or_its_parent_counts(granted, expected):
    def opener(path, right):
        return right == 0x20019 and path == KEY or (path, right) in granted
    assert rp.user_can_change(opener) is expected


def test_user_can_change_end_to_end_with_fake_winreg(monkeypatch):
    fake_shell32(monkeypatch, 0)
    tree = {KEY: Key(KEY)}
    fake_winreg(monkeypatch, tree)
    assert rp.user_can_change() is True  # the fake opens the key for any right
    tree.clear()
    assert rp.user_can_change() is False


def test_fingerprint_is_stable_and_none_for_nothing():
    assert rp.fingerprint({}) is None
    a = rp.fingerprint({"b": 1, "a": [1, 2]})
    assert a == rp.fingerprint({"a": [1, 2], "b": 1}) and len(a) == 64
    assert a != rp.fingerprint({"a": [2, 1], "b": 1})
