"""PB-005: the app and scheduled runs share one token cache file. Each change is made to the file's
latest contents under a lock, so neither writes back a stale copy: a sign-out or a sign-in in one
process survives a save from another that read the file earlier."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from fabric_desktop import auth

OWN_APP = "11111111-2222-3333-4444-555555555555"
TENANT = "99999999-8888-7777-6666-555555555555"
SIDECAR = Path(auth.__file__).resolve().parents[1]


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def sign_in(a: auth.Authenticator, user: str, token: str = "access") -> None:
    """What MSAL stores for a sign-in or a token refresh of ``user``: account, id token, refresh and access token."""
    claims = {"oid": user, "tid": TENANT, "preferred_username": f"{user}@contoso.com", "sub": user,
              "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": a.client_id}
    a.cache.add({"client_id": a.client_id, "scope": ["s"],
                 "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
                 "response": {"access_token": token, "refresh_token": f"rt-{user}", "expires_in": 3600,
                              "token_type": "Bearer", "client_info": _b64({"uid": user, "utid": TENANT}),
                              "id_token": f"{_b64({'alg': 'none'})}.{_b64(claims)}."}})


def users_on_disk(path: Path) -> set[str]:
    """Who the file signs in, read as a new process would."""
    return {acc["username"].split("@")[0] for acc in auth.Authenticator(OWN_APP, cache_path=path).app.get_accounts()}


def refresh_tokens_on_disk(path: Path) -> set[str]:
    return {rt["secret"] for rt in json.loads(auth.read_cache(path)[0])["RefreshToken"].values()}


def test_a_sign_out_survives_a_stale_save(tmp_path):
    path = tmp_path / "msal_cache.bin"
    app = auth.Authenticator(OWN_APP, cache_path=path)
    sign_in(app, "ana")
    scheduled = auth.Authenticator(OWN_APP, cache_path=path)  # a scheduled run starts and reads the file
    assert scheduled.signed_in_user() == "ana@contoso.com"
    app.sign_out()
    sign_in(scheduled, "ana", token="refreshed")  # its refresh was already on its way
    scheduled._save()
    assert users_on_disk(path) == set() and refresh_tokens_on_disk(path) == set()
    assert scheduled.signed_in_user() is None


def test_a_sign_in_survives_a_stale_save(tmp_path):
    path = tmp_path / "msal_cache.bin"
    app = auth.Authenticator(OWN_APP, cache_path=path)
    sign_in(app, "ana")
    scheduled = auth.Authenticator(OWN_APP, cache_path=path)
    sign_in(app, "ben")  # another account signs in in the app
    sign_in(scheduled, "ana", token="refreshed")
    scheduled._save()
    assert users_on_disk(path) == {"ana", "ben"} and refresh_tokens_on_disk(path) == {"rt-ana", "rt-ben"}


def test_signing_in_again_here_brings_back_an_account_signed_out_elsewhere(tmp_path):
    path = tmp_path / "msal_cache.bin"
    app = auth.Authenticator(OWN_APP, cache_path=path)
    sign_in(app, "ana")
    other = auth.Authenticator(OWN_APP, cache_path=path)
    other.sign_out()
    assert app.signed_in_user() is None and app.cache.removed_elsewhere
    app.cache.removed_elsewhere.clear()  # what _token does before an interactive sign-in
    sign_in(app, "ana")
    assert users_on_disk(path) == {"ana"}


WRITER = """
import sys
from fabric_desktop import auth
sys.path.insert(0, sys.argv[3])
from test_token_cache_shared import OWN_APP, sign_in
a = auth.Authenticator(OWN_APP, cache_path=auth.Path(sys.argv[1]))
for i in range(15):
    sign_in(a, f"{sys.argv[2]}{i}")
"""


def test_two_processes_saving_at_once_keep_both_changes(tmp_path):
    path = tmp_path / "msal_cache.bin"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SIDECAR), os.environ.get("PYTHONPATH", "")])}
    here = str(Path(__file__).parent)
    runs = [subprocess.Popen([sys.executable, "-c", WRITER, str(path), who, here], env=env) for who in ("a", "b")]
    assert [r.wait(timeout=120) for r in runs] == [0, 0]
    assert users_on_disk(path) == {f"{who}{i}" for who in "ab" for i in range(15)}


def test_a_file_that_cant_be_read_for_a_moment_keeps_the_sign_in(tmp_path, monkeypatch):
    path = tmp_path / "msal_cache.bin"
    app = auth.Authenticator(OWN_APP, cache_path=path)
    sign_in(app, "ana")
    auth.Authenticator(OWN_APP, cache_path=path).sign_out()  # another process changes the file
    real = auth.read_cache

    def busy(p):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")
    monkeypatch.setattr(auth, "read_cache", busy)
    assert app.signed_in_user() == "ana@contoso.com"  # the lookup works on what it had
    monkeypatch.setattr(auth, "read_cache", real)
    assert app.signed_in_user() is None  # and the next one reads the change
