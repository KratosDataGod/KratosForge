"""Organization policy fails closed: a policy.json that exists but can't be read, or a restrictive key
of the wrong type, gives each restriction its most restrictive value, not the default (HD-010). An
unreadable file still lets people sign in and work on their PC (Chris, 2026-10-18)."""

import json

import pytest

from fabric_desktop import app_settings
from fabric_desktop.fabric_api import POLICY_BLOCKED, FabricClient, FabricError
from fabric_desktop.integrations import policy as integrations_policy
from fabric_desktop.integrations.registry import PERMISSIONS
from fabric_desktop.receipts import REDACTIONS

HOME = "11111111-2222-3333-4444-555555555555"
AWAY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
USER_CAN_CHANGE = app_settings.user_can_change  # before conftest replaces it for each test


@pytest.fixture
def files(monkeypatch, tmp_path):
    """Writes the policy file (a dict as JSON, a str as is) and the person's own settings."""
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    user = tmp_path / "la" / "FabricDesktop" / "app-settings.json"
    path.parent.mkdir(parents=True)
    user.parent.mkdir(parents=True)
    user.write_text(json.dumps({"show_preview": True, "telemetry": True}))

    def write(data):
        path.write_text(data if isinstance(data, str) else json.dumps(data))
    write.path = path
    return write


def assert_everything_restricted():
    s = app_settings.load()
    for key, value in (("allow_fabric_runs", False), ("show_preview", False), ("telemetry", False)):
        assert s[key] == {"value": value, "source": "policy"}, key
    assert app_settings.tenant_allowed(HOME) and app_settings.tenant_allowed(AWAY)  # sign-in still works
    rules = integrations_policy.current()
    assert rules["enabled"] is False and rules["http"] is False
    assert rules["max_permissions"] == [] and rules["extensions"] == []
    assert set(integrations_policy.receipt_redactions()) == set(REDACTIONS)
    client = FabricClient(lambda: "tok", sleep=lambda s: None)
    with pytest.raises(FabricError) as exc:
        client.run_item_job("ws", "nb", "RunNotebook")
    assert exc.value.code == POLICY_BLOCKED


@pytest.mark.parametrize("text", ['{"allow_fabric_runs": false,}', "{not json", "[]", "\"stable\""])
def test_a_file_that_cant_be_read_restricts_everything(files, text):
    files(text)
    assert_everything_restricted()
    problems = app_settings.policy_problems()
    assert len(problems) == 1 and str(files.path) in problems[0] and "most restrictive" in problems[0]
    assert integrations_policy.current()["problems"] == problems  # both readers say the same thing


def test_a_policy_path_that_cant_be_opened_restricts_everything(files):
    files.path.mkdir()  # a folder where the file should be: exists, but can't be read
    assert_everything_restricted()
    assert "most restrictive" in app_settings.policy_problems()[0]


def test_users_cannot_undo_a_fail_closed_setting(files):
    files("{broken")
    for key in ("show_preview", "telemetry"):
        with pytest.raises(ValueError, match="organization"):
            app_settings.save({key: True})


@pytest.mark.parametrize("data", [{"allow_fabric_runs": "false"}, {"allow_fabric_runs": 0},
                                  {"allow_fabric_runs": None}])
def test_wrongly_typed_fabric_runs_is_off(files, data):
    files(data)
    assert app_settings.load()["allow_fabric_runs"] == {"value": False, "source": "policy"}
    assert any(p.startswith("allow_fabric_runs should be bool") for p in app_settings.policy_problems())


@pytest.mark.parametrize("tenants", [HOME, {"tenant": HOME}, [HOME, 5], None])
def test_wrongly_typed_allowed_tenants_allows_none(files, tenants):
    files({"allowed_tenants": tenants})
    assert not app_settings.tenant_allowed(HOME) and not app_settings.tenant_allowed(AWAY)
    assert any(p.startswith("allowed_tenants") for p in app_settings.policy_problems())


def test_wrongly_typed_preview_and_telemetry_are_off(files):
    files({"show_preview": "yes", "telemetry": 1})
    s = app_settings.load()
    assert s["show_preview"] == {"value": False, "source": "policy"}
    assert s["telemetry"] == {"value": False, "source": "policy"}


def test_wrongly_typed_integration_keys_are_most_restrictive_and_reported(files):
    files({"integrations": "yes", "integrations_max_permissions": "all", "integrations_http": "no",
           "integrations_extensions": "notebook-outline", "receipt_redactions": "everything"})
    rules = integrations_policy.current()
    assert rules["enabled"] is False and rules["max_permissions"] == [] and rules["http"] is False
    assert rules["extensions"] == []
    assert set(integrations_policy.receipt_redactions()) == set(REDACTIONS)
    problems = app_settings.policy_problems()
    for key in ("integrations", "integrations_max_permissions", "integrations_http", "integrations_extensions",
                "receipt_redactions"):
        assert any(p.startswith(f"{key} should be") for p in problems), key
    assert not any("isn't a setting" in p for p in problems)


def test_integration_and_receipt_keys_are_known_settings(files):
    files({"integrations": False, "integrations_max_permissions": ["READ_METADATA"], "integrations_http": False,
           "integrations_extensions": [], "receipt_redactions": ["identity"], "allow_fabric_runs": False})
    assert app_settings.policy_problems() == []
    rules = integrations_policy.current()
    assert rules["enabled"] is False and rules["max_permissions"] == ["READ_METADATA"]
    assert app_settings.value("allow_fabric_runs") is False  # both readers read the same file
    files({"chanel": "stable"})
    assert app_settings.policy_problems()[0].startswith("chanel isn't a setting")
    assert "receipt_redactions" in app_settings.policy_problems()[0]  # the list of settings includes it


def test_fabric_view_sign_in_hosts_is_a_setting_and_bad_entries_are_reported(files):
    files({"fabric_view_sign_in_hosts": ["adfs.contoso.com", "contoso.okta.com"]})
    assert app_settings.policy_problems() == []
    files({"fabric_view_sign_in_hosts": ["adfs.contoso.com", "*.okta.com", "https://sso.contoso.com",
                                         "adfs.contoso.com:443", "adfs.contoso.com/adfs", 7]})
    problems = app_settings.policy_problems()
    assert len(problems) == 5 and all(p.startswith("fabric_view_sign_in_hosts: ") for p in problems)
    files({"fabric_view_sign_in_hosts": "adfs.contoso.com"})
    assert app_settings.policy_problems()[0].startswith("fabric_view_sign_in_hosts should be a list")
    assert "fabric_view_sign_in_hosts" not in app_settings.load()  # the shell applies it, not the engine


@pytest.mark.parametrize("host,ok", [
    ("adfs.contoso.com", True), ("xn--bcher-kva.example", True), ("a.b-c.co.uk", True),
    ("*.okta.com", False), ("localhost", False), ("10.0.0.5", False), ("adfs.contoso.com.", False),
    ("-a.com", False), ("a-.com", False), ("a..com", False), ("bücher.example", False), ("", False),
    ("a" * 64 + ".com", False), ("a." * 126 + "com", False),
])
def test_sign_in_host_names_match_the_shell(host, ok):
    assert app_settings.dns_name(host) is ok


def test_a_policy_file_people_can_change_is_reported(files, monkeypatch):
    files({"allow_fabric_runs": False})
    monkeypatch.setattr(app_settings, "user_can_change", lambda path: path == files.path)
    [problem] = app_settings.policy_problems()
    assert "can be changed by the account you're signed in" in problem and "only administrators" in problem
    assert app_settings.load()["allow_fabric_runs"] == {"value": False, "source": "policy"}  # still applies


def test_user_can_change_checks_write_access(files, monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "user_can_change", USER_CAN_CHANGE)  # the real check, not conftest's
    monkeypatch.setattr(app_settings.sys, "platform", "linux")
    monkeypatch.setattr(app_settings.os, "geteuid", lambda: 1000, raising=False)
    files({})
    assert USER_CAN_CHANGE(files.path)  # the test account wrote it
    assert not USER_CAN_CHANGE(tmp_path / "missing.json")
    monkeypatch.setattr(app_settings.os, "geteuid", lambda: 0, raising=False)
    assert not USER_CAN_CHANGE(files.path)  # an administrator can change it anyway


@pytest.mark.skipif(not hasattr(__import__("os"), "chown"), reason="file owners are POSIX here")
def test_owning_the_folder_counts_even_when_the_file_is_read_only(files, monkeypatch):
    """Someone who made the folder before IT pushed the file can delete and replace that file."""
    import os
    if os.geteuid() != 0:
        pytest.skip("needs root to hand the folder to another account")
    files({})
    os.chmod(files.path, 0o444)
    os.chown(files.path.parent, 1000, 1000)
    os.chmod(files.path.parent, 0o755)
    monkeypatch.setattr(app_settings.sys, "platform", "linux")
    monkeypatch.setattr(app_settings.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(app_settings.os, "access", lambda path, mode: False)  # root can open anything
    assert USER_CAN_CHANGE(files.path)
    os.chown(files.path.parent, 0, 0)
    assert not USER_CAN_CHANGE(files.path)  # IT's folder and file: nothing to change


def test_missing_file_keeps_the_defaults(files):
    s = app_settings.load()
    assert s["allow_fabric_runs"] == {"value": True, "source": "default"}
    assert s["show_preview"] == {"value": True, "source": "user"} and s["telemetry"]["source"] == "user"
    assert app_settings.tenant_allowed(AWAY) and app_settings.tenant_allowed(None)
    assert app_settings.policy_problems() == []
    rules = integrations_policy.current()
    assert rules["enabled"] is None and rules["http"] is True and rules["extensions"] is None
    assert rules["max_permissions"] == list(PERMISSIONS) and rules["problems"] == []
    assert integrations_policy.receipt_redactions() == []


def test_valid_values_still_apply(files):
    files({"allowed_tenants": [], "allow_fabric_runs": True, "show_preview": True})
    assert app_settings.tenant_allowed(AWAY)  # an empty list still means any tenant
    assert app_settings.value("allow_fabric_runs") is True and app_settings.value("show_preview") is True
    files({"allowed_tenants": [HOME.upper()]})
    assert app_settings.tenant_allowed(HOME) and not app_settings.tenant_allowed(AWAY)
    files({"support_url": 5})  # not a restriction: ignored and reported, as before
    assert app_settings.load()["support_url"] == {"value": "", "source": "default"}
    assert app_settings.load()["allow_fabric_runs"]["value"] is True


def test_a_broken_file_still_lets_people_sign_in(files):
    from fabric_desktop.auth import Authenticator

    class Signed:
        signed_out = False

        def sign_out(self):
            self.signed_out = True

        def signed_in_tenant(self):
            return HOME
    me = Signed()
    files("{not json")
    Authenticator._check_tenant(me, {"id_token_claims": {"tid": HOME}})
    assert not me.signed_out
    assert app_settings.load()["allowed_tenants"]["source"] != "policy"
    [problem] = app_settings.policy_problems()
    assert "Sign-in still works" in problem and "no sign-in" not in problem
    files({"allowed_tenants": [AWAY]})
    with pytest.raises(RuntimeError, match="belongs to another organization"):
        Authenticator._check_tenant(Signed(), {"id_token_claims": {"tid": HOME}})


def test_a_broken_file_keeps_the_last_copy_that_could_be_read(files):
    files({"allow_fabric_runs": False, "show_preview": True, "allowed_tenants": [HOME]})
    assert app_settings.load()["allow_fabric_runs"]["value"] is False  # read once: a copy is kept
    files('{"allow_fabric_runs": true,')  # IT's next push has a stray comma
    s = app_settings.load()
    assert s["allow_fabric_runs"] == {"value": False, "source": "policy"}
    assert s["show_preview"] == {"value": True, "source": "policy"}  # not locked down: the copy applies
    assert app_settings.tenant_allowed(HOME) and not app_settings.tenant_allowed(AWAY)
    assert app_settings.read_policy()[1] is None  # so sign-in isn't blocked
    [problem] = app_settings.policy_problems()
    assert str(files.path) in problem and "last copy that could be read" in problem
    files({"allow_fabric_runs": True})  # fixed: the new file applies, and becomes the copy
    assert app_settings.load()["allow_fabric_runs"]["value"] is True and app_settings.policy_problems() == []
    files("{broken")
    assert app_settings.load()["allow_fabric_runs"]["value"] is True


def test_removing_the_file_forgets_the_copy(files):
    files({"allow_fabric_runs": False})
    app_settings.load()
    files.path.unlink()  # IT removed the policy
    assert app_settings.load()["allow_fabric_runs"] == {"value": True, "source": "default"}
    assert not app_settings.saved_policy_path().exists()
    files("{broken")  # a broken file later has no old copy to fall back on
    assert_everything_restricted()


def test_a_damaged_copy_fails_closed(files):
    files({"show_preview": True})
    app_settings.load()
    app_settings.saved_policy_path().write_text("{damaged")
    files("{broken")
    assert_everything_restricted()


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows access check")
def test_the_windows_check_runs(files):
    files({})
    assert USER_CAN_CHANGE(files.path) in (True, False)  # CI's account is an administrator, so usually False


def test_register_link_scheme_is_a_setting_the_shell_reads(files):
    files({"register_link_scheme": False})
    assert app_settings.policy_problems() == []
    files({"register_link_scheme": "false"})
    assert app_settings.policy_problems()[0].startswith("register_link_scheme should be bool")


@pytest.mark.parametrize("bad", ["prod", ["prod", 3], {"prod": True}])
def test_a_separate_promoter_list_that_cant_be_read_covers_test_and_prod(files, bad):
    files({"separate_promoter_envs": bad})
    assert app_settings.load()["separate_promoter_envs"] == {"value": ["test", "prod"], "source": "policy"}
    assert any("separate_promoter_envs" in p for p in app_settings.policy_problems())
    files({"separate_promoter_envs": ["prod"]})
    assert app_settings.value("separate_promoter_envs") == ["prod"]
    assert not any("separate_promoter_envs" in p for p in app_settings.policy_problems())
