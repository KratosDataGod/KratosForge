"""HD-013 / D-C2: on Windows the sign-in token cache is encrypted with DPAPI for the Windows account.

Elsewhere DPAPI is stood in by a reversible fake, so the read, write and migration paths run on
every CI runner; the round trip through the real CryptProtectData runs on the Windows runner."""

import sys

import pytest

from fabric_desktop import auth, dpapi

OWN_APP = "11111111-2222-3333-4444-555555555555"


def _add_token(a):
    a.cache.add({"client_id": a.client_id, "scope": ["s"],
                 "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                 "response": {"access_token": "secret-access", "expires_in": 3600, "token_type": "Bearer"}})


@pytest.fixture
def fake_dpapi(monkeypatch):
    """Windows DPAPI on any OS: reverses the bytes, and refuses blobs it didn't make."""
    monkeypatch.setattr(dpapi, "available", lambda: True)
    monkeypatch.setattr(dpapi, "protect", lambda data: b"FAKE" + data[::-1])

    def unprotect(data):
        if not data.startswith(b"FAKE"):
            raise OSError(13, "CryptUnprotectData failed")
        return data[4:][::-1]
    monkeypatch.setattr(dpapi, "unprotect", unprotect)


def test_saved_cache_is_encrypted_and_reads_back(tmp_path, fake_dpapi):
    path = tmp_path / "msal_cache.bin"
    a = auth.Authenticator(OWN_APP, cache_path=path)
    _add_token(a)
    a._save()
    raw = path.read_bytes()
    assert raw.startswith(auth.DPAPI_HEADER) and b"secret-access" not in raw
    assert sorted(tmp_path.iterdir()) == [path, path.with_name(path.name + ".lock")]  # the temporary file was swapped in
    again = auth.Authenticator(OWN_APP, cache_path=path)
    assert "secret-access" in again.cache.serialize()


def test_plain_cache_from_an_earlier_version_is_encrypted_on_first_start(tmp_path, fake_dpapi, monkeypatch):
    path = tmp_path / "msal_cache.bin"
    monkeypatch.setattr(dpapi, "available", lambda: False)
    a = auth.Authenticator(OWN_APP, cache_path=path)
    _add_token(a)
    a._save()
    assert b"secret-access" in path.read_bytes()  # what earlier versions wrote

    monkeypatch.setattr(dpapi, "available", lambda: True)
    b = auth.Authenticator(OWN_APP, cache_path=path)  # still signed in, nothing to do
    assert "secret-access" in b.cache.serialize()
    raw = path.read_bytes()
    assert raw.startswith(auth.DPAPI_HEADER) and b"secret-access" not in raw


def test_cache_another_account_cant_decrypt_means_signing_in_again(tmp_path, fake_dpapi):
    path = tmp_path / "msal_cache.bin"
    path.write_bytes(auth.DPAPI_HEADER + b"made by another Windows account")
    a = auth.Authenticator(OWN_APP, cache_path=path)
    assert a.app.get_accounts() == []
    assert path.read_bytes() == auth.DPAPI_HEADER + b"made by another Windows account"  # left until a sign-in replaces it
    _add_token(a)
    a._save()
    assert "secret-access" in auth.Authenticator(OWN_APP, cache_path=path).cache.serialize()


def test_damaged_plain_cache_is_not_rewritten(tmp_path, fake_dpapi):
    path = tmp_path / "msal_cache.bin"
    path.write_text('{"AccessToken": {"half-writ')
    a = auth.Authenticator(OWN_APP, cache_path=path)
    assert a.app.get_accounts() == [] and path.read_text() == '{"AccessToken": {"half-writ'


def test_off_windows_the_cache_stays_a_private_plain_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dpapi, "available", lambda: False)
    path = tmp_path / "msal_cache.bin"
    a = auth.Authenticator(OWN_APP, cache_path=path)
    _add_token(a)
    a._save()
    assert b"secret-access" in path.read_bytes()
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform != "win32", reason="real DPAPI is Windows only")
def test_real_dpapi_round_trip():
    blob = dpapi.protect(b"refresh-token")
    assert blob != b"refresh-token" and b"refresh-token" not in blob
    assert dpapi.unprotect(blob) == b"refresh-token"
    with pytest.raises(OSError):
        dpapi.unprotect(b"not a DPAPI blob")


def test_when_dpapi_refuses_the_last_file_is_kept_and_nothing_is_written_in_plain(tmp_path, fake_dpapi, monkeypatch):
    path = tmp_path / "msal_cache.bin"
    a = auth.Authenticator(OWN_APP, cache_path=path)
    _add_token(a)
    a._save()
    before = path.read_bytes()

    def refuse(data):
        raise OSError(2, "CryptProtectData failed")
    monkeypatch.setattr(dpapi, "protect", refuse)
    _add_token(a)
    a.cache.has_state_changed = True
    a._save()  # no error: the run that refreshed the token carries on
    assert path.read_bytes() == before and sorted(tmp_path.iterdir()) == [path, path.with_name(path.name + ".lock")]
