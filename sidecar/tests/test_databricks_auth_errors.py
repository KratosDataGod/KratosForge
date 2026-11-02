"""Databricks sign-in error paths (KUDP hardening): every failure is a DatabricksAuthError that says what
to do and never repeats a secret, including secrets in forms redaction doesn't recognise."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fabric_desktop.engines import databricks_auth as dba

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
SECRET = "Sup3r-S3cret-value"     # a client secret: no pattern redaction knows
CONN = {"platform": "databricks", "host": HOST}


def cli_profile(name="sp"):
    return CONN | {"credential": {"method": "cli-profile", "profile": name}}


@pytest.mark.parametrize("text, line", [
    (f"[sp]\nhost = {HOST}\nclient_secret {SECRET}\n", "line 3"),             # a line with no "="
    (f"client_secret = {SECRET}\n[sp]\nhost = {HOST}\n", "line 1"),           # before any section
    (f"[sp]\nhost = {HOST}\nclient_secret = {SECRET}\n[sp]\nhost = x\n", "line 4"),   # a section twice
    (f"[sp]\nhost = {HOST}\nclient_secret = {SECRET}\nclient_secret = {SECRET}\n", "line 4"),  # a key twice
])
def test_a_broken_cli_configuration_says_where_never_what(tmp_path, text, line):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(text)
    with pytest.raises(dba.DatabricksAuthError) as exc:
        dba.provider_for(cli_profile(), cfg_path=cfg, http=object())
    message = str(exc.value)
    assert "isn't a valid Databricks CLI configuration" in message and line in message
    assert SECRET not in message and "client_secret" not in message


def test_a_cli_configuration_that_isnt_text_says_so(tmp_path):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_bytes(b"\xff\xfe\x00[sp]")
    with pytest.raises(dba.DatabricksAuthError, match="couldn't be read"):
        dba.provider_for(cli_profile(), cfg_path=cfg, http=object())


def test_a_missing_cli_configuration_says_how_to_sign_in(tmp_path):
    with pytest.raises(dba.DatabricksAuthError, match="databricks auth login"):
        dba.provider_for(cli_profile(), cfg_path=tmp_path / "none", http=object())


class _Reply:
    def __init__(self, status=200, body=None, raw=False):
        self.status_code, self._body, self._raw = status, body, raw

    def json(self):
        if self._raw:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


@pytest.mark.parametrize("reply", [
    _Reply(raw=True),                                         # an HTML page from a proxy
    _Reply(body=["access_token"]),                            # JSON, but not an object
    _Reply(body={"access_token": "t", "expires_in": "soon"}),  # a lifetime that isn't a number
], ids=["not-json", "not-an-object", "bad-lifetime"])
def test_an_m2m_token_reply_that_isnt_the_documented_one_is_a_sign_in_error(reply):
    http = SimpleNamespace(post=lambda *a, **k: reply)
    with pytest.raises(dba.DatabricksAuthError, match="didn't answer with the OAuth token"):
        dba._m2m_token(HOST, "abc", SECRET, http, lambda: 0.0)


def test_an_m2m_network_failure_names_the_host_not_the_secret():
    def post(*a, **k):
        raise ConnectionError(f"proxy said no to {SECRET}")
    with pytest.raises(dba.DatabricksAuthError, match=f"Couldn't reach {HOST}: ConnectionError") as exc:
        dba._m2m_token(HOST, "abc", SECRET, SimpleNamespace(post=post), lambda: 0.0)
    assert SECRET not in str(exc.value)


def test_an_m2m_token_without_a_lifetime_is_kept_until_it_fails():
    http = SimpleNamespace(post=lambda *a, **k: _Reply(body={"access_token": "t"}))
    assert dba._m2m_token(HOST, "abc", SECRET, http, lambda: 0.0) == ("t", None)


def test_an_empty_token_is_an_error_never_an_empty_header():
    p = dba.TokenProvider(HOST, lambda: ("", None), "pat")
    with pytest.raises(dba.DatabricksAuthError, match="gave no access token"):
        p.headers()


def test_a_cli_answer_that_isnt_json_asks_for_a_newer_cli(monkeypatch):
    monkeypatch.setattr(dba, "find_cli", lambda: "/usr/bin/databricks")
    p = dba.provider_for(CONN | {"credential": {"method": "oauth-u2m"}}, http=object(),
                         run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="token: abc", stderr=""))
    with pytest.raises(dba.DatabricksAuthError, match="0.205 or later") as exc:
        p.token()
    assert "abc" not in str(exc.value)


@pytest.mark.parametrize("expiry", ["", "tomorrow", 12345, None])
def test_an_expiry_that_cant_be_read_means_no_known_expiry(expiry):
    assert dba._expiry(expiry) is None


@pytest.mark.parametrize("credential, problem", [
    ({"method": "cli-profile"}, "Name the Databricks CLI profile"),
    ({"method": "oauth-m2m", "client_id": "abc"}, "needs client_id and a secret reference"),
    ({"method": "oauth-m2m", "secret": "env:X"}, "needs client_id and a secret reference"),
    ({"method": "magic"}, "Unknown Databricks sign-in method 'magic'"),
])
def test_an_incomplete_sign_in_is_refused_before_anything_runs(credential, problem):
    with pytest.raises(dba.DatabricksAuthError, match=problem):
        dba.provider_for(CONN | {"credential": credential}, http=object(),
                         run=lambda *a, **k: pytest.fail("ran the CLI"))


def test_a_connection_for_another_platform_is_refused():
    with pytest.raises(dba.DatabricksAuthError, match="isn't a Databricks one"):
        dba.provider_for({"platform": "snowflake", "host": HOST}, http=object())


@pytest.mark.parametrize("ref", ["env:", "s3cret", "vault:x", 7])
def test_a_secret_that_isnt_a_reference_is_refused_without_repeating_it(ref):
    with pytest.raises(dba.DatabricksAuthError, match="must be a reference") as exc:
        dba.resolve_secret(ref, environ={})
    assert str(exc.value) == "The secret must be a reference (env:NAME or credman:TARGET)."


def test_json_tokens_from_the_cli_keep_their_expiry(monkeypatch):
    monkeypatch.setattr(dba, "find_cli", lambda: "/usr/bin/databricks")
    out = json.dumps({"access_token": "t", "expiry": "2030-01-01T00:00:00Z"})
    token, expires = dba._cli_token(["--host", HOST], lambda *a, **k: SimpleNamespace(returncode=0, stdout=out, stderr=""))
    assert token == "t" and expires == 1893456000.0
