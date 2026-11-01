"""Databricks Connect's worker environment and frame output (KUDP hardening): each sign-in method is
checked in the order a person can act on, a CLI profile for another workspace never lends its sign-in
to this one, and a frame with mixed types still shows. Nothing reaches a workspace."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import connprofiles
from fabric_desktop.engines import databricks_sql
from fabric_desktop.engines import dbconnect as dbc
from fabric_desktop.engines.databricks_auth import DatabricksAuthError

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
OTHER = "https://adb-6543210987654321.3.azuredatabricks.net"
CONN = {"platform": "databricks", "host": HOST, "credential": {"method": "cli-profile", "profile": "dev"}}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / ".databrickscfg"
    path.write_text(f"[dev]\nhost = {HOST}\nauth_type = databricks-cli\n")
    monkeypatch.setattr(connprofiles, "databricks_config_path", lambda: path)
    return path


@pytest.fixture
def no_cli(monkeypatch):
    monkeypatch.setattr(dbc, "find_cli", lambda environ=None: None)


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(dbc, "find_cli", lambda environ=None: "/opt/databricks/databricks")


def with_cred(cred):
    return CONN | {"credential": cred}


# ---------------------------------------------------------------- the order of the checks


@pytest.mark.parametrize("profile", ["", None])
def test_a_cli_profile_with_no_name_asks_for_one_before_looking_for_the_cli(cfg, no_cli, profile):
    with pytest.raises(DatabricksAuthError, match="^Name the Databricks CLI profile to use.$"):
        dbc.worker_env(with_cred({"method": "cli-profile", "profile": profile}), "c1", {})


def test_a_cli_profile_with_no_name_asks_for_one_when_the_cli_is_there_too(cfg, cli):
    with pytest.raises(DatabricksAuthError, match="^Name the Databricks CLI profile to use.$"):
        dbc.worker_env(with_cred({"method": "cli-profile"}), "c1", {})


def test_a_profile_that_isnt_in_the_configuration_is_named(cfg, cli):
    with pytest.raises(DatabricksAuthError, match="has no profile named 'prod'"):
        dbc.worker_env(with_cred({"method": "cli-profile", "profile": "prod"}), "c1", {})


# ---------------------------------------------------------------- a profile for another workspace


@pytest.mark.parametrize("body", ["auth_type = databricks-cli", "token = t", "client_id = a\nclient_secret = s"],
                         ids=["cli", "pat", "service-principal"])
def test_a_profile_for_another_workspace_never_signs_in_here(cfg, cli, body):
    # The SDK would take DATABRICKS_HOST over the profile's host and send that profile's sign-in here.
    cfg.write_text(f"[dev]\nhost = {OTHER}\n{body}\n")
    with pytest.raises(DatabricksAuthError, match="'dev' is for another workspace"):
        dbc.worker_env(CONN, "c1", {})


def test_a_profile_with_a_trailing_slash_on_its_host_is_the_same_workspace(cfg, cli):
    cfg.write_text(f"[dev]\nhost = {HOST}/\nauth_type = databricks-cli\n")
    assert dbc.worker_env(CONN, "c1", {})["DATABRICKS_CONFIG_PROFILE"] == "dev"


def test_a_profile_with_no_host_of_its_own_uses_the_connections(cfg, cli):
    cfg.write_text("[dev]\nauth_type = databricks-cli\n")
    env = dbc.worker_env(CONN, "c1", {})
    assert env["DATABRICKS_HOST"] == HOST and env["DATABRICKS_CONFIG_PROFILE"] == "dev"


def test_a_profile_whose_host_isnt_a_databricks_workspace_is_refused(cfg, cli):
    cfg.write_text("[dev]\nhost = https://evil.example.com\ntoken = t\n")
    with pytest.raises(DatabricksAuthError):
        dbc.worker_env(CONN, "c1", {})


# ---------------------------------------------------------------- each method without a CLI


@pytest.mark.parametrize("body", ["token = t", "client_id = a\nclient_secret = s"], ids=["pat", "service-principal"])
def test_a_profile_with_its_own_secret_needs_no_cli(cfg, no_cli, body):
    cfg.write_text(f"[dev]\nhost = {HOST}\n{body}\n")
    env = dbc.worker_env(CONN, "c1", {"PATH": "/bin"})
    assert env["DATABRICKS_CONFIG_PROFILE"] == "dev" and "DATABRICKS_CLI_PATH" not in env


def test_a_profile_with_only_a_client_id_still_needs_the_cli(cfg, no_cli):
    cfg.write_text(f"[dev]\nhost = {HOST}\nclient_id = a\n")
    with pytest.raises(DatabricksAuthError, match="uses the Databricks CLI"):
        dbc.worker_env(CONN, "c1", {})


def test_a_service_principal_needs_no_cli_and_its_reference_variable_is_dropped(cfg, no_cli):
    m2m = with_cred({"method": "oauth-m2m", "client_id": "abc", "secret": "env:SP_SECRET"})
    env = dbc.worker_env(m2m, "c1", {"PATH": "/bin", "SP_SECRET": "s3cret", "sp_secret": "s3cret"},
                         environ={"SP_SECRET": "s3cret"})
    assert env["DATABRICKS_CLIENT_SECRET"] == "s3cret" and env["DATABRICKS_CLIENT_ID"] == "abc"
    assert "SP_SECRET" not in env and "sp_secret" not in env and env["PATH"] == "/bin"


@pytest.mark.parametrize("cred", [{"method": "oauth-m2m", "client_id": "abc"},
                                  {"method": "oauth-m2m", "secret": "env:SP"}])
def test_a_service_principal_missing_a_part_is_refused_before_any_secret_is_read(cfg, cli, cred):
    with pytest.raises(DatabricksAuthError, match="needs client_id and a secret reference"):
        dbc.worker_env(with_cred(cred), "c1", {}, environ={"SP": "s"})


def test_a_service_principal_secret_that_isnt_set_says_which_variable(cfg, cli):
    m2m = with_cred({"method": "oauth-m2m", "client_id": "abc", "secret": "env:SP"})
    with pytest.raises(DatabricksAuthError, match="SP isn't set"):
        dbc.worker_env(m2m, "c1", {}, environ={})


@pytest.mark.parametrize("finder", ["cli", "no_cli"])
def test_an_unknown_method_is_refused_with_or_without_the_cli(cfg, request, finder):
    request.getfixturevalue(finder)
    with pytest.raises(DatabricksAuthError, match="Unknown Databricks sign-in method 'magic'"):
        dbc.worker_env(with_cred({"method": "magic"}), "c1", {})


def test_a_connection_with_no_method_signs_in_through_the_cli(cfg, cli):
    env = dbc.worker_env(with_cred({}), "c1", {})
    assert env["DATABRICKS_AUTH_TYPE"] == "databricks-cli" and "DATABRICKS_CONFIG_PROFILE" not in env


def test_uses_cli_is_false_for_methods_that_dont(cfg):
    assert dbc._uses_cli({"method": "oauth-m2m"}) is False
    assert dbc._uses_cli({"method": "magic"}) is False
    assert dbc._uses_cli({}) is True


# ---------------------------------------------------------------- frames


def test_a_frame_shows_its_rows_as_a_preview_with_no_total():
    out = dbc._frame_output({"columns": ["id", "name"], "rows": [[1, "a"], [2, None]]})
    assert out["total_rows"] is None and out["preview_only"] is True
    assert out["columns"] == ["id", "name"]
    assert out["rows"] == [[1, "a"], [2, None]]


def test_an_empty_frame_keeps_its_columns():
    out = dbc._frame_output({"columns": ["id", "name"], "rows": []})
    assert out["columns"] == ["id", "name"] and out["rows"] == []
    assert out["total_rows"] is None and out["preview_only"] is True


@pytest.mark.parametrize("rows", [[[1, "a"], ["two", "b"]], [[1, {"k": 1}], [2, [1, 2]]]],
                         ids=["int-and-text", "struct-and-list"])
def test_a_frame_arrow_cant_type_falls_back_to_text_and_keeps_nulls(rows):
    out = dbc._frame_output({"columns": ["id", "v"], "rows": rows + [[None, None]]})
    assert out["rows"][-1] == [None, None]
    assert all(isinstance(v, str) for r in out["rows"][:-1] for v in r)
    assert out["total_rows"] is None and out["preview_only"] is True


def test_the_cluster_is_read_with_the_connections_own_sign_in(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, provider):
            seen["provider"] = provider

        def cluster(self, cid):
            seen["cluster"] = cid
            return {"spark_version": "16.4.x-scala2.12", "state": "RUNNING"}

    from fabric_desktop.engines import databricks_auth
    monkeypatch.setattr(databricks_sql, "Client", Client)
    monkeypatch.setattr(databricks_auth, "provider_for", lambda conn: SimpleNamespace(conn=conn))
    info = dbc._read_cluster(CONN, "0123-456789-abcdefgh")
    assert info["state"] == "RUNNING" and seen["cluster"] == "0123-456789-abcdefgh"
    assert seen["provider"].conn is CONN
