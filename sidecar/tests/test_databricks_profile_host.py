"""A CLI profile's sign-in goes only to the workspace the profile names (KUDP hardening): a profile that
keeps its own token or client secret but names no workspace is refused in both places a sign-in is
built, provider_for and Databricks Connect's worker_env, before any secret leaves this PC."""

from __future__ import annotations

import pytest

from fabric_desktop import connprofiles
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import dbconnect as dbc

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
CONN = {"platform": "databricks", "host": HOST, "credential": {"method": "cli-profile", "profile": "dev"}}
SECRETS = {"pat": "token = t0ken", "service-principal": "client_id = a\nclient_secret = s3cret",
           "secret-only": "client_secret = s3cret", "azure-client-secret": "azure_client_secret = s3cret",
           "password": "username = u\npassword = s3cret"}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / ".databrickscfg"
    monkeypatch.setattr(connprofiles, "databricks_config_path", lambda: path)
    monkeypatch.setattr(dbc, "find_cli", lambda environ=None: "/opt/databricks/databricks")
    return path


def no_network(*a, **k):
    pytest.fail("a sign-in left this PC")


@pytest.mark.parametrize("body", SECRETS.values(), ids=SECRETS.keys())
def test_provider_for_refuses_a_hostless_profile_that_keeps_a_secret(cfg, body):
    cfg.write_text(f"[dev]\n{body}\n")
    http = type("H", (), {"post": staticmethod(no_network)})()
    with pytest.raises(dba.DatabricksAuthError, match="'dev' keeps a sign-in but names no workspace") as exc:
        dba.provider_for(CONN, http=http, run=no_network).token()
    assert f"host = {HOST}" in str(exc.value)
    assert "t0ken" not in str(exc.value) and "s3cret" not in str(exc.value)


@pytest.mark.parametrize("body", SECRETS.values(), ids=SECRETS.keys())
def test_worker_env_refuses_a_hostless_profile_that_keeps_a_secret(cfg, body):
    cfg.write_text(f"[dev]\n{body}\n")
    with pytest.raises(dba.DatabricksAuthError, match="'dev' keeps a sign-in but names no workspace"):
        dbc.worker_env(CONN, "c1", {})


def test_a_hostless_profile_signed_in_through_the_cli_is_still_used(cfg):
    # The CLI keeps that sign-in in its own token cache, per host; the profile holds no secret.
    cfg.write_text("[dev]\nauth_type = databricks-cli\n")
    assert dba.provider_for(CONN, http=object(), run=no_network).method == "databricks-cli"
    assert dbc.worker_env(CONN, "c1", {})["DATABRICKS_CONFIG_PROFILE"] == "dev"


@pytest.mark.parametrize("body", SECRETS.values(), ids=SECRETS.keys())
def test_a_profile_that_names_this_workspace_keeps_working(cfg, body):
    cfg.write_text(f"[dev]\nhost = {HOST}\n{body}\n")
    assert dbc.worker_env(CONN, "c1", {})["DATABRICKS_HOST"] == HOST
    if "token" in body:
        assert dba.provider_for(CONN, http=object()).token() == "t0ken"


def test_a_connection_with_no_host_takes_the_profiles(cfg):
    cfg.write_text(f"[dev]\nhost = {HOST}\ntoken = t0ken\n")
    provider = dba.provider_for(CONN | {"host": ""}, http=object())
    assert provider.host == HOST and provider.token() == "t0ken"


def test_check_profile_host_says_another_workspace_before_anything_else():
    with pytest.raises(dba.DatabricksAuthError, match="is for another workspace"):
        dba.check_profile_host("dev", {"host": "https://adb-6543210987654321.3.azuredatabricks.net",
                                       "token": "t"}, HOST)
    dba.check_profile_host("dev", {"host": HOST + "/", "token": "t"}, HOST)
    dba.check_profile_host("dev", {"client_id": "a"}, HOST)    # a client id alone isn't a secret
