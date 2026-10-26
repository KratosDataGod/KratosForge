"""Organization policy: update control, keeping work off Fabric capacity, and allowed tenants."""

import json

import pytest

from fabric_desktop import app_settings
from fabric_desktop.auth import Authenticator
from fabric_desktop.fabric_api import POLICY_BLOCKED, FabricClient, FabricError
from fabric_desktop.service import Service

HOME = "11111111-2222-3333-4444-555555555555"
AWAY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def policy(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True)
    return lambda data: path.write_text(data if isinstance(data, str) else json.dumps(data))


def test_defaults_change_nothing(policy):
    s = app_settings.load()
    assert s["auto_update"]["value"] is True and s["allow_fabric_runs"]["value"] is True
    assert s["allowed_tenants"]["value"] == []
    assert app_settings.tenant_allowed(AWAY) and app_settings.tenant_allowed(None)
    assert app_settings.policy_problems() == []


def test_policy_problems_are_reported_not_applied(policy):
    policy({"auto_update": "false", "chanel": "stable", "channel": "nightly", "allowed_tenants": ["contoso.com"]})
    problems = app_settings.policy_problems()
    assert any(p.startswith("auto_update should be bool") for p in problems)
    assert any(p.startswith("chanel isn't a setting") for p in problems)
    assert any(p.startswith("channel must be") for p in problems)
    assert any(p.startswith("allowed_tenants must list tenant ids") for p in problems)
    assert app_settings.value("auto_update") is True  # a string "false" is ignored, not read as true or false
    policy("{not json")
    assert "last copy that could be read" in app_settings.policy_problems()[0]  # the file above was read
    policy("[]")
    assert "one JSON object" in app_settings.policy_problems()[0]


def test_service_shows_problems(policy):
    policy({"show_preview": "no"})
    assert Service.app_settings(None)["policy_problems"] == [  # type: ignore[arg-type]
        "show_preview should be bool, not str, so it takes its most restrictive value"]


def test_users_cannot_set_it_keys(policy):
    for key in ("auto_update", "allow_fabric_runs", "allowed_tenants"):
        with pytest.raises(ValueError, match="can change"):
            app_settings.save({key: False})


class _Session:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        raise AssertionError("policy should stop the call before it reaches Fabric")


def test_fabric_runs_can_be_turned_off(policy):
    policy({"allow_fabric_runs": False})
    session = _Session()
    client = FabricClient(lambda: "tok", session=session, sleep=lambda s: None)  # type: ignore[arg-type]
    with pytest.raises(FabricError, match="doesn't allow starting jobs") as exc:
        client.run_item_job("ws", "nb", "RunNotebook")
    assert exc.value.code == POLICY_BLOCKED
    with pytest.raises(FabricError, match="doesn't allow starting jobs"):
        client.run_item_execute("ws", "cj", "CopyJob")
    with pytest.raises(FabricError, match="scheduling"):
        client.create_schedule("ws", "nb", "RunNotebook", {})
    with pytest.raises(FabricError, match="scheduling"):
        client.update_schedule("ws", "nb", "RunNotebook", "s1", True, {})
    assert session.calls == []


class _App:
    """Just enough of msal.PublicClientApplication."""

    def __init__(self, tenant):
        self.accounts = [{"username": "pat@fabrikam.com", "home_account_id": f"oid.{tenant}"}]
        self.tenant = tenant

    def get_accounts(self):
        return list(self.accounts)

    def remove_account(self, account):
        self.accounts.remove(account)

    def acquire_token_silent(self, scopes, account):
        return {"access_token": "tok", "id_token_claims": {"tid": self.tenant}}


def _auth(tmp_path, tenant):
    auth = Authenticator(cache_path=tmp_path / "cache.bin", interactive=False)
    auth.app = _App(tenant)  # type: ignore[assignment]
    return auth


def test_other_tenants_are_signed_out(policy, tmp_path):
    policy({"allowed_tenants": [HOME.upper()]})
    assert _auth(tmp_path, HOME).fabric_token() == "tok"  # case doesn't matter
    away = _auth(tmp_path, AWAY)
    assert away.silent_token(["https://api.fabric.microsoft.com/.default"]) is None
    assert away.app.get_accounts() == []
    away = _auth(tmp_path, AWAY)
    with pytest.raises(RuntimeError, match="only allows signing in with its own"):
        away.fabric_token()
    assert away.app.get_accounts() == []
