"""Group Policy in the registry (registry_policy.py): the values the ADMX templates write are read as
policy settings, typed by the schema, and win over policy.json. Only HKLM is read. A value of the wrong
type is left out and reported, a key the person could change is warned about, and a change is recorded
like a change to the policy file. The registry is a stand-in; nothing here touches a real one."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fabric_desktop import app_settings, policy_schema, policy_watch, registry_policy
from fabric_desktop.audit import AuditLog

DWORD, SZ = registry_policy.REG_DWORD, registry_policy.REG_SZ


@pytest.fixture
def policy(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True)
    return lambda data: path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def gp(monkeypatch):
    """Sets what this PC's Group Policy key holds: ({value: (data, type)}, {list: [items]})."""
    held: dict = {"values": {}, "lists": {}}
    monkeypatch.setattr(registry_policy, "_reader", lambda: (lambda: (held["values"], held["lists"])))
    monkeypatch.setattr(registry_policy, "_opener", lambda: None)

    def set_(values=None, lists=None):
        held["values"], held["lists"] = dict(values or {}), dict(lists or {})
    return set_


def test_each_kind_of_setting_reads_as_the_templates_write_it(gp):
    gp({"telemetry": (0, DWORD), "sql_page_rows": (500, DWORD), "channel": ("stable", SZ),
        "notebook_package_folder": ("\\\\server\\wheels", SZ)},
       {"allowed_tenants": ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]})
    data, problems = registry_policy.read()
    assert problems == []
    assert data == {"telemetry": False, "sql_page_rows": 500, "channel": "stable",
                    "notebook_package_folder": "\\\\server\\wheels",
                    "allowed_tenants": ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]}


def test_group_policy_wins_over_the_policy_file_and_the_rest_of_the_file_stays(policy, gp):
    policy({"telemetry": True, "channel": "preview", "auto_update": False})
    gp({"telemetry": (0, DWORD)})
    data, reason = app_settings.read_policy()
    assert reason is None and data == {"telemetry": False, "channel": "preview", "auto_update": False}
    assert app_settings.load()["telemetry"]["value"] is False


def test_group_policy_alone_needs_no_policy_file(policy, gp):
    gp({"show_preview": (0, DWORD)})
    assert app_settings.read_policy() == ({"show_preview": False}, None)
    assert app_settings.policy_fingerprint()["state"] == "applied"


@pytest.mark.parametrize("values,lists,says", [
    ({"telemetry": ("0", SZ)}, {}, "telemetry should be a DWORD"),
    ({"telemetry": (2, DWORD)}, {}, "telemetry should be 1 or 0"),
    ({"channel": (1, DWORD)}, {}, "channel should be a string"),
    ({"not_a_setting": (1, DWORD)}, {}, "'not_a_setting' isn't a setting"),
    ({"allowed_tenants": ("x", SZ)}, {}, "'allowed_tenants' isn't a setting"),  # a list is a subkey
    ({}, {"telemetry": ["1"]}, "'telemetry' isn't a list setting"),
    ({"platform": ("{}", SZ)}, {}, "'platform' isn't a setting"),  # structured: the policy file only
])
def test_a_value_of_the_wrong_type_is_left_out_and_said_so_and_the_file_applies(policy, gp, values, lists, says):
    policy({"telemetry": False, "channel": "preview"})
    gp(values, lists)
    data, _ = app_settings.read_policy()
    assert data["telemetry"] is False and data["channel"] == "preview"
    assert any(says in p for p in app_settings.policy_problems()), app_settings.policy_problems()


def test_a_key_that_cant_be_read_is_said_so_and_only_the_file_applies(policy, monkeypatch):
    policy({"telemetry": False})

    def refused():
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(registry_policy, "_reader", lambda: refused)
    assert app_settings.read_policy() == ({"telemetry": False}, None)
    assert any("Group Policy settings couldn't be read" in p for p in app_settings.policy_problems())


def test_a_key_the_person_could_change_is_warned_about(policy, gp, monkeypatch):
    gp({"telemetry": (0, DWORD)})
    granted = {(policy_schema.REGISTRY_KEY, 0x20019), (policy_schema.REGISTRY_KEY, 0x2)}  # read, and KEY_SET_VALUE
    monkeypatch.setattr(registry_policy, "_opener", lambda: (lambda path, right: (path, right) in granted))
    assert any("Group Policy settings" in p and "can be changed" in p for p in app_settings.policy_problems())
    assert app_settings.policy_fingerprint()["user_can_change"] is True
    granted.discard((policy_schema.REGISTRY_KEY, 0x2))  # read only, as Group Policy leaves it
    assert not any("Group Policy settings" in p for p in app_settings.policy_problems())


def test_a_change_in_group_policy_is_recorded_like_a_change_to_the_file(policy, gp, tmp_path):
    policy({"channel": "stable"})
    audit = AuditLog(tmp_path / "audit")
    first = policy_watch.check(tmp_path, audit)
    assert first["registry_sha256"] is None
    gp({"telemetry": (0, DWORD)})
    changed = policy_watch.check(tmp_path, audit)
    assert changed["registry_sha256"] and changed["sha256"] == first["sha256"] and not changed["first"]
    assert policy_watch.check(tmp_path, audit) == changed  # nothing new: nothing recorded
    lines = [json.loads(line) for p in sorted((tmp_path / "audit").rglob("*.jsonl")) for line in p.read_text().splitlines()]
    assert [entry["action"] for entry in lines if entry.get("action", "").startswith("policy_file")] == \
        ["policy_file_seen", "policy_file_changed"]


def test_only_the_machines_key_is_read():
    text = Path(registry_policy.__file__).read_text(encoding="utf-8")
    assert "HKEY_LOCAL_MACHINE" in text and "HKEY_CURRENT_USER" not in text
    assert policy_schema.REGISTRY_KEY == r"Software\Policies\KratosBI\KratosForge"
    admx = (Path(__file__).resolve().parents[2] / "deploy" / "policy" / "KratosForge.admx").read_text(encoding="utf-8")
    assert re.findall(r'class="(\w+)"', admx) and set(re.findall(r'class="(\w+)"', admx)) == {"Machine"}
    assert all('valuePrefix=""' in line for line in admx.splitlines() if "<list " in line)  # items named 1, 2, 3


def test_an_empty_list_subkey_leaves_the_files_list_in_force(policy, gp):
    # Not Configured after Enabled can leave the subkey behind with no items: that mustn't allow every tenant.
    tenant = "11111111-1111-1111-1111-111111111111"
    policy({"allowed_tenants": [tenant]})
    gp({}, {"allowed_tenants": []})
    assert app_settings.read_policy()[0]["allowed_tenants"] == [tenant]
    assert app_settings.load()["allowed_tenants"]["value"] == [tenant]
    assert registry_policy.read() == ({}, ["Group Policy key allowed_tenants has no items, so it sets nothing and the policy file's value applies"])


def test_list_items_are_read_up_to_the_first_missing_number(monkeypatch):
    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    names = {"1": "a.contoso.com", "3": "c.contoso.com", "x": "ignored"}

    class Winreg:
        HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_64KEY = 0, 0x20019, 0x100

        @staticmethod
        def OpenKey(*a):
            return Key()

        @staticmethod
        def QueryInfoKey(key):
            return (1, 0, 0) if key is root else (0, len(names), 0)

        @staticmethod
        def EnumKey(key, i):
            return "fabric_view_sign_in_hosts"

        @staticmethod
        def EnumValue(key, j):
            name = list(names)[j]
            return name, names[name], SZ

    root = Key()
    monkeypatch.setattr(Winreg, "OpenKey", staticmethod(lambda base, path, *a: root if path == policy_schema.REGISTRY_KEY else Key()))
    monkeypatch.setitem(__import__("sys").modules, "winreg", Winreg)
    monkeypatch.setattr(registry_policy.sys, "platform", "win32")
    values, lists = registry_policy._winreg_read()
    assert values == {} and lists == {"fabric_view_sign_in_hosts": ["a.contoso.com"]}  # as the shell reads them


def test_a_broken_policy_file_still_fails_closed_whatever_group_policy_sets(policy, gp):
    policy("{not json")
    gp({"telemetry": (1, DWORD), "allow_fabric_runs": (1, DWORD)})
    assert app_settings.policy_fingerprint()["state"] == "fail_closed"
    settings = app_settings.load()
    assert settings["allow_fabric_runs"]["value"] is False and settings["telemetry"]["value"] is False
