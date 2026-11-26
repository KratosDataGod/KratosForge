"""Policy keys follow the documented classification (app_settings.policy_effect). integrations_http and
appstudio_hosts only take away from what the person could do, so they are restrictions: from a policy file
the person could change, from the last good copy in their profile, and while Group Policy can't be read,
IT's false or host list still applies. platform chooses the Fabric workspace whose functions run as the
person, so it loosens, as client_id does: only a source the person couldn't change may set it."""

from __future__ import annotations

import pytest

from fabric_desktop import app_settings, registry_policy
from fabric_desktop.appstudio import policy as appstudio_policy
from fabric_desktop.integrations import policy as integrations_policy

from test_policy_loosening import file_changeable, gp, policy  # noqa: F401 - fixtures

HOSTS = ["contoso.openai.azure.com"]
WORKSPACES = {"prod": {"workspace_id": "33333333-3333-3333-3333-333333333333",
                       "functions_id": "44444444-4444-4444-4444-444444444444"}}


@pytest.fixture
def unreadable_gp(monkeypatch):
    """Group Policy's key exists but can't be read, so its permissions can't be checked either."""
    def read():
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(registry_policy, "_reader", lambda: read)
    monkeypatch.setattr(registry_policy, "_opener", lambda: None)
    monkeypatch.setattr(app_settings, "registry_key_untrusted",
                        lambda lists: "Group Policy's key isn't one only administrators could have written: "
                                      "its permissions can't be read (PermissionError)")


def test_the_three_keys_have_the_effect_they_are_documented_with():
    assert app_settings.policy_effect("integrations_http") == "restricts"
    assert app_settings.policy_effect("appstudio_hosts") == "restricts"
    assert app_settings.policy_effect("platform") == "loosens"


# integrations_http: false turns the local HTTP API off

def test_integrations_http_false_applies_from_a_policy_file_the_person_could_change(policy, gp, file_changeable):  # noqa: F811
    policy({"integrations_http": False})
    assert integrations_policy.current()["http"] is False
    assert not [p for p in app_settings.policy_problems() if "integrations_http" in p and "ignored, because" in p]


def test_integrations_http_false_applies_from_the_last_good_copy(policy, gp):  # noqa: F811
    policy({"integrations_http": False})
    app_settings.read_policy()  # keeps the copy
    policy("{broken")
    assert integrations_policy.current()["http"] is False


def test_integrations_http_false_applies_while_group_policy_cant_be_read(policy, unreadable_gp):  # noqa: F811
    policy({"integrations_http": False})
    assert integrations_policy.current()["http"] is False
    assert not [p for p in app_settings.policy_problems() if "integrations_http" in p and "ignored, because" in p]


# appstudio_hosts: the only hosts a chosen-address model connection may reach

def test_appstudio_hosts_apply_from_a_policy_file_the_person_could_change(policy, gp, file_changeable):  # noqa: F811
    policy({"appstudio_hosts": HOSTS})
    assert appstudio_policy.current()["hosts"] == HOSTS


def test_appstudio_hosts_apply_from_the_last_good_copy(policy, gp):  # noqa: F811
    policy({"appstudio_hosts": HOSTS})
    app_settings.read_policy()  # keeps the copy
    policy("{broken")
    assert appstudio_policy.current()["hosts"] == HOSTS


def test_appstudio_hosts_apply_while_group_policy_cant_be_read(policy, unreadable_gp):  # noqa: F811
    policy({"appstudio_hosts": HOSTS})
    assert appstudio_policy.current()["hosts"] == HOSTS


def test_an_unreadable_appstudio_hosts_subkey_allows_no_chosen_host(policy, gp):  # noqa: F811
    policy({"appstudio_hosts": HOSTS})
    gp({}, {"appstudio_hosts": PermissionError(5, "Access is denied")})
    assert appstudio_policy.current()["hosts"] == []  # its most restrictive value until the key can be read


# platform: which Fabric workspace and functions run as the person

def test_platform_from_a_policy_file_the_person_could_change_is_ignored_and_reported(policy, gp, file_changeable):  # noqa: F811
    policy({"platform": WORKSPACES, "telemetry": False})
    settings = app_settings.load()
    assert settings["platform"]["source"] != "policy" and settings["platform"]["value"] == {}
    assert settings["telemetry"] == {"value": False, "source": "policy"}  # that file's restrictions still apply
    ignored = [p for p in app_settings.policy_problems() if "ignored, because" in p]
    assert len(ignored) == 1 and ignored[0].startswith("platform is ignored")


def test_platform_from_the_last_good_copy_is_ignored(policy, gp):  # noqa: F811
    policy({"platform": WORKSPACES})
    app_settings.read_policy()  # keeps the copy
    policy("{broken")
    assert app_settings.load()["platform"]["source"] != "policy"


def test_platform_from_a_policy_file_only_admins_could_write_applies(policy, gp):  # noqa: F811
    policy({"platform": WORKSPACES})
    assert app_settings.load()["platform"] == {"value": WORKSPACES, "source": "policy"}
