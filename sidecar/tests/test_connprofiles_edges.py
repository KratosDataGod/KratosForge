"""Connection profiles: the checks and CLI readers the main tests don't reach. Offline; nothing signs in."""

from __future__ import annotations

import itertools
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import connprofiles as cp
from tests.test_connprofiles import hybrid, snowflake


def test_a_snowflake_name_field_that_isnt_a_name_is_refused():
    p = snowflake()
    p["connections"][0]["warehouse"] = "W" * 300
    p["connections"][0]["role"] = 42
    found = cp.problems(p)
    assert "connections[0].warehouse must be a name (up to 255 characters)." in found
    assert "connections[0].role must be a name (up to 255 characters)." in found


def test_a_connection_to_an_unknown_platform_is_named_and_skipped():
    p = hybrid()
    p["connections"].append({"id": "bq", "platform": "bigquery"})
    found = cp.problems(p)
    assert any(m.startswith("connections[2].platform must be one of:") for m in found)


def test_execution_and_validation_values_are_checked():
    found = cp.problems(hybrid(execution={"remote_compute": "sometimes"}, validation={"status": "great"}))
    assert any(m.startswith("execution.remote_compute must be one of:") for m in found)
    assert any(m.startswith("validation.status must be one of:") for m in found)


def test_an_imported_profile_never_takes_an_id_already_in_use(monkeypatch):
    doc = cp.export(cp.validate(hybrid()))
    hexes = itertools.chain(["a" * 32], itertools.repeat("b" * 32))
    monkeypatch.setattr(cp, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=next(hexes))))
    back = cp.import_(doc, existing_ids={"p-" + "a" * 12})
    assert back["id"] == "p-" + "b" * 12


def test_the_databricks_config_file_is_the_one_the_cli_uses(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(tmp_path / "custom.cfg"))
    assert cp.databricks_config_path() == tmp_path / "custom.cfg"
    monkeypatch.delenv("DATABRICKS_CONFIG_FILE")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert cp.databricks_config_path() == tmp_path / ".databrickscfg"


def test_an_unreadable_snowflake_config_says_so(tmp_path):
    (tmp_path / "config.toml").write_text("this is = = not toml", encoding="utf-8")
    found = cp.snowflake_cli_connections(tmp_path)
    assert found["found"] is True and found["connections"] == []
    assert found["error"] == f"Couldn't read {tmp_path / 'config.toml'}."


def test_a_connection_that_isnt_a_table_is_skipped(tmp_path):
    (tmp_path / "connections.toml").write_text('loose = "value"\n[dev]\naccount = "myorg-dev"\n', encoding="utf-8")
    found = cp.snowflake_cli_connections(tmp_path)
    assert [c["name"] for c in found["connections"]] == ["dev"]


@pytest.mark.skipif(os.name == "nt", reason="mode bits are POSIX; the Snowflake CLI checks them only there")
def test_a_file_gone_since_it_was_read_gets_no_note(tmp_path, monkeypatch):
    (tmp_path / "connections.toml").write_text('[dev]\naccount = "myorg-dev"\n', encoding="utf-8")
    (tmp_path / "connections.toml").chmod(0o666)
    real_stat, real_is_file, real_read = Path.stat, Path.is_file, cp._read_toml
    gone = []

    def read_then_delete(path):  # another program removes the file right after it was read
        out = real_read(path)
        gone.append(path.name)
        return out

    def stat(self, *a, **k):
        if self.name in gone:
            raise FileNotFoundError(self)
        return real_stat(self, *a, **k)

    monkeypatch.setattr(cp, "_read_toml", read_then_delete)
    monkeypatch.setattr(Path, "is_file", lambda self: True if self.name in gone else real_is_file(self))
    monkeypatch.setattr(Path, "stat", stat)
    found = cp.snowflake_cli_connections(tmp_path)
    assert "notes" not in found


@pytest.mark.parametrize("host", ["https://adb-1.7.azuredatabricks.net:notaport", "https://[adb-1.azuredatabricks.net"])
def test_a_host_a_url_parser_cant_read_is_refused(host):
    p = hybrid()
    p["connections"][1]["host"] = host
    assert any("connections[1]" in m and "host" in m for m in cp.problems(p))
