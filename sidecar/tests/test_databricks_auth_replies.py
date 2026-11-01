"""What the Databricks CLI and a workspace's token endpoint answer, when the answer isn't the one
they document (KUDP hardening): a CLI that won't start, JSON that isn't an object, a token that
isn't text, a lifetime that would keep a token forever or fetch one on every call, and a CLI
reason long enough or odd enough to flood an error. Each is a DatabricksAuthError that never
repeats a secret. Nothing runs a CLI or reaches a workspace."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fabric_desktop.engines import databricks_auth as dba

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
SECRET = "Sup3r-S3cret-value"
PAT = "dapi" + "0123456789abcdef" * 2   # built here so no token-shaped text is committed


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(dba, "find_cli", lambda: "/usr/bin/databricks")

    def answer(returncode=0, stdout="", stderr=""):
        return lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return answer


def m2m(body):
    reply = SimpleNamespace(status_code=200, json=lambda: body)
    return dba._m2m_token(HOST, "abc", SECRET, SimpleNamespace(post=lambda *a, **k: reply), lambda: 1000.0)


# ---------------------------------------------------------------- the CLI


@pytest.mark.parametrize("error", [PermissionError("Access is denied"), OSError(8, "Exec format error")])
def test_a_cli_that_wont_start_says_where_it_is(cli, error):
    def run(*a, **k):
        raise error
    with pytest.raises(dba.DatabricksAuthError, match="at /usr/bin/databricks couldn't be started") as exc:
        dba._cli_token(["--host", HOST], run)
    assert type(error).__name__ in str(exc.value)


@pytest.mark.parametrize("stdout", [json.dumps(["access_token"]), json.dumps("tok"), "null",
                                    json.dumps({"access_token": {"value": SECRET}}),
                                    json.dumps({"access_token": 12345})])
def test_a_cli_answer_that_isnt_the_documented_object_asks_for_a_newer_cli(cli, stdout):
    with pytest.raises(dba.DatabricksAuthError, match="wasn't the JSON it documents") as exc:
        dba._cli_token(["--host", HOST], cli(stdout=stdout))
    assert SECRET not in str(exc.value)


def test_a_cli_answer_with_no_token_gives_an_empty_one_the_provider_refuses(cli):
    p = dba.TokenProvider(HOST, lambda: dba._cli_token(["--host", HOST], cli(stdout="{}")), "oauth-u2m")
    with pytest.raises(dba.DatabricksAuthError, match="gave no access token"):
        p.token()


def test_the_clis_reason_is_one_bounded_line_with_secrets_redacted(cli):
    stderr = f"Error: refresh failed\nError: \x1b[31mtoken {PAT} expired\x1b[0m " + "x" * 2000
    with pytest.raises(dba.DatabricksAuthError) as exc:
        dba._cli_token(["--host", HOST], cli(returncode=1, stderr=stderr))
    text = str(exc.value)
    assert PAT not in text and "\x1b" not in text and "\n" not in text
    assert len(text) < 500 and "…" in text and text.endswith(f"databricks auth login --host {HOST}")


def test_a_cli_with_nothing_on_stderr_still_says_how_to_sign_in(cli):
    with pytest.raises(dba.DatabricksAuthError, match=r"couldn't give a token\. Sign in again"):
        dba._cli_token(["--profile", "dev"], cli(returncode=1))


# ---------------------------------------------------------------- the token endpoint (M2M)


@pytest.mark.parametrize("lifetime", ["inf", "-inf", "nan", -60, "-1", 0.0000])
def test_a_lifetime_that_would_keep_a_token_forever_or_refetch_on_every_call_is_refused(lifetime):
    body = {"access_token": "t", "expires_in": lifetime}
    if lifetime == 0.0:   # 0 is "no lifetime given", as before: kept until it fails
        assert m2m(body) == ("t", None)
        return
    with pytest.raises(dba.DatabricksAuthError, match="didn't answer with the OAuth token it documents"):
        m2m(body)


@pytest.mark.parametrize("token", [{"value": SECRET}, ["t"], 12345])
def test_a_token_that_isnt_text_is_refused_never_sent_as_its_repr(token):
    with pytest.raises(dba.DatabricksAuthError, match="didn't answer with the OAuth token") as exc:
        m2m({"access_token": token, "expires_in": 3600})
    assert SECRET not in str(exc.value)


def test_a_documented_reply_keeps_its_token_and_lifetime():
    assert m2m({"access_token": "t", "expires_in": "3600"}) == ("t", 4600.0)
    assert m2m({"access_token": "t", "expires_in": 3600, "token_type": "Bearer"}) == ("t", 4600.0)
