"""While Group Policy's key exists but can't be read, each list restriction takes its most restrictive value,
whatever the policy file sets, as each on/off restriction does. The tenant list is left as it is, so people can
still sign in. A PC with no Group Policy key, and a key that can be read, are unchanged."""

from __future__ import annotations

from typing import Any

import pytest

from fabric_desktop import app_settings, registry_policy
from fabric_desktop.appstudio import policy as appstudio_policy
from fabric_desktop.appstudio import providers
from fabric_desktop.integrations import policy as integrations_policy
from fabric_desktop.integrations.registry import PERMISSIONS
from fabric_desktop.receipts import REDACTIONS

from test_policy_loosening import gp, policy  # noqa: F401 - fixtures

SAFEST: dict[str, Any] = {**app_settings.RESTRICTED, **registry_policy._safest()}
SIGN_IN = "allowed_tenants"  # left as it is, so people can still sign in (Chris, 2026-10-18)
# Every list restriction, read from app_settings and registry_policy, so a new one is covered here on its own.
LISTS = sorted(k for k, v in SAFEST.items() if isinstance(v, list) and k in app_settings.POLICY_RESTRICTS and k != SIGN_IN)

# A list for each that allows more than its safest value; a new key gets a generic one.
WIDER: dict[str, list[str]] = {"integrations_max_permissions": list(PERMISSIONS),
                               "integrations_extensions": ["contoso.reporter"],
                               "receipt_redactions": [],
                               "appstudio_providers": list(providers.PROVIDERS)}
GP_LIST: dict[str, list[str]] = {"integrations_max_permissions": [PERMISSIONS[0]],
                                 "integrations_extensions": ["contoso.gp"],
                                 "receipt_redactions": [next(iter(REDACTIONS))],
                                 "appstudio_providers": [next(iter(providers.PROVIDERS))]}


def wider(key: str) -> list[str]:
    return WIDER.get(key, ["contoso.example"] if not SAFEST[key] else [])


def gp_list(key: str) -> list[str]:
    return GP_LIST.get(key, ["contoso-gp.example"])


@pytest.fixture
def unreadable(monkeypatch):
    def read():
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(registry_policy, "_reader", lambda: read)
    monkeypatch.setattr(registry_policy, "_opener", lambda: None)


def test_the_list_restrictions_are_found():
    assert {"integrations_max_permissions", "integrations_extensions", "receipt_redactions",
            "appstudio_providers"} <= set(LISTS)
    for key in LISTS:
        assert wider(key) != SAFEST[key] and gp_list(key) != SAFEST[key]


@pytest.mark.parametrize("key", LISTS)
def test_an_unreadable_key_gives_each_list_restriction_its_safest_value(key, policy, unreadable):  # noqa: F811
    policy({key: wider(key)})
    out, _, unknown = registry_policy.read_all()
    assert unknown == {registry_policy.ALL}
    assert out[key] == SAFEST[key]
    data, _ = app_settings.read_policy()
    assert data[key] == SAFEST[key]  # the policy file's wider list doesn't apply


@pytest.mark.parametrize("key", LISTS)
def test_with_no_group_policy_key_the_policy_files_list_applies(key, policy, gp):  # noqa: F811
    gp()
    policy({key: wider(key)})
    assert registry_policy.read_all() == ({}, [], frozenset())
    data, _ = app_settings.read_policy()
    assert data[key] == wider(key)


@pytest.mark.parametrize("key", LISTS)
def test_with_no_group_policy_key_and_no_file_value_nothing_is_set(key, policy, gp):  # noqa: F811
    gp()
    policy({})
    data, _ = app_settings.read_policy()
    assert key not in data


@pytest.mark.parametrize("key", LISTS)
def test_a_readable_key_sets_its_own_list(key, policy, gp):  # noqa: F811
    gp({}, {key: gp_list(key)})
    policy({key: wider(key)})
    out, problems, unknown = registry_policy.read_all()
    assert out == {key: gp_list(key)} and problems == [] and unknown == frozenset()
    data, _ = app_settings.read_policy()
    assert data[key] == gp_list(key)


@pytest.mark.parametrize("key", LISTS)
def test_a_readable_key_without_the_list_leaves_the_files_list(key, policy, gp):  # noqa: F811
    gp({"telemetry": (0, registry_policy.REG_DWORD)})
    policy({key: wider(key)})
    data, _ = app_settings.read_policy()
    assert data[key] == wider(key) and data["telemetry"] is False


def test_the_tenant_list_is_left_as_it_is_so_people_can_sign_in(policy, unreadable):  # noqa: F811
    policy({SIGN_IN: ["contoso.com"]})
    out, _, _ = registry_policy.read_all()
    assert SIGN_IN not in out
    assert app_settings.read_policy()[0][SIGN_IN] == ["contoso.com"]


def test_app_studio_and_ai_tools_allow_nothing_listed_while_it_cant_be_read(policy, unreadable):  # noqa: F811
    policy({"appstudio_providers": list(providers.PROVIDERS), "integrations_max_permissions": list(PERMISSIONS),
            "integrations_extensions": ["contoso.reporter"], "receipt_redactions": []})
    assert appstudio_policy.current()["providers"] == []
    tools = integrations_policy.current()
    assert tools["max_permissions"] == [] and tools["extensions"] == []
    assert sorted(app_settings.read_policy()[0]["receipt_redactions"]) == sorted(REDACTIONS)


def test_app_studio_hosts_from_the_file_dont_apply_while_it_cant_be_read(policy, unreadable):  # noqa: F811
    # appstudio_hosts widens what KratosForge trusts (LOOSENING), so the file's list is already left out here and
    # its default applies; once it is a list restriction (POLICY_RESTRICTS) the tests above cover it as well.
    policy({"appstudio_hosts": ["contoso.openai.azure.com"]})
    assert "appstudio_hosts" not in app_settings.read_policy()[0]
