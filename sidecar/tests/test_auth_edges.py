"""Sign-in: the edges the main tests don't reach. Scope URLs that aren't, the window Windows sign-in
opens over, a cache this Windows account can't decrypt (and DPAPI going away), a plain cache that
can't be encrypted yet, the Windows broker failing to start, a save that fails, a headless call with
no sign-in, and the interactive sign-in path. MSAL is replaced by a stand-in; nothing signs in."""

from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

import pytest

from fabric_desktop import auth, dpapi

OWN_APP = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def no_dpapi(monkeypatch):
    monkeypatch.setattr(dpapi, "available", lambda: False)
    monkeypatch.setattr(auth, "_broker_available", lambda: False)


class FakeApp:
    def __init__(self, accounts=(), silent=None, interactive=None):
        self.accounts, self.silent, self.interactive = list(accounts), silent, interactive
        self.calls = []

    def get_accounts(self):
        return self.accounts

    def acquire_token_silent(self, scopes, account, force_refresh=False):
        self.calls.append(("silent", tuple(scopes), force_refresh))
        return self.silent

    def acquire_token_interactive(self, scopes, **kw):
        self.calls.append(("interactive", tuple(scopes), tuple(sorted(kw))))
        if isinstance(self.interactive, Exception):
            raise self.interactive
        return self.interactive


def authenticator(tmp_path, app=None, **kw):
    a = auth.Authenticator(OWN_APP, cache_path=tmp_path / "msal_cache.bin", **kw)
    if app is not None:
        a.app = app
    a._check_tenant = lambda result: None
    return a


def test_a_scope_that_isnt_a_url_is_refused():
    with pytest.raises(ValueError, match="isn't a scope URL"):
        auth.resource_of("Item.ReadWrite.All")
    assert auth.resource_of("https://api.fabric.microsoft.com/") == "https://api.fabric.microsoft.com"


def test_windows_sign_in_opens_over_the_window_in_front(monkeypatch):
    import ctypes
    monkeypatch.setattr(sys, "platform", "win32")
    handles = iter([4242, 0])
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(GetForegroundWindow=lambda: next(handles))),
                        raising=False)
    assert auth._window_handle() == 4242
    assert auth._window_handle() is auth.msal.PublicClientApplication.CONSOLE_WINDOW_HANDLE  # nothing in front


def test_an_encrypted_cache_is_unusable_once_dpapi_is_gone(tmp_path):
    path = tmp_path / "msal_cache.bin"
    path.write_bytes(auth.DPAPI_HEADER + b"blob")
    assert auth.read_cache(path) == (None, False)


def test_a_plain_cache_that_cant_be_encrypted_yet_keeps_the_sign_in(tmp_path, monkeypatch, caplog):
    path = tmp_path / "msal_cache.bin"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dpapi, "available", lambda: True)  # so the plain file is due to be encrypted

    def refuse(self, force=False):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(auth.Authenticator, "_save", refuse)
    with caplog.at_level(logging.WARNING, logger=auth.__name__):
        a = auth.Authenticator(OWN_APP, cache_path=path)
    assert a.cache.migrate and "Couldn't encrypt the saved sign-in yet" in caplog.text


def test_the_windows_broker_failing_to_start_falls_back_to_the_browser(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(auth, "_broker_available", lambda: True)
    real = auth.msal.PublicClientApplication

    def app(client_id, **kw):
        if kw.get("enable_broker_on_windows"):
            raise RuntimeError("pymsalruntime failed to load")
        return real(client_id, **kw)

    monkeypatch.setattr(auth.msal, "PublicClientApplication", app)
    with caplog.at_level(logging.WARNING, logger=auth.__name__):
        a = auth.Authenticator(cache_path=tmp_path / "c.bin")
    assert a.microsoft and a.broker is False and "using the browser" in caplog.text


def test_a_save_that_fails_says_so_unless_it_was_the_encryption(tmp_path, monkeypatch, caplog):
    a = authenticator(tmp_path)

    def full():
        raise OSError("disk full")

    monkeypatch.setattr(a.cache, "write", full)
    with caplog.at_level(logging.WARNING, logger=auth.__name__):
        a._save(force=False)  # nothing changed: nothing written
        a.cache.has_state_changed = True
        a._save()
    assert "Couldn't save the sign-in" in caplog.text
    with pytest.raises(OSError):
        a._save(force=True)


def test_a_headless_call_with_no_sign_in_says_to_open_the_app(tmp_path):
    a = authenticator(tmp_path, FakeApp(), interactive=False)
    with pytest.raises(RuntimeError, match="isn't signed in to Fabric"):
        a.fabric_token()


def test_windows_sign_in_opens_over_the_app_window(tmp_path, monkeypatch):
    app = FakeApp(interactive={"access_token": "w"})
    a = authenticator(tmp_path, app)
    a.broker = True
    monkeypatch.setattr(auth, "_window_handle", lambda: 99)
    assert a.warehouse_token() == "w"
    assert app.calls[-1][0] == "interactive" and app.calls[-1][2] == ("parent_window_handle",)


def test_kusto_and_warehouse_tokens_are_for_their_own_resources(tmp_path):
    app = FakeApp(accounts=[{"home_account_id": "x"}], silent={"access_token": "t", "expires_in": 3600})
    a = authenticator(tmp_path, app)
    a.microsoft = True
    assert a.kusto_token() == "t" and a.warehouse_token() == "t"
    asked = [c[1][0] for c in app.calls]
    assert asked[0] == "https://kusto.kusto.windows.net/.default"
    assert asked[1].endswith("/.default") and "kusto" not in asked[1]


def test_a_browser_sign_in_left_open_times_out(tmp_path):
    a = authenticator(tmp_path, FakeApp(interactive=auth.msal.BrowserInteractionTimeoutError("closed")))
    with pytest.raises(auth.SignInError, match="wasn't finished within"):
        a.fabric_token()
