"""Settings > Connections (Preview, engines_service.py): profiles and the CLIs' own profiles listed by
reference, discovery against a fake workspace (GETs only), and adding a CLI profile or connection
to a profile by name, so its host or account always comes from the CLI's file, never from the app."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import audit, connprofiles
from fabric_desktop import engines_service as es
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql

from test_databricks_discovery import HOST, TOKEN, FakeWorkspace, app_python  # noqa: F401 (the app's Python 3.12)

CLI = {"path": "x", "found": True, "profiles": [
    {"name": "dev", "host": HOST, "auth_type": "databricks-cli", "cluster_id": "", "warehouse_id": "",
     "serverless": False, "warning": ""},
    {"name": "nohost", "host": "", "auth_type": "", "cluster_id": "", "warehouse_id": "", "serverless": False,
     "warning": ""}]}
SF = {"path": "y", "found": True, "default": "sf", "connections": [
    {"name": "sf", "account": "myorg-acct", "user": "dev", "role": "", "warehouse": "WH", "database": "SALES",
     "schema": "PUBLIC", "authenticator": "", "private_key_file": "", "method": "oauth-local", "usable": True,
     "warnings": []}]}


class Svc(es.EnginesMixin):
    def __init__(self, tmp_path, ws=None):
        self._tmp = tmp_path
        self.ws = ws or FakeWorkspace()
        self.clients = []

    def _profiles(self):
        (self._tmp / "profiles").mkdir(exist_ok=True)
        return connprofiles.ProfileStore(self._tmp / "profiles")

    def _discovery_client(self, connection):
        self.clients.append(connection)
        return dbsql.Client(dba.TokenProvider(HOST, lambda: (TOKEN, None), "test"), self.ws)


@pytest.fixture(autouse=True)
def clis(monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: CLI)
    monkeypatch.setattr(connprofiles, "snowflake_cli_connections", lambda folder=None: SF)


def test_the_new_calls_are_rpc_methods_and_only_the_reads_skip_the_audit():
    from fabric_desktop import server
    reads, writes = {"engine_profiles", "engine_discover"}, {"engine_connection_save", "engine_connection_remove"}
    assert reads | writes <= es.METHODS <= server.METHODS
    assert reads <= audit.READ_ONLY and not writes & audit.READ_ONLY


def test_profiles_lists_the_default_and_both_clis_by_reference(tmp_path):
    out = Svc(tmp_path).engine_profiles()
    assert [p["id"] for p in out["profiles"]] == ["default"] and out["active"] == "default"
    assert out["profiles"][0]["connections"][0]["platform"] == "fabric"
    assert [p["name"] for p in out["databricks_cli"]["profiles"]] == ["dev", "nohost"]
    assert out["snowflake_cli"]["connections"][0]["account"] == "myorg-acct"


def test_discover_reads_with_gets_only_and_suggests_compute(tmp_path):
    svc = Svc(tmp_path)
    out = svc.engine_discover("cli:dev")
    assert out["signed_in"] and out["host"] == HOST
    assert {m for m, _, _ in svc.ws.calls} == {"GET"}
    assert out["suggested"] == {"warehouse_id": "wh-run", "cluster_id": "c-ok"}
    assert svc.clients[0]["credential"] == {"method": "cli-profile", "profile": "dev"}
    assert TOKEN not in json.dumps(out)


def test_discover_a_refused_part_says_why_without_the_token(tmp_path):
    svc = Svc(tmp_path, FakeWorkspace(refuse={"/api/2.1/clusters/list": 403}))
    out = svc.engine_discover("cli:dev")
    assert not out["parts"]["clusters"]["ok"] and out["parts"]["warehouses"]["ok"]
    assert TOKEN not in json.dumps(out)


def test_discover_says_when_it_cant_sign_in(tmp_path):
    class NoSignIn(Svc):
        def _discovery_client(self, connection):
            raise dba.DatabricksAuthError(f"Sign in with databricks auth login (token {TOKEN})")
    out = NoSignIn(tmp_path).engine_discover("cli:dev")
    assert not out["signed_in"] and "databricks auth login" in out["error"] and TOKEN not in out["error"]


def test_discover_names_an_unknown_connection(tmp_path):
    with pytest.raises(ValueError, match="no Databricks CLI profile 'gone'"):
        Svc(tmp_path).engine_discover("cli:gone")


def test_saving_a_cli_profile_makes_a_databricks_profile_with_the_host_from_the_cli(tmp_path):
    svc = Svc(tmp_path)
    out = svc.engine_connection_save(profile_name="Sales dev", databricks_cli="dev", warehouse_id="wh-run",
                                     cluster_id="c-ok", catalog="main", schema="sales")
    saved = svc._profiles().load(out["profile"])
    assert saved["kind"] == "databricks" and saved["name"] == "Sales dev"
    conn = saved["connections"][0]
    assert conn["host"] == HOST and conn["credential"] == {"method": "cli-profile", "profile": "dev"}
    assert conn["compute"]["warehouse_id"] == "wh-run" and conn["compute"]["cluster_id"] == "c-ok"
    assert (conn["catalog"], conn["schema"]) == ("main", "sales")
    assert out["key"] == f"profile:{out['profile']}:dev"
    # The engine picker finds it.
    assert out["key"] in {c["key"] for c in svc.engine_connections()["connections"]}


def test_adding_to_the_default_profile_makes_it_hybrid_and_ids_stay_unique(tmp_path):
    svc = Svc(tmp_path)
    svc.engine_connection_save(profile="default", databricks_cli="dev")
    out = svc.engine_connection_save(profile="default", databricks_cli="dev", warehouse_id="wh-stop")
    saved = svc._profiles().load("default")
    assert saved["kind"] == "hybrid" and [c["id"] for c in saved["connections"]] == ["fabric", "dev", "dev-2"]
    assert out["connection"] == "dev-2"


def test_replace_changes_one_connection_in_place(tmp_path):
    svc = Svc(tmp_path)
    pid = svc.engine_connection_save(profile_name="P", databricks_cli="dev")["profile"]
    svc.engine_connection_save(profile=pid, databricks_cli="dev", warehouse_id="wh-run", replace="dev")
    conns = svc._profiles().load(pid)["connections"]
    assert [c["id"] for c in conns] == ["dev"] and conns[0]["compute"]["warehouse_id"] == "wh-run"


def test_snowflake_cli_connection_is_added_by_name(tmp_path):
    svc = Svc(tmp_path)
    pid = svc.engine_connection_save(profile_name="SF", snowflake_cli="sf")["profile"]
    saved = svc._profiles().load(pid)
    assert saved["kind"] == "snowflake"
    assert saved["connections"][0]["credential"] == {"method": "cli-connection", "connection": "sf"}
    assert svc.engine_profiles()["profiles"][1]["connections"][0]["account"] == "myorg-acct"


@pytest.mark.parametrize("kw, why", [
    ({"databricks_cli": "gone"}, "no Databricks CLI profile"),
    ({"databricks_cli": "nohost"}, "with a workspace"),
    ({"databricks_cli": "dev", "snowflake_cli": "sf"}, "Pick one"),
    ({}, "Pick one"),
    ({"databricks_cli": "dev", "warehouse_id": "https://evil/x"}, "warehouse id"),
    ({"databricks_cli": "dev", "catalog": "a\nb"}, "catalog"),
])
def test_save_refuses_what_isnt_a_cli_name_or_a_plain_id(tmp_path, kw, why):
    with pytest.raises(ValueError, match=why):
        Svc(tmp_path).engine_connection_save(profile_name="P", **kw)


def test_save_never_writes_a_value_that_looks_like_a_secret(tmp_path):
    with pytest.raises(connprofiles.ProfileError):
        Svc(tmp_path).engine_connection_save(profile_name="P", databricks_cli="dev", schema=TOKEN)
    assert not list((tmp_path / "profiles").glob("p-*.json"))


def test_remove_takes_one_connection_out_and_its_mappings(tmp_path):
    svc = Svc(tmp_path)
    pid = svc.engine_connection_save(profile_name="P", databricks_cli="dev")["profile"]
    svc.engine_connection_save(profile=pid, snowflake_cli="sf")
    data = svc._profiles().load(pid)
    data["defaults"]["connection"] = "dev"
    data["environments"]["dev"] = {"dev": {"catalog": "main"}}
    svc._profiles().save(data)
    svc.engine_connection_remove(pid, "dev")
    saved = svc._profiles().load(pid)
    assert saved["kind"] == "snowflake" and [c["id"] for c in saved["connections"]] == ["sf"]
    assert saved["defaults"]["connection"] == "" and saved["environments"]["dev"] == {}
    with pytest.raises(ValueError, match="no connection"):
        svc.engine_connection_remove(pid, "dev")
