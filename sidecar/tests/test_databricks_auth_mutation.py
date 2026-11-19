"""Databricks sign-in edges the main tests don't pin down (mutation batch 131,
docs/quality/mutation-testing.md): limits at their exact values, how the CLI is started, the
refresh moment, the refused-token memory's size, env: references, and which sign-in a CLI profile
picks."""

from __future__ import annotations

import ctypes
import json
import subprocess
from types import SimpleNamespace

import pytest

from fabric_desktop.engines import databricks_auth as dba

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
CONN = {"platform": "databricks", "host": HOST}


class Reply:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class Http:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        return self.reply


@pytest.fixture(autouse=True)
def _no_refused(monkeypatch):
    monkeypatch.setattr(dba, "_REFUSED", {})


def test_the_limits_are_the_documented_ones():
    assert dba.CLI_TIMEOUT == 60 and dba.REFRESH_SECONDS == 120 and dba.MAX_REFUSED == 256
    assert dba.CRED_MAX_BLOB == 2560 and dba._PART_MAX == 300  # Windows' CRED_MAX_CREDENTIAL_BLOB_SIZE


def test_the_cli_is_started_as_documented(monkeypatch):
    monkeypatch.setattr(dba, "find_cli", lambda: "/usr/bin/databricks")
    seen = {}

    def run(args, **kw):
        seen.update(kw, args=args)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"access_token": "t"}), stderr="")

    assert dba._cli_token(["--host", HOST], run) == ("t", None)
    assert seen["args"] == ["/usr/bin/databricks", "auth", "token", "--host", HOST]
    assert seen["capture_output"] is True and seen["text"] is True and seen["encoding"] == "utf-8"
    assert seen["timeout"] == 60 and seen["stdin"] is subprocess.DEVNULL


def test_no_cli_says_how_to_install_it_and_a_hung_one_says_so(monkeypatch):
    monkeypatch.setattr(dba, "find_cli", lambda: None)
    with pytest.raises(dba.DatabricksAuthError, match="isn't installed"):
        dba._cli_token([], lambda *a, **k: pytest.fail("ran without a CLI"))
    monkeypatch.setattr(dba, "find_cli", lambda: "/usr/bin/databricks")

    def hang(*a, **k):
        raise subprocess.TimeoutExpired("databricks", 60)

    with pytest.raises(dba.DatabricksAuthError, match="within a minute"):
        dba._cli_token([], hang)


def test_a_token_is_fetched_again_from_exactly_refresh_seconds_before_it_expires():
    now = [1000.0]
    fetched = []

    def fetch():
        fetched.append(now[0])
        return f"t{len(fetched)}", 1000.0 + 600

    p = dba.TokenProvider(HOST, fetch, "x", clock=lambda: now[0])
    assert p.token() == "t1"
    now[0] = 1600 - dba.REFRESH_SECONDS - 0.001
    assert p.token() == "t1"
    now[0] = 1600 - dba.REFRESH_SECONDS
    assert p.token() == "t2" and len(fetched) == 2


def test_the_refused_memory_keeps_exactly_the_newest_max_refused():
    for n in range(dba.MAX_REFUSED):
        dba._remember_refused(HOST, f"t{n}")
    assert dba.was_refused(HOST, "t0") and len(dba._REFUSED) == dba.MAX_REFUSED
    dba._remember_refused(HOST, "one more")
    assert not dba.was_refused(HOST, "t0") and dba.was_refused(HOST, "t1") and dba.was_refused(HOST, "one more")


def test_advapi32_is_opened_with_last_error(monkeypatch):
    seen = []
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: seen.append((name, kw)) or "dll", raising=False)
    assert dba._advapi() == "dll" and seen == [("advapi32", {"use_last_error": True})]


def test_an_env_reference_reads_the_given_environment_or_this_pcs():
    assert dba.resolve_secret("env:DBX_SECRET", {"DBX_SECRET": "value"}) == "value"  # pragma: allowlist secret (fake)
    with pytest.raises(dba.DatabricksAuthError, match="DBX_SECRET isn't set"):
        dba.resolve_secret("env:DBX_SECRET", {"DBX_SECRET": ""})
    with pytest.raises(dba.DatabricksAuthError, match="DBX_SECRET isn't set"):
        dba.resolve_secret("env:DBX_SECRET", {})


def test_an_env_reference_with_no_environment_given_reads_this_pcs(monkeypatch):
    monkeypatch.setenv("KF_DBX_MUTATION_SECRET", "from-os")
    assert dba.resolve_secret("env:KF_DBX_MUTATION_SECRET") == "from-os"
    assert dba.resolve_secret("env:KF_DBX_MUTATION_SECRET", {"KF_DBX_MUTATION_SECRET": "given"}) == "given"  # pragma: allowlist secret (test value)


def test_a_missing_cli_profile_is_named(tmp_path):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(f"[other]\nhost = {HOST}\n")
    with pytest.raises(dba.DatabricksAuthError, match="no profile named 'sp'"):
        dba.provider_for(CONN | {"credential": {"method": "cli-profile", "profile": "sp"}}, cfg_path=cfg, http=object())


def test_a_long_cli_reason_is_cut_to_the_limit_with_an_ellipsis():
    cut = dba._part("x" * 400)
    assert len(cut) == dba._PART_MAX and cut == "x" * (dba._PART_MAX - 1) + "…"
    assert dba._part("y" * dba._PART_MAX) == "y" * dba._PART_MAX


def test_the_token_endpoint_is_asked_with_a_30_second_timeout_and_a_refusal_says_so():
    http = Http(Reply(200, {"access_token": "a", "expires_in": 0.5}))
    assert dba._m2m_token(HOST, "cid", "sec", http, lambda: 100.0) == ("a", 100.5)  # a lifetime under 1 s is still one
    url, kw = http.calls[0]
    assert url == f"{HOST}/oidc/v1/token" and kw["timeout"] == 30 and kw["auth"] == ("cid", "sec")
    with pytest.raises(dba.DatabricksAuthError, match="HTTP 401"):
        dba._m2m_token(HOST, "cid", "sec", Http(Reply(401, {})), lambda: 0.0)


def test_a_connection_holding_a_secret_value_is_refused_by_where_it_is():
    conn = CONN | {"credential": {"method": "oauth-m2m", "client_id": "c", "secret": "plain-value"}}  # pragma: allowlist secret (fake)
    with pytest.raises(dba.DatabricksAuthError, match="credential.secret must be a reference"):
        dba.provider_for(conn, http=object())


def test_with_no_http_given_the_apps_session_is_used(monkeypatch):
    from fabric_desktop import http_session
    marker = object()
    monkeypatch.setattr(http_session, "session", lambda: marker)
    monkeypatch.setattr(dba, "_m2m_token", lambda host, cid, sec, http, clock: (("tok" if http is marker else "wrong"), None))
    p = dba.provider_for(CONN | {"credential": {"method": "oauth-m2m", "client_id": "c", "secret": "env:S"}},
                         environ={"S": "v"})
    assert p.token() == "tok"


def test_a_cli_profile_with_a_client_id_but_no_secret_signs_in_with_the_cli(tmp_path):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(f"[sp]\nhost = {HOST}\nclient_id = abc\n")
    p = dba.provider_for(CONN | {"credential": {"method": "cli-profile", "profile": "sp"}}, cfg_path=cfg, http=object())
    assert p.method == "databricks-cli"
    cfg.write_text(f"[sp]\nhost = {HOST}\nclient_secret = s\n")  # pragma: allowlist secret (fake)
    p = dba.provider_for(CONN | {"credential": {"method": "cli-profile", "profile": "sp"}}, cfg_path=cfg, http=object())
    assert p.method == "databricks-cli"


def test_a_cli_profile_with_a_service_principal_signs_in_at_the_token_endpoint(tmp_path):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(f"[sp]\nhost = {HOST}\nclient_id = abc\nclient_secret = s\n")  # pragma: allowlist secret (fake)
    http = Http(Reply(200, {"access_token": "m2m", "expires_in": 3600}))
    p = dba.provider_for(CONN | {"credential": {"method": "cli-profile", "profile": "sp"}}, cfg_path=cfg, http=http)
    assert p.method == "oauth-m2m" and p.sign_in_again == dba.M2M_REFUSED and p.token() == "m2m"
    assert http.calls[0][1]["auth"] == ("abc", "s")


def test_oauth_m2m_reads_its_secret_reference_and_signs_in():
    http = Http(Reply(200, {"access_token": "m2m"}))
    p = dba.provider_for(CONN | {"credential": {"method": "oauth-m2m", "client_id": "c", "secret": "env:S"}},
                         http=http, environ={"S": "v"})
    assert p.method == "oauth-m2m" and p.token() == "m2m" and http.calls[0][1]["auth"] == ("c", "v")
