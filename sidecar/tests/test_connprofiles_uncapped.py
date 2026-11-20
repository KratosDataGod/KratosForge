"""Connection profile paths that the uncapped mutation run (batch 184, docs/quality/mutation-testing.md)
showed no test checked."""

from __future__ import annotations

import pytest

from fabric_desktop import connprofiles as cp


def test_a_key_file_that_is_not_a_full_path_says_so():
    assert cp.key_file_problem("keys/sf.p8", windows=False) == "the private key file must be a full path"
    assert cp.key_file_problem("keys\\sf.p8", windows=True) == (
        "the private key file must be a full path, like C:\\keys\\snowflake.p8")


def test_a_key_in_a_message_is_cut_after_60_characters():
    assert cp._key_text("k" * 60) == "k" * 60
    assert cp._key_text("k" * 61) == "k" * 59 + "…"


def test_normalize_fills_in_the_defaults():
    p = cp.normalize({"name": "x", "kind": "databricks", "connections": [{"platform": "databricks"}]})
    assert p["id"].startswith("p-") and len(p["id"]) == 14
    assert p["local"]["prefer_local"] is True
    assert p["connections"][0]["compute"] == {"cluster_id": "", "warehouse_id": "", "serverless": False}


def _budget_problems(minutes):
    return [m for m in cp.problems({"name": "x", "kind": "local",
                                    "execution": {"budget": {"remote_minutes_per_day": minutes}}})
            if "remote_minutes_per_day" in m]


def test_a_budget_of_zero_minutes_is_allowed_and_a_negative_one_is_not():
    assert _budget_problems(0) == []
    assert _budget_problems(-1) == ["execution.budget.remote_minutes_per_day must be a number of minutes, or empty."]


def test_a_databricks_config_that_cannot_be_parsed_is_found_with_an_error(tmp_path):
    path = tmp_path / "databrickscfg"
    path.write_bytes(b"host = https://x\n")  # no [section] header
    out = cp.databricks_cli_profiles(path)
    assert out["found"] is True
    assert out["profiles"] == []
    assert out["error"] == "The file can't be read: MissingSectionHeaderError."


def test_connections_are_read_from_config_toml_when_there_is_no_connections_toml(tmp_path):
    (tmp_path / "config.toml").write_bytes(b'[connections.dev]\naccount = "myorg-acct"\n')
    out = cp.snowflake_cli_connections(tmp_path)
    assert [c["name"] for c in out["connections"]] == ["dev"]
    assert out["connections"][0]["account"] == "myorg-acct"


def test_a_connections_entry_in_config_toml_that_is_not_a_table_is_no_connections(tmp_path):
    (tmp_path / "config.toml").write_bytes(b'connections = ["dev"]\n')
    out = cp.snowflake_cli_connections(tmp_path)
    assert out["found"] is True
    assert out["connections"] == []


def test_a_databricks_profile_with_a_client_id_but_no_client_secret_signs_in_with_the_cli(tmp_path):
    path = tmp_path / "databrickscfg"
    path.write_bytes(b"[a]\nhost = https://adb-1.2.azuredatabricks.net\nclient_id = c\n")
    assert [p["auth_type"] for p in cp.databricks_cli_profiles(path)["profiles"]] == ["databricks-cli"]


def test_import_names_each_connection_to_set_up_by_its_id():
    p = cp.validate({"name": "D", "kind": "databricks", "connections": [
        {"id": "dbx", "platform": "databricks",
         "credential": {"method": "oauth-m2m", "client_id": "c", "secret": "env:S"}}]})
    assert cp.import_(cp.export(p))["validation"]["problems"] == ["dbx: add your own sign-in"]


def test_a_cli_profile_on_another_cloud_keeps_that_cloud():
    conn = cp.connection_from_cli_profile({"name": "a", "host": "https://dbc-1.cloud.databricks.com/"})
    assert conn["cloud"] == "aws"
    assert conn["host"] == "https://dbc-1.cloud.databricks.com"


def test_switching_with_one_open_session_says_session():
    plan = cp.switch_plan(None, {"id": "b", "name": "B"}, [{"id": 1}], [])
    assert plan["message"] == "Switching to B closes 1 open session; cells and outputs stay. Variables don't carry over."
    plan = cp.switch_plan(None, {"id": "b", "name": "B"}, [{"id": 1}, {"id": 2}], [])
    assert plan["message"].startswith("Switching to B closes 2 open sessions;")


@pytest.mark.skipif(cp.os.name == "nt", reason="file mode bits are POSIX only")
def test_only_group_or_other_write_bits_earn_the_chmod_note(tmp_path):
    config = tmp_path / "config.toml"
    config.write_bytes(b"")
    config.chmod(0o601)
    assert "notes" not in cp.snowflake_cli_connections(tmp_path)
    config.chmod(0o620)
    assert cp.snowflake_cli_connections(tmp_path)["notes"] == [
        f"{config} can be changed by other users of this computer (chmod 0600 it)."]
