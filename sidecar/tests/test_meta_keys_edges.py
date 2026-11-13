"""Surrogate key tables and source systems of a metadata project (meta/keys.py), the paths the main
tests don't reach: key and source system files that can't be read, the save gate's other refusals,
values written back to TOML, and the platform's key service. All on local disk."""

from __future__ import annotations

import polars as pl
import pytest

from fabric_desktop.central.core import PlatformError
from fabric_desktop.meta import keys as K
from fabric_desktop.meta.project import validate
from fabric_desktop.meta.store import LocalStore
from fabric_desktop.meta.templates import init_project

from test_meta import DAY2, append_entity


@pytest.fixture
def proj(tmp_path):
    return init_project(tmp_path / "project", "3", "Chris", True)


def _keys(p):
    return K.keys_dir(p.root)


def _entity(p, name):
    return p.root / "metadata" / "entities" / f"{name}.toml"


# ---- files that can't be read


def test_a_key_table_file_that_isnt_toml_or_has_another_name_is_reported(proj):
    (_keys(proj) / "broken.toml").write_text("name = ")
    (_keys(proj) / "vendor.toml").write_text('name = "supplier"\n')
    tables, errors = K.load_key_tables(proj.root)
    assert any(e.startswith("keys/broken.toml: not valid TOML") for e in errors)
    assert "keys/vendor.toml: file name must match its name (supplier)" in errors
    assert "supplier" in tables and "broken" not in tables


def test_a_source_systems_file_that_isnt_toml_or_has_a_text_id_is_reported(proj):
    path = _keys(proj) / K.SOURCE_SYSTEMS_FILE
    path.write_text("[[source_system]\n")
    assert K.load_source_systems(proj.root)[0] == []
    assert "not valid TOML" in K.load_source_systems(proj.root)[1][0]
    path.write_text('[[source_system]]\nid = "first"\ncode = "CRM"\n')
    systems, errors = K.load_source_systems(proj.root)
    assert systems == [] and errors == [f"keys/{K.SOURCE_SYSTEMS_FILE}: source system CRM needs a whole-number id"]


# ---- the save gate


def test_the_save_gate_refuses_bad_key_and_source_system_names(proj):
    (_keys(proj) / "Bad-Name.toml").write_text('name = "Bad-Name"\n')
    K.write_source_systems(proj.root, [K.SourceSystem(0, "1crm")])
    path = _keys(proj) / K.SOURCE_SYSTEMS_FILE
    path.write_text(path.read_text() + '[source_system.connection.qa]\nserver_host = "x"\n')
    proj.reload()
    errors = validate(proj, DAY2)
    assert "keys/Bad-Name: name must be lowercase letters, digits or _ and start with a letter" in errors
    assert "source system 1crm: id must be a whole number above 0" in errors
    assert "source system 1crm: code must be letters, digits or _ and start with a letter" in errors
    assert "source system 1crm: unknown environment qa" in errors


def test_the_save_gate_refuses_keys_that_arent_blocks(proj):
    path = _entity(proj, "gold.dim_customer")
    path.write_text('keys = "customer"\n' + path.read_text())
    proj.reload()
    assert "gold.dim_customer: keys must be [[keys]] blocks" in validate(proj, DAY2)


def test_a_key_block_needs_a_source_entity_a_business_key_and_an_int_column(proj):
    append_entity(proj.root, "bronze.customers", '[[keys]]\ntable = "customer"\nbusiness_key = "customer_id"\n')
    silver = _entity(proj, "silver.customers")
    text = silver.read_text().replace('business_key = "customer_id"', "business_key = []")
    silver.write_text(text.replace("[contract.columns]\n", '[contract.columns]\ncustomer_sk = "string"\n'))
    proj.reload()
    errors = validate(proj, DAY2)
    assert "bronze.customers: [[keys]] needs a source entity to read business keys from" in errors
    assert "silver.customers: key table customer needs a business_key column" in errors
    assert "silver.customers: customer_sk is a surrogate key, so its type must be int" in errors


# ---- files written back


def test_values_written_back_to_toml():
    assert K._toml_value(True) == "true" and K._toml_value(False) == "false"
    assert K._toml_value(3) == "3" and K._toml_value(None) == '""' and K._toml_value('a"b') == '"a\\"b"'


def test_a_key_table_name_or_source_system_id_that_cant_be_used_is_refused(proj):
    with pytest.raises(ValueError, match="A key table name must be lowercase"):
        K.write_key_table(proj.root, "Customer")
    with pytest.raises(ValueError, match="The source system id must be a whole number"):
        K.source_system_from_dict({"id": "first", "code": "CRM"})


def test_a_port_that_isnt_a_number_is_left_empty(proj):
    K.write_source_systems(proj.root, [K.SourceSystem(1, "CRM", connections={"dev": {"server_host": "db",
                                                                                     "port": "high"}})])
    proj.reload()
    conns = K.config_tables(proj)["source_connection"]
    assert conns.filter(pl.col("environment") == "dev")["port"].to_list() == [None]


# ---- the platform's key service


class Service:
    """The platform's key service: ensure() says whether it made the table, or refuses."""
    env = "dev"

    def __init__(self, refuse=()):
        self.refuse = set(refuse)

    def ensure(self, name, bk, description):
        if name in self.refuse:
            raise PlatformError("Only platform admins create key tables")
        return True


def test_sync_with_the_key_service_says_what_it_made_and_what_it_couldnt(proj, monkeypatch):
    monkeypatch.setattr(K, "is_service", lambda store: True)
    names = sorted(proj.key_tables)
    out = K.sync(proj, Service(refuse=names[:1]))
    assert out["platform"] is True and out["missing"] == names[:1] and out["created"] == names[1:]


def test_a_key_table_made_while_another_run_keeps_committing_gives_up(proj, monkeypatch):
    from deltalake.exceptions import CommitFailedError

    store = LocalStore(proj.root / ".local" / "dev", "dev")
    kt = next(iter(proj.key_tables.values()))

    def collide(*args, **kwargs):
        raise CommitFailedError("another run committed first")

    monkeypatch.setattr(store, "merge_insert", collide)
    monkeypatch.setattr(K.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="other runs kept changing the key table"):
        K.ensure_key_table(store, kt)
