"""Settings that widen what KratosForge trusts (app_settings.LOOSENING) apply only from a policy source
the person using the PC couldn't change: from a policy.json or Group Policy key they could change, or
the last good copy in their own profile, they are ignored and reported, while that source's
restrictions still apply. The registry and the file's permissions are stand-ins."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import app_settings, registry_policy
from fabric_desktop.integrations import policy as integrations_policy

DWORD, SZ = registry_policy.REG_DWORD, registry_policy.REG_SZ
TENANT = "11111111-1111-1111-1111-111111111111"
WIDE = {"fabric_view_sign_in_hosts": ["adfs.contoso.com"], "dbconnect_index_url": "https://pypi.contoso.com/simple",
        "notebook_package_folder": "D:\\wheels", "client_id": "22222222-3333-4444-5555-666666666666",
        "support_url": "https://help.contoso.com"}


@pytest.fixture
def policy(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True)
    return lambda data: path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


@pytest.fixture
def gp(monkeypatch):
    held: dict = {"values": {}, "lists": {}}
    monkeypatch.setattr(registry_policy, "_reader", lambda: (lambda: (held["values"], held["lists"])))
    monkeypatch.setattr(registry_policy, "_opener", lambda: None)

    def set_(values=None, lists=None):
        held["values"], held["lists"] = dict(values or {}), dict(lists or {})
    return set_


@pytest.fixture
def file_changeable(monkeypatch):
    monkeypatch.setattr(app_settings, "user_can_change", lambda path: True)


@pytest.fixture
def key_changeable(monkeypatch):
    monkeypatch.setattr(registry_policy, "user_can_change", lambda opener=None: True)


def test_every_loosening_setting_is_a_policy_setting():
    assert set(app_settings.LOOSENING) <= set(app_settings.POLICY_KEYS)


def test_a_file_only_admins_can_change_sets_them(policy, gp):
    policy({**WIDE, "telemetry": False})
    data, _ = app_settings.read_policy()
    assert all(data[k] == v for k, v in WIDE.items())
    assert app_settings.load()["client_id"] == {"value": WIDE["client_id"], "source": "policy"}


def test_a_file_the_user_could_change_sets_no_loosening_but_its_restrictions_stay(policy, gp, file_changeable):
    policy({**WIDE, "telemetry": False, "allowed_tenants": [TENANT], "integrations_http": ["api.contoso.com"]})
    data, reason = app_settings.read_policy()
    assert reason is None
    assert not set(data) & set(app_settings.LOOSENING)
    assert data["telemetry"] is False and data["allowed_tenants"] == [TENANT]  # restrictions still apply
    settings = app_settings.load()
    assert settings["client_id"]["source"] != "policy" and settings["support_url"]["value"] == ""
    assert settings["telemetry"] == {"value": False, "source": "policy"}
    problems = app_settings.policy_problems()
    ignored = [p for p in problems if "ignored, because" in p]
    assert len(ignored) == 1 and "client_id, dbconnect_index_url, fabric_view_sign_in_hosts, integrations_http" in ignored[0]
    assert str(app_settings.policy_path()) in ignored[0] and ignored[0].endswith("Its restrictions still apply")
    assert any("can be changed by the account" in p and "Ask IT" in p for p in problems)  # the warning stays


def test_integrations_read_the_same_filtered_settings(policy, gp, file_changeable):
    policy({"integrations_http": ["api.contoso.com"]})
    assert "integrations_http" not in integrations_policy.app_settings.read_policy()[0]


def test_a_changeable_file_without_loosening_settings_isnt_checked(policy, gp, monkeypatch):
    asked = []
    monkeypatch.setattr(app_settings, "user_can_change", lambda path: asked.append(path) or True)
    policy({"telemetry": False})
    app_settings.read_policy()
    assert asked == []  # only a source that sets one is checked, so most loads ask nothing


def test_a_group_policy_key_the_user_could_change_sets_no_loosening(policy, gp, key_changeable):
    policy({"support_url": "https://help.contoso.com"})  # the file is IT's: it still applies
    gp({"telemetry": (0, DWORD), "client_id": ("22222222-3333-4444-5555-666666666666", SZ)},
       {"fabric_view_sign_in_hosts": ["evil.example.com"]})
    data, _ = app_settings.read_policy()
    assert data == {"support_url": "https://help.contoso.com", "telemetry": False}
    problems = app_settings.policy_problems()
    assert any(p.startswith("client_id, fabric_view_sign_in_hosts are ignored, because Group Policy") for p in problems)


def test_group_policy_ignored_leaves_the_files_value_in_force(policy, gp, key_changeable):
    policy({"fabric_view_sign_in_hosts": ["adfs.contoso.com"]})
    gp({}, {"fabric_view_sign_in_hosts": ["evil.example.com"]})
    assert app_settings.read_policy()[0]["fabric_view_sign_in_hosts"] == ["adfs.contoso.com"]


def test_the_last_good_copy_in_the_profile_sets_no_loosening(policy, gp):
    policy({**WIDE, "telemetry": False})
    app_settings.read_policy()  # keeps the copy
    policy("{broken")
    data, reason = app_settings.read_policy()
    assert reason is None and data == {"telemetry": False}  # the copy's restrictions stand in, nothing loosening
    problems = app_settings.policy_problems()
    assert any("the last copy of the policy file kept in your profile" in p for p in problems)


def test_the_fingerprint_still_covers_everything_group_policy_sets(policy, gp, key_changeable):
    gp({"client_id": ("22222222-3333-4444-5555-666666666666", SZ)})
    assert app_settings.policy_fingerprint()["registry_sha256"] == registry_policy.fingerprint(registry_policy.read()[0])


def test_the_shells_sign_in_host_refusal_is_reported(policy, gp, monkeypatch):
    policy({"fabric_view_sign_in_hosts": ["adfs.contoso.com"]})
    monkeypatch.setenv("FABRIC_DESKTOP_SIGN_IN_HOSTS_IGNORED", "the policy file's are ignored: its folder isn't owned by administrators")
    assert any(p.startswith("fabric_view_sign_in_hosts: the policy file's are ignored") for p in app_settings.policy_problems())
