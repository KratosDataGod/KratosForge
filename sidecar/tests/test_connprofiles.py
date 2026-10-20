"""Connection profiles: simple by default, references instead of secrets, separate credentials per
platform, safe export and import, CLI profile discovery that never returns a token."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import connprofiles as cp

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
PAT = "dapi" + "0123456789abcdef" * 2
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXNpZ25hdHVyZQ"


def hybrid(**extra):
    return {"name": "Sales", "kind": "hybrid", "connections": [
        {"id": "fabric", "platform": "fabric", "workspace": {"id": "ws-1", "name": "Sales Dev"}},
        {"id": "dbx", "platform": "databricks", "host": HOST, "credential": {"method": "cli-profile", "profile": "DEFAULT"},
         "catalog": "main", "schema": "dev"}], **extra}


def test_a_local_profile_needs_only_a_name_and_kind():
    p = cp.validate({"name": "Laptop", "kind": "local"})
    assert p["connections"] == [] and p["defaults"]["engine"] == "local-duckdb"
    assert p["execution"]["remote_compute"] == "ask" and p["data_access"]["cross_platform_copy"] == "ask"
    assert p["validation"]["status"] == "not_checked" and set(p["environments"]) == set(cp.ENVIRONMENTS)
    assert p["id"].startswith("p-")


def test_the_default_profile_is_what_the_app_does_today():
    p = cp.default_profile()
    assert p["kind"] == "fabric" and p["connections"][0]["credential"] == {"method": "app-sign-in"}


def test_kinds_need_their_platforms():
    assert "needs a Azure Databricks connection" in " ".join(cp.problems({"name": "x", "kind": "databricks"}))
    assert any("needs a Microsoft Fabric connection" in m for m in cp.problems({"name": "x", "kind": "hybrid",
               "connections": [{"platform": "databricks"}]}))
    assert any("make it hybrid" in m for m in cp.problems({"name": "x", "kind": "fabric",
               "connections": [{"platform": "fabric"}, {"platform": "databricks"}]}))
    assert any("local profile has no platform" in m for m in cp.problems({"name": "x", "kind": "local",
               "connections": [{"platform": "fabric"}]}))
    assert cp.problems(hybrid()) == []
    assert any("needs a name" in m for m in cp.problems({"kind": "local"}))
    assert any("kind must be" in m for m in cp.problems({"name": "x", "kind": "cloud"}))


@pytest.mark.parametrize("value", [PAT, JWT, "Bearer abcdefghijklmnop", "https://u:p4ssw0rd@host/x"])
def test_secrets_anywhere_are_refused_and_never_repeated(value):
    data = hybrid(description=value)
    found = cp.problems(data)
    assert any("looks like a token" in m for m in found)
    assert value not in " ".join(found)
    with pytest.raises(cp.ProfileError):
        cp.validate(data)


def test_secret_fields_must_be_references():
    data = {"name": "x", "kind": "databricks", "connections": [
        {"platform": "databricks", "host": HOST, "credential": {"method": "oauth-m2m", "client_id": "abc", "secret": "hunter2"}}]}
    assert any("must be a reference" in m for m in cp.problems(data))
    data["connections"][0]["credential"]["secret"] = "env:DBX_SP_SECRET"
    assert cp.problems(data) == []
    data["connections"][0]["credential"]["secret"] = "credman:KratosForge/dbx-sp"
    assert cp.problems(data) == []
    data["connections"][0]["token"] = "abc"
    assert any("connections[0].token must be a reference" in m for m in cp.problems(data))


def test_methods_and_their_required_parts():
    base = {"name": "x", "kind": "databricks"}
    assert any("method must be one of" in m for m in cp.problems(base | {"connections": [
        {"platform": "databricks", "credential": {"method": "app-sign-in"}}]}))
    assert any("needs client_id and a secret" in m for m in cp.problems(base | {"connections": [
        {"platform": "databricks", "credential": {"method": "oauth-m2m"}}]}))
    assert any("name the Databricks CLI profile" in m for m in cp.problems(base | {"connections": [
        {"platform": "databricks", "credential": {"method": "cli-profile"}}]}))
    assert cp.problems({"name": "x", "kind": "fabric", "connections": [
        {"platform": "fabric", "credential": {"method": "managed-identity"}}]}) == []


@pytest.mark.parametrize("host", ["http://adb-1.2.azuredatabricks.net", "https://evil.example.com",
                                  "https://adb-1.2.azuredatabricks.net/sql", "https://adb-1.2.azuredatabricks.net.evil.com",
                                  "https://u:x@adb-1.2.azuredatabricks.net"])
def test_databricks_hosts_must_be_azure_workspace_addresses(host):
    data = hybrid()
    data["connections"][1]["host"] = host
    assert any(".host must be the workspace address" in m or "looks like" in m for m in cp.problems(data))


def test_other_databricks_clouds_are_isolated_and_not_on_yet():
    data = hybrid()
    data["connections"][1] |= {"cloud": "aws", "host": "https://dbc-1.cloud.databricks.com"}
    assert any("AWS isn't supported yet" in m for m in cp.problems(data))
    data["connections"][1]["cloud"] = "oracle"
    assert any("cloud must be one of" in m for m in cp.problems(data))


def test_hybrid_platforms_never_share_a_credential():
    data = hybrid()
    data["connections"][0]["credential"] = {"method": "service-principal", "tenant_id": "t", "client_id": "c",
                                            "secret": "env:SP"}
    data["connections"][1]["credential"] = {"method": "oauth-m2m", "client_id": "c", "secret": "env:SP"}
    assert any("different credentials" in m for m in cp.problems(data))
    data["connections"][1]["credential"]["secret"] = "env:DBX"
    assert cp.problems(data) == []


def test_mappings_and_deploy_targets_name_real_connections():
    data = hybrid(environments={"prod": {"dbx": {"schema": "prod"}, "nope": {}}},
                  deploy={"prod": {"dbx": {"bundle_target": "prod"}}, "qa": {}},
                  defaults={"connection": "ghost"})
    found = " ".join(cp.problems(data))
    assert "environments.prod maps 'nope'" in found and "deploy.qa" in found and "'ghost'" in found
    assert "environments.prod maps 'dbx'" not in found


def test_execution_policy_rules():
    found = " ".join(cp.problems(hybrid(execution={"remote_compute": "never", "allowed_remote_engines": ["databricks-sql"],
                                                   "budget": {"remote_minutes_per_day": -1}})))
    assert "no remote engines can be allowed" in found and "remote_minutes_per_day" in found
    assert any("data never moves" in m for m in cp.problems(hybrid(data_access={"cross_platform_copy": "always"})))


def test_export_drops_what_belongs_to_this_pc_and_import_asks_for_it_again():
    data = hybrid()
    data["connections"][0]["credential"] = {"method": "service-principal", "tenant_id": "t", "client_id": "c",
                                            "secret": "credman:KratosForge/sp"}
    p = cp.validate(data | {"validation": {"status": "ok", "checked_at": "2026-10-20T00:00:00Z", "problems": []}})
    doc = cp.export(p)
    text = json.dumps(doc)
    assert "credman:" not in text and "DEFAULT" not in text and "id" not in doc["profile"]
    assert doc["profile"]["validation"]["status"] == "not_checked"
    back = cp.import_(json.loads(text), existing_ids={p["id"]})
    assert back["id"] != p["id"] and back["validation"]["status"] == "warnings"
    assert len(back["validation"]["problems"]) == 2
    assert back["connections"][0]["credential"]["secret"] == "env:SET_ME"
    assert back["connections"][1]["credential"]["profile"] == "DEFAULT"


def test_import_refuses_secrets_and_other_files():
    doc = cp.export(cp.validate(hybrid()))
    doc["profile"]["description"] = PAT
    with pytest.raises(cp.ProfileError, match="looks like a token"):
        cp.import_(doc)
    with pytest.raises(cp.ProfileError, match="isn't a KratosForge"):
        cp.import_({"kind": "something"})


def test_switching_profiles_closes_sessions_and_waits_for_unsaved_work():
    a, b = cp.validate(hybrid(id="p-a")), cp.validate({"id": "p-b", "name": "Laptop", "kind": "local"})
    sessions = [{"notebook": "nb1", "engine": "local-duckdb"}, {"notebook": "nb2", "engine": "databricks-sql"}]
    plan = cp.switch_plan(a, b, sessions, ["nb1"])
    assert plan["close_sessions"] == sessions and not plan["can_switch"] and "nb1 has unsaved" in plan["blockers"][0]
    assert "closes 2 open sessions" in plan["message"]
    assert cp.switch_plan(a, b, [], [])["can_switch"]
    assert cp.switch_plan(a, a, sessions, ["nb1"])["same"] and cp.switch_plan(a, a, sessions, ["nb1"])["can_switch"]


def test_cli_profiles_are_read_without_their_secrets(tmp_path):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(f"""
[DEFAULT]
host = {HOST}
token = {PAT}

[oauth]
host = {HOST}/
auth_type = databricks-cli
cluster_id = 0123-456789-abc

[sp]
host = {HOST}
client_id = abc
client_secret = shh-very-secret
serverless_compute_id = auto
""")
    found = cp.databricks_cli_profiles(cfg)
    text = json.dumps(found)
    assert PAT not in text and "shh-very-secret" not in text
    by = {p["name"]: p for p in found["profiles"]}
    assert by["DEFAULT"]["auth_type"] == "pat" and "plain text" in by["DEFAULT"]["warning"]
    assert by["oauth"]["auth_type"] == "databricks-cli" and by["oauth"]["cluster_id"] == "0123-456789-abc"
    assert by["sp"]["auth_type"] == "oauth-m2m" and by["sp"]["serverless"] and not by["sp"]["warning"]
    conn = cp.connection_from_cli_profile(by["oauth"])
    assert conn["host"] == HOST and conn["credential"] == {"method": "cli-profile", "profile": "oauth"}
    assert cp.problems({"name": "x", "kind": "databricks", "connections": [conn]}) == []
    assert cp.databricks_cli_profiles(tmp_path / "missing")["found"] is False
    bad = tmp_path / "bad"
    bad.write_text("no section header\n")
    assert "error" in cp.databricks_cli_profiles(bad)


def test_store_saves_lists_and_keeps_an_active_profile(tmp_path):
    store = cp.ProfileStore(tmp_path)
    assert store.active() == "default" and store.load("default")["kind"] == "fabric"
    p = store.save(hybrid())
    assert json.loads((tmp_path / f"{p['id']}.json").read_text())["name"] == "Sales"
    assert [x["name"] for x in store.list()] == ["Sales"] and store.list()[0]["problems"] == 0
    store.set_active(p["id"])
    assert store.active() == p["id"]
    with pytest.raises(cp.ProfileError, match="Switch to another"):
        store.delete(p["id"])
    with pytest.raises(cp.ProfileError):
        store.save(hybrid(description=PAT))
    with pytest.raises(cp.ProfileError, match="letters, digits"):
        store.load("../evil")
    with pytest.raises(cp.ProfileError, match="no profile"):
        store.load("p-missing")
    store.set_active("default")
    store.delete(p["id"])
    assert store.list() == []
