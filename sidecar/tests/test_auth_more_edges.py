"""Sign-in: more edges. One token for several resources, a cache change MSAL makes in place, Windows
sign-in starting, a refused sign-in the file can't drop, a short token Entra won't renew, a forced
refresh the window then refuses, and the signed-in object id. MSAL is replaced; nothing signs in."""

from __future__ import annotations

import pytest

from fabric_desktop import auth, dpapi
from tests.test_auth_edges import OWN_APP, FakeApp, authenticator


@pytest.fixture(autouse=True)
def no_dpapi(monkeypatch):
    monkeypatch.setattr(dpapi, "available", lambda: False)
    monkeypatch.setattr(auth, "_broker_available", lambda: False)


def test_one_token_cant_cover_two_resources():
    with pytest.raises(ValueError, match="several resources"):
        auth.default_scope(["https://api.fabric.microsoft.com/Item.Read.All", "https://storage.azure.com/user_impersonation"])
    assert auth.default_scope(["https://storage.azure.com/user_impersonation"]) == "https://storage.azure.com/.default"


def test_a_change_msal_makes_in_place_is_written_under_the_lock(tmp_path, monkeypatch):
    cache = auth.SharedTokenCache(tmp_path / "msal_cache.bin")
    changed, written = [], []
    monkeypatch.setattr(auth.msal.SerializableTokenCache, "modify",
                        lambda self, kind, old, new=None: changed.append((kind, old, new)))
    monkeypatch.setattr(cache, "write_or_retry_later", lambda: written.append(True))
    cache.modify("AccessToken", {"k": 1}, {"secret": "x"})
    assert changed == [("AccessToken", {"k": 1}, {"secret": "x"})] and written == [True]
    with cache.locked():  # inside another change: the outer one writes
        cache.modify("AccessToken", {"k": 2})
    assert written == [True]


def test_windows_sign_in_is_used_when_its_broker_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_broker_available", lambda: True)
    made = []

    def app(client_id, **kw):
        made.append(kw)
        return FakeApp()

    monkeypatch.setattr(auth.msal, "PublicClientApplication", app)
    a = auth.Authenticator(cache_path=tmp_path / "c.bin")
    assert a.microsoft and a.broker is True
    assert made == [made[0]] and made[0]["enable_broker_on_windows"] is True


def test_another_app_registration_never_uses_windows_sign_in(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_broker_available", lambda: True)
    a = auth.Authenticator(OWN_APP, cache_path=tmp_path / "c.bin")
    assert a.microsoft is False and a.broker is False


def test_a_refused_sign_in_the_file_cant_drop_is_still_ended_here(tmp_path, monkeypatch):
    (tmp_path / "msal_cache.bin").write_text("{}", encoding="utf-8")  # a saved sign-in file to compare against
    a = authenticator(tmp_path, FakeApp())
    warned = []
    monkeypatch.setattr(auth._log, "warning", lambda msg, *args: warned.append(msg % args))
    forgot = []
    monkeypatch.setattr(a.cache, "forget_changes", lambda: forgot.append(True))

    def locked():
        raise OSError("file in use")

    monkeypatch.setattr(a.cache, "locked", locked)
    a._end({"home_account_id": "oid.tid"}, "AADSTS700082")
    assert forgot == [True]
    assert warned[0] == "The saved sign-in was refused (AADSTS700082); signing in again is needed"
    assert warned[1].startswith("Couldn't drop the refused sign-in from the saved file")
    assert a._ended() is True  # nothing asks Entra again until the file changes


def test_a_short_token_entra_wont_renew_is_not_handed_out(tmp_path):
    (tmp_path / "msal_cache.bin").write_text("{}", encoding="utf-8")
    a = authenticator(tmp_path, interactive=False)

    class App(FakeApp):
        def acquire_token_silent(self, scopes, account, force_refresh=False):
            self.calls.append(("silent", tuple(scopes), force_refresh))
            if force_refresh:
                a._ended_at = a.cache._signature()  # Entra said the sign-in is finished
                return None
            return {"access_token": "short", "expires_in": 60}

    a.app = App(accounts=[{"home_account_id": "oid.tid"}])
    with pytest.raises(auth.SignInRequired):
        a.storage_token()
    assert [c[2] for c in a.app.calls] == [False, True]


def test_a_forced_refresh_the_window_then_refuses_raises_the_refusal(tmp_path):
    refusal = {"error": "access_denied", "error_description": "AADSTS50105: The user isn't assigned to a role."}
    app = FakeApp(accounts=[{"home_account_id": "oid.tid"}], silent=None, interactive=refusal)
    a = authenticator(tmp_path, app)
    with pytest.raises(auth.SignInError) as raised:
        a.refreshed_token(auth.FABRIC_SCOPES)
    assert raised.value.code == "AADSTS50105"
    assert [c[0] for c in app.calls][-1] == "interactive"


def test_a_forced_refresh_headless_says_to_sign_in_again(tmp_path):
    a = authenticator(tmp_path, FakeApp(accounts=[{"home_account_id": "oid.tid"}]), interactive=False)
    with pytest.raises(auth.SignInRequired):
        a.refreshed_token(auth.FABRIC_SCOPES)


@pytest.mark.parametrize("accounts, oid", [
    ([{"home_account_id": "obj-1.tenant-1", "username": "ana@contoso.com"}], "obj-1"),
    ([{"home_account_id": "no-tenant-part"}], None),
    ([], None),
])
def test_the_signed_in_object_id_is_the_first_part_of_the_home_account(tmp_path, accounts, oid):
    a = authenticator(tmp_path, FakeApp(accounts=accounts))
    assert a.signed_in_object_id() == oid
