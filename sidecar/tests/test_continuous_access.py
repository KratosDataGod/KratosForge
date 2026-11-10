"""Continuous Access Evaluation (Preview): the sign-in tells Entra it handles claims challenges (CP1)
only while Preview is on, an API's 401 claims challenge is read safely, and the new token is asked for
with it, silently first and in the sign-in window only when a person must step in. MSAL and the APIs
are stand-ins; nothing signs in."""

from __future__ import annotations

import base64
import json

import pytest

from fabric_desktop import app_settings, auth, claims, dpapi
from fabric_desktop.fabric_api import FabricClient

OWN_APP = "11111111-2222-3333-4444-555555555555"
ACCOUNT = {"home_account_id": "oid.tid", "username": "ana@contoso.com"}
ASKED = '{"access_token":{"nbf":{"essential":true,"value":"1762800000"}}}'


def header(text=ASKED, error="insufficient_claims", pad=False):
    raw = base64.b64encode(text.encode()).decode()
    raw = raw if pad else raw.rstrip("=")
    return {"WWW-Authenticate": f'Bearer realm="", authorization_uri="https://login.microsoftonline.com/common/oauth2/authorize", '
                                f'error="{error}", claims="{raw}"'}


@pytest.fixture(autouse=True)
def no_dpapi(monkeypatch):
    monkeypatch.setattr(dpapi, "available", lambda: False)
    monkeypatch.setattr(auth, "_broker_available", lambda: False)


# The challenge


def test_a_claims_challenge_is_decoded():
    assert claims.challenge(header()) == ASKED
    assert claims.challenge(header(pad=True)) == ASKED
    text = '{"a":"??>??>"}'  # its base64 has + (standard) or - (URL-safe): both are read
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        raw = encode(text.encode()).decode()
        assert claims.challenge({"WWW-Authenticate": f'Bearer error="insufficient_claims", claims="{raw}"'}) == text


def test_the_header_name_is_matched_whatever_its_case():
    assert claims.challenge({"www-authenticate": header()["WWW-Authenticate"]}) == ASKED


@pytest.mark.parametrize("headers", [
    None, {}, {"WWW-Authenticate": "Bearer"},
    header(error="invalid_token"),               # not a claims challenge
    header(text="[1, 2]"),                        # not one JSON object
    header(text="not json"),
    header(text=json.dumps({"x": "y" * 5000})),   # longer than any real challenge
    {"WWW-Authenticate": 'Bearer error="insufficient_claims", claims="!!!"'},
    {"WWW-Authenticate": 'Bearer error="insufficient_claims"'},
])
def test_anything_else_is_no_challenge(headers):
    assert claims.challenge(headers) == ""


def test_a_refresh_gets_the_claims_only_when_it_can_take_them():
    calls = []
    refresh = claims.Refresh(lambda scopes, *more: calls.append((scopes, more)) or "new", ["s"])
    resp = type("R", (), {"headers": header()})()
    assert claims.renew(refresh, resp) == "new" and calls[-1] == (["s"], (ASKED,))
    assert claims.renew(refresh, type("R", (), {"headers": {}})()) == "new" and calls[-1] == (["s"], ())
    assert claims.renew(lambda: "plain", resp) == "plain"  # a refresh that takes none is called as before


# The sign-in


class FakeApp:
    def __init__(self, silent=None, interactive=None):
        self.silent, self.interactive = silent, interactive
        self.lookups: list[dict] = []
        self.windows: list[dict] = []

    def get_accounts(self):
        return [ACCOUNT]

    def acquire_token_silent_with_error(self, scopes, account, **kw):
        self.lookups.append(kw)
        return self.silent(kw) if callable(self.silent) else self.silent

    def acquire_token_interactive(self, scopes, **kw):
        self.windows.append(kw)
        return self.interactive


def authenticator(tmp_path, app=None, **kw):
    a = auth.Authenticator(OWN_APP, cache_path=tmp_path / "msal_cache.bin", **kw)
    if app is not None:
        a.app = app
    a._check_tenant = lambda result: None
    return a


@pytest.mark.parametrize("on", [True, False])
def test_cp1_is_declared_only_while_continuous_access_is_on(tmp_path, monkeypatch, on):
    made = []
    real = auth.msal.PublicClientApplication
    monkeypatch.setattr(auth.msal, "PublicClientApplication", lambda *a, **kw: made.append(kw) or real(*a, **kw))
    a = authenticator(tmp_path, continuous_access=on)
    assert a.continuous_access is on
    assert made[-1].get("client_capabilities") == (["CP1"] if on else None)


def test_the_new_token_is_asked_for_with_the_apis_claims(tmp_path):
    app = FakeApp(silent=lambda kw: {"access_token": "new", "expires_in": 3600} if kw.get("claims_challenge") else
                  {"access_token": "old", "expires_in": 3600})
    assert authenticator(tmp_path, app).refreshed_token(auth.FABRIC_SCOPES, ASKED) == "new"
    assert app.lookups[0] == {"force_refresh": True, "claims_challenge": ASKED}
    assert app.windows == []


def test_without_claims_the_lookup_is_as_before(tmp_path):
    app = FakeApp(silent={"access_token": "new", "expires_in": 3600})
    authenticator(tmp_path, app).refreshed_token(auth.FABRIC_SCOPES)
    assert app.lookups[0] == {"force_refresh": True}


def test_when_a_person_must_step_in_the_window_gets_the_apis_claims(tmp_path):
    def silent(kw):  # Entra wants the person (no claims of its own); the plain lookup has the refused token
        return {"error": "interaction_required", "error_description": "AADSTS50076: MFA"} if kw.get("force_refresh") \
            else {"access_token": "old", "expires_in": 3600}
    app = FakeApp(silent=silent, interactive={"access_token": "new", "expires_in": 3600})
    assert authenticator(tmp_path, app).refreshed_token(auth.FABRIC_SCOPES, ASKED) == "new"
    assert app.windows[0]["claims_challenge"] == ASKED


def test_entras_own_claims_win_over_the_apis_for_the_window(tmp_path):
    entra = '{"access_token":{"capolids":{"essential":true,"values":["x"]}}}'

    def silent(kw):
        return {"error": "interaction_required", "claims": entra} if kw.get("force_refresh") \
            else {"access_token": "old", "expires_in": 3600}
    app = FakeApp(silent=silent, interactive={"access_token": "new", "expires_in": 3600})
    authenticator(tmp_path, app).refreshed_token(auth.FABRIC_SCOPES, ASKED)
    assert app.windows[0]["claims_challenge"] == entra


def test_headless_gets_no_window_whatever_the_api_asks(tmp_path):
    app = FakeApp(silent={"error": "interaction_required", "error_description": "AADSTS50076: MFA"})
    with pytest.raises(auth.SignInRequired):
        authenticator(tmp_path, app, interactive=False).refreshed_token(auth.FABRIC_SCOPES, ASKED)
    assert app.windows == []


# The APIs


class Resp:
    def __init__(self, status, headers=None, body=None):
        self.status_code, self.headers, self._body = status, headers or {}, body or {}
        self.content = json.dumps(self._body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class Session:
    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []

    def request(self, method, url, headers=None, **kw):
        self.sent.append(headers["Authorization"])
        return self.replies.pop(0)


def test_fabric_sends_its_claims_challenge_to_the_refresh_and_tries_once_more():
    asked = []
    refresh = claims.Refresh(lambda scopes, c="": asked.append(c) or "new", auth.FABRIC_SCOPES)
    http = Session(Resp(401, header()), Resp(200, body={"value": []}))
    client = FabricClient(lambda: "old", session=http, refresh=refresh, sleep=lambda s: None)
    client._request("GET", "/workspaces")
    assert asked == [ASKED] and http.sent == ["Bearer old", "Bearer new"]


def test_a_fabric_401_without_a_challenge_refreshes_as_before():
    asked = []
    refresh = claims.Refresh(lambda scopes, *c: asked.append(c) or "new", auth.FABRIC_SCOPES)
    http = Session(Resp(401), Resp(200, body={}))
    FabricClient(lambda: "old", session=http, refresh=refresh, sleep=lambda s: None)._request("GET", "/workspaces")
    assert asked == [()]


def test_the_livy_and_kusto_clients_pass_the_challenge_too():
    from fabric_desktop.engines.fabric_spark import LivyClient
    asked = []
    refresh = claims.Refresh(lambda scopes, c="": asked.append(c) or "new", auth.FABRIC_SCOPES)
    livy = LivyClient(lambda: "old", "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", refresh=refresh)
    assert livy._fresh_token(Resp(401, header())) == "new" and asked == [ASKED]
    import inspect

    from fabric_desktop import warehouse_monitoring
    assert "claims.renew(refresh, resp)" in inspect.getsource(warehouse_monitoring)


# Preview


def test_the_service_turns_it_on_and_off_with_preview(tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    monkeypatch.setattr(auth.Authenticator, "__init__", lambda self, client_id="", cache_path=None, interactive=True,
                        continuous_access=False: self.__dict__.update(client_id=client_id, continuous_access=continuous_access))
    preview = {"on": True}
    real = app_settings.load
    monkeypatch.setattr(app_settings, "load", lambda: {**real(), "show_preview": {"value": preview["on"], "source": "user"}})
    svc = Service.__new__(Service)
    svc._client_id, svc._auth, svc.headless = OWN_APP, None, False
    svc._auth_lock = __import__("threading").Lock()
    first = svc.auth
    assert first.continuous_access is True and svc.auth is first  # built once
    preview["on"] = False
    assert svc.auth is not first and svc.auth.continuous_access is False
    assert isinstance(svc._refreshed(auth.FABRIC_SCOPES), claims.Refresh) or not callable(getattr(svc.auth, "refreshed_token", None))
