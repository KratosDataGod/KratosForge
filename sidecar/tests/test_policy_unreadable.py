"""Group Policy that exists but can't be read never quietly drops what it sets: each on/off restriction takes
its most restrictive value (as for a policy file that can't be read), the other settings it may manage stay as
they are and can't be changed (except made stricter), no source sets a setting that widens what
KratosForge trusts, the problem says so and the record of policy changes notes it. One list subkey that
can't be read leaves only its own setting unknown. Read again each time, so a passing error clears."""

from __future__ import annotations

import pytest

from fabric_desktop import app_settings, policy_watch, registry_policy
from fabric_desktop.audit import AuditLog

from test_policy_loosening import gp, policy  # noqa: F401 - fixtures

HOSTS = ["login.contoso.example"]


@pytest.fixture
def broken(monkeypatch):
    """Group Policy's key, unreadable until ``fixed`` is set."""
    state = {"fixed": False}

    def read():
        if not state["fixed"]:
            raise PermissionError(5, "Access is denied")
        return {"telemetry": (0, registry_policy.REG_DWORD)}, {}
    monkeypatch.setattr(registry_policy, "_reader", lambda: read)
    monkeypatch.setattr(registry_policy, "_opener", lambda: None)
    return state


def test_an_unreadable_key_is_reported_as_unknown_and_its_restrictions_take_their_safest_values(policy, broken):  # noqa: F811
    out, problems, unknown = registry_policy.read_all()
    assert unknown == {registry_policy.ALL}
    on_off = {**app_settings.RESTRICTED, **registry_policy._safest()}  # external AI tools and App Studio's too
    assert out == {k: v for k, v in on_off.items() if not isinstance(v, list)}
    assert out["integrations"] is False and out["appstudio_models"] is False
    assert "allowed_tenants" not in out  # people can still sign in (Chris, 2026-10-18)
    assert problems == [("The Group Policy settings couldn't be read ([Errno 5] Access is denied). Until they can, "
                         "each on/off restriction takes its most restrictive value, as it does when the policy file "
                         "can't be read, and settings that widen what KratosForge trusts don't apply. Other settings "
                         "Group Policy can manage stay as they are and can only be made stricter here. Sign-in still "
                         "works. If this doesn't clear, ask IT to check that this PC can read the Group Policy key.")]
    assert problems[0] in app_settings.policy_problems()


def test_a_restriction_set_only_in_group_policy_still_applies_while_it_cant_be_read(policy, gp, monkeypatch):  # noqa: F811
    gp({"allow_fabric_runs": (0, registry_policy.REG_DWORD)})
    assert app_settings.value("allow_fabric_runs") is False

    def unreadable():
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(registry_policy, "_reader", lambda: unreadable)
    assert app_settings.load()["allow_fabric_runs"] == {"value": False, "source": "policy"}


def test_while_it_cant_be_read_the_policy_file_sets_nothing_that_loosens(policy, broken):  # noqa: F811
    policy({"fabric_view_sign_in_hosts": HOSTS, "support_url": "https://help.contoso.example", "telemetry": False})
    data, _ = app_settings.read_policy()
    assert "fabric_view_sign_in_hosts" not in data and "support_url" not in data
    assert data["telemetry"] is False  # its restrictions still apply
    broken["fixed"] = True  # the next read works: the policy file's settings apply again
    data, _ = app_settings.read_policy()
    assert data["fabric_view_sign_in_hosts"] == HOSTS


def test_while_it_cant_be_read_settings_stay_as_they_are(policy, broken):  # noqa: F811
    assert app_settings.load()["show_preview"] == {"value": False, "source": "policy"}  # its safest value
    with pytest.raises(ValueError, match="it may set show_preview, so show_preview can only be made stricter"):
        app_settings.save({"show_preview": True})
    with pytest.raises(ValueError, match="Group Policy on this PC couldn't be read, and it may set ui_skin, so "
                                         "ui_skin can only be made stricter"):
        app_settings.save({"ui_skin": "forge"})
    broken["fixed"] = True
    app_settings.save({"show_preview": True, "ui_skin": "forge"})
    assert app_settings.value("show_preview") is True


def test_a_stricter_change_goes_through_while_a_list_is_unknown(policy, gp):  # noqa: F811
    gp({}, {"allowed_tenants": PermissionError(5, "Access is denied")})
    app_settings.save({"telemetry": False})  # not unknown: saved as usual
    assert app_settings.value("telemetry") is False


def test_one_unreadable_list_leaves_only_that_setting_unknown(policy, gp):  # noqa: F811
    gp({"telemetry": (0, registry_policy.REG_DWORD)},
       {"fabric_view_sign_in_hosts": PermissionError(5, "Access is denied"), "allowed_tenants": ["contoso.com"]})
    out, problems, unknown = registry_policy.read_all()
    assert out == {"telemetry": False, "allowed_tenants": ["contoso.com"]}
    assert unknown == {"fabric_view_sign_in_hosts"}
    assert problems == [("Group Policy key fabric_view_sign_in_hosts couldn't be read ([Errno 5] Access is denied), "
                        "so fabric_view_sign_in_hosts is kept as it is and can't be changed here until the key can be read. "
                        "Ask IT to check that this PC can read the fabric_view_sign_in_hosts key in Group Policy.")]
    policy({"fabric_view_sign_in_hosts": HOSTS, "support_url": "https://help.contoso.example"})
    data, _ = app_settings.read_policy()
    assert "fabric_view_sign_in_hosts" not in data  # unknown: no source loosens it
    assert data["support_url"] == "https://help.contoso.example"  # the rest of policy applies as usual
    app_settings.save({"show_preview": True})  # settings it doesn't touch can still change


def test_an_unreadable_subkey_that_isnt_a_list_setting_is_only_ignored(policy, gp):  # noqa: F811
    gp({}, {"not_a_setting": PermissionError(5, "Access is denied")})
    out, problems, unknown = registry_policy.read_all()
    assert out == {} and unknown == frozenset()
    assert problems == [("Group Policy key 'not_a_setting' isn't a list setting KratosForge reads, so it's ignored. "
                         "Ask IT to remove it from Group Policy.")]


def test_the_record_of_policy_changes_notes_group_policy_it_couldnt_read(policy, broken, tmp_path):  # noqa: F811
    audit = AuditLog(tmp_path / "audit")
    record = policy_watch.check(tmp_path, audit)
    assert record["registry_sha256"]  # unreadable is a state of its own, not "no Group Policy"
    [line] = audit.recent(5, "policy_file_seen")
    assert line["target"]["registry_unreadable"] == "*"
    broken["fixed"] = True
    policy_watch.check(tmp_path, audit)
    [changed] = audit.recent(5, "policy_file_changed")
    assert "registry_unreadable" not in changed["target"]


def test_an_unreadable_tenant_list_says_the_restriction_may_not_apply(policy, gp):  # noqa: F811
    gp({}, {"allowed_tenants": PermissionError(5, "Access is denied")})
    _, problems, unknown = registry_policy.read_all()
    assert unknown == {"allowed_tenants"}
    assert problems == [("Group Policy key allowed_tenants couldn't be read ([Errno 5] Access is denied), so the "
                         "restriction it sets may not apply. Until the key can be read, allowed_tenants comes from "
                         "the policy file or its default. Ask IT to check that this PC can read the allowed_tenants key in "
                         "Group Policy.")]
