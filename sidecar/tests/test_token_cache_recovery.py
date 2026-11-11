"""A saved sign-in that is damaged, from another account, or locked by another process leads to one
clean sign-in, never a crash, a hang or a window on every call. MSAL's sign-in window is a stand-in
that stores what a real sign-in stores; nothing signs in."""

from __future__ import annotations

import base64
import json
import time

import pytest

from fabric_desktop import auth, dpapi
from fabric_desktop.leases import _file_lock

OWN_APP = "11111111-2222-3333-4444-555555555555"
TENANT = "99999999-8888-7777-6666-555555555555"


@pytest.fixture(autouse=True)
def no_dpapi(monkeypatch):
    monkeypatch.setattr(dpapi, "available", lambda: False)
    monkeypatch.setattr(auth, "_broker_available", lambda: False)


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def signing_in(a: auth.Authenticator) -> list:
    """Stands in for the sign-in window: each call stores a sign-in for the scopes asked, as MSAL does."""
    windows: list = []

    def window(scopes, **kw):
        windows.append(scopes)
        claims = {"oid": "ana", "tid": TENANT, "preferred_username": "ana@contoso.com", "sub": "ana",
                  "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": a.client_id}
        response = {"access_token": f"token-{len(windows)}", "refresh_token": "rt", "expires_in": 3600,
                    "token_type": "Bearer", "scope": " ".join(scopes),
                    "client_info": _b64({"uid": "ana", "utid": TENANT}),
                    "id_token": f"{_b64({'alg': 'none'})}.{_b64(claims)}."}
        a.cache.add({"client_id": a.client_id, "scope": scopes, "response": response,
                     "token_endpoint": "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"})  # the app's own
        return {**response, "id_token_claims": claims}
    a.app.acquire_token_interactive = window
    a._check_tenant = lambda result: None
    return windows


DAMAGED = {
    "truncated": b'{"Account": {"a": {"home_account_id": "x"',
    "not an object": b"[]",
    "sections of the wrong kind": b'{"AccessToken": "x", "Account": 5}',
    "empty sections": b'{"Account": null, "RefreshToken": [1, 2]}',
    "binary": b"\xff\xfe\x00junk",
    "empty file": b"",
}


@pytest.mark.parametrize("data", DAMAGED.values(), ids=DAMAGED.keys())
def test_a_damaged_saved_sign_in_means_one_clean_sign_in(tmp_path, data, caplog):
    path = tmp_path / "msal_cache.bin"
    path.write_bytes(data)
    a = auth.Authenticator(OWN_APP, cache_path=path)  # never raises
    assert a.signed_in_user() is None and a.silent_token(auth.FABRIC_SCOPES) is None
    windows = signing_in(a)
    assert a.fabric_token() == "token-1"
    assert a.fabric_token() == "token-1"  # the second call is silent
    assert len(windows) == 1
    fresh = auth.Authenticator(OWN_APP, cache_path=path)  # the damaged file was replaced
    assert fresh.signed_in_user() == "ana@contoso.com"


def test_a_saved_sign_in_from_another_windows_account_means_one_sign_in(tmp_path, monkeypatch):
    path = tmp_path / "msal_cache.bin"
    path.write_bytes(auth.DPAPI_HEADER + b"another account's blob")
    monkeypatch.setattr(dpapi, "available", lambda: True)

    def refuse(blob):
        raise OSError("The parameter is incorrect")  # what DPAPI says for another account's data
    monkeypatch.setattr(dpapi, "unprotect", refuse)
    monkeypatch.setattr(dpapi, "protect", lambda data: b"mine:" + data)
    a = auth.Authenticator(OWN_APP, cache_path=path)
    windows = signing_in(a)
    a.fabric_token()
    a.fabric_token()
    assert len(windows) == 1
    assert path.read_bytes().startswith(auth.DPAPI_HEADER + b"mine:")  # replaced with this account's own


def test_a_lock_another_process_never_lets_go_of_doesnt_hang_sign_in(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "LOCK_SECONDS", 1.0)
    path = tmp_path / "msal_cache.bin"
    a = auth.Authenticator(OWN_APP, cache_path=path)
    windows = signing_in(a)
    with _file_lock(a.cache.lock_path):  # another process holds it and hangs
        started = time.monotonic()
        assert a.fabric_token() == "token-1"  # the sign-in is kept in memory
        assert a.fabric_token() == "token-1"  # and used: no second window
        assert time.monotonic() - started < 1.5  # one wait of LOCK_SECONDS, not one per call or entry
    assert len(windows) == 1 and a.cache.has_state_changed
    a.cache._busy_until = 0  # the holder let go, and the wait after a busy file is over
    a._save()
    assert auth.Authenticator(OWN_APP, cache_path=path).signed_in_user() == "ana@contoso.com"


def test_a_lock_with_a_time_limit_says_so(tmp_path):
    lock = tmp_path / "x.lock"
    with _file_lock(lock):
        with pytest.raises(TimeoutError, match="held by another process"):
            with _file_lock(lock, timeout=0.1):
                pass
    with _file_lock(lock, timeout=0.1):  # free again
        pass


def test_a_sign_out_while_the_file_is_busy_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "LOCK_SECONDS", 0.1)
    a = auth.Authenticator(OWN_APP, cache_path=tmp_path / "msal_cache.bin")
    signing_in(a)
    a.fabric_token()
    with _file_lock(a.cache.lock_path):
        with pytest.raises(RuntimeError, match="Couldn't sign out"):
            a.sign_out()
    assert a.signed_in_user() == "ana@contoso.com"  # nothing seemed to sign out
