"""Organization policy fails closed: a policy.json that exists but can't be read, or a restrictive key
of the wrong type, gives each restriction its most restrictive value, not the default (HD-010)."""

import json

import pytest

from fabric_desktop import app_settings
from fabric_desktop.fabric_api import POLICY_BLOCKED, FabricClient, FabricError
from fabric_desktop.integrations import policy as integrations_policy
from fabric_desktop.integrations.registry import PERMISSIONS
from fabric_desktop.receipts import REDACTIONS

HOME = "11111111-2222-3333-4444-555555555555"
AWAY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


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
    assert not app_settings.tenant_allowed(HOME) and not app_settings.tenant_allowed(AWAY)
    assert not app_settings.tenant_allowed(None)
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


def test_sign_in_blocked_by_a_broken_file_says_so(files):
    from fabric_desktop.auth import Authenticator

    class Signed:
        signed_out = False

        def sign_out(self):
            self.signed_out = True

        def signed_in_tenant(self):
            return HOME
    me = Signed()
    files("{not json")
    with pytest.raises(RuntimeError, match="settings file .* can't be read"):
        Authenticator._check_tenant(me, {"id_token_claims": {"tid": HOME}})
    assert me.signed_out
    files({"allowed_tenants": [AWAY]})
    with pytest.raises(RuntimeError, match="belongs to another organization"):
        Authenticator._check_tenant(Signed(), {"id_token_claims": {"tid": HOME}})
