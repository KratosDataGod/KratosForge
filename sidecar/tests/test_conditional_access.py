"""Sign-in under Conditional Access: what the organization's rules ask for reaches the sign-in window,
a refusal another window can't change opens no second window, a scheduled run says a person is needed,
and a device check refused in the browser says Windows sign-in didn't start. MSAL is replaced by a
stand-in; nothing signs in."""

from __future__ import annotations

import sys

import pytest

from fabric_desktop import auth, dpapi

OWN_APP = "11111111-2222-3333-4444-555555555555"
ACCOUNT = {"home_account_id": "oid.tid", "username": "ana@contoso.com"}
CLAIMS = '{"access_token":{"capolids":{"essential":true,"values":["c0ffee00-0000-0000-0000-000000000000"]}}}'
MFA = {"error": "interaction_required", "claims": CLAIMS,
       "error_description": "AADSTS50076: Due to a configuration change made by your administrator, you must use MFA."}


def refused(code, text="Access has been blocked by Conditional Access policies."):
    return {"error": "invalid_grant" if code != "AADSTS53003" else "access_denied", "error_description": f"{code}: {text}"}


@pytest.fixture(autouse=True)
def no_dpapi(monkeypatch):
    monkeypatch.setattr(dpapi, "available", lambda: False)
    monkeypatch.setattr(auth, "_broker_available", lambda: False)


class FakeApp:
    def __init__(self, silent=None, interactive=None, accounts=(ACCOUNT,)):
        self.accounts, self.silent, self.interactive = list(accounts), silent, interactive
        self.windows: list[dict] = []

    def get_accounts(self):
        return self.accounts

    def acquire_token_silent_with_error(self, scopes, account, force_refresh=False):
        return self.silent

    def acquire_token_interactive(self, scopes, **kw):
        self.windows.append(kw)
        if self.interactive and "access_token" in self.interactive:
            self.silent = self.interactive  # signed in: the next lookup finds it
        return self.interactive


def authenticator(tmp_path, app, client_id=OWN_APP, **kw):
    a = auth.Authenticator(client_id, cache_path=tmp_path / "msal_cache.bin", **kw)
    a.app = app
    a._check_tenant = lambda result: None
    return a


def test_what_conditional_access_asks_for_goes_to_the_sign_in_window(tmp_path):
    app = FakeApp(silent=MFA, interactive={"access_token": "new", "expires_in": 3600})
    assert authenticator(tmp_path, app).fabric_token() == "new"
    assert [w.get("claims_challenge") for w in app.windows] == [CLAIMS]


def test_a_sign_in_with_nothing_asked_sends_no_claims(tmp_path):
    app = FakeApp(silent=None, interactive={"access_token": "new", "expires_in": 3600}, accounts=())
    authenticator(tmp_path, app).fabric_token()
    assert "claims_challenge" not in app.windows[0]


def test_a_blocked_sign_in_opens_one_window_not_one_per_call(tmp_path):
    app = FakeApp(silent=MFA, interactive=refused("AADSTS53003"))
    a = authenticator(tmp_path, app)
    for _ in range(3):
        with pytest.raises(auth.SignInError) as err:
            a.fabric_token()
        assert err.value.code == "AADSTS53003"
    assert len(app.windows) == 1
    assert "No new sign-in window was opened" in str(err.value)
    a.retry_sign_in()  # the person chose Sign in: a window again
    with pytest.raises(auth.SignInError):
        a.fabric_token()
    assert len(app.windows) == 2


def test_a_blocked_sign_in_is_tried_again_after_a_while(tmp_path, monkeypatch):
    app = FakeApp(silent=MFA, interactive=refused("AADSTS53000", "Device is not in required device state."))
    a = authenticator(tmp_path, app)
    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    for _ in range(2):
        with pytest.raises(auth.SignInError):
            a.fabric_token()
    assert len(app.windows) == 1
    now[0] += auth.REFUSED_WAIT + 1
    with pytest.raises(auth.SignInError):
        a.fabric_token()
    assert len(app.windows) == 2


def test_a_refusal_another_window_can_change_still_opens_one(tmp_path):
    # The wrong account (another tenant's): the next window can pick the right one.
    app = FakeApp(silent=MFA, interactive=refused("AADSTS50020", "User account does not exist in tenant."))
    a = authenticator(tmp_path, app)
    for _ in range(2):
        with pytest.raises(auth.SignInError):
            a.fabric_token()
    assert len(app.windows) == 2


def test_the_service_sign_in_opens_a_window_after_a_block(tmp_path):
    from fabric_desktop.service import Service
    app = FakeApp(silent=MFA, interactive=refused("AADSTS53003"))
    a = authenticator(tmp_path, app)
    with pytest.raises(auth.SignInError):
        a.fabric_token()
    svc = Service.__new__(Service)
    svc.__dict__["_auth"] = a  # what Service.auth builds once
    svc._client_id, svc._auth_lock = OWN_APP, __import__("threading").Lock()
    app.interactive = {"access_token": "new", "expires_in": 3600}
    svc.sign_in()
    assert len(app.windows) == 2


@pytest.mark.parametrize("win, broker, client, noted", [
    ("win32", False, auth.MICROSOFT_CLIENT_ID, True),   # Windows sign-in should have run, and didn't
    ("win32", True, auth.MICROSOFT_CLIENT_ID, False),   # it ran: the PC really isn't managed
    ("win32", False, OWN_APP, False),                   # an organization's own app always uses the browser
    ("linux", False, auth.MICROSOFT_CLIENT_ID, False),
])
def test_a_device_check_refused_in_the_browser_says_windows_sign_in_didnt_start(tmp_path, monkeypatch, win, broker, client, noted):
    app = FakeApp(silent=MFA, interactive=refused("AADSTS53000", "Device is not in required device state: compliant."))
    a = authenticator(tmp_path, app, client_id=client)
    a.broker = broker
    monkeypatch.setattr(auth, "_window_handle", lambda: 0)
    monkeypatch.setattr(sys, "platform", win)
    with pytest.raises(auth.SignInError) as err:
        a.fabric_token()
    assert err.value.code == "AADSTS53000"
    assert (auth.BROWSER_NOTE in str(err.value)) is noted


def test_a_block_that_isnt_about_the_device_gets_no_browser_note(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    a = authenticator(tmp_path, FakeApp(silent=MFA, interactive=refused("AADSTS53003")), client_id=auth.MICROSOFT_CLIENT_ID)
    with pytest.raises(auth.SignInError) as err:
        a.fabric_token()
    assert auth.BROWSER_NOTE not in str(err.value)


def test_a_scheduled_run_says_a_person_must_take_the_step(tmp_path):
    a = authenticator(tmp_path, FakeApp(silent=MFA), interactive=False)
    with pytest.raises(auth.SignInRequired, match=r"step only a person can take.*\(AADSTS50076\)"):
        a.fabric_token()
    with pytest.raises(auth.SignInRequired, match=r"step only a person can take"):
        a.refreshed_token(auth.FABRIC_SCOPES)


def test_a_scheduled_run_with_no_sign_in_says_so(tmp_path):
    a = authenticator(tmp_path, FakeApp(silent=None, accounts=()), interactive=False)
    with pytest.raises(auth.SignInRequired, match="isn't signed in"):
        a.fabric_token()
    assert auth.headless_refusal({"error": "server_error"}) == auth.SIGNED_OUT_HEADLESS
    assert auth.headless_refusal({"error": "interaction_required"}).endswith("Open KratosForge and sign in, then try again.")


def test_a_sign_in_frequency_rule_ends_the_saved_sign_in_and_asks_once(tmp_path):
    # AADSTS70043: the refresh token is past the sign-in frequency Conditional Access sets.
    ended = {"error": "invalid_grant", "error_description": "AADSTS70043: The refresh token has expired due to sign-in frequency."}
    app = FakeApp(silent=ended, interactive={"access_token": "new", "expires_in": 3600})
    assert authenticator(tmp_path, app).fabric_token() == "new"
    assert len(app.windows) == 1 and "claims_challenge" not in app.windows[0]
