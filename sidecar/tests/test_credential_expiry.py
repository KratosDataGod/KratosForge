"""A sign-in that expires or is revoked, mid-run or between runs: one clear message to sign in again, no
retry storm against Entra or the platform, nothing that locks the account, and no request resent with a
token that was refused. Covers the app's sign-in (auth.py), the agent's app identities (credentials.py),
Databricks (databricks_auth / databricks_sql) and the Power BI embed. MSAL and HTTP are stand-ins."""

from __future__ import annotations

import base64
import json
import threading
import time

import pytest

from fabric_desktop import auth, credentials, dpapi, retrying
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.fabric_api import FabricClient
from fabric_desktop.powerbi_embed import EmbedClient

OWN_APP = "11111111-2222-3333-4444-555555555555"
HOME = "uid.tid"
REVOKED = {"error": "invalid_grant", "error_description": "AADSTS50173: The provided grant has expired due to it "
           "being revoked, a fresh auth token is needed."}
INACTIVE = {"error": "invalid_grant", "error_description": "AADSTS700082: The refresh token has expired due to "
            "inactivity."}
CONSENT = {"error": "invalid_grant", "suberror": "consent_required",
           "error_description": "AADSTS65001: The user or administrator has not consented to use the application."}


@pytest.fixture(autouse=True)
def no_dpapi(monkeypatch):
    monkeypatch.setattr(dpapi, "available", lambda: False)
    monkeypatch.setattr(auth, "_broker_available", lambda: False)


def _client_info() -> str:
    return base64.urlsafe_b64encode(json.dumps({"uid": "uid", "utid": "tid"}).encode()).decode().rstrip("=")


class Entra:
    """MSAL's app as the engine uses it, over the real shared cache. ``refuse`` is what a refresh gets."""

    def __init__(self, cache, refuse=None, cached="old", interactive="new"):
        self.cache, self.refuse, self.cached, self.interactive = cache, refuse, cached, interactive
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def get_accounts(self):
        return [{"home_account_id": HOME, "username": "you@contoso.com"}]

    def acquire_token_silent_with_error(self, scopes, account, force_refresh=False):
        with self.lock:
            if self.cached and not force_refresh:
                self.calls.append("cache")
                return {"access_token": self.cached, "expires_in": 3000}
            self.calls.append("refresh")
            return self.refuse or {"access_token": "refreshed", "expires_in": 3600}

    def acquire_token_interactive(self, scopes, **kw):
        self.calls.append("window")
        time.sleep(0.05)
        self.cached, self.refuse = self.interactive, None
        self.cache.add({"client_id": OWN_APP, "scope": list(scopes),
                        "token_endpoint": "https://login.microsoftonline.com/tid/oauth2/v2.0/token",
                        "response": {"access_token": self.interactive, "refresh_token": "rt2", "expires_in": 3600,
                                     "client_info": _client_info(), "token_type": "Bearer"}})
        return {"access_token": self.interactive, "expires_in": 3600}


def signed_in(tmp_path, interactive=True, **kw) -> tuple[auth.Authenticator, Entra]:
    a = auth.Authenticator(OWN_APP, cache_path=tmp_path / "msal_cache.bin", interactive=interactive)
    a.cache.add({"client_id": OWN_APP, "scope": auth.FABRIC_SCOPES,
                 "token_endpoint": "https://login.microsoftonline.com/tid/oauth2/v2.0/token",
                 "response": {"access_token": "old", "refresh_token": "rt", "expires_in": 3600,
                              "client_info": _client_info(), "token_type": "Bearer"}})
    app = Entra(a.cache, **kw)
    a.app = app
    a._check_tenant = lambda result: None
    return a, app


def _tokens(a: auth.Authenticator, kind) -> list:
    return list(auth.msal.TokenCache.search(a.cache, kind, query={"home_account_id": HOME}))


# ---- the app's sign-in


def test_a_revoked_sign_in_refused_mid_run_says_sign_in_again_and_drops_the_refused_tokens(tmp_path):
    a, entra = signed_in(tmp_path, interactive=False, refuse=REVOKED)
    assert _tokens(a, a.cache.CredentialType.REFRESH_TOKEN)
    with pytest.raises(auth.SignInRequired, match="sign in"):
        a.refreshed_token(auth.FABRIC_SCOPES)  # Fabric said 401 to the cached token
    assert entra.calls == ["refresh"]
    assert _tokens(a, a.cache.CredentialType.ACCESS_TOKEN) == []  # nobody sends the refused token again
    assert _tokens(a, a.cache.CredentialType.REFRESH_TOKEN) == []
    assert a.signed_in_user() == "you@contoso.com"  # the app still knows who to sign in again


def test_after_one_refusal_nothing_asks_entra_again_until_someone_signs_in(tmp_path):
    a, entra = signed_in(tmp_path, interactive=False, refuse=INACTIVE, cached="")
    for _ in range(20):  # every background poll and every table of a run
        with pytest.raises(auth.SignInRequired):
            a.fabric_token()
        assert a.silent_token(auth.STORAGE_SCOPES) is None
    assert entra.calls == ["refresh"]  # one refusal, not one per call: no storm, nothing for Entra to throttle


def test_a_sign_in_in_the_app_lets_a_scheduled_run_use_the_new_one(tmp_path):
    a, entra = signed_in(tmp_path, interactive=False, refuse=REVOKED, cached="")
    with pytest.raises(auth.SignInRequired):
        a.fabric_token()
    app = auth.Authenticator(OWN_APP, cache_path=a.cache_path)  # the app, in its own process
    app.app = Entra(app.cache, refuse=REVOKED, cached="")
    app._check_tenant = lambda result: None
    app._sign_in(app.scopes_for(auth.FABRIC_SCOPES))  # the person signs in again there
    entra.cached, entra.refuse = "new", None
    assert a.fabric_token() == "new"  # the file changed, so the scheduled run asks again


def test_in_the_app_a_refused_sign_in_opens_one_sign_in_window_never_the_refused_token(tmp_path):
    a, entra = signed_in(tmp_path, refuse=REVOKED)
    assert a.refreshed_token(auth.FABRIC_SCOPES) == "new"
    assert entra.calls == ["refresh", "window"]


MFA = {"error": "invalid_grant", "suberror": "basic_action",
       "error_description": "AADSTS50076: Due to a configuration change, you must use multi-factor authentication."}


def test_a_refusal_that_doesnt_end_the_sign_in_still_never_hands_back_the_refused_token(tmp_path):
    a, entra = signed_in(tmp_path, refuse=MFA)  # Fabric refused "old"; Entra now wants MFA to refresh
    assert a.refreshed_token(auth.FABRIC_SCOPES) == "new"
    assert entra.calls[-1] == "window" and "refresh" in entra.calls


def test_an_unknown_code_that_starts_with_a_known_one_gets_no_advice():
    assert credentials.refusal("sp", {"error_description": "AADSTS70002220: something else"}) == ""
    assert "client secret has expired" in credentials.refusal("sp", {"error_description": "AADSTS7000222: x"})


def test_calls_waiting_on_a_sign_in_share_its_window(tmp_path):
    a, entra = signed_in(tmp_path, refuse=REVOKED, cached="")
    got: list[str] = []
    threads = [threading.Thread(target=lambda: got.append(a.fabric_token())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert got == ["new"] * 4 and entra.calls.count("window") == 1


def test_a_refusal_for_one_new_scope_doesnt_end_the_sign_in(tmp_path):
    a, entra = signed_in(tmp_path, interactive=False, refuse=CONSENT, cached="")
    with pytest.raises(auth.SignInRequired):
        a._token(auth.GRAPH_GROUP_SCOPES)  # consent for that scope: only that request
    assert not a._ended() and _tokens(a, a.cache.CredentialType.REFRESH_TOKEN)


@pytest.mark.parametrize("result,code", [(REVOKED, "AADSTS50173"), (INACTIVE, "AADSTS700082"),
                                         ({"error": "invalid_grant", "suberror": "bad_token"}, "bad_token"),
                                         (CONSENT, ""), ({"error": "temporarily_unavailable"}, ""),
                                         ({"access_token": "x"}, ""), (None, "")])
def test_which_refusals_end_the_sign_in(result, code):
    assert auth.ended(result) == code


def test_a_write_fabric_refuses_is_never_sent_again_with_the_refused_token(tmp_path):
    a, entra = signed_in(tmp_path, interactive=False, refuse=REVOKED)
    sent: list[str] = []

    class Http:
        def request(self, method, url, headers=None, **kw):
            sent.append(headers["Authorization"])
            return type("R", (), {"status_code": 401, "headers": {}, "text": "", "json": lambda self: {}})()

    client = FabricClient(a.fabric_token, session=Http(), sleep=lambda s: None,
                          refresh=lambda: a.refreshed_token(auth.FABRIC_SCOPES))
    with pytest.raises(auth.SignInRequired):
        client._request("POST", "/workspaces/w/items", json={"displayName": "x"})
    assert sent == ["Bearer old"]  # once: nothing half-done twice


def test_a_refused_sign_in_is_never_retried_as_passing_trouble():
    assert not retrying.is_retryable(auth.SignInRequired(auth.SIGNED_OUT_HEADLESS))


# ---- the agent's app identities


class Confidential:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def acquire_token_for_client(self, scopes):
        self.calls += 1
        return self.result


@pytest.mark.parametrize("code,says", [("AADSTS7000222", "client secret has expired"),
                                       ("AADSTS700027", "certificate"), ("AADSTS7000112", "disabled")])
def test_an_agent_whose_credential_expired_says_what_to_change_and_stops_asking(code, says, monkeypatch):
    app = Confidential({"error": "invalid_client", "error_description": f"{code}: refused. Trace ID: x"})
    sp = credentials.ServicePrincipal("tid", "cid", app=app)
    clock = [1000.0]
    monkeypatch.setattr(credentials.time, "monotonic", lambda: clock[0])
    for _ in range(10):
        with pytest.raises(auth.SignInRequired, match=says):
            sp.fabric_token()
    assert app.calls == 1 and sp.silent_token(auth.STORAGE_SCOPES) is None and app.calls == 1
    clock[0] += auth.REFUSED_WAIT
    app.result = {"access_token": "t", "expires_in": 3600}  # fixed, without a restart
    assert sp.fabric_token() == "t" and app.calls == 2


def test_passing_trouble_at_entra_is_asked_again_next_time():
    app = Confidential({"error": "temporarily_unavailable", "error_description": "AADSTS90033: busy"})
    sp = credentials.ServicePrincipal("tid", "cid", app=app)
    for _ in range(2):
        with pytest.raises(RuntimeError) as err:
            sp.fabric_token()
        assert not isinstance(err.value, auth.SignInRequired)
    assert app.calls == 2


# ---- Databricks


class Workspace:
    def __init__(self, good: set[str]):
        self.good, self.sent = good, []

    def request(self, method, url, headers=None, timeout=None, **kw):
        token = headers["Authorization"].removeprefix("Bearer ")
        self.sent.append((method, token))
        ok = token in self.good
        body = {"id": "1"} if ok else {"error_code": "UNAUTHENTICATED", "message": "Token is expired."}
        return type("R", (), {"status_code": 200 if ok else 401, "headers": {}, "content": b"{}",
                              "json": lambda self: body})()


def test_a_databricks_sign_in_renewed_with_the_cli_is_used_at_once(monkeypatch):
    tokens = iter([("old", time.time() + 3000), ("new", time.time() + 3600)])
    provider = dba.TokenProvider("https://adb-1.azuredatabricks.net", lambda: next(tokens), "oauth-u2m",
                                 sign_in_again="Sign in again with: databricks auth login --host h")
    ws = Workspace({"new"})
    client = dbsql.Client(provider, ws, sleep=lambda s: None)
    assert client._call("POST", "/api/2.0/sql/statements", json={}) == {"id": "1"}
    assert ws.sent == [("POST", "old"), ("POST", "new")]  # refused unread, then once with the new token
    assert client.warehouse("abc123") == {"id": "1"} and ws.sent[-1] == ("GET", "new")


def test_a_revoked_personal_access_token_is_sent_once_and_says_to_make_a_new_one(tmp_path):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text("[work]\nhost = https://adb-1.azuredatabricks.net\ntoken = dapi-revoked\n", encoding="utf-8")
    connection = {"platform": "databricks", "host": "https://adb-1.azuredatabricks.net",
                  "credential": {"method": "cli-profile", "profile": "work"}}
    ws = Workspace(set())
    client = dbsql.Client(dba.provider_for(connection, cfg_path=cfg, http=object()), ws, sleep=lambda s: None)
    with pytest.raises(dbsql.DatabricksError, match="personal access token in the CLI profile 'work' has expired"
                                                    " or was revoked") as err:
        client._call("POST", "/api/2.0/sql/statements", json={})
    assert "dapi-revoked" not in str(err.value)
    later = dbsql.Client(dba.provider_for(connection, cfg_path=cfg, http=object()), ws, sleep=lambda s: None)
    for c in (client, later, client):  # this action, a later one, and a new session for the same profile
        with pytest.raises(dba.DatabricksAuthError, match="isn't sent again. The personal access token in the CLI "
                                                          "profile 'work' has expired") as err:
            c._call("POST", "/api/2.0/sql/statements", json={})
        assert "dapi-revoked" not in str(err.value)
    assert ws.sent == [("POST", "dapi-revoked")]  # sent once, never again
    cfg.write_text("[work]\nhost = https://adb-1.azuredatabricks.net\ntoken = dapi-new\n", encoding="utf-8")
    ws.good.add("dapi-new")
    renewed = dbsql.Client(dba.provider_for(connection, cfg_path=cfg, http=object()), ws, sleep=lambda s: None)
    assert renewed._call("POST", "/api/2.0/sql/statements", json={}) == {"id": "1"}  # a new token works at once


def test_a_cli_that_hands_back_the_refused_token_gets_one_sign_in_again_message():
    provider = dba.TokenProvider("https://adb-1.azuredatabricks.net", lambda: ("cached", time.time() + 3000),
                                 "oauth-u2m", sign_in_again="Sign in again with: databricks auth login --host h")
    ws = Workspace(set())
    client = dbsql.Client(provider, ws, sleep=lambda s: None)
    with pytest.raises(dbsql.DatabricksError, match=r"refused the sign-in \(HTTP 401\)\. Sign in again with"):
        client._call("GET", "/api/2.0/sql/warehouses/abc")
    with pytest.raises(dba.DatabricksAuthError, match="isn't sent again. Sign in again with: databricks auth login"):
        client._call("GET", "/api/2.0/sql/warehouses/abc")
    assert ws.sent == [("GET", "cached")]


def test_a_service_principal_whose_token_was_refused_gets_a_new_one_never_the_old():
    tokens = iter([("m2m-1", time.time() + 3000), ("m2m-1", time.time() + 3000), ("m2m-2", time.time() + 3600)])
    provider = dba.TokenProvider("https://adb-1.azuredatabricks.net", lambda: next(tokens), "oauth-m2m",
                                 sign_in_again=dba.M2M_REFUSED)
    ws = Workspace({"m2m-2"})
    client = dbsql.Client(provider, ws, sleep=lambda s: None)
    with pytest.raises(dbsql.DatabricksError, match="service principal's sign-in was refused"):
        client._call("GET", "/api/2.0/sql/warehouses/abc")  # the endpoint handed back the refused token
    assert client._call("GET", "/api/2.0/sql/warehouses/abc") == {"id": "1"}  # its next token is new
    assert ws.sent == [("GET", "m2m-1"), ("GET", "m2m-2")]


def test_a_token_refused_by_one_workspace_is_still_sent_to_another():
    for host in ("https://adb-1.azuredatabricks.net", "https://adb-2.azuredatabricks.net"):
        provider = dba.TokenProvider(host, lambda: ("shared", None), "pat", sign_in_again="Renew it.")
        if host.startswith("https://adb-1"):
            provider.drop("shared")
            with pytest.raises(dba.DatabricksAuthError, match="Renew it."):
                provider.token()
        else:
            assert provider.token() == "shared"


def test_the_refused_list_keeps_fingerprints_and_stays_small():
    provider = dba.TokenProvider("https://adb-1.azuredatabricks.net", lambda: ("x", None), "pat")
    for i in range(dba.MAX_REFUSED + 10):
        provider.drop(f"secret-{i}")
    assert len(dba._REFUSED) == dba.MAX_REFUSED
    assert not any("secret" in k for k in dba._REFUSED)
    assert dba.was_refused(provider.host, f"secret-{dba.MAX_REFUSED + 9}")
    assert not dba.was_refused(provider.host, "secret-0")  # the oldest made room


def test_a_databricks_cli_that_can_no_longer_refresh_says_how_to_sign_in_again():
    def expired():
        raise dba.DatabricksAuthError("The Databricks CLI couldn't give a token: refresh token is invalid. "
                                      "Sign in again with: databricks auth login --host https://adb-1.azuredatabricks.net")
    tokens = iter([lambda: ("old", time.time() + 3000), expired])
    provider = dba.TokenProvider("https://adb-1.azuredatabricks.net", lambda: next(tokens)(), "oauth-u2m")
    ws = Workspace(set())
    with pytest.raises(dba.DatabricksAuthError, match="databricks auth login"):
        dbsql.Client(provider, ws, sleep=lambda s: None)._call("GET", "/api/2.0/sql/warehouses/abc")
    assert ws.sent == [("GET", "old")]


# ---- the Power BI embed


def test_an_embed_whose_sign_in_was_revoked_says_sign_in_again_after_one_new_token():
    sent: list[str] = []

    class Http:
        def request(self, method, url, headers=None, **kw):
            sent.append(headers["Authorization"])
            return type("R", (), {"status_code": 401, "headers": {}, "text": "",
                                  "json": lambda self: {"error": {"code": "TokenExpired", "message": "m"}}})()

    fresh = iter(["new"])
    embed = EmbedClient(FabricClient(lambda: "old", session=Http(), sleep=lambda s: None, refresh=lambda: next(fresh)))
    with pytest.raises(auth.SignInRequired, match="Sign in again"):
        embed.content("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002", "Report")
    assert sent == ["Bearer old", "Bearer new"]


def test_a_refusal_holds_however_the_workspace_address_is_spelled():
    dba.TokenProvider("https://adb-1.azuredatabricks.net", lambda: ("t", None), "pat").drop("t")
    other = dba.TokenProvider("HTTPS://ADB-1.azuredatabricks.net/", lambda: ("t", None), "pat", sign_in_again="Renew it.")
    with pytest.raises(dba.TokenRefused, match="Renew it. If you're sure the token is still valid, restart KratosForge"):
        other.token()
