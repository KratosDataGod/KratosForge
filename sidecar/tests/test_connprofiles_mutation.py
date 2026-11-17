"""Connection profile paths that mutation testing (batch 13, docs/quality/mutation-testing.md) showed
no test checked: the key file share refusal, a secret outside the exported profile, clouds that aren't
turned on, CLI profile and connection reading, and the store's delete of a missing profile."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import connprofiles as cp

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
PAT = "dapi" + "0123456789abcdef" * 2


def test_a_key_file_on_a_network_share_is_refused_with_its_own_reason():
    for path in ("\\\\server\\keys\\sf.p8", "//server/keys/sf.p8"):
        assert cp.key_file_problem(path, windows=True) == "the private key file must be on this PC, not a network share"
        assert cp.key_file_problem(path, windows=False) == "the private key file must be on this PC, not a network share"
    problems = cp.problems({"name": "SF", "kind": "snowflake", "connections": [
        {"platform": "snowflake", "account": "myorg-acct",
         "credential": {"method": "keypair", "private_key_file": "\\\\server\\keys\\sf.p8"}}]})
    assert "connections[0]: the private key file must be on this PC, not a network share." in problems


def test_import_refuses_a_secret_anywhere_in_the_document_not_only_in_the_profile():
    doc = cp.export(cp.default_profile())
    doc["note"] = PAT  # outside "profile": validate() of the profile alone would never see it
    with pytest.raises(cp.ProfileError) as caught:
        cp.import_(doc)
    assert caught.value.problems == ["note looks like a token, key or password. Profiles keep references only."]
    assert PAT not in str(caught.value)


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_databricks_on_a_cloud_that_isnt_turned_on_is_refused(cloud):
    problems = cp.problems({"name": "D", "kind": "databricks", "connections": [
        {"platform": "databricks", "cloud": cloud, "credential": {"method": "oauth-u2m"}}]})
    assert f"connections[0]: Databricks on {cloud.upper()} isn't supported yet; Azure Databricks is." in problems


def test_a_secret_method_needs_the_secret_even_when_the_client_id_is_there():
    for cred in ({"method": "service-principal", "tenant_id": "t", "client_id": "app"},
                 {"method": "service-principal", "tenant_id": "t", "secret": "env:SP_SECRET"}):
        problems = cp.problems({"name": "F", "kind": "fabric", "connections": [{"platform": "fabric", "credential": cred}]})
        assert problems == [("connections[0]: service-principal needs client_id and a secret reference "
                             "(env:NAME or credman:TARGET).")]
    ok = {"method": "service-principal", "tenant_id": "t", "client_id": "app", "secret": "env:SP_SECRET"}
    assert cp.problems({"name": "F", "kind": "fabric", "connections": [{"platform": "fabric", "credential": ok}]}) == []


def test_the_local_cache_defaults_to_ten_gigabytes():
    assert cp.normalize({"name": "L", "kind": "local"})["local"]["cache"] == {"enabled": True, "max_gb": 10}


def test_export_marks_only_filled_in_fields_as_needing_setup():
    p = cp.validate({"name": "D", "kind": "databricks", "connections": [
        {"platform": "databricks", "host": HOST, "credential": {"method": "oauth-u2m", "profile": ""}}]})
    cred = cp.export(p)["profile"]["connections"][0]["credential"]
    assert cred == {"method": "oauth-u2m", "profile": ""}
    p = cp.validate({"name": "D", "kind": "databricks", "connections": [
        {"platform": "databricks", "host": HOST, "credential": {"method": "cli-profile", "profile": "DEFAULT"}}]})
    cred = cp.export(p)["profile"]["connections"][0]["credential"]
    assert cred == {"method": "cli-profile", "profile": "", "needs_setup": True}


def test_cli_profiles_say_the_file_was_found_and_how_each_signs_in(tmp_path):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(f"[hostonly]\nhost = {HOST}\n\n[empty]\ncluster_id = c1\n\n[m2m]\nhost = {HOST}\n"
                   "client_id = app\nclient_secret = s3cr3t-value\n", encoding="utf-8")
    got = cp.databricks_cli_profiles(cfg)
    assert got["found"] is True
    by_name = {p["name"]: p["auth_type"] for p in got["profiles"]}
    assert by_name == {"hostonly": "databricks-cli", "empty": "", "m2m": "oauth-m2m"}


def test_a_long_cli_profile_or_connection_name_makes_an_id_of_forty_characters():
    name = "a" * 60
    dbx = cp.connection_from_cli_profile({"name": name, "host": HOST})
    sf = cp.connection_from_cli_connection({"name": name, "account": "myorg-acct"})
    assert dbx["id"] == "a" * 40 and sf["id"] == "a" * 40
    assert dbx["credential"]["profile"] == name and sf["credential"]["connection"] == name


def test_the_snowflake_folder_follows_this_computers_system(tmp_path, monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_HOME", raising=False)
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(cp, "os", SimpleNamespace(name="posix", environ={}))
    assert cp.snowflake_config_dir() == tmp_path / ".config" / "snowflake"
    monkeypatch.setattr(cp, "os", SimpleNamespace(name="nt", environ={}))
    assert cp.snowflake_config_dir() == tmp_path / "AppData" / "Local" / "snowflake"


def test_a_snowflake_cli_connection_warns_only_about_a_sign_in_it_cant_use(tmp_path):
    (tmp_path / "connections.toml").write_text(
        '[keys]\naccount = "myorg-acct"\nauthenticator = "SNOWFLAKE_JWT"\n\n'
        '[plain]\naccount = "myorg-acct"\n', encoding="utf-8")
    got = {c["name"]: c for c in cp.snowflake_cli_connections(tmp_path)["connections"]}
    assert got["keys"]["warnings"] == [] and got["keys"]["usable"] is True
    assert got["plain"]["usable"] is False
    assert got["plain"]["warnings"] == [("Signs in with snowflake, which KratosForge doesn't use. "
                                         "Key pair, programmatic access tokens and OAuth sign-in work.")]


def test_deleting_a_profile_that_isnt_saved_does_nothing(tmp_path):
    store = cp.ProfileStore(tmp_path)
    store.delete("never-saved")
    assert list(tmp_path.iterdir()) == []
