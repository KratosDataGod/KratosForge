"""registry_policy.py: what mutation testing showed no test checked. The registry types and rights are
Windows' own values; keys are opened with reserved 0 in the 64-bit view; a list subkey is read to 1,000 items;
a bool reads 1 as on; a string setting needs both a string type and a string; and a long unknown name is cut
to 80 characters in the message."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from fabric_desktop import policy_schema, registry_policy as rp

KEY = policy_schema.REGISTRY_KEY
PARENT = KEY.rsplit("\\", 1)[0]
SZ, EXPAND_SZ, DWORD, BINARY = 1, 2, 4, 3  # winnt.h REG_SZ, REG_EXPAND_SZ, REG_DWORD, REG_BINARY


def read(values=None, lists=None):
    return rp.read(lambda: (values or {}, lists or {}))


def test_the_registry_types_are_windows_values():
    assert (rp.REG_SZ, rp.REG_EXPAND_SZ, rp.REG_DWORD) == (SZ, EXPAND_SZ, DWORD)


def test_values_of_windows_types_read_as_the_templates_write_them():
    out, problems = read({"telemetry": (1, DWORD), "show_preview": (0, DWORD), "sql_page_rows": (500, DWORD),
                          "ui_skin": ("forge", SZ), "sql_temp_dir": (r"%TEMP%\kf", EXPAND_SZ)})
    assert out == {"telemetry": True, "show_preview": False, "sql_page_rows": 500, "ui_skin": "forge",
                   "sql_temp_dir": r"%TEMP%\kf"}
    assert problems == []


@pytest.mark.parametrize("value", [("forge", DWORD), ("forge", BINARY), (7, SZ)])
def test_a_string_setting_needs_a_string_type_and_a_string(value):
    out, problems = read({"ui_skin": value})
    assert out == {} and problems == [("Group Policy value ui_skin should be a string, so it's ignored. "
                                         "Ask IT to fix ui_skin in Group Policy.")]


def test_a_bool_out_of_range_takes_the_restrictive_value():
    out, problems = read({"allow_fabric_runs": (2, DWORD)})
    assert out == {"allow_fabric_runs": False}
    assert problems == [("Group Policy value allow_fabric_runs should be 1 or 0, so its most restrictive value applies. "
                         "Ask IT to fix allow_fabric_runs in Group Policy.")]


def test_long_unknown_names_are_cut_to_80_characters():
    name = "x" * 79 + "yz" + "w" * 20
    _, problems = read({name: (1, DWORD)}, {name: ["a"]})
    cut = repr(name[:80])
    assert problems == [(f"Group Policy value {cut} isn't a setting KratosForge reads, so it's ignored. "
                         "Ask IT to remove it from Group Policy."),
                        (f"Group Policy key {cut} isn't a list setting KratosForge reads, so it's ignored. "
                         "Ask IT to remove it from Group Policy.")]
    assert cut == repr("x" * 79 + "y")  # z, the 81st, is cut off


def test_the_rights_asked_for_are_windows_values_in_order():
    asked = []

    def opener(path, right):
        asked.append((path, right))
        return right == 0x20019
    assert rp.user_can_change(opener) is False
    assert asked == [(KEY, 0x20019), (KEY, 0x2), (KEY, 0x4), (KEY, 0x10000), (KEY, 0x40000), (KEY, 0x80000),
                     (PARENT, 0x4), (PARENT, 0x40000), (PARENT, 0x80000)]


class Key:
    def __init__(self, values=(), subkeys=()):
        self.values, self.subkeys = list(values), list(subkeys)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def Close(self):
        pass


def fake_winreg(monkeypatch, tree):
    opened = []

    def open_key(base, sub, reserved, access):
        opened.append((sub, reserved, access))
        path = sub if base == "HKLM" else f"{KEY}\\{sub}"
        if path not in tree:
            raise FileNotFoundError(2, "missing")
        return tree[path]
    mod = types.ModuleType("winreg")
    mod.HKEY_LOCAL_MACHINE, mod.KEY_READ, mod.KEY_WOW64_64KEY = "HKLM", 0x20019, 0x100
    mod.OpenKey = open_key
    mod.QueryInfoKey = lambda k: (len(k.subkeys), len(k.values), 0)
    mod.EnumValue = lambda k, i: k.values[i]
    mod.EnumKey = lambda k, i: k.subkeys[i]
    monkeypatch.setitem(sys.modules, "winreg", mod)
    monkeypatch.setattr(rp, "sys", SimpleNamespace(platform="win32"))
    return opened


def test_keys_open_with_reserved_zero_and_a_list_stops_at_1000_items(monkeypatch):
    items = [(str(i), f"h{i}.contoso.com", SZ) for i in range(1, 1201)]
    opened = fake_winreg(monkeypatch, {KEY: Key(subkeys=["fabric_view_sign_in_hosts"]),
                                       KEY + "\\fabric_view_sign_in_hosts": Key(items)})
    _, lists = rp._winreg_read()
    assert len(lists["fabric_view_sign_in_hosts"]) == rp.MAX_ITEMS == 1000
    assert lists["fabric_view_sign_in_hosts"][-1] == "h1000.contoso.com"
    assert [reserved for _, reserved, _ in opened] == [0, 0]
    opened.clear()
    assert rp._opens(KEY, 0x2) is True
    assert opened == [(KEY, 0, 0x2 | 0x100)]
