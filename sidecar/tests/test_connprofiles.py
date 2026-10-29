"""Connection profiles: simple by default, references instead of secrets, separate credentials per
platform, safe export and import, CLI profile discovery that never returns a token."""

from __future__ import annotations

import json
import os

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
    assert any("two or more platforms (it has: Azure Databricks)" in m for m in cp.problems(
        {"name": "x", "kind": "hybrid", "connections": [{"platform": "databricks"}]}))
    assert "needs a Snowflake connection" in " ".join(cp.problems({"name": "x", "kind": "snowflake"}))
    assert any("make it hybrid" in m for m in cp.problems({"name": "x", "kind": "snowflake",
               "connections": [{"platform": "snowflake"}, {"platform": "fabric"}]}))
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
    store.save({"id": "abc", "name": "Mine", "kind": "local"})
    assert [x["id"] for x in store.list()] == ["abc"]


@pytest.mark.parametrize("bad", ["x\n", "a/b", "", "x" * 41])
def test_ids_and_references_match_whole_values(bad):
    assert any("letters, digits" in m for m in cp.problems({"id": bad, "name": "x", "kind": "local"}))


def test_a_reference_with_a_trailing_newline_is_not_a_reference():
    data = {"name": "x", "kind": "databricks", "connections": [{"platform": "databricks", "host": HOST,
            "credential": {"method": "oauth-m2m", "client_id": "c", "secret": "env:X\n"}}]}
    assert any("must be a reference" in m for m in cp.problems(data))


# ---------------------------------------------------------------- Snowflake (SF-00)

KEY = "C:\\keys\\sf.p8"


def snowflake(**extra):
    return {"name": "Snow", "kind": "snowflake", "connections": [
        {"id": "sf", "platform": "snowflake", "account": "myorg-myaccount", "user": "CHRIS", "role": "DEV",
         "warehouse": "DEV_WH", "database": "SALES", "schema": "PUBLIC"}], **extra}


def test_a_snowflake_profile_defaults_to_browser_sign_in_and_is_schema_2():
    p = cp.validate({"name": "Snow", "kind": "snowflake", "connections": [{"platform": "snowflake"}]})
    conn = p["connections"][0]
    assert conn["credential"] == {"method": "oauth-local"} and conn["account"] == "" and p["schema"] == 2
    assert cp.problems(snowflake()) == []


@pytest.mark.parametrize("data", [
    {"name": "Laptop", "kind": "local"},
    {"name": "Fab", "kind": "fabric", "connections": [{"platform": "fabric"}]},
    {"name": "Dbx", "kind": "databricks", "connections": [{"platform": "databricks", "host": HOST}]},
    "hybrid",
])
def test_profiles_schema_1_knew_load_unchanged_and_save_as_schema_1(data, tmp_path):
    data = hybrid() if data == "hybrid" else data
    old = {**data, "schema": 1}
    data = {**data, "id": "p-same"}
    old = {**data, "schema": 1}
    assert cp.problems(old) == []
    assert cp.validate(old) == cp.validate(data)
    saved = cp.ProfileStore(tmp_path).save(old)
    assert saved["schema"] == 1 and json.loads((tmp_path / f"{saved['id']}.json").read_text())["schema"] == 1
    assert cp.export(saved)["schema"] == 1
    # A file stamped 2 that schema 1 could hold is written back as 1, so an older app still opens it.
    assert cp.validate({**data, "schema": 2})["schema"] == 1


def test_profiles_with_snowflake_or_other_hybrids_are_schema_2_and_newer_ones_are_refused():
    assert cp.validate(snowflake())["schema"] == 2 and cp.export(cp.validate(snowflake()))["schema"] == 2
    three = hybrid()
    three["connections"].append(snowflake()["connections"][0])
    assert cp.problems(three) == [] and cp.validate(three)["schema"] == 2
    assert any("newer than this app knows (2). Update KratosForge" in m
               for m in cp.problems({**snowflake(), "schema": 3}))


def test_hybrids_need_two_platforms_and_no_two_may_share_a_secret():
    fab_sf = {"name": "x", "kind": "hybrid", "connections": [{"platform": "fabric"}, *snowflake()["connections"]]}
    dbx_sf = {"name": "x", "kind": "hybrid", "connections": [hybrid()["connections"][1], *snowflake()["connections"]]}
    assert cp.problems(fab_sf) == [] and cp.problems(dbx_sf) == []
    assert any("two or more platforms" in m for m in cp.problems(
        {"name": "x", "kind": "hybrid", "connections": snowflake()["connections"]}))
    three = hybrid()
    three["connections"].append(snowflake()["connections"][0])
    three["connections"][0]["credential"] = {"method": "service-principal", "tenant_id": "t", "client_id": "c",
                                             "secret": "env:A"}
    three["connections"][1]["credential"] = {"method": "oauth-m2m", "client_id": "c", "secret": "env:B"}
    three["connections"][2]["credential"] = {"method": "pat", "secret": "env:C"}
    assert cp.problems(three) == []
    for shared_with in (0, 1):  # Snowflake against Fabric, then against Databricks
        data = json.loads(json.dumps(three))
        data["connections"][2]["credential"]["secret"] = data["connections"][shared_with]["credential"]["secret"]
        assert any("must use different credentials" in m and "Snowflake" in m for m in cp.problems(data))
    data = json.loads(json.dumps(three))
    data["connections"][2]["credential"] = {"method": "keypair", "private_key_file": "/keys/sf.p8",
                                            "passphrase": "env:A"}
    assert any("different credentials" in m for m in cp.problems(data))


@pytest.mark.parametrize("account", ["myorg-myaccount", "my_org-acct_1", "xy12345", "xy12345.east-us-2.azure"])
def test_snowflake_accounts_are_identifiers(account):
    data = snowflake()
    data["connections"][0]["account"] = account
    assert cp.problems(data) == []
    assert cp.snowflake_host(account) == f"https://{account.lower().replace('_', '-')}.snowflakecomputing.com"


@pytest.mark.parametrize("account", ["https://myorg-myaccount.snowflakecomputing.com", "myorg/acct", "me@myorg",
                                     "myorg-acct.evil.com/x", "a b"])
def test_snowflake_accounts_are_never_urls_or_paths(account):
    data = snowflake()
    data["connections"][0]["account"] = account
    assert any("account identifier" in m for m in cp.problems(data))
    with pytest.raises(ValueError):
        cp.snowflake_host(account)


def test_snowflake_sign_in_methods_and_their_parts():
    def found(cred):
        data = snowflake()
        data["connections"][0]["credential"] = cred
        return " ".join(cp.problems(data))
    assert found({"method": "oauth-local"}) == ""
    assert "programmatic access token needs a secret reference" in found({"method": "pat"})
    assert found({"method": "pat", "secret": "credman:snowflake-pat"}) == ""
    assert "must be a reference" in found({"method": "pat", "secret": "plain-value"})
    assert "name the Snowflake CLI connection" in found({"method": "cli-connection"})
    assert "name the private key file" in found({"method": "keypair"})
    assert "passphrase must be a reference" in found({"method": "keypair", "private_key_file": "/k/sf.p8",
                                                       "passphrase": "hunter2"})
    assert "must be one of: oauth-local, keypair, pat, cli-connection" in found({"method": "password"})


@pytest.mark.parametrize("path,windows,ok", [
    (KEY, True, True), ("D:/keys/sf.p8", True, True),
    ("\\\\server\\share\\sf.p8", True, False), ("\\\\?\\C:\\keys\\sf.p8", True, False),
    ("//server/share/sf.p8", True, False), ("keys\\sf.p8", True, False), ("/home/me/sf.p8", True, False),
    ("/home/me/sf.p8", False, True), ("sf.p8", False, False), ("//server/share/sf.p8", False, False), ("", True, False),
])
def test_key_files_are_full_paths_on_this_pc(path, windows, ok):
    assert (cp.key_file_problem(path, windows=windows) == "") is ok


def test_snowflake_profiles_refuse_pasted_tokens_and_keys():
    pem = "-----BEGIN " + "PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n-----END PRIVATE KEY-----"
    for value in (pem, JWT):
        found = cp.problems(snowflake(description=value))
        assert any("looks like a token" in m for m in found) and value not in " ".join(found)


@pytest.mark.parametrize("method", ["oauth-local", "keypair", "pat", "cli-connection"])
def test_a_plain_passphrase_or_raw_key_is_refused_under_every_method_and_never_exported(method):
    base = {"oauth-local": {}, "keypair": {"private_key_file": KEY}, "pat": {"secret": "env:SF_PAT"},
            "cli-connection": {"connection": "dev"}}[method]
    for field, value in (("passphrase", "correct horse battery"), ("private_key_raw", "MIIEvQIBADANBgkqhkiG9w0BAQEF"),
                         ("private_key_passphrase", "correct horse battery")):
        data = snowflake()
        data["connections"][0]["credential"] = {"method": method, **base, field: value}
        found = cp.problems(data)
        assert found and value not in " ".join(found), (method, field)
        with pytest.raises(ValueError) as err:
            cp.export(data)
        assert value not in str(err.value)


def test_snowflake_credentials_hold_only_their_methods_fields():
    data = snowflake()
    data["connections"][0]["credential"] = {"method": "pat", "secret": "env:SF_PAT", "connection": "dev"}
    assert any("can't hold connection" in m for m in cp.problems(data))


def test_export_drops_snowflake_parts_tied_to_this_pc_and_import_asks_for_them():
    data = snowflake()
    key = KEY if os.name == "nt" else "/home/me/sf.p8"  # a full path on the PC the test runs on
    data["connections"].append({"id": "sf2", "platform": "snowflake", "account": "myorg-other",
                                "credential": {"method": "keypair", "private_key_file": key,
                                               "passphrase": "env:SF_PASS"}})
    data["connections"].append({"id": "sf3", "platform": "snowflake",
                                "credential": {"method": "cli-connection", "connection": "my-cli-conn"}})
    data["connections"].append({"id": "sf4", "platform": "snowflake", "credential": {"method": "pat",
                                                                                     "secret": "credman:sf"}})
    doc = cp.export(cp.validate(data))
    text = json.dumps(doc)
    assert json.dumps(key)[1:-1] not in text and "SF_PASS" not in text and "credman:sf" not in text and "my-cli-conn" not in text
    back = cp.import_(doc)
    assert back["validation"]["status"] == "warnings" and len(back["validation"]["problems"]) == 3
    creds = [c["credential"] for c in back["connections"]]
    assert creds[1]["private_key_file"].endswith("SET_ME.p8") and creds[2]["connection"] == "default"
    assert creds[3]["secret"] == "env:SET_ME"


# ---------------------------------------------------------------- Snowflake CLI connections

SECRETS = {"password": "Hunter2-Secret-Value", "token": "tok-ABCDEF0123456789", "private_key_raw": "RAWKEYMATERIAL99",
           "private_key_passphrase": "PassphraseValue77"}


def _write(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def test_snowflake_cli_connections_are_read_without_their_secrets(tmp_path):
    _write(tmp_path, "config.toml", 'default_connection_name = "dev"\n[connections.ignored]\naccount = "x-y"\n')
    _write(tmp_path, "connections.toml", f"""
[dev]
account = "myorg-dev"
user = "CHRIS"
authenticator = "SNOWFLAKE_JWT"
private_key_file = "C:/keys/sf.p8"
private_key_passphrase = "{SECRETS['private_key_passphrase']}"

[pat]
account = "myorg-dev"
authenticator = "PROGRAMMATIC_ACCESS_TOKEN"
token = "{SECRETS['token']}"

[sso]
account = "myorg-dev"
authenticator = "externalbrowser"

[old]
account = "myorg-dev"
password = "{SECRETS['password']}"
private_key_raw = "{SECRETS['private_key_raw']}"
""")
    found = cp.snowflake_cli_connections(tmp_path)
    text = json.dumps(found) + repr(found)
    for value in SECRETS.values():
        assert value not in text
    assert found["found"] and found["default"] == "dev"
    by = {c["name"]: c for c in found["connections"]}
    assert set(by) == {"dev", "pat", "sso", "old"}  # connections.toml wins over config.toml
    assert by["dev"]["method"] == "keypair" and by["dev"]["usable"] and by["dev"]["private_key_file"] == "C:/keys/sf.p8"
    assert any("plain text" in w for w in by["dev"]["warnings"])
    assert by["pat"]["method"] == "pat" and any("plain text" in w for w in by["pat"]["warnings"])
    assert not by["sso"]["usable"] and any("single sign-on" in w for w in by["sso"]["warnings"])
    assert not by["old"]["usable"] and any("plain text" in w for w in by["old"]["warnings"])
    conn = cp.connection_from_cli_connection(by["dev"])
    assert conn["credential"] == {"method": "cli-connection", "connection": "dev"} and conn["account"] == "myorg-dev"
    assert cp.problems({"name": "x", "kind": "snowflake", "connections": [conn]}) == []


def test_a_broken_snowflake_file_never_echoes_its_lines(tmp_path):
    _write(tmp_path, "connections.toml", f'[dev]\naccount = "myorg-dev"\npassword = "{SECRETS["password"]}\n')
    found = cp.snowflake_cli_connections(tmp_path)
    assert found["error"] == f"Couldn't read {tmp_path / 'connections.toml'}."
    assert SECRETS["password"] not in json.dumps(found) and found["connections"] == []


def test_snowflake_cli_folder_follows_the_cli_rules(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("SNOWFLAKE_HOME", raising=False)
    assert cp.snowflake_config_dir(windows=True) == home / "AppData" / "Local" / "snowflake"
    (home / ".snowflake").mkdir()
    assert cp.snowflake_config_dir(windows=True) == home / ".snowflake"
    custom = tmp_path / "sf"
    monkeypatch.setenv("SNOWFLAKE_HOME", str(custom))
    assert cp.snowflake_config_dir(windows=True) is None  # set but missing: no fallback, as the CLI does
    assert cp.snowflake_cli_connections() == {"path": str(custom), "found": False, "default": None, "connections": []}
    custom.mkdir()
    assert cp.snowflake_config_dir(windows=True) == custom
    assert cp.snowflake_cli_connections() == {"path": str(custom), "found": False, "default": None, "connections": []}


def test_snowflake_hosts_use_hyphens_for_underscores():
    assert cp.snowflake_host("my_org-acct_1") == "https://my-org-acct-1.snowflakecomputing.com"


@pytest.mark.skipif(os.name == "nt", reason="mode bits are POSIX; the Snowflake CLI checks them only there")
def test_a_snowflake_file_others_can_change_gets_a_note(tmp_path):
    _write(tmp_path, "connections.toml", '[dev]\naccount = "myorg-dev"\n')
    assert "notes" not in cp.snowflake_cli_connections(tmp_path)
    (tmp_path / "connections.toml").chmod(0o666)
    (note,) = cp.snowflake_cli_connections(tmp_path)["notes"]
    assert "chmod 0600" in note
