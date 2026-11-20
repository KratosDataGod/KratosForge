"""Every policy setting says what it does when a policy source sets it (app_settings.policy_effect): it
loosens (widens what KratosForge trusts, or where it sends or fetches things), restricts, is the person's
own preference, or is neutral with a reason. A setting added to the policy file, Group Policy or the ADMX
template without that decision fails here, so a new loosening setting can't be read from a source the
person could change by mistake."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fabric_desktop import app_settings, registry_policy

from test_policy_loosening import file_changeable, gp, key_changeable, policy  # noqa: F401 - fixtures

REPO = Path(__file__).resolve().parents[2]
ADMX = REPO / "deploy" / "policy" / "KratosForge.admx"
SHELL = REPO / "app" / "src-tauri" / "src"
EFFECTS = {"loosens", "restricts", "preference", "neutral"}
# Settings still to come that widen trust. When one lands it must be a loosening setting (Chris, 2026-11-10).
LOOSENING_TO_COME = ("sign_in_tenant",)


@pytest.mark.parametrize("key", app_settings.POLICY_KEYS)
def test_every_policy_setting_says_what_it_does(key):
    assert app_settings.policy_effect(key) in EFFECTS, \
        f"{key}: list it in LOOSENING, POLICY_RESTRICTS or POLICY_NEUTRAL (with why), or make it a person's own setting"


def test_every_setting_the_admx_template_holds_says_what_it_does():
    names = set(re.findall(r'valueName="([A-Za-z0-9_]+)"', ADMX.read_text(encoding="utf-8")))
    assert names and not [n for n in sorted(names) if app_settings.policy_effect(n) not in EFFECTS]


def test_each_setting_has_one_effect():
    groups = {"loosens": set(app_settings.LOOSENING) | set(app_settings.SHELL_LOOSENING),
              "restricts": set(app_settings.POLICY_RESTRICTS), "neutral": set(app_settings.POLICY_NEUTRAL)}
    for a in groups:
        for b in groups:
            assert a == b or not groups[a] & groups[b], (a, b, groups[a] & groups[b])
    assert all(set(g) <= set(app_settings.POLICY_KEYS) for g in groups.values())


def test_a_preference_is_one_the_person_can_choose_and_a_neutral_one_says_why():
    for key in app_settings.POLICY_KEYS:
        if app_settings.policy_effect(key) == "preference":
            assert key in app_settings.USER_KEYS, key
    for key, why in app_settings.POLICY_NEUTRAL.items():
        assert key not in app_settings.USER_KEYS and len(why) >= 20, key


def test_settings_still_to_come_loosen_when_they_land():
    for key in LOOSENING_TO_COME:
        if key in app_settings.POLICY_KEYS:
            assert app_settings.policy_effect(key) == "loosens", f"{key} must join app_settings.LOOSENING"


@pytest.mark.parametrize("key", app_settings.SHELL_LOOSENING)
def test_a_loosening_setting_the_shell_reads_is_checked_by_the_shell(key):
    assert key in app_settings.SHELL_KEYS
    text = "\n".join(p.read_text(encoding="utf-8") for p in SHELL.glob("*.rs"))
    assert re.search(rf"\b{key}\b", text), f"the shell must read {key} and check its source itself"


def _value(key):
    kind = app_settings.TYPES.get(key)
    return {bool: True, int: 7, list: ["contoso.com"], dict: {}}.get(kind, "https://contoso.example/x")


@pytest.mark.parametrize("key", app_settings.LOOSENING)
def test_each_loosening_setting_is_ignored_from_a_policy_file_the_person_could_change(key, policy, gp, file_changeable):  # noqa: F811
    policy({key: _value(key), "telemetry": False})
    data, _ = app_settings.read_policy()
    assert key not in data and data.get("telemetry") is False  # the loosening goes; the restriction stays


@pytest.mark.parametrize("key", app_settings.LOOSENING)
def test_each_loosening_setting_is_ignored_from_a_group_policy_key_the_person_could_change(
        key, policy, gp, key_changeable):  # noqa: F811
    value = _value(key)
    if isinstance(value, list):
        gp({"telemetry": (0, registry_policy.REG_DWORD)}, {key: value})
    elif isinstance(value, bool):
        gp({"telemetry": (0, registry_policy.REG_DWORD), key: (1, registry_policy.REG_DWORD)})
    elif isinstance(value, int):
        gp({"telemetry": (0, registry_policy.REG_DWORD), key: (value, registry_policy.REG_DWORD)})
    else:
        gp({"telemetry": (0, registry_policy.REG_DWORD), key: (value, registry_policy.REG_SZ)})
    data, _ = app_settings.read_policy()
    assert key not in data and data.get("telemetry") is False
