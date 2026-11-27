"""Group Policy's Windows registry calls, driven through a stand-in ``winreg`` (and ``ctypes.windll`` for the
administrator check) so every branch of the read and of the rights check runs on any OS: values read as
they are, a list subkey read up to its first missing number, an item of another type kept as None, a
subkey that can't be opened recorded as its error, no key at all, an administrator given no opener, and a
right the key refuses counted as not held. The real registry is never touched."""

from __future__ import annotations

import ctypes
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

from fabric_desktop import registry_policy
from fabric_desktop.policy_schema import REGISTRY_KEY

HKLM = object()
KEY_READ = 0x20019
KEY_WOW64_64KEY = 0x0100


class Key:
    """One registry key: its values as (name, data, type) and its subkeys (a Key, or the OSError opening it raises)."""

    def __init__(self, values: list[tuple[str, Any, int]] | None = None,
                 subkeys: dict[str, Key | OSError] | None = None) -> None:
        self.values = values or []
        self.subkeys = subkeys or {}
        self.closed = False

    def __enter__(self) -> Key:
        return self

    def __exit__(self, *exc: object) -> None:
        self.closed = True

    def Close(self) -> None:  # noqa: N802 - winreg's name
        self.closed = True


def _winreg(tree: dict[str, Key | OSError], opened: list[tuple[str, int]] | None = None) -> types.ModuleType:
    """A winreg with only HKLM\\<path> keys from ``tree``; a missing path raises FileNotFoundError."""
    mod = types.ModuleType("winreg")
    calls = opened if opened is not None else []

    def open_key(parent: Any, path: str, reserved: int, access: int) -> Key:
        calls.append((path, access))
        found = tree.get(path) if parent is HKLM else parent.subkeys.get(path)
        if found is None:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        if isinstance(found, OSError):
            raise found
        return found

    mod.HKEY_LOCAL_MACHINE = HKLM  # type: ignore[attr-defined]
    mod.KEY_READ = KEY_READ  # type: ignore[attr-defined]
    mod.KEY_WOW64_64KEY = KEY_WOW64_64KEY  # type: ignore[attr-defined]
    mod.OpenKey = open_key  # type: ignore[attr-defined]
    mod.QueryInfoKey = lambda key: (len(key.subkeys), len(key.values), 0)  # type: ignore[attr-defined]
    mod.EnumValue = lambda key, i: key.values[i]  # type: ignore[attr-defined]
    mod.EnumKey = lambda key, i: list(key.subkeys)[i]  # type: ignore[attr-defined]
    return mod


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch):
    """Runs as on Windows with the given registry tree; returns a function that installs one."""
    monkeypatch.setattr(sys, "platform", "win32")

    def install(tree: dict[str, Key | OSError], opened: list[tuple[str, int]] | None = None) -> None:
        monkeypatch.setitem(sys.modules, "winreg", _winreg(tree, opened))
    return install


def test_values_and_list_subkeys_are_read_from_the_64_bit_view(windows):
    opened: list[tuple[str, int]] = []
    items = Key([("2", "b.example", registry_policy.REG_EXPAND_SZ), ("1", "a.example", registry_policy.REG_SZ),
                 ("note", "not an item", registry_policy.REG_SZ), ("4", "after the missing 3", registry_policy.REG_SZ)])
    root = Key([("telemetry", 0, registry_policy.REG_DWORD), ("channel", "stable", registry_policy.REG_SZ)],
               {"appstudio_hosts": items})
    windows({REGISTRY_KEY: root}, opened)

    values, lists = registry_policy._winreg_read()

    assert values == {"telemetry": (0, registry_policy.REG_DWORD), "channel": ("stable", registry_policy.REG_SZ)}
    assert lists == {"appstudio_hosts": ["a.example", "b.example"]}  # stops before the missing 3
    assert opened == [(REGISTRY_KEY, KEY_READ | KEY_WOW64_64KEY), ("appstudio_hosts", KEY_READ | KEY_WOW64_64KEY)]
    assert root.closed and items.closed


def test_a_list_item_of_another_type_is_kept_as_none(windows):
    items = Key([("1", "a.example", registry_policy.REG_SZ), ("2", 7, registry_policy.REG_DWORD)])
    windows({REGISTRY_KEY: Key(subkeys={"appstudio_hosts": items})})

    assert registry_policy._winreg_read() == ({}, {"appstudio_hosts": ["a.example", None]})


def test_a_subkey_that_cannot_be_opened_is_recorded_as_its_error(windows):
    denied = PermissionError(5, "Access is denied")
    windows({REGISTRY_KEY: Key([("telemetry", 1, registry_policy.REG_DWORD)],
                               {"appstudio_hosts": denied, "appstudio_providers": Key([("1", "x", 1)])})})

    values, lists = registry_policy._winreg_read()

    assert values == {"telemetry": (1, registry_policy.REG_DWORD)}
    assert lists["appstudio_hosts"] is denied
    assert lists["appstudio_providers"] == ["x"]


def test_list_items_past_the_limit_are_not_read(windows, monkeypatch):
    monkeypatch.setattr(registry_policy, "MAX_ITEMS", 2)
    windows({REGISTRY_KEY: Key(subkeys={"appstudio_hosts": Key([("1", "a", 1), ("2", "b", 1), ("3", "c", 1)])})})

    assert registry_policy._winreg_read()[1] == {"appstudio_hosts": ["a", "b"]}


def test_no_key_reads_as_nothing(windows):
    windows({})

    assert registry_policy._winreg_read() == ({}, {})
    assert registry_policy.read() == ({}, [])


def test_read_all_uses_the_registry_on_windows(windows):
    windows({REGISTRY_KEY: Key([("telemetry", 0, registry_policy.REG_DWORD)],
                               {"appstudio_hosts": Key([("1", "a.example", 1)]),
                                "allowed_tenants": PermissionError(5, "Access is denied")})})

    out, problems, unknown = registry_policy.read_all()

    assert out["telemetry"] is False and out["appstudio_hosts"] == ["a.example"]
    assert "allowed_tenants" not in out  # a restriction without a lockout: the file's value or the default
    assert unknown == frozenset({"allowed_tenants"})
    assert any("allowed_tenants couldn't be read" in p and "may not apply" in p for p in problems)


def test_an_unreadable_list_that_restricts_nothing_is_kept_as_it_is():
    out, problems, unknown = registry_policy.read_all(lambda: ({}, {"wh_monitoring_auto": OSError(5, "denied")}))

    assert out == {} and unknown == frozenset({"wh_monitoring_auto"})
    assert len(problems) == 1 and "kept as it is" in problems[0]


def test_off_windows_nothing_is_read_or_opened(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    assert registry_policy._reader() is None and registry_policy._opener() is None
    assert registry_policy.read_all() == ({}, [], frozenset())
    assert registry_policy._winreg_read() == ({}, {})
    assert registry_policy._opens(REGISTRY_KEY, KEY_READ) is False
    assert registry_policy.user_can_change() is False


def _admin(monkeypatch: pytest.MonkeyPatch, admin: bool) -> None:
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: admin)),
                        raising=False)


def test_an_administrator_gets_no_opener(windows, monkeypatch):
    _admin(monkeypatch, True)
    windows({REGISTRY_KEY: Key()})

    assert registry_policy._opener() is None
    assert registry_policy.user_can_change() is False


def test_a_standard_user_gets_the_registry_opener(windows, monkeypatch):
    _admin(monkeypatch, False)
    windows({REGISTRY_KEY: Key()})

    assert registry_policy._opener() is registry_policy._opens


def test_opens_closes_the_key_it_opened_and_asks_the_64_bit_view(windows):
    key = Key()
    opened: list[tuple[str, int]] = []
    windows({REGISTRY_KEY: key}, opened)

    assert registry_policy._opens(REGISTRY_KEY, 0x2) is True
    assert key.closed
    assert opened == [(REGISTRY_KEY, 0x2 | KEY_WOW64_64KEY)]


def test_opens_is_false_when_the_key_refuses_or_is_missing(windows):
    windows({REGISTRY_KEY: PermissionError(5, "Access is denied")})

    assert registry_policy._opens(REGISTRY_KEY, 0x2) is False
    assert registry_policy._opens(REGISTRY_KEY + "\\Missing", KEY_READ) is False


def test_a_standard_user_who_cannot_open_the_key_cannot_change_it(windows, monkeypatch):
    _admin(monkeypatch, False)
    windows({})  # no key at all

    assert registry_policy.user_can_change() is False


def test_no_key_means_nothing_to_change_and_no_rights_are_tried():
    asked: list[tuple[str, int]] = []

    def opener(path: str, access: int) -> bool:
        asked.append((path, access))
        return False

    assert registry_policy.user_can_change(opener) is False
    assert asked == [(REGISTRY_KEY, 0x20019)]
